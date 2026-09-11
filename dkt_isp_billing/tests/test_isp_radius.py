from unittest.mock import patch

from odoo.tests import HttpCase, TransactionCase, tagged


@tagged('post_install', '-at_install', 'dkt_isp_radius')
class TestIspRadius(TransactionCase):
    """RADIUS: dual-write SQL, isolir ganti profile (bukan disable/reject)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.area = cls.env['isp.area'].create({
            'name': 'Desa Uji RADIUS',
            'code': 'UJI_RADIUS',
        })
        cls.router = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Uji RADIUS',
            'host': '192.0.2.89:18728',
            'username': 'test',
            'password': 'test',
            'active': True,
            'area_id': cls.area.id,
            'session_backend': 'radius',
            'radius_nas_ip': '203.0.113.10',
            'radius_nas_identifier': 'desa-uji-radius',
            'radius_secret': 'secret-nas-uji',
        })
        cls.secret_router = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Secret Lama',
            'host': '192.0.2.90:18728',
            'username': 'test',
            'password': 'test',
            'active': True,
            'area_id': cls.area.id,
            'session_backend': 'mikrotik_secret',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Pelanggan Uji RADIUS',
            'customer_rank': 1,
            'area_id': cls.area.id,
        })
        cls.package = cls.env.ref('dkt_isp_billing.isp_package_150')
        cls.cpe = cls.env['isp.cpe'].create({
            'name': 'CPE Uji RADIUS',
            'partner_id': cls.partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'uji-radius-1',
            'pppoe_password': 'pwdRadius1',
            'mikrotik_config_id': cls.router.id,
            'state': 'open',
        })
        cls.sub = cls.env['isp.subscription'].create({
            'partner_id': cls.partner.id,
            'cpe_id': cls.cpe.id,
            'package_id': cls.package.id,
            'state': 'open',
        })
        cls.Radius = cls.env['isp.radius']

    def test_auto_isolate_stays_off(self):
        self.assertFalse(self.env['isp.subscription']._auto_isolate_config()['enabled'])

    def test_isolir_profile_name(self):
        self.env['isp.pppoe.profile.template']._ensure_canonical_profiles()
        self.assertEqual(self.Radius.isolir_profile_name(), 'isolir')
        self.assertEqual(self.Radius.open_profile_name(self.cpe), '150-7M')

    def test_nas_identifier_from_router(self):
        self.assertEqual(self.Radius.nas_identifier(self.router), 'desa-uji-radius')

    def test_sync_cpe_builds_password_and_nas_check(self):
        self.env['isp.pppoe.profile.template']._ensure_canonical_profiles()
        captured = []

        def fake_execute(statements):
            captured.extend(statements)
            return True, None

        with patch.object(type(self.Radius), '_execute', side_effect=fake_execute):
            ok, err = self.Radius.sync_cpe(self.cpe, isolated=False)
        self.assertTrue(ok)
        self.assertFalse(err)
        attrs = [row[1][1] if 'INSERT INTO radcheck' in row[0] else None for row in captured]
        self.assertIn('Cleartext-Password', attrs)
        self.assertIn('NAS-Identifier', attrs)
        insert_pw = [row for row in captured if 'INSERT INTO radcheck' in row[0] and row[1][1] == 'Cleartext-Password']
        self.assertEqual(insert_pw[0][1][3], 'pwdRadius1')
        self.assertTrue(any('INSERT INTO radusergroup' in row[0] for row in captured))
        usergroup = [row for row in captured if 'INSERT INTO radusergroup' in row[0]]
        self.assertEqual(usergroup[0][1][1], '150-7M')
        self.assertFalse(any(
            'Mikrotik-Group' in (row[1][1] if len(row[1]) > 1 else '')
            and 'INSERT INTO radreply' in row[0]
            for row in captured
        ))

    def test_sync_isolated_sets_group_not_reject(self):
        captured = []

        def fake_execute(statements):
            captured.extend(statements)
            return True, None

        with patch.object(type(self.Radius), '_execute', side_effect=fake_execute):
            self.Radius.sync_cpe(self.cpe, isolated=True)
        sql = ' '.join(row[0] for row in captured)
        params = ' '.join(str(row[1]) for row in captured)
        self.assertNotIn('Auth-Type', sql)
        self.assertIn('Mikrotik-Group', params)
        self.assertIn('isolir', params)
        self.assertTrue(any('INSERT INTO radusergroup' in row[0] for row in captured))

    def test_sync_package_groups_registers_profiles(self):
        self.env['isp.pppoe.profile.template']._ensure_canonical_profiles()
        captured = []

        def fake_execute(statements):
            captured.extend(statements)
            return True, None

        with patch.object(type(self.Radius), '_execute', side_effect=fake_execute):
            ok, err = self.Radius.sync_package_groups()
        self.assertTrue(ok)
        self.assertFalse(err)
        groups = [
            row[1][0] for row in captured
            if 'INSERT INTO radgroupreply' in row[0] and row[1][1] == 'Mikrotik-Group'
        ]
        self.assertIn('200-10M', groups)
        self.assertIn('isolir', groups)
        rates = [
            row[1][3] for row in captured
            if 'INSERT INTO radgroupreply' in row[0] and row[1][1] == 'Mikrotik-Rate-Limit'
        ]
        self.assertIn('10M/10M', rates)

    def test_radius_db_hosts_keeps_local_and_production(self):
        hosts = self.Radius._radius_db_hosts('dkt-isp-db')
        self.assertEqual(hosts[0], 'dkt-isp-db')
        self.assertIn('db', hosts)

    def test_isolate_radius_uses_profile_not_disable(self):
        with patch.object(
            type(self.router), 'apply_isolir_profile', return_value=(True, None),
        ) as apply_iso, patch.object(
            type(self.router), 'set_secret_disabled', return_value=(True, None),
        ) as disable:
            ok, err = self.sub.isolate_safe()
        self.assertTrue(ok)
        self.assertFalse(err)
        apply_iso.assert_called_once()
        disable.assert_not_called()
        self.assertEqual(self.sub.state, 'isolated')

    def test_isolate_secret_backend_still_disables(self):
        self.cpe.mikrotik_config_id = self.secret_router.id
        with patch.object(
            type(self.secret_router), 'set_secret_disabled', return_value=(True, None),
        ) as disable, patch.object(
            type(self.secret_router), 'apply_isolir_profile', return_value=(True, None),
        ) as apply_iso:
            ok, err = self.sub.isolate_safe()
        self.assertTrue(ok)
        disable.assert_called_once()
        apply_iso.assert_not_called()

    def test_enable_radius_uses_profile(self):
        self.sub.write({'state': 'isolated'})
        self.cpe.write({'state': 'isolated'})
        with patch.object(
            type(self.router), 'apply_isolir_profile', return_value=(True, None),
        ) as apply_iso:
            ok, err = self.sub.enable_safe()
        self.assertTrue(ok)
        apply_iso.assert_called_once()
        self.assertEqual(apply_iso.call_args.kwargs.get('isolated'), False)
        self.assertEqual(self.sub.state, 'open')

    def test_landing_url(self):
        url = self.Radius.landing_url()
        self.assertIn('/isp/isolir', url)


@tagged('post_install', '-at_install', 'dkt_isp_radius')
class TestIspIsolirLanding(HttpCase):

    def test_isolir_page_is_public_and_polite(self):
        response = self.url_open('/isp/isolir')
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn('Layanan sementara dibatasi', body)
        self.assertIn('administrasi tagihan', body)
        self.assertNotIn('denda', body.lower())
