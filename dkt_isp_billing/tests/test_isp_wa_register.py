import base64
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.dkt_isp_billing.models.isp_whatsapp_message import TEST_WA_PHONE

GROUP_ID = '120363aktivasiuji'
BILLING_ID = '120363billinguji'
TECH_PHONE = '6281299900001'
CUSTOMER_PHONE = '6281399900099'
PNG = b'\x89PNG\r\n\x1a\nnot-a-real-image'


@tagged('post_install', '-at_install', 'dkt_isp_wa_register')
class TestIspWaRegister(TransactionCase):
    """Bot /daftar /pasang /eviden. Default aman: grup lain dan BAYAR tidak berubah."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_inbound_enabled', 'True')
        ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
        ICP.set_param('dkt_isp_billing.wa_fonnte_token', '')
        ICP.set_param('dkt_isp_billing.wa_register_enabled', 'False')
        ICP.set_param('dkt_isp_billing.wa_register_mode', 'queue')
        ICP.set_param('dkt_isp_billing.wa_register_group_id', GROUP_ID)
        ICP.set_param('dkt_isp_billing.wa_billing_group_id', BILLING_ID)
        ICP.set_param('dkt_isp_billing.wa_evidence_required', 'False')
        cls.area = cls.env['isp.area'].create({
            'name': 'Desa Uji Daftar',
            'code': 'UJI_DAFTAR',
        })
        cls.router = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Uji Daftar',
            'host': '192.0.2.77:18728',
            'username': 'test',
            'password': 'test',
            'active': True,
            'area_id': cls.area.id,
            'health_state': 'reachable',
            'last_test_ok': True,
        })
        cls.package = cls.env.ref('dkt_isp_billing.isp_package_150')
        tech_group = cls.env.ref('dkt_isp_billing.group_isp_technician')
        cls.tech = cls.env['res.users'].create({
            'name': 'Teknisi Uji Daftar',
            'login': 'tech_wa_daftar',
            'group_ids': [Command.set([tech_group.id])],
        })
        cls.tech.partner_id.write({
            'phone': '081299900001',
            'phone_wa': TECH_PHONE,
        })
        cls.WA = cls.env['isp.whatsapp.message']
        cls.Reg = cls.env['isp.wa.registration']
        cls.old_partner = cls.env['res.partner'].create({
            'name': 'Pelanggan Lama Daftar',
            'customer_rank': 1,
            'phone': '08116343031',
            'phone_wa': TEST_WA_PHONE,
        })
        cls.old_cpe = cls.env['isp.cpe'].create({
            'name': 'CPE Lama Daftar',
            'partner_id': cls.old_partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'lama-daftar-08116343031',
            'pppoe_password': 'pwdLama1',
            'mikrotik_config_id': cls.router.id,
            'state': 'open',
            'isp_onboarding': False,
        })

    def _enable_bot(self, mode='queue', evidence=False):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_register_enabled', 'True')
        ICP.set_param('dkt_isp_billing.wa_register_mode', mode)
        ICP.set_param('dkt_isp_billing.wa_evidence_required', 'True' if evidence else 'False')

    def _inbound(self, **payload):
        with patch.object(type(self.WA), 'send_inbound_ack', return_value=True) as ack:
            result = self.WA.process_fonnte_inbound(payload)
        return result, ack

    def test_group_ignored_when_bot_off(self):
        result, _ack = self._inbound(
            sender=GROUP_ID,
            member=TECH_PHONE,
            message='/pasang',
        )
        self.assertEqual(result.get('ignored'), 'group')
        self.assertFalse(self.Reg.search([('source', '=', 'pasang')]))

    def test_other_group_ignored_when_bot_on(self):
        self._enable_bot()
        result, _ack = self._inbound(
            sender='120363gruplain',
            member=TECH_PHONE,
            message='/pasang',
        )
        self.assertEqual(result.get('ignored'), 'other_group')

    def test_group_chatter_ignored(self):
        self._enable_bot()
        result, _ack = self._inbound(
            sender=GROUP_ID,
            member=TECH_PHONE,
            message='otw pintu angin ya',
        )
        self.assertEqual(result.get('ignored'), 'group_chatter')

    def test_bayar_in_group_ignored(self):
        self._enable_bot()
        result, _ack = self._inbound(
            sender=GROUP_ID,
            member=TECH_PHONE,
            message='BAYAR lama-daftar-08116343031',
            url='https://example.com/struk.png',
            extension='png',
        )
        self.assertEqual(result.get('ignored'), 'bayar_in_group')
        self.assertFalse(self.env['isp.payment.proof'].search([
            ('public_phone', '=', TECH_PHONE),
        ]))

    def test_pasang_from_any_group_member(self):
        self._enable_bot()
        result, ack = self._inbound(
            sender=GROUP_ID,
            member='6281999000111',
            message='/pasang',
        )
        self.assertTrue(result.get('replied'))
        rec = self.Reg.search([
            ('source', '=', 'pasang'),
            ('actor_phone', '=', '6281999000111'),
        ], limit=1)
        self.assertEqual(rec.state, 'chatting')
        self.assertFalse(rec.technician_id)
        self.assertTrue(ack.call_args.kwargs['message'].startswith('~bot\n'))

    def test_bot_mark_and_group_target(self):
        self.assertEqual(self.Reg._mark_bot('Nomor HP pelanggan?'), '~bot\nNomor HP pelanggan?')
        self.assertEqual(self.Reg._mark_bot('~bot\nsudah'), '~bot\nsudah')
        gid = '120363429674835408@g.us'
        self.assertTrue(self.WA.is_group_target(gid))
        self.assertEqual(self.WA.resolve_ack_target(gid), gid)
        self.assertTrue(self.WA.is_group_target('120363429674835408'))
        self.assertFalse(self.WA.is_group_target(CUSTOMER_PHONE))
        self.assertEqual(self.WA.resolve_ack_target(CUSTOMER_PHONE), CUSTOMER_PHONE)
        chunks = self.Reg._chunk_wa_message(self.Reg._mark_bot(self.Reg._help_text('group')))
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(part) <= 450 for part in chunks))
        self.assertEqual(
            self.Reg._norm_group('120363429674835408@g.us'),
            self.Reg._norm_group('120363429674835408'),
        )
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_register_group_id', '120363429674835408@g.us')
        self.assertTrue(self.Reg._is_activation_group('120363429674835408'))
        ICP.set_param('dkt_isp_billing.wa_register_group_id', GROUP_ID)

    def test_send_inbound_ack_does_not_crash_in_tests(self):
        self.assertTrue(self.WA.send_inbound_ack(CUSTOMER_PHONE, message='tes'))
        self.assertTrue(self.WA.send_inbound_ack(
            '120363429674835408@g.us', message='~bot\ntes',
        ))
        group_payload = self.WA._inbound_ack_payload(
            '120363429674835408@g.us', '~bot\nhelp', inbox_id='99',
        )
        self.assertNotIn('inboxid', group_payload)
        self.assertEqual(group_payload['countryCode'], '0')
        direct_payload = self.WA._inbound_ack_payload(
            CUSTOMER_PHONE, 'tes', inbox_id='99',
        )
        self.assertEqual(direct_payload['inboxid'], '99')

    def test_help_without_member_if_sender_is_group(self):
        self._enable_bot()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_register_group_id', '120363429674835408@g.us')
        result, ack = self._inbound(
            sender='120363429674835408@g.us',
            message='/help',
        )
        self.assertTrue(result.get('help'))
        self.assertTrue(ack.called)
        ICP.set_param('dkt_isp_billing.wa_register_group_id', GROUP_ID)

    def test_daftar_queues_without_partner(self):
        self._enable_bot()
        partners_before = self.env['res.partner'].search_count([])
        self._inbound(sender=CUSTOMER_PHONE, message='/daftar')
        self._inbound(sender=CUSTOMER_PHONE, message='Siti Uji Daftar')
        self._inbound(sender=CUSTOMER_PHONE, message=self.area.name)
        self._inbound(sender=CUSTOMER_PHONE, message=self.package.name)
        result, _ack = self._inbound(sender=CUSTOMER_PHONE, message='YA')
        rec = self.Reg.search([('actor_phone', '=', CUSTOMER_PHONE)], limit=1)
        self.assertTrue(rec)
        self.assertEqual(rec.state, 'queued')
        self.assertFalse(rec.mikrotik_config_id)
        self.assertFalse(rec.partner_id)
        self.assertFalse(rec.cpe_id)
        self.assertEqual(self.env['res.partner'].search_count([]), partners_before)
        self.assertTrue(result.get('handled'))

    def test_pasang_queue_mode_does_not_create_user(self):
        self._enable_bot(mode='queue')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='/pasang')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='081399900099')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='Budi Uji Pasang')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.area.name)
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.router.name)
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.package.name)
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='YA')
        rec = self.Reg.search([('source', '=', 'pasang'), ('customer_name', '=', 'Budi Uji Pasang')], limit=1)
        self.assertEqual(rec.state, 'queued')
        self.assertEqual(rec.mikrotik_config_id, self.router)
        self.assertFalse(rec.cpe_id)

    def test_pasang_tech_auto_creates_user_without_invoice(self):
        self._enable_bot(mode='tech_auto')
        with patch.object(type(self.env['isp.subscription']), 'action_open', return_value=True):
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='/pasang')
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='081399900099')
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='Budi Auto Pasang')
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.area.name)
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.router.name)
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.package.name)
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='YA')
        rec = self.Reg.search([('customer_name', '=', 'Budi Auto Pasang')], limit=1)
        self.assertEqual(rec.state, 'created')
        self.assertTrue(rec.partner_id)
        self.assertNotEqual(rec.partner_id.id, 1)
        self.assertEqual(rec.mikrotik_config_id, self.router)
        self.assertEqual(rec.cpe_id.mikrotik_config_id, self.router)
        self.assertTrue(rec.cpe_id.isp_onboarding)
        self.assertTrue(rec.pppoe_username)
        invoices = self.env['account.move'].search([
            ('partner_id', '=', rec.partner_id.id),
        ])
        self.assertFalse(invoices)

    def test_approve_queued_creates_user(self):
        self._enable_bot()
        rec = self.Reg.create({
            'source': 'daftar',
            'channel': 'direct',
            'state': 'queued',
            'step': 'done',
            'actor_phone': CUSTOMER_PHONE,
            'customer_phone': CUSTOMER_PHONE,
            'customer_name': 'Ani Approve',
            'area_id': self.area.id,
            'mikrotik_config_id': self.router.id,
            'package_id': self.package.id,
        })
        with patch.object(type(self.env['isp.subscription']), 'action_open', return_value=True):
            rec.action_approve()
        self.assertEqual(rec.state, 'created')
        self.assertTrue(rec.cpe_id.isp_onboarding)

    def test_approve_queued_requires_router(self):
        self._enable_bot()
        rec = self.Reg.create({
            'source': 'daftar',
            'channel': 'direct',
            'state': 'queued',
            'step': 'done',
            'actor_phone': CUSTOMER_PHONE,
            'customer_phone': CUSTOMER_PHONE,
            'customer_name': 'Ani Tanpa Router',
            'area_id': self.area.id,
            'package_id': self.package.id,
        })
        with self.assertRaises(UserError):
            rec.action_approve()
        self.assertEqual(rec.state, 'queued')

    def test_evidence_complete_and_restore(self):
        partner = self.env['res.partner'].create({
            'name': 'Eviden Uji',
            'customer_rank': 1,
        })
        self.assertFalse(partner.isp_evidence_complete)
        partner.write({
            'isp_ktp_image': base64.b64encode(PNG),
            'isp_house_image': base64.b64encode(PNG),
            'isp_latitude': 3.041,
            'isp_longitude': 98.312,
        })
        self.assertTrue(partner.isp_evidence_complete)

    def test_cron_revokes_only_onboarding_past_deadline(self):
        self._enable_bot(evidence=True)
        now = fields.Datetime.now()
        new_cpe = self.env['isp.cpe'].create({
            'name': 'CPE Onboard Uji',
            'partner_id': self.old_partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'baru-daftar-0813999',
            'pppoe_password': 'pwdBaru1',
            'mikrotik_config_id': self.router.id,
            'state': 'open',
            'isp_onboarding': True,
            'isp_first_connected_at': now - timedelta(hours=25),
            'isp_evidence_deadline': now - timedelta(minutes=5),
            'isp_evidence_notified': True,
        })
        with patch.object(type(self.router), 'set_secret_disabled', return_value=(True, None)) as disable:
            self.Reg._cron_onboarding_evidence()
        self.assertTrue(new_cpe.isp_activation_revoked)
        self.assertTrue(disable.called)
        self.assertFalse(self.old_cpe.isp_activation_revoked)
        self.assertEqual(self.old_cpe.state, 'open')

    def test_first_connect_sets_deadline_only_if_onboarding(self):
        self._enable_bot(evidence=True)
        new_cpe = self.env['isp.cpe'].create({
            'name': 'CPE Connect Uji',
            'partner_id': self.old_partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'connect-daftar-1',
            'pppoe_password': 'pwdConn1',
            'mikrotik_config_id': self.router.id,
            'state': 'open',
            'isp_onboarding': True,
        })
        with patch.object(type(self.WA), 'send_inbound_ack', return_value=True):
            self.Reg.notify_first_connect(new_cpe)
            self.Reg.notify_first_connect(self.old_cpe)
        self.assertTrue(new_cpe.isp_first_connected_at)
        self.assertTrue(new_cpe.isp_evidence_deadline)
        self.assertFalse(self.old_cpe.isp_first_connected_at)

    def test_bayar_one_to_one_still_works_with_bot_on(self):
        self._enable_bot()
        with patch.object(type(self.WA), '_download_https_file', return_value=PNG):
            result, _ack = self._inbound(
                sender=TEST_WA_PHONE,
                message='BAYAR lama-daftar-08116343031',
                url='https://example.com/bukti.png',
                extension='png',
                filename='bukti.png',
            )
        self.assertTrue(result.get('proof_name'))
        proof = self.env['isp.payment.proof'].search([('name', '=', result['proof_name'])])
        self.assertEqual(proof.partner_id, self.old_partner)

    def test_company_partner_not_used(self):
        self.assertTrue(self.Reg._protected_partner(self.env.company.partner_id))
        self.assertTrue(self.Reg._protected_partner(self.env['res.partner'].browse(1)))

    def test_help_in_group_is_manual_without_session(self):
        self._enable_bot()
        result, ack = self._inbound(
            sender=GROUP_ID,
            member=TECH_PHONE,
            message='/help',
        )
        self.assertTrue(result.get('help'))
        self.assertTrue(result.get('replied'))
        msg = '\n'.join(call.kwargs['message'] for call in ack.call_args_list)
        self.assertTrue(ack.call_args_list[0].kwargs['message'].startswith('~bot\n'))
        self.assertIn('/pasang', msg)
        self.assertIn('/daftar', msg)
        self.assertIn('/eviden', msg)
        self.assertIn('/status', msg)
        self.assertIn('BAYAR', msg)
        self.assertIn('[online]', msg)
        self.assertIn('pin lokasi', msg.lower())
        self.assertFalse(self.Reg.search([('actor_phone', '=', TECH_PHONE), ('state', '=', 'chatting')]))

    def test_help_keeps_pasang_session(self):
        self._enable_bot()
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='/pasang')
        rec = self.Reg.search([('actor_phone', '=', TECH_PHONE), ('state', '=', 'chatting')], limit=1)
        self.assertEqual(rec.step, 'phone')
        result, ack = self._inbound(
            sender=GROUP_ID,
            member=TECH_PHONE,
            message='/bantuan',
        )
        self.assertTrue(result.get('help'))
        msg = '\n'.join(call.kwargs['message'] for call in ack.call_args_list)
        self.assertIn('/pasang', msg)
        rec.invalidate_recordset()
        self.assertEqual(rec.state, 'chatting')
        self.assertEqual(rec.step, 'phone')

    def test_help_in_direct_chat(self):
        self._enable_bot()
        result, ack = self._inbound(sender=CUSTOMER_PHONE, message='/help')
        self.assertTrue(result.get('handled'))
        self.assertTrue(result.get('help'))
        msg = '\n'.join(call.kwargs['message'] for call in ack.call_args_list)
        self.assertIn('/daftar', msg)
        self.assertIn('/status', msg)
        self.assertIn('BAYAR', msg)
        self.assertIn('chat pribadi', msg.lower())

    def test_audit_who_registered_whom(self):
        self._enable_bot(mode='tech_auto')
        with patch.object(type(self.env['isp.subscription']), 'action_open', return_value=True):
            self._inbound(
                sender=GROUP_ID, member=TECH_PHONE, name='Andi Teknisi',
                message='/pasang',
            )
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='081399900099')
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='Budi Audit Pasang')
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.area.name)
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.router.name)
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.package.name)
            self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='YA')
        rec = self.Reg.search([('customer_name', '=', 'Budi Audit Pasang')], limit=1)
        self.assertEqual(rec.actor_name, 'Andi Teknisi')
        self.assertIn(TECH_PHONE, rec.registered_by)
        self.assertIn('Andi Teknisi', rec.registered_by)
        self.assertIn('mendaftarkan Budi Audit Pasang', rec.audit_log)
        self.assertIn(rec.pppoe_username, rec.audit_log)
        self.assertNotIn(rec.cpe_id.pppoe_password, rec.audit_log or '')
        self.assertEqual(rec.partner_id.isp_registered_by_phone, TECH_PHONE)
        self.assertEqual(rec.partner_id.isp_registered_by_user_id, self.tech)
        self.assertEqual(rec.partner_id.isp_wa_registration_id, rec)
        chatter = ' '.join(rec.message_ids.mapped('body'))
        self.assertIn('mendaftarkan Budi Audit Pasang', chatter)

    def test_pasang_lists_all_routers_not_only_desa(self):
        self._enable_bot()
        other_area = self.env['isp.area'].create({
            'name': 'Lau Baleng Uji',
            'code': 'LAU_BALENG_UJI',
        })
        other = self.env['isp.mikrotik.config'].create({
            'name': 'Lau Baleng Uji',
            'host': '192.0.2.78:18728',
            'username': 'test',
            'password': 'test',
            'active': True,
            'area_id': other_area.id,
        })
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='/pasang')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='081399900099')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='Budi Pilih Router')
        _result, ack = self._inbound(
            sender=GROUP_ID, member=TECH_PHONE, message=self.area.name,
        )
        rec = self.Reg.search([('customer_name', '=', 'Budi Pilih Router')], limit=1)
        self.assertEqual(rec.step, 'router')
        msg = ack.call_args.kwargs['message']
        self.assertIn('Pilih router', msg)
        self.assertIn(self.router.name, msg)
        self.assertIn('%s [online]' % self.router.name, msg)
        self.assertIn(other.name, msg)
        self.assertIn('%s [belum dicek]' % other.name, msg)
        self.assertIn('Saran desa', msg)
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=other.name)
        rec.invalidate_recordset()
        self.assertEqual(rec.step, 'package')
        self.assertEqual(rec.area_id, self.area)
        self.assertEqual(rec.mikrotik_config_id, other)

    def _make_router(self, name, host, health=None, area=None):
        vals = {
            'name': name,
            'host': host,
            'username': 'test',
            'password': 'test',
            'active': True,
            'area_id': (area or self.area).id,
        }
        if health:
            vals['health_state'] = health
            vals['last_test_ok'] = health == 'reachable'
        return self.env['isp.mikrotik.config'].create(vals)

    def test_pasang_router_list_shows_health(self):
        self._enable_bot()
        offline = self._make_router('Sinaman Uji Offline', '192.0.2.79:18728', 'timeout')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='/pasang')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='081399900099')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='Budi Lihat Health')
        _result, ack = self._inbound(
            sender=GROUP_ID, member=TECH_PHONE, message=self.area.name,
        )
        msg = ack.call_args.kwargs['message']
        self.assertIn('%s [online]' % self.router.name, msg)
        self.assertIn('%s [offline]' % offline.name, msg)
        self.assertIn('Pilih yang [online]', msg)

    def test_pasang_rejects_offline_router(self):
        self._enable_bot()
        offline = self._make_router('POP Gangguan Uji', '192.0.2.80:18728', 'timeout')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='/pasang')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='081399900099')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='Budi Router Offline')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.area.name)
        rec = self.Reg.search([('customer_name', '=', 'Budi Router Offline')], limit=1)
        self.assertEqual(rec.step, 'router')
        _result, ack = self._inbound(
            sender=GROUP_ID, member=TECH_PHONE, message=offline.name,
        )
        rec.invalidate_recordset()
        self.assertEqual(rec.step, 'router')
        self.assertFalse(rec.mikrotik_config_id)
        msg = ack.call_args.kwargs['message']
        self.assertIn('[offline]', msg)
        self.assertIn('gangguan', msg.lower())
        self.assertIn('/pasang', msg)
        self.assertIn('/batal', msg)
        self.assertNotIn('Pilih paket', msg)

    def test_pasang_online_router_still_advances(self):
        self._enable_bot()
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='/pasang')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='081399900099')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message='Budi Router Online')
        self._inbound(sender=GROUP_ID, member=TECH_PHONE, message=self.area.name)
        _result, ack = self._inbound(
            sender=GROUP_ID, member=TECH_PHONE, message=self.router.name,
        )
        rec = self.Reg.search([('customer_name', '=', 'Budi Router Online')], limit=1)
        self.assertEqual(rec.step, 'package')
        self.assertEqual(rec.mikrotik_config_id, self.router)
        self.assertIn('Pilih paket', ack.call_args.kwargs['message'])

    def test_approve_rejects_offline_router(self):
        self._enable_bot()
        offline = self._make_router('POP Approve Offline', '192.0.2.81:18728', 'timeout')
        rec = self.Reg.create({
            'source': 'pasang',
            'channel': 'group',
            'state': 'queued',
            'step': 'done',
            'actor_phone': TECH_PHONE,
            'customer_phone': '6281399900888',
            'customer_name': 'Ani Router Offline',
            'area_id': self.area.id,
            'mikrotik_config_id': offline.id,
            'package_id': self.package.id,
        })
        partners_before = self.env['res.partner'].search_count([])
        with self.assertRaises(UserError) as err:
            rec.action_approve()
        self.assertIn('offline', str(err.exception).lower())
        self.assertIn('gangguan', str(err.exception).lower())
        rec.invalidate_recordset()
        self.assertEqual(rec.state, 'queued')
        self.assertFalse(rec.cpe_id)
        self.assertFalse(rec.partner_id)
        self.assertEqual(self.env['res.partner'].search_count([]), partners_before)

    def test_daftar_does_not_ask_router(self):
        self._enable_bot()
        empty = self.env['isp.area'].create({
            'name': 'Desa Tanpa Router Rumah',
            'code': 'TANPA_R',
        })
        self._inbound(sender=CUSTOMER_PHONE, message='/daftar')
        self._inbound(sender=CUSTOMER_PHONE, message='Siti Desa Lain')
        _result, ack = self._inbound(sender=CUSTOMER_PHONE, message=empty.name)
        rec = self.Reg.search([('actor_phone', '=', CUSTOMER_PHONE)], limit=1)
        self.assertEqual(rec.step, 'package')
        self.assertEqual(rec.area_id, empty)
        self.assertFalse(rec.mikrotik_config_id)
        self.assertIn('Pilih paket', ack.call_args.kwargs['message'])

    def _prepare_status_cpe(self):
        self.old_partner.write({
            'isp_latitude': 3.041,
            'isp_longitude': 98.312,
        })
        self.old_cpe.write({
            'state': 'open',
            'pppoe_status': 'connected',
            'pppoe_uptime': '2h10m',
            'pppoe_address': '10.10.10.2',
        })
        if not self.old_cpe.subscription_id:
            self.env['isp.subscription'].create({
                'partner_id': self.old_partner.id,
                'cpe_id': self.old_cpe.id,
                'package_id': self.package.id,
                'state': 'open',
                'due_day': 1,
            })
            self.old_cpe.invalidate_recordset(['subscription_id'])
        return self.old_cpe

    def test_status_in_group_by_username(self):
        self._enable_bot()
        cpe = self._prepare_status_cpe()
        result, ack = self._inbound(
            sender=GROUP_ID,
            member=TECH_PHONE,
            message='/status %s' % cpe.pppoe_username,
        )
        self.assertTrue(result.get('status'))
        msg = ack.call_args.kwargs['message']
        self.assertIn('STATUS %s' % cpe.pppoe_username, msg)
        self.assertIn('Isolir: tidak', msg)
        self.assertIn('PPPoE: *online*', msg)
        self.assertIn('2h10m', msg)
        self.assertIn('Lokasi:', msg)
        self.assertNotIn('Peta:', msg)
        self.assertIn('Router Uji Daftar', msg)
        self.assertIn('Desa Uji Daftar', msg)
        self.assertIn('https://maps.google.com/?q=', msg)
        self.assertIn('3.041', msg)
        self.assertIn('98.312', msg)
        self.assertNotIn(cpe.pppoe_password, msg)

    def test_status_isolated_and_revoked(self):
        self._enable_bot()
        cpe = self._prepare_status_cpe()
        cpe.write({'state': 'isolated', 'pppoe_status': 'disconnected'})
        _result, ack = self._inbound(
            sender=GROUP_ID,
            member=TECH_PHONE,
            message='/status %s' % cpe.pppoe_username,
        )
        self.assertIn('Isolir: *YA (nunggak)*', ack.call_args.kwargs['message'])
        cpe.write({'state': 'open', 'isp_activation_revoked': True})
        _result, ack = self._inbound(
            sender=GROUP_ID,
            member=TECH_PHONE,
            message='/status %s' % cpe.pppoe_username,
        )
        msg = ack.call_args.kwargs['message']
        self.assertIn('Isolir: tidak', msg)
        self.assertIn('eviden 24 jam', msg)

    def test_status_in_group_by_phone_and_usage(self):
        self._enable_bot()
        cpe = self._prepare_status_cpe()
        usage, usage_ack = self._inbound(
            sender=GROUP_ID, member=TECH_PHONE, message='/status',
        )
        self.assertTrue(usage.get('status'))
        self.assertIn('/status username', usage_ack.call_args.kwargs['message'])
        result, ack = self._inbound(
            sender=GROUP_ID,
            member=TECH_PHONE,
            message='/status 08116343031',
        )
        self.assertTrue(result.get('status'))
        self.assertIn('STATUS %s' % cpe.pppoe_username, ack.call_args.kwargs['message'])

    def test_status_direct_only_own_account(self):
        self._enable_bot()
        cpe = self._prepare_status_cpe()
        result, ack = self._inbound(
            sender=CUSTOMER_PHONE,
            message='/status %s' % cpe.pppoe_username,
        )
        self.assertTrue(result.get('status'))
        self.assertIn('hanya status akun Anda', ack.call_args.kwargs['message'])
        own, own_ack = self._inbound(sender=TEST_WA_PHONE, message='/status')
        self.assertTrue(own.get('status'))
        own_msg = own_ack.call_args.kwargs['message']
        self.assertIn('STATUS %s' % cpe.pppoe_username, own_msg)
        self.assertNotIn(cpe.pppoe_password, own_msg)

    def test_billing_group_bayar_matches_caption_not_reporter(self):
        self._enable_bot()
        reporter = '6281888000999'
        with patch.object(type(self.WA), '_download_https_file', return_value=PNG):
            result, ack = self._inbound(
                sender=BILLING_ID,
                member=reporter,
                name='Andi Lapangan',
                message='BAYAR lama-daftar-08116343031',
                url='https://example.com/bukti.png',
                extension='png',
                filename='bukti.png',
            )
        self.assertTrue(result.get('billing'))
        self.assertTrue(result.get('matched'))
        proof = self.env['isp.payment.proof'].search([
            ('name', '=', result['proof_name']),
        ], limit=1)
        self.assertEqual(proof.partner_id, self.old_partner)
        self.assertEqual(proof.public_phone, reporter)
        self.assertEqual(proof.source, 'whatsapp_group')
        self.assertNotEqual(proof.state, 'paid')
        self.assertIn('Bukti masuk', ack.call_args.kwargs['message'])
        self.assertIn('Belum lunas', ack.call_args.kwargs['message'])

    def test_billing_group_accepts_any_member(self):
        result, ack = self._inbound(
            sender=BILLING_ID,
            member='6281777000111',
            message='/help',
        )
        self.assertTrue(result.get('billing'))
        self.assertTrue(result.get('help'))
        msg = '\n'.join(call.kwargs['message'] for call in ack.call_args_list)
        self.assertIn('BAYAR', msg)
        self.assertIn('/status', msg)
        self.assertIn('tanpa caption', msg.lower())
        self.assertIn('kirim ulang', msg.lower())

    def test_billing_group_status_works(self):
        cpe = self._prepare_status_cpe()
        result, ack = self._inbound(
            sender=BILLING_ID,
            member='6281777000111',
            message='/status %s' % cpe.pppoe_username,
        )
        self.assertTrue(result.get('status'))
        self.assertTrue(result.get('billing'))
        self.assertIn('STATUS %s' % cpe.pppoe_username, ack.call_args.kwargs['message'])

    def test_billing_group_chatter_ignored(self):
        result, _ack = self._inbound(
            sender=BILLING_ID,
            member=TECH_PHONE,
            message='sudah transfer ya',
        )
        self.assertEqual(result.get('ignored'), 'billing_chatter')
        self.assertFalse(self.env['isp.payment.proof'].search([
            ('public_phone', '=', TECH_PHONE),
            ('source', '=', 'whatsapp_group'),
        ]))

    def test_billing_group_duplicate_same_day(self):
        with patch.object(type(self.WA), '_download_https_file', return_value=PNG):
            first, _ack = self._inbound(
                sender=BILLING_ID,
                member=TECH_PHONE,
                message='BAYAR lama-daftar-08116343031',
                url='https://example.com/bukti.png',
                extension='png',
            )
            second, ack = self._inbound(
                sender=BILLING_ID,
                member=TECH_PHONE,
                message='BAYAR lama-daftar-08116343031',
                url='https://example.com/bukti2.png',
                extension='png',
            )
        self.assertTrue(first.get('proof_name'))
        self.assertTrue(second.get('duplicate'))
        self.assertEqual(second.get('proof_name'), first.get('proof_name'))
        self.assertIn('Sudah ada antrian', ack.call_args.kwargs['message'])
        self.assertEqual(self.env['isp.payment.proof'].search_count([
            ('partner_id', '=', self.old_partner.id),
            ('source', '=', 'whatsapp_group'),
        ]), 1)

    def test_billing_group_receipt_without_caption_warns(self):
        with patch.object(type(self.WA), '_download_https_file', return_value=PNG), \
             patch.object(
                 type(self.Reg), '_image_looks_like_receipt', return_value=True,
             ):
            result, ack = self._inbound(
                sender=BILLING_ID,
                member='6281888000999',
                name='Andi Lapangan',
                message='bukti transfer',
                url='https://example.com/struk.png',
                extension='png',
            )
        self.assertTrue(result.get('receipt_hint'))
        self.assertFalse(result.get('proof_name'))
        msg = ack.call_args.kwargs['message']
        self.assertIn('@6281888000999', msg)
        self.assertIn('BAYAR username', msg)
        self.assertIn('tidak masuk antrian', msg.lower())
        self.assertFalse(self.env['isp.payment.proof'].search([
            ('public_phone', '=', '6281888000999'),
            ('source', '=', 'whatsapp_group'),
        ]))

    def test_billing_group_other_photo_ignored(self):
        with patch.object(type(self.WA), '_download_https_file', return_value=PNG), \
             patch.object(
                 type(self.Reg), '_image_looks_like_receipt', return_value=False,
             ):
            result, _ack = self._inbound(
                sender=BILLING_ID,
                member=TECH_PHONE,
                message='foto tiang',
                url='https://example.com/tiang.png',
                extension='png',
            )
        self.assertEqual(result.get('ignored'), 'billing_chatter')

    def test_activation_group_still_ignores_bayar(self):
        self._enable_bot()
        result, _ack = self._inbound(
            sender=GROUP_ID,
            member=TECH_PHONE,
            message='BAYAR lama-daftar-08116343031',
            url='https://example.com/struk.png',
            extension='png',
        )
        self.assertEqual(result.get('ignored'), 'bayar_in_group')
