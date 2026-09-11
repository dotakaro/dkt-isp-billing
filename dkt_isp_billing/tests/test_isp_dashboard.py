from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dkt_isp_dashboard')
class TestIspDashboard(TransactionCase):
    """KPI dashboard + health router tanpa N+1 dan tanpa memaksa Liang Pangi."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Pelanggan Dashboard',
            'customer_rank': 1,
            'state': 'active',
        })
        cls.router = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Uji Dashboard',
            'host': 'id01.example.test:16287',
            'username': 'test',
            'password': 'test',
            'active': True,
        })
        cls.router_loop = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Loopback Uji',
            'host': '127.0.0.1:18730',
            'username': 'test',
            'password': 'test',
            'active': True,
        })
        cls.router_liang = cls.env['isp.mikrotik.config'].create({
            'name': 'Liang Pangi',
            'host': 'id01.example.test:16500',
            'username': 'test',
            'password': 'test',
            'active': True,
        })
        cls.router_off = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Mati Uji',
            'host': 'id01.example.test:16014',
            'username': 'test',
            'password': 'test',
            'active': False,
        })

    def _create_cpe(self, name, username, router, **vals):
        values = {
            'name': name,
            'partner_id': self.partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': username,
            'pppoe_password': 'secret123',
            'mikrotik_config_id': router.id,
            'state': 'open',
            'pppoe_status': 'connected',
        }
        values.update(vals)
        return self.env['isp.cpe'].create(values)

    def test_dashboard_counts_and_actions(self):
        self._create_cpe('CPE on', 'dash-on', self.router, pppoe_status='connected')
        self._create_cpe(
            'CPE off', 'dash-off', self.router, pppoe_status='disconnected',
        )
        self._create_cpe(
            'CPE multi', 'dash-multi', self.router,
            pppoe_status='connected', is_multisession=True, pppoe_session_count=2,
        )
        self._create_cpe(
            'CPE isolir', 'dash-iso', self.router,
            state='isolated', pppoe_status='disconnected',
        )
        data = self.env['isp.dashboard'].get_dashboard_data()
        self.assertGreaterEqual(data['network']['connected'], 2)
        self.assertGreaterEqual(data['network']['disconnected'], 2)
        self.assertGreaterEqual(data['network']['multisession'], 1)
        self.assertGreaterEqual(data['network']['isolated'], 1)
        self.assertIn('finance', data)
        self.assertTrue(data['routers'])
        names = [r['name'] for r in data['routers']]
        self.assertIn('Liang Pangi', names)
        row = next(r for r in data['routers'] if r['id'] == self.router.id)
        self.assertGreaterEqual(row['cpe_connected'], 2)
        self.assertGreaterEqual(row['cpe_disconnected'], 1)
        self.assertGreaterEqual(row['cpe_multisession'], 1)

        connected = self.env['isp.dashboard'].action_open_kpi('cpe_connected')
        self.assertEqual(connected['type'], 'ir.actions.act_window')
        self.assertEqual(connected['res_model'], 'isp.cpe')
        self.assertIn(('pppoe_status', '=', 'connected'), connected['domain'])

        router_action = self.env['isp.dashboard'].action_open_kpi(
            'cpe_router',
            {'router_id': self.router.id, 'pppoe_status': 'disconnected'},
        )
        self.assertIn(('mikrotik_config_id', '=', self.router.id), router_action['domain'])
        self.assertIn(('pppoe_status', '=', 'disconnected'), router_action['domain'])

        form = self.env['isp.dashboard'].action_open_kpi(
            'router_form', {'router_id': self.router.id},
        )
        self.assertEqual(form['res_model'], 'isp.mikrotik.config')
        self.assertEqual(form['res_id'], self.router.id)

        review = self.env['isp.dashboard'].action_open_kpi('subscription_review')
        self.assertEqual(review['res_model'], 'isp.subscription')
        self.assertIn(('is_special_treatment', '=', True), review['domain'])

        overdue = self.env['isp.dashboard'].action_open_kpi('subscription_overdue')
        self.assertIn('|', overdue['domain'])

        draft = self.env['isp.dashboard'].action_open_kpi('invoice_draft_month')
        self.assertEqual(draft['res_model'], 'account.move')
        self.assertIn(('state', '=', 'draft'), draft['domain'])

    def test_dashboard_query_count_not_n_plus_one(self):
        for idx in range(12):
            self._create_cpe(
                'CPE mass %s' % idx, 'dash-mass-%s' % idx, self.router,
                pppoe_status='connected' if idx % 2 == 0 else 'disconnected',
            )
        with self.assertQueryCount(80):
            data = self.env['isp.dashboard'].get_dashboard_data()
        self.assertGreaterEqual(data['network']['total'], 12)

    def test_health_skips_loopback_inactive_liang(self):
        self.router_loop._update_health()
        self.assertEqual(self.router_loop.health_state, 'skipped')
        self.assertFalse(self.router_loop.last_ping_ok)
        self.assertIn('127', self.router_loop.last_error)

        self.router_off._update_health()
        self.assertEqual(self.router_off.health_state, 'inactive')
        self.assertFalse(self.router_off.last_ping_ok)

        self.router_liang._update_health()
        self.assertEqual(self.router_liang.health_state, 'inactive')
        self.assertFalse(self.router_liang.last_ping_ok)
        self.assertIn('Tidak dipaksa', self.router_liang.last_error)

    def test_health_tcp_success_does_not_write_mac(self):
        cpe = self._create_cpe('CPE health', 'dash-health', self.router)
        mac_before = cpe.mac_address
        with patch.object(
            type(self.router), '_icmp_ping', return_value=(True, 11, False),
        ), patch('socket.create_connection') as mock_conn:
            mock_conn.return_value.__enter__.return_value = object()
            mock_conn.return_value.__exit__.return_value = False
            result = self.router._update_health()
        self.assertTrue(result['ok'])
        self.assertEqual(self.router.health_state, 'reachable')
        self.assertTrue(self.router.last_ping_ok)
        self.assertGreater(self.router.last_ping_ms, 0)
        self.assertFalse(self.router.last_error)
        self.assertEqual(cpe.mac_address, mac_before)

    def test_dashboard_load_does_not_ping(self):
        with patch.object(
            type(self.env['isp.mikrotik.config']), '_tcp_ping',
        ) as mock_tcp:
            self.env['isp.dashboard'].get_dashboard_data()
            mock_tcp.assert_not_called()

    def test_billing_dashboard_kpis_and_actions(self):
        Dash = self.env['isp.dashboard']
        data = Dash.get_billing_dashboard_data()
        self.assertIn('period_label', data)
        self.assertIn('kpis', data)
        self.assertIn('areas', data)
        self.assertIn('draft_count', data['kpis'])
        self.assertIn('outstanding_count', data['kpis'])
        self.assertIn('proof_queue_count', data['kpis'])
        self.assertIsInstance(data['areas'], list)

        draft = Dash.action_open_billing_kpi('invoice_draft_month')
        self.assertEqual(draft['res_model'], 'account.move')
        self.assertIn(('state', '=', 'draft'), draft['domain'])

        outstanding = Dash.action_open_billing_kpi('invoice_outstanding')
        self.assertEqual(outstanding['res_model'], 'account.move')
        self.assertIn(('payment_state', 'in', ['not_paid', 'partial', 'in_payment']), outstanding['domain'])
        self.assertIn(('amount_residual', '>', 0), outstanding['domain'])
        self.assertIn(('state', '=', 'paid'), Dash._domain_payment_month())

        late = Dash.action_open_billing_kpi('subscription_overdue')
        self.assertEqual(late['res_model'], 'isp.subscription')
        self.assertIn(('is_special_treatment', '=', False), late['domain'])

        run = Dash.action_open_billing_kpi('billing_run')
        self.assertEqual(run['res_model'], 'isp.billing.run.wizard')

        isolate = Dash.action_open_billing_kpi('isolate_bulk')
        self.assertEqual(isolate['res_model'], 'isp.isolate.bulk.wizard')

        proofs = Dash.action_open_billing_kpi('proof_queue')
        self.assertEqual(proofs['res_model'], 'isp.payment.proof')

    def test_billing_dashboard_query_count(self):
        with self.assertQueryCount(80):
            data = self.env['isp.dashboard'].get_billing_dashboard_data()
        self.assertIn('kpis', data)
