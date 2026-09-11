from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dkt_isp_bandwidth_cap')
class TestIspBandwidthCap(TransactionCase):
    """Cap 7M/7M kecuali Sinaman dan Bulan Jahe. Tidak kick, tidak isolir."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['isp.pppoe.profile.template']
        cls.Template._ensure_canonical_profiles()
        cls.sapu = cls.Template.search([('name', '=', 'SAPU-JAGAD')], limit=1)
        cls.pkg200 = cls.Template.search([('name', '=', '200-10M')], limit=1)
        cls.isolir = cls.Template.search([('is_isolir', '=', True)], limit=1)
        cls.capped = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Hemat Uji',
            'host': '192.0.2.17:18728',
            'username': 'test',
            'password': 'test',
            'active': True,
        })
        cls.sinaman = cls.env['isp.mikrotik.config'].search([
            ('name', '=', 'Sinaman'),
        ], limit=1)
        if not cls.sinaman:
            cls.sinaman = cls.env['isp.mikrotik.config'].create({
                'name': 'Sinaman',
                'host': '192.0.2.18:18728',
                'username': 'test',
                'password': 'test',
                'active': True,
            })
        cls.bulan = cls.env['isp.mikrotik.config'].search([
            ('name', '=', 'Bulan Jahe'),
        ], limit=1)
        if not cls.bulan:
            cls.bulan = cls.env['isp.mikrotik.config'].create({
                'name': 'Bulan Jahe',
                'host': '192.0.2.19:18728',
                'username': 'test',
                'password': 'test',
                'active': True,
            })

    def test_exempt_by_name_even_without_flag(self):
        self.assertTrue(self.sinaman.is_bandwidth_cap_exempt())
        self.assertTrue(self.bulan.is_bandwidth_cap_exempt())
        self.assertFalse(self.capped.is_bandwidth_cap_exempt())

    def test_prepare_vals_keeps_catalog_before_cap(self):
        self.assertFalse(self.Template._bandwidth_cap_enabled())
        self.assertEqual(self.pkg200._prepare_mikrotik_vals(self.capped).get('rate-limit'), '10M/10M')
        self.assertEqual(self.sapu._prepare_mikrotik_vals(self.capped).get('rate-limit'), '')
        self.assertEqual(self.isolir._prepare_mikrotik_vals(self.capped).get('rate-limit'), '64k/64k')

    def test_prepare_vals_caps_non_exempt_after_flag(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'dkt_isp_billing.bandwidth_cap_7m', 'True',
        )
        self.assertEqual(self.pkg200._applied_rate_limit(self.capped), '7M/7M')
        self.assertEqual(self.sapu._applied_rate_limit(self.capped), '7M/7M')
        self.assertEqual(self.pkg200._applied_rate_limit(self.sinaman), '10M/10M')
        self.assertEqual(self.sapu._applied_rate_limit(self.sinaman), '')
        self.assertEqual(self.isolir._applied_rate_limit(self.capped), '64k/64k')

    def test_samura_uses_10m_cap(self):
        samura = self.env['isp.mikrotik.config'].search([
            ('name', '=', 'Samura'),
        ], limit=1)
        if not samura:
            samura = self.env['isp.mikrotik.config'].create({
                'name': 'Samura',
                'host': '192.0.2.22:18728',
                'username': 'test',
                'password': 'test',
                'active': True,
            })
        self.env['ir.config_parameter'].sudo().set_param(
            'dkt_isp_billing.bandwidth_cap_7m', 'True',
        )
        self.assertEqual(samura.bandwidth_cap_rate_applied(), '10M/10M')
        self.assertEqual(self.sapu._applied_rate_limit(samura), '10M/10M')
        self.assertEqual(self.pkg200._applied_rate_limit(samura), '10M/10M')
        self.assertEqual(self.isolir._applied_rate_limit(samura), '64k/64k')
        self.assertEqual(self.capped.bandwidth_cap_rate_applied(), '7M/7M')

    def test_apply_marks_exempt_and_skips_those_routers(self):
        fake_api = type('Api', (), {})()

        class FakeResource:
            def get(self):
                return [
                    {'.id': '*1', 'name': 'SAPU-JAGAD', 'rate-limit': ''},
                    {'.id': '*2', 'name': '200-10M', 'rate-limit': '10M/10M'},
                    {'.id': '*3', 'name': 'isolir', 'rate-limit': '64k/64k'},
                ]

            def set(self, **kwargs):
                self.last = kwargs

        resource = FakeResource()

        def fake_get_resource(path):
            self.assertEqual(path, '/ppp/profile')
            return resource

        fake_api.get_resource = fake_get_resource
        fake_api.connection_pool = type('Pool', (), {'disconnect': lambda self: None})()

        def fake_connect():
            return fake_api

        with patch.object(
            type(self.capped), 'get_connection', side_effect=fake_connect,
        ), patch.object(
            type(self.env['isp.radius']), 'sync_package_groups',
            return_value=(True, None),
        ):
            self.Template.action_apply_bandwidth_savings_7m()

        self.assertTrue(self.Template._bandwidth_cap_enabled())
        self.assertTrue(self.sinaman.bandwidth_cap_exempt)
        self.assertTrue(self.bulan.bandwidth_cap_exempt)
        self.assertEqual(resource.last.get('rate-limit'), '7M/7M')

    def test_radius_groups_use_7m_when_cap_and_radius_nas(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'dkt_isp_billing.bandwidth_cap_7m', 'True',
        )
        self.env['isp.mikrotik.config'].create({
            'name': 'Pintu Angin Uji Cap',
            'host': '192.0.2.21:18728',
            'username': 'test',
            'password': 'test',
            'active': True,
            'session_backend': 'radius',
            'radius_nas_ip': '203.0.113.21',
            'radius_secret': 'x',
        })
        captured = []

        def fake_execute(statements):
            captured.extend(statements)
            return True, None

        with patch.object(
            type(self.env['isp.radius']), '_execute', side_effect=fake_execute,
        ):
            ok, err = self.env['isp.radius'].sync_package_groups()
        self.assertTrue(ok)
        self.assertFalse(err)
        rates = {
            row[1][0]: row[1][3]
            for row in captured
            if 'INSERT INTO radgroupreply' in row[0] and row[1][1] == 'Mikrotik-Rate-Limit'
        }
        self.assertEqual(rates.get('SAPU-JAGAD'), '7M/7M')
        self.assertEqual(rates.get('200-10M'), '7M/7M')
        self.assertEqual(rates.get('isolir'), '64k/64k')
