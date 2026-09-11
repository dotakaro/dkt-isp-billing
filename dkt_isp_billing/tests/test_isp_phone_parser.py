from odoo.tests import TransactionCase, tagged

from odoo.addons.dkt_isp_billing.models.isp_phone import (
    mask_username,
    pack_id_mobile_token,
    pack_username_phone_suffix,
    parse_phone_from_username,
    phones_equivalent,
    username_phone_match,
)


@tagged('post_install', '-at_install', 'dkt_isp_phone_parser')
class TestIspPhoneParser(TransactionCase):
    """Parser nomor dari username PPPoE, bukan comment secret."""

    def test_pack_id_mobile_token_strips_separators(self):
        self.assertEqual(pack_id_mobile_token('081-4859-384'), '0814859384')
        self.assertEqual(pack_id_mobile_token('081 4859 384'), '0814859384')
        self.assertEqual(pack_id_mobile_token('081.4859.384'), '0814859384')
        self.assertEqual(pack_id_mobile_token('081-1634-3031'), '08116343031')
        self.assertEqual(pack_id_mobile_token('081 - 1634 - 3031'), '08116343031')
        self.assertEqual(pack_id_mobile_token('+62 811-634-3031'), '628116343031')
        self.assertEqual(pack_id_mobile_token('62 811 6343 031'), '628116343031')
        self.assertFalse(pack_id_mobile_token('dkt-uat-08116343031'))
        self.assertFalse(pack_id_mobile_token('lama-daftar-08116343031'))
        self.assertFalse(pack_id_mobile_token('rosmina-082363197866'))
        self.assertFalse(pack_id_mobile_token('dota'))

    def test_pack_username_phone_suffix(self):
        self.assertEqual(
            pack_username_phone_suffix('username-0812-12384-9485'),
            'username-0812123849485',
        )
        self.assertEqual(
            pack_username_phone_suffix('rosmina-0823-6319-7866'),
            'rosmina-082363197866',
        )
        self.assertEqual(
            pack_username_phone_suffix('dkt-uat-081-1634-3031'),
            'dkt-uat-08116343031',
        )
        self.assertEqual(
            pack_username_phone_suffix('dkt-uat-081 1634 3031'),
            'dkt-uat-08116343031',
        )
        self.assertEqual(
            pack_username_phone_suffix('lama-daftar-08116343031'),
            'lama-daftar-08116343031',
        )
        self.assertEqual(
            pack_username_phone_suffix('pirdaus-0813-7612-8482'),
            'pirdaus-081376128482',
        )
        self.assertFalse(pack_username_phone_suffix('081-4859-384'))
        self.assertFalse(pack_username_phone_suffix('dota'))
        self.assertFalse(pack_username_phone_suffix('andelta-simanjorang'))
        self.assertTrue(username_phone_match(
            'rosmina-0823-6319-7866',
            'rosmina-082363197866',
        ))
        self.assertTrue(username_phone_match(
            'nama-081234567890',
            'nama-6281234567890',
        ))
        self.assertFalse(username_phone_match(
            'rosmina-082363197866',
            'dkt-uat-08116343031',
        ))

    def test_parse_common_username_patterns(self):
        cases = {
            'dul-081376756102': ('081376756102', '6281376756102', 'dul'),
            'kiki-081269686244': ('081269686244', '6281269686244', 'kiki'),
            'trisna-081260099130': ('081260099130', '6281260099130', 'trisna'),
            'dafrius-081263669061': ('081263669061', '6281263669061', 'dafrius'),
            'jean-pierre-081234567890': ('081234567890', '6281234567890', 'jean-pierre'),
            'nama-6281234567890': ('081234567890', '6281234567890', 'nama'),
            'arjun-87723847172': ('087723847172', '6287723847172', 'arjun'),
            'pirdaus-0813-7612-8482': ('081376128482', '6281376128482', 'pirdaus'),
            'username-0812-12384-9485': ('0812123849485', '62812123849485', 'username'),
            'dkt-uat-081-1634-3031': ('08116343031', '628116343031', 'dkt-uat'),
        }
        for username, (phone, wa, first) in cases.items():
            parsed = parse_phone_from_username(username)
            self.assertTrue(parsed, username)
            self.assertEqual(parsed['phone'], phone, username)
            self.assertEqual(parsed['phone_wa'], wa, username)
            self.assertEqual(parsed['first_name'], first, username)

    def test_skip_username_without_phone(self):
        for username in (
            'dota',
            'nasri',
            'andelta-simanjorang',
            'garamata-01',
            'garamata-08',
            'junius-afrianto-sitepu',
            'JOIMNT11000003',
            '',
            False,
        ):
            self.assertFalse(
                parse_phone_from_username(username),
                'harusnya skip: %s' % username,
            )

    def test_mask_does_not_leak_full_number(self):
        masked = mask_username('dul-081376756102')
        self.assertNotIn('081376756102', masked)
        self.assertIn('dul', masked)

    def test_fill_empty_phone_and_skip_manual(self):
        partner = self.env['res.partner'].create({
            'name': 'Uji Parser Kosong',
            'customer_rank': 1,
        })
        self.env['isp.cpe'].create({
            'name': 'CPE Uji Parser',
            'partner_id': partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'dul-081376756102',
            'pppoe_password': 'secret123',
        })
        stats = self.env['res.partner']._backfill_phone_from_pppoe_username(
            partners=partner,
        )
        self.assertEqual(stats['parsed'], 1)
        self.assertEqual(stats['filled'], 1)
        self.assertTrue(phones_equivalent(partner.phone, '081376756102'))
        self.assertEqual(partner.phone_wa, '6281376756102')
        self.assertTrue(partner.isp_phone_from_secret)

        stats2 = self.env['res.partner']._backfill_phone_from_pppoe_username(
            partners=partner,
        )
        self.assertEqual(stats2['unchanged'], 1)
        self.assertTrue(phones_equivalent(partner.phone, '081376756102'))

        other = self.env['res.partner'].create({
            'name': 'Uji Manual',
            'customer_rank': 1,
            'phone': '081200000000',
        })
        self.env['isp.cpe'].create({
            'name': 'CPE Uji Manual',
            'partner_id': other.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'kiki-081269686244',
            'pppoe_password': 'secret123',
        })
        stats3 = self.env['res.partner']._backfill_phone_from_pppoe_username(
            partners=other,
        )
        self.assertEqual(stats3['skip_manual'], 1)
        self.assertEqual(other.phone, '081200000000')
        self.assertFalse(other.isp_phone_from_secret)

    def test_same_username_many_cpe_parsed_once(self):
        partner = self.env['res.partner'].create({
            'name': 'Uji Duplikat User',
            'customer_rank': 1,
        })
        for idx in (1, 2):
            router = self.env['isp.mikrotik.config'].create({
                'name': 'Router Dup %s' % idx,
                'host': '192.0.2.%s:18728' % (10 + idx),
                'username': 'test',
                'password': 'test',
                'active': True,
            })
            self.env['isp.cpe'].create({
                'name': 'CPE Dup %s' % idx,
                'partner_id': partner.id,
                'connection_type': 'pppoe',
                'pppoe_username': 'bina-089684155094',
                'pppoe_password': 'secret123',
                'mikrotik_config_id': router.id,
            })
        stats = self.env['res.partner']._backfill_phone_from_pppoe_username(
            partners=partner,
        )
        self.assertEqual(stats['parsed'], 1)
        self.assertEqual(stats['partners'], 1)
        self.assertTrue(phones_equivalent(partner.phone, '089684155094'))

    def test_whatsapp_uses_parsed_phone_wa(self):
        partner = self.env['res.partner'].create({
            'name': 'Uji WA Parser',
            'customer_rank': 1,
            'phone': '081376756102',
            'phone_wa': '6281376756102',
            'isp_phone_from_secret': True,
        })
        WA = self.env['isp.whatsapp.message']
        self.assertEqual(WA.partner_phone(partner), '6281376756102')
        self.assertTrue(phones_equivalent(partner.phone, partner.phone_wa))

    def test_skip_dota_does_not_invent_number(self):
        partner = self.env['res.partner'].create({
            'name': 'Uji Dota',
            'customer_rank': 1,
        })
        self.env['isp.cpe'].create({
            'name': 'CPE Dota',
            'partner_id': partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'dota',
            'pppoe_password': 'secret123',
        })
        stats = self.env['res.partner']._backfill_phone_from_pppoe_username(
            partners=partner,
        )
        self.assertEqual(stats['skipped'], 1)
        self.assertFalse(partner.phone)
        self.assertFalse(partner.phone_wa)
