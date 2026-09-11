import base64
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.dkt_isp_billing.models.isp_whatsapp_message import (
    INBOUND_ACK,
    TEST_WA_PHONE,
)


@tagged('post_install', '-at_install', 'dkt_isp_fonnte_inbound')
class TestIspFonnteInbound(TransactionCase):
    """Webhook Fonnte: antrian bukti, tanpa lunas, tanpa blast."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_inbound_enabled', 'True')
        ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
        ICP.set_param('dkt_isp_billing.wa_fonnte_token', '')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Pelanggan WA Inbound',
            'customer_rank': 1,
            'phone': '08116343031',
            'phone_wa': TEST_WA_PHONE,
        })
        cls.WA = cls.env['isp.whatsapp.message']
        cls.png = b'\x89PNG\r\n\x1a\nnot-a-real-image'

    def test_editable_template_overrides_default(self):
        self.env['isp.whatsapp.template'].ensure_defaults()
        rec = self.env['isp.whatsapp.template'].search([('code', '=', 'thanks')], limit=1)
        self.assertTrue(rec)
        rec.body = 'Halo {name}, lunas tes.'
        body = self.WA.render_template('thanks', self.partner)
        self.assertEqual(body, 'Halo Pelanggan WA Inbound, lunas tes.')

    def test_custom_blast_uses_free_text(self):
        body = self.WA.render_template(
            'custom', self.partner, body='Gangguan di area. Halo {name}.',
        )
        self.assertIn('Pelanggan WA Inbound', body)
        self.assertIn('Gangguan', body)

    def test_render_all_operational_templates(self):
        for kind in (
            'invoice', 'due', 'due_soon', 'late',
            'isolir_warn', 'isolated', 'thanks', 'restored', 'test',
        ):
            body = self.WA.render_template(kind, self.partner)
            self.assertTrue(body)
            self.assertNotIn('{', body)
            self.assertIn(self.partner.name.split()[0], body)
        for kind in ('invoice', 'due', 'due_soon', 'late', 'isolir_warn', 'isolated'):
            body = self.WA.render_template(kind, self.partner)
            self.assertIn('Abaikan pesan ini jika sudah membayar', body)

    def test_parse_bayar_caption(self):
        parse = self.WA.parse_bayar_caption
        self.assertEqual(parse('BAYAR dota'), 'dota')
        self.assertEqual(parse('bayar: dul-081376756102'), 'dul-081376756102')
        self.assertEqual(parse('bukti tf\nBAYAR 081376756102'), '081376756102')
        self.assertEqual(parse('BAYAR 081-4859-384'), '0814859384')
        self.assertEqual(parse('BAYAR 081 4859 384'), '0814859384')
        self.assertEqual(parse('BAYAR 081.4859.384'), '0814859384')
        self.assertEqual(parse('BAYAR 081-1634-3031'), '08116343031')
        self.assertEqual(parse('BAYAR 081 - 1634 - 3031'), '08116343031')
        self.assertEqual(parse('BAYAR +62 811-634-3031'), '628116343031')
        self.assertEqual(parse('BAYAR 628116343031'), '628116343031')
        self.assertEqual(parse('BAYAR dkt-uat-08116343031'), 'dkt-uat-08116343031')
        self.assertEqual(parse('BAYAR lama-daftar-08116343031'), 'lama-daftar-08116343031')
        self.assertEqual(parse('BAYAR rosmina-082363197866'), 'rosmina-082363197866')
        self.assertEqual(parse('BAYAR rosmina-0823-6319-7866'), 'rosmina-082363197866')
        self.assertEqual(parse('BAYAR username-0812-12384-9485'), 'username-0812123849485')
        self.assertEqual(parse('BAYAR dkt-uat-081-1634-3031'), 'dkt-uat-08116343031')
        self.assertEqual(parse('BAYAR dkt-uat-081 1634 3031'), 'dkt-uat-08116343031')
        self.assertFalse(parse('bukti transfer'))
        self.assertFalse(parse('pembayaran lunas'))

    def test_bayar_formatted_phone_matches_partner(self):
        for caption in (
            'BAYAR 081-1634-3031',
            'BAYAR 081 1634 3031',
            'BAYAR 081.1634.3031',
            'BAYAR 081 - 1634 - 3031',
        ):
            token = self.WA.parse_bayar_caption(caption)
            partner, how = self.WA.find_partner_by_bayar_token(token)
            self.assertEqual(partner, self.partner, caption)
            self.assertEqual(how, 'format', caption)
        raw_partner, raw_how = self.WA.find_partner_by_bayar_token('081-1634-3031')
        self.assertEqual(raw_partner, self.partner)
        self.assertEqual(raw_how, 'format')

    def test_bayar_username_with_minus_stays_username(self):
        other = self.env['res.partner'].create({
            'name': 'Pelanggan Username Minus',
            'customer_rank': 1,
            'phone': '082363197866',
        })
        self.env['isp.cpe'].create({
            'name': 'CPE-rosmina',
            'partner_id': other.id,
            'pppoe_username': 'rosmina-082363197866',
            'pppoe_password': 'x',
        })
        token = self.WA.parse_bayar_caption('BAYAR rosmina-082363197866')
        self.assertEqual(token, 'rosmina-082363197866')
        partner, how = self.WA.find_partner_by_bayar_token(token)
        self.assertEqual(partner, other)
        self.assertEqual(how, 'format')
        lama = self.env['res.partner'].create({
            'name': 'Pelanggan Lama Daftar',
            'customer_rank': 1,
        })
        self.env['isp.cpe'].create({
            'name': 'CPE-lama-daftar',
            'partner_id': lama.id,
            'pppoe_username': 'lama-daftar-08116343031',
            'pppoe_password': 'x',
        })
        lama_token = self.WA.parse_bayar_caption('BAYAR lama-daftar-08116343031')
        self.assertEqual(lama_token, 'lama-daftar-08116343031')
        lama_partner, lama_how = self.WA.find_partner_by_bayar_token(lama_token)
        self.assertEqual(lama_partner, lama)
        self.assertEqual(lama_how, 'format')
        dashed_token = self.WA.parse_bayar_caption('BAYAR rosmina-0823-6319-7866')
        self.assertEqual(dashed_token, 'rosmina-082363197866')
        dashed_partner, dashed_how = self.WA.find_partner_by_bayar_token(
            'rosmina-0823-6319-7866',
        )
        self.assertEqual(dashed_partner, other)
        self.assertEqual(dashed_how, 'format')

    def test_create_from_whatsapp_matches_partner(self):
        proof = self.env['isp.payment.proof'].create_from_whatsapp({
            'public_phone': TEST_WA_PHONE,
            'public_name': 'Tes',
            'image': base64.b64encode(self.png),
            'image_filename': 'bukti.png',
        })
        self.assertEqual(proof.source, 'whatsapp')
        self.assertEqual(proof.partner_id, self.partner)
        self.assertIn(proof.state, ('submitted', 'review'))
        self.assertNotEqual(proof.state, 'paid')
        self.assertFalse(proof.payment_id)

    def test_unknown_number_without_bayar_is_ignored(self):
        proof = self.env['isp.payment.proof'].create_from_whatsapp({
            'public_phone': '6281999888777',
            'image': base64.b64encode(self.png),
            'image_filename': 'bukti.jpg',
        })
        self.assertFalse(proof)
        result = self.WA.process_fonnte_inbound({
            'sender': '6281999888777',
            'url': 'https://example.com/foto-keluarga.png',
            'filename': 'foto.png',
            'extension': 'png',
            'message': 'foto liburan',
        })
        self.assertEqual(result.get('ignored'), 'not_billing')
        self.assertFalse(self.env['isp.payment.proof'].search([
            ('public_phone', '=', '6281999888777'),
        ]))

    def test_bayar_username_matches_when_sender_unknown(self):
        other = self.env['res.partner'].create({
            'name': 'Pelanggan Username',
            'customer_rank': 1,
            'phone': '081376756102',
        })
        self.env['isp.cpe'].create({
            'name': 'CPE-dul',
            'partner_id': other.id,
            'pppoe_username': 'zzz-tes-bayar-unik',
            'pppoe_password': 'x',
        })
        proof = self.env['isp.payment.proof'].create_from_whatsapp({
            'public_phone': '6281999888777',
            'image': base64.b64encode(self.png),
            'image_filename': 'bukti.jpg',
            'caption': 'BAYAR zzz-tes-bayar-unik',
        })
        self.assertEqual(proof.partner_id, other)
        self.assertEqual(proof.wa_match, 'format')
        self.assertNotEqual(proof.state, 'paid')

    def test_bayar_phone_matches_when_sender_unknown(self):
        proof = self.env['isp.payment.proof'].create_from_whatsapp({
            'public_phone': '6281999888777',
            'image': base64.b64encode(self.png),
            'image_filename': 'bukti.jpg',
            'caption': 'bayar 08116343031',
        })
        self.assertEqual(proof.partner_id, self.partner)
        self.assertEqual(proof.wa_match, 'format')

    def test_sender_phone_wins_over_bayar_caption(self):
        other = self.env['res.partner'].create({
            'name': 'Bukan Pengirim',
            'customer_rank': 1,
        })
        self.env['isp.cpe'].create({
            'name': 'CPE-lain',
            'partner_id': other.id,
            'pppoe_username': 'orang-lain',
            'pppoe_password': 'x',
        })
        proof = self.env['isp.payment.proof'].create_from_whatsapp({
            'public_phone': TEST_WA_PHONE,
            'image': base64.b64encode(self.png),
            'image_filename': 'bukti.jpg',
            'caption': 'BAYAR orang-lain',
        })
        self.assertEqual(proof.partner_id, self.partner)
        self.assertEqual(proof.wa_match, 'phone')

    def test_registered_image_without_bayar_is_ignored(self):
        with patch.object(type(self.WA), '_download_https_file') as download:
            result = self.WA.process_fonnte_inbound({
                'sender': TEST_WA_PHONE,
                'url': 'https://example.com/foto-keluarga.png',
                'filename': 'foto.png',
                'extension': 'png',
                'message': 'foto liburan',
            })
        download.assert_not_called()
        self.assertEqual(result.get('ignored'), 'not_billing')
        self.assertFalse(result.get('replied'))
        self.assertFalse(self.env['isp.payment.proof'].search([
            ('partner_id', '=', self.partner.id),
            ('source', '=', 'whatsapp'),
        ]))

    def test_payment_like_caption_without_bayar_stays_silent(self):
        with patch.object(type(self.WA), '_download_https_file') as download, \
             patch.object(type(self.WA), 'send_inbound_ack') as ack:
            result = self.WA.process_fonnte_inbound({
                'sender': TEST_WA_PHONE,
                'url': 'https://example.com/bukti.png',
                'filename': 'bukti.png',
                'extension': 'png',
                'message': 'bukti tf',
            })
        download.assert_not_called()
        ack.assert_not_called()
        self.assertEqual(result.get('ignored'), 'not_billing')
        self.assertFalse(result.get('replied'))

    def test_unknown_sender_image_with_bayar_is_accepted(self):
        payload = {
            'sender': '6281999888777',
            'url': 'https://example.com/bukti.png',
            'filename': 'bukti.png',
            'extension': 'png',
            'name': 'Orang Lain',
            'message': 'BAYAR 08116343031',
        }
        with patch.object(type(self.WA), '_download_https_file', return_value=self.png), \
             patch.object(type(self.WA), 'send_inbound_ack', return_value=True):
            result = self.WA.process_fonnte_inbound(payload)
        self.assertTrue(result['ok'])
        self.assertTrue(result['matched'])
        proof = self.env['isp.payment.proof'].search([
            ('public_phone', '=', '6281999888777'),
            ('source', '=', 'whatsapp'),
        ], limit=1)
        self.assertEqual(proof.partner_id, self.partner)
        self.assertEqual(proof.wa_match, 'format')

    def test_process_inbound_image_replies_ack_without_invoice(self):
        payload = {
            'sender': TEST_WA_PHONE,
            'url': 'https://example.com/bukti.png',
            'filename': 'bukti.png',
            'extension': 'png',
            'name': 'Tes',
            'message': 'BAYAR 08116343031',
        }
        with patch.object(type(self.WA), '_download_https_file', return_value=self.png), \
             patch.object(type(self.WA), 'send_inbound_ack', return_value=True) as ack:
            result = self.WA.process_fonnte_inbound(payload)
        self.assertTrue(result['ok'])
        self.assertTrue(result['matched'])
        self.assertTrue(result['replied'])
        ack.assert_called_once()
        self.assertEqual(ack.call_args.args[0], TEST_WA_PHONE)
        self.assertEqual(INBOUND_ACK, 'Bukti diterima, menunggu verifikasi admin pusat')
        self.assertNotIn('invoice', INBOUND_ACK.lower())
        self.assertNotIn('Rp', INBOUND_ACK)
        proof = self.env['isp.payment.proof'].search([
            ('source', '=', 'whatsapp'),
            ('partner_id', '=', self.partner.id),
        ], limit=1)
        self.assertTrue(proof)
        self.assertNotEqual(proof.state, 'paid')

    def test_process_inbound_ignores_text_only(self):
        result = self.WA.process_fonnte_inbound({
            'sender': TEST_WA_PHONE,
            'message': 'halo',
        })
        self.assertEqual(result.get('ignored'), 'not_image')
        self.assertFalse(self.env['isp.payment.proof'].search([
            ('source', '=', 'whatsapp'),
            ('inbound_note', 'ilike', 'halo'),
        ]))

    def test_bayar_text_without_image_hints_sender(self):
        with patch.object(type(self.WA), 'send_inbound_ack', return_value=True) as ack:
            result = self.WA.process_fonnte_inbound({
                'sender': '6281999888777',
                'message': 'BAYAR dkt-uat-08116343031',
            })
        self.assertEqual(result.get('ignored'), 'bayar_no_image')
        ack.assert_called_once()
        self.assertIn('BAYAR', ack.call_args.kwargs['message'])

    def test_thanks_goes_to_partner_and_other_sender(self):
        proof = self.env['isp.payment.proof'].create_from_whatsapp({
            'public_phone': '6281999888777',
            'image': base64.b64encode(self.png),
            'image_filename': 'bukti.jpg',
            'caption': 'BAYAR 08116343031',
        })
        phones = proof._thanks_target_phones()
        self.assertEqual(phones, [TEST_WA_PHONE, '6281999888777'])

    def test_thanks_does_not_duplicate_same_phone(self):
        proof = self.env['isp.payment.proof'].create_from_whatsapp({
            'public_phone': TEST_WA_PHONE,
            'image': base64.b64encode(self.png),
            'image_filename': 'bukti.jpg',
            'caption': 'BAYAR 08116343031',
        })
        self.assertEqual(proof._thanks_target_phones(), [TEST_WA_PHONE])

    def test_queue_and_send_stays_dry_without_force(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
        ICP.set_param('dkt_isp_billing.wa_fonnte_token', 'dummy-not-a-real-token')
        with patch.object(type(self.WA), '_send_fonnte') as send:
            result = self.WA.queue_and_send(self.partner, template_kind='test')
        send.assert_not_called()
        self.assertEqual(result['mode'], 'dry_run')
        self.assertEqual(result['created'].state, 'dry_run')

    def test_test_send_rejects_other_numbers(self):
        with self.assertRaises(UserError):
            self.WA.test_send(phone='6281234567890')

    def test_force_dry_run_false_calls_http_once(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
        ICP.set_param('dkt_isp_billing.wa_fonnte_token', 'dummy-not-a-real-token')
        fake = (
            200,
            '{"status":true,"id":"tes-id-1","detail":"success! message in queue"}',
            'tes-id-1',
        )
        with patch.object(type(self.WA), '_send_fonnte', return_value=fake) as send:
            result = self.WA.queue_and_send(
                self.partner, template_kind='test', force_dry_run=False,
            )
        send.assert_called_once()
        self.assertEqual(result['mode'], 'live')
        self.assertEqual(result['sent'], 1)
        rec = result['created']
        self.assertEqual(rec.state, 'sent')
        self.assertEqual(rec.phone, TEST_WA_PHONE)
        self.assertIn('pesan tes WhatsApp DKT', rec.body)

    def test_webhook_url_localhost_not_public(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('web.base.url', 'http://localhost:8196')
        ICP.set_param('dkt_isp_billing.wa_public_base_url', '')
        info = self.WA.get_webhook_info()
        self.assertEqual(info['url'], 'http://localhost:8196/isp/whatsapp/fonnte')
        self.assertFalse(info['is_public'])
        public = self.WA.get_webhook_info(public_base='https://billing.example.co.id')
        self.assertTrue(public['is_public'])
        self.assertEqual(public['url'], 'https://billing.example.co.id/isp/whatsapp/fonnte')
