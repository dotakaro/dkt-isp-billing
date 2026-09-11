from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dkt_isp_multisession')
class TestPppoeMultisession(TransactionCase):
    """Deteksi multi-sesi per (router + username), tanpa menulis MAC/IP."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Pelanggan Uji Multi-sesi',
            'customer_rank': 1,
        })
        cls.router_a = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Uji Multi A',
            'host': '127.0.0.1:18728',
            'username': 'test-a',
            'password': 'test-a',
            'active': False,
        })
        cls.router_b = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Uji Multi B',
            'host': '127.0.0.1:18729',
            'username': 'test-b',
            'password': 'test-b',
            'active': False,
        })

    def _create_cpe(self, name, username, router):
        return self.env['isp.cpe'].create({
            'name': name,
            'partner_id': self.partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': username,
            'pppoe_password': 'secret123',
            'mikrotik_config_id': router.id,
            'state': 'open',
        })

    def _session(self, name, caller_id, address='10.10.10.10', session_id='1'):
        return {
            'name': name,
            'caller-id': caller_id,
            'address': address,
            'uptime': '1h',
            'session-id': session_id,
            'interface': '',
        }

    def test_group_counts_same_username_on_same_router(self):
        grouped = self.env['isp.cpe']._group_pppoe_sessions_by_name([
            self._session('user-satu', 'AA:BB:CC:00:00:01', session_id='1'),
            self._session('USER-SATU', 'AA:BB:CC:00:00:02', session_id='2'),
            self._session('user-lain', 'AA:BB:CC:00:00:03', session_id='3'),
        ])
        self.assertEqual(len(grouped['user-satu']), 2)
        self.assertEqual(len(grouped['user-lain']), 1)

    def test_prepare_vals_flags_multisession_and_skips_mac_ip(self):
        cpe = self._create_cpe('CPE multi', 'zz-multi-flag', self.router_a)
        sessions = [
            self._session('zz-multi-flag', 'AA:BB:CC:11:11:01', '10.1.1.1', '1'),
            self._session('zz-multi-flag', 'AA:BB:CC:11:11:02', '10.1.1.2', '2'),
        ]
        vals = cpe._prepare_pppoe_status_vals(
            sessions[0], {}, fields.Datetime.now(), sessions=sessions,
        )
        self.assertNotIn('mac_address', vals)
        self.assertNotIn('ip_address', vals)
        self.assertEqual(vals['pppoe_session_count'], 2)
        self.assertTrue(vals['is_multisession'])
        self.assertIn('AA:BB:CC:11:11:01', vals['pppoe_caller_ids'])
        self.assertIn('AA:BB:CC:11:11:02', vals['pppoe_caller_ids'])

    def test_single_or_zero_session_clears_flag(self):
        cpe = self._create_cpe('CPE clear', 'zz-multi-clear', self.router_a)
        one = [self._session('zz-multi-clear', 'AA:BB:CC:22:22:01')]
        vals_one = cpe._prepare_pppoe_status_vals(
            one[0], {}, fields.Datetime.now(), sessions=one,
        )
        self.assertEqual(vals_one['pppoe_session_count'], 1)
        self.assertFalse(vals_one['is_multisession'])

        vals_zero = cpe._prepare_pppoe_status_vals(
            None, {}, fields.Datetime.now(), sessions=[],
        )
        self.assertEqual(vals_zero['pppoe_session_count'], 0)
        self.assertFalse(vals_zero['is_multisession'])
        self.assertEqual(vals_zero['pppoe_status'], 'disconnected')

    def test_same_username_different_router_is_not_multisession(self):
        cpe_a = self._create_cpe('CPE area A', 'zz-shared-user', self.router_a)
        cpe_b = self._create_cpe('CPE area B', 'zz-shared-user', self.router_b)
        session_a = [self._session('zz-shared-user', 'AA:BB:CC:33:33:01')]
        session_b = [self._session('zz-shared-user', 'AA:BB:CC:33:33:02')]
        vals_a = cpe_a._prepare_pppoe_status_vals(
            session_a[0], {}, fields.Datetime.now(), sessions=session_a,
        )
        vals_b = cpe_b._prepare_pppoe_status_vals(
            session_b[0], {}, fields.Datetime.now(), sessions=session_b,
        )
        self.assertFalse(vals_a['is_multisession'])
        self.assertFalse(vals_b['is_multisession'])
        self.assertEqual(vals_a['pppoe_session_count'], 1)
        self.assertEqual(vals_b['pppoe_session_count'], 1)

    def test_write_status_sets_and_clears_flag_without_validation_error(self):
        cpe = self._create_cpe('CPE write', 'zz-multi-write', self.router_a)
        sessions = [
            self._session('zz-multi-write', 'AA:BB:CC:44:44:01', session_id='1'),
            self._session('zz-multi-write', 'AA:BB:CC:44:44:02', session_id='2'),
        ]
        try:
            cpe._write_pppoe_status(
                cpe._prepare_pppoe_status_vals(
                    sessions[0], {}, fields.Datetime.now(), sessions=sessions,
                ),
                source='manual',
            )
        except ValidationError as exc:
            self.fail(f'Refresh multi-sesi tidak boleh raise ValidationError: {exc}')
        self.assertTrue(cpe.is_multisession)
        self.assertEqual(cpe.pppoe_session_count, 2)
        self.assertFalse(cpe.mac_address)
        self.assertFalse(cpe.ip_address)

        cpe._write_pppoe_status(
            cpe._prepare_pppoe_status_vals(
                None, {}, fields.Datetime.now(), sessions=[],
            ),
            source='manual',
        )
        self.assertFalse(cpe.is_multisession)
        self.assertEqual(cpe.pppoe_session_count, 0)

    def test_history_only_when_multisession_changes(self):
        cpe = self._create_cpe('CPE hist', 'zz-multi-hist', self.router_a)
        sessions = [
            self._session('zz-multi-hist', 'AA:BB:CC:55:55:01', session_id='1'),
            self._session('zz-multi-hist', 'AA:BB:CC:55:55:02', session_id='2'),
        ]
        now = fields.Datetime.now()
        vals_multi = cpe._prepare_pppoe_status_vals(
            sessions[0], {}, now, sessions=sessions,
        )
        cpe._write_pppoe_status(vals_multi, source='manual')
        enter = cpe.pppoe_history_ids.filtered(
            lambda h: h.event_type == 'multisession_enter'
        )
        self.assertEqual(len(enter), 1)
        self.assertEqual(enter.session_count, 2)

        cpe._write_pppoe_status(vals_multi, source='manual')
        enter_again = cpe.pppoe_history_ids.filtered(
            lambda h: h.event_type == 'multisession_enter'
        )
        self.assertEqual(len(enter_again), 1)

        cpe._write_pppoe_status(
            cpe._prepare_pppoe_status_vals(
                sessions[:1][0], {}, now, sessions=sessions[:1],
            ),
            source='manual',
        )
        leave = cpe.pppoe_history_ids.filtered(
            lambda h: h.event_type == 'multisession_leave'
        )
        self.assertEqual(len(leave), 1)
        self.assertEqual(leave.session_count, 1)
