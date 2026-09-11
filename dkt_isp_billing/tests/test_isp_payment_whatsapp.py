import base64
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.dkt_isp_billing.models.isp_ocr import (
    looks_like_payment_receipt, parse_amount_id, parse_ocr_text,
)


@tagged('post_install', '-at_install', 'dkt_isp_payment_whatsapp')
class TestIspPaymentWhatsapp(TransactionCase):
    """Kas, OCR, daftar review, dan WA dry-run tanpa kirim nyata."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Pelanggan Uji Bayar',
            'customer_rank': 1,
            'phone': '081234567890',
        })

    def test_ocr_parse_indonesian_receipt_text(self):
        text = 'Transfer BCA Rp 150.000 Tanggal 01/08/2026 Ref: TRX99ABC'
        parsed = parse_ocr_text(text)
        self.assertEqual(parsed['amount'], 150000.0)
        self.assertTrue(parsed['date'])
        self.assertEqual(parsed['ref'], 'TRX99ABC')
        self.assertGreaterEqual(parsed['confidence'], 0.55)
        self.assertLess(parsed['confidence'], 0.85)
        self.assertEqual(parse_amount_id('250.000'), 250000.0)
        self.assertTrue(looks_like_payment_receipt(text))
        self.assertFalse(looks_like_payment_receipt('foto liburan di pantai'))

    def test_proof_image_without_ocr_engine_goes_to_review(self):
        proof = self.env['isp.payment.proof'].create({
            'partner_id': self.partner.id,
            'image': base64.b64encode(b'\x89PNG\r\n\x1a\nnot-a-real-image'),
            'image_filename': 'bukti.png',
        })
        self.assertEqual(proof.state, 'review')
        self.assertTrue(proof.needs_review)
        self.assertIn(proof.ocr_engine, ('none', 'pytesseract'))
        review = self.env['isp.payment.proof'].search([
            ('needs_review', '=', True),
            ('id', '=', proof.id),
        ])
        self.assertTrue(review)

    def test_proof_plain_text_does_not_auto_post(self):
        proof = self.env['isp.payment.proof'].create({
            'partner_id': self.partner.id,
            'image': base64.b64encode(b'Transfer Rp 150.000'),
            'image_filename': 'bukti.txt',
        })
        self.assertNotEqual(proof.state, 'paid')
        self.assertTrue(proof.needs_review)
        self.assertFalse(proof.payment_id)
        self.assertEqual(proof.amount_ocr, 150000.0)

    def test_cash_wizard_requires_invoice(self):
        wiz = self.env['isp.cash.payment.wizard'].create({
            'partner_id': self.partner.id,
            'amount': 10000,
        })
        with self.assertRaises(UserError):
            wiz.action_confirm()

    def test_whatsapp_send_is_dry_run_without_http(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
        ICP.set_param('dkt_isp_billing.wa_fonnte_token', '')
        ICP.set_param('dkt_isp_billing.wa_meta_token', '')
        wiz = self.env['isp.notification.prepare.wizard'].create({
            'only_late': False,
            'add_queue_tag': True,
            'wa_dry_run': True,
            'template_kind': 'invoice',
            'line_ids': [(0, 0, {
                'partner_id': self.partner.id,
                'phone': self.partner.phone,
                'selected': True,
            })],
        })
        with patch('urllib.request.urlopen') as mock_http:
            self.assertTrue(wiz.line_ids)
            wiz.action_send_whatsapp()
            mock_http.assert_not_called()
        logs = self.env['isp.whatsapp.message'].search([
            ('partner_id', '=', self.partner.id),
        ])
        self.assertTrue(logs)
        self.assertTrue(all(rec.state in ('dry_run', 'failed') for rec in logs))
        self.assertFalse(any(rec.state == 'sent' for rec in logs))

    def test_notification_prepare_still_does_not_send(self):
        wiz = self.env['isp.notification.prepare.wizard'].create({
            'only_late': False,
            'add_queue_tag': True,
            'add_auto_tag': True,
            'line_ids': [(0, 0, {
                'partner_id': self.partner.id,
                'phone': self.partner.phone,
                'selected': True,
            })],
        })
        with patch.object(
            type(self.env['isp.whatsapp.message']), 'queue_and_send',
        ) as mock_send:
            wiz.action_prepare()
            mock_send.assert_not_called()
        self.assertTrue(self.partner.isp_notif_prepared)

    def test_phone_normalize_and_template(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
        ICP.set_param('dkt_isp_billing.wa_fonnte_token', '')
        ICP.set_param('dkt_isp_billing.wa_meta_token', '')
        WA = self.env['isp.whatsapp.message']
        self.assertEqual(WA.normalize_phone('081234567890'), '6281234567890')
        self.assertEqual(WA.normalize_phone('081-2345-67890'), '6281234567890')
        self.assertEqual(WA.normalize_phone('081 2345 67890'), '6281234567890')
        self.assertEqual(WA.normalize_phone('081.2345.67890'), '6281234567890')
        body = WA.render_template('late', self.partner)
        self.assertIn('Halo Pelanggan Uji Bayar', body)
        self.assertIn('DOTAKARO', body)
        cfg = WA.get_wa_config()
        self.assertTrue(cfg['dry_run'])
        self.assertFalse(WA.has_wa_credentials(cfg))

    def test_fonnte_and_meta_payload_from_existing_templates(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_provider', 'meta')
        WA = self.env['isp.whatsapp.message']
        rec = WA.create({
            'partner_id': self.partner.id,
            'phone': '6281234567890',
            'body': WA.render_template('invoice', self.partner),
            'template_kind': 'invoice',
            'provider': 'meta',
        })
        fonnte = rec._fonnte_payload()
        self.assertEqual(fonnte['target'], '6281234567890')
        self.assertIn('Halo Pelanggan Uji Bayar', fonnte['message'])
        self.assertEqual(fonnte['countryCode'], '62')
        text_payload = rec._meta_payload({
            'meta_template_invoice': '',
            'meta_lang': 'id',
        })
        self.assertEqual(text_payload['type'], 'text')
        self.assertIn('Halo Pelanggan Uji Bayar', text_payload['text']['body'])
        tpl_payload = rec._meta_payload({
            'meta_template_invoice': 'isp_tagihan',
            'meta_lang': 'id',
        })
        self.assertEqual(tpl_payload['type'], 'template')
        self.assertEqual(tpl_payload['template']['name'], 'isp_tagihan')
        params = tpl_payload['template']['components'][0]['parameters']
        self.assertEqual(len(params), 5)
        self.assertEqual(params[0]['text'], 'Pelanggan Uji Bayar')
        wiz = self.env['isp.notification.prepare.wizard'].create({})
        self.assertEqual(wiz.wa_provider, 'meta')

    def test_wizard_loads_selection_without_sending(self):
        wiz = self.env['isp.notification.prepare.wizard'].with_context(
            active_model='res.partner',
            active_ids=self.partner.ids,
        ).create({})
        self.assertEqual(wiz.line_ids.partner_id, self.partner)
        with patch('urllib.request.urlopen') as mock_http:
            wiz.action_send_whatsapp()
            mock_http.assert_not_called()
        logs = self.env['isp.whatsapp.message'].search([
            ('partner_id', '=', self.partner.id),
        ])
        self.assertTrue(logs)
        self.assertFalse(any(rec.state == 'sent' for rec in logs))
