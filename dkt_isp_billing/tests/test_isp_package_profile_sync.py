from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dkt_isp_package_profile')
class TestIspPackageProfileSync(TransactionCase):
    """Profile per paket + secret mengikuti langganan, tanpa isolir/kick."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.area = cls.env['isp.area'].create({
            'name': 'Desa Uji Profile',
            'code': 'UJI_PROFILE',
        })
        cls.router = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Uji Profile',
            'host': '192.0.2.77:18728',
            'username': 'test',
            'password': 'test',
            'active': True,
            'area_id': cls.area.id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Pelanggan Uji Profile',
            'customer_rank': 1,
            'area_id': cls.area.id,
        })
        cls.package = cls.env.ref('dkt_isp_billing.isp_package_200')
        cls.cpe = cls.env['isp.cpe'].create({
            'name': 'CPE Uji Profile',
            'partner_id': cls.partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'uji-profile-200',
            'pppoe_password': 'x',
            'mikrotik_config_id': cls.router.id,
            'state': 'open',
        })
        cls.sub = cls.env['isp.subscription'].create({
            'partner_id': cls.partner.id,
            'cpe_id': cls.cpe.id,
            'package_id': cls.package.id,
            'state': 'open',
        })

    def test_assigned_profile_ignores_rate_limit_flag(self):
        self.env['isp.pppoe.profile.template']._ensure_canonical_profiles()
        self.assertFalse(self.env['isp.package']._apply_package_rate_limit())
        self.assertEqual(self.package.get_pppoe_profile_name(), 'SAPU-JAGAD')
        self.assertEqual(self.package.get_assigned_profile_name(), '200-10M')
        self.assertEqual(self.sub._get_assigned_profile_name(), '200-10M')

    def test_subscription_profile_override_keeps_package_price(self):
        self.env['isp.pppoe.profile.template']._ensure_canonical_profiles()
        variant = self.env['isp.pppoe.profile.template'].search([
            ('name', '=', '200-15M'),
        ], limit=1)
        self.assertTrue(variant)
        self.sub.profile_template_id = variant.id
        self.assertEqual(self.sub.package_id.price, 200000)
        self.assertEqual(self.sub._get_assigned_profile_name(), '200-15M')

    def test_allow_multisession_sets_only_one_false(self):
        templates = self.env['isp.pppoe.profile.template'].search([
            ('active', '=', True),
        ])
        templates.write({'only_one': True})
        with patch.object(
            type(templates), 'action_push_to_all_routers', return_value=True,
        ) as push:
            self.env['isp.pppoe.profile.template'].action_allow_multisession_all()
        push.assert_called_once()
        self.assertFalse(any(templates.mapped('only_one')))

    def test_sync_assigns_subscription_profile_and_skips_isolated(self):
        isolated_partner = self.env['res.partner'].create({
            'name': 'Pelanggan Isolir Profile',
            'customer_rank': 1,
            'area_id': self.area.id,
        })
        isolated_cpe = self.env['isp.cpe'].create({
            'name': 'CPE Isolir Profile',
            'partner_id': isolated_partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'uji-profile-isolir',
            'pppoe_password': 'x',
            'mikrotik_config_id': self.router.id,
            'state': 'isolated',
        })
        self.env['isp.subscription'].create({
            'partner_id': isolated_partner.id,
            'cpe_id': isolated_cpe.id,
            'package_id': self.package.id,
            'state': 'isolated',
        })
        captured = {}

        def fake_assign(mapping):
            captured.update(mapping)
            return {'updated': len(mapping), 'unchanged': 0, 'missing': 0, 'errors': []}

        with patch.object(
            type(self.env['isp.pppoe.profile.template']),
            'action_push_to_all_routers',
            return_value={'params': {'message': 'push ok'}},
        ), patch.object(
            type(self.router), 'assign_secret_profiles', side_effect=fake_assign,
        ):
            self.env['isp.package'].action_sync_router_profiles_from_subscriptions()
        self.assertEqual(captured.get('uji-profile-200'), '200-10M')
        self.assertNotIn('uji-profile-isolir', captured)
        self.assertTrue(self.env['isp.package']._apply_package_rate_limit())
        self.assertFalse(any(
            self.env['isp.pppoe.profile.template'].search([
                ('active', '=', True),
            ]).mapped('only_one')
        ))
