import base64
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

BILLING_ID = '120363billinguji'
REPORTER = '6281888000999'
ID_MONTHS = {
    1: 'Januari', 2: 'Februari', 3: 'Maret', 4: 'April',
    5: 'Mei', 6: 'Juni', 7: 'Juli', 8: 'Agustus',
    9: 'September', 10: 'Oktober', 11: 'November', 12: 'Desember',
}


@tagged('post_install', '-at_install', 'dkt_isp_wa_register')
class TestIspWaBillingConfirm(TransactionCase):
    """Struk tanpa caption: nama unik + dest benar + YA = lunas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_inbound_enabled', 'True')
        ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
        ICP.set_param('dkt_isp_billing.wa_fonnte_token', '')
        ICP.set_param('dkt_isp_billing.wa_billing_group_id', BILLING_ID)
        ICP.set_param('dkt_isp_billing.ocr_auto_post_enabled', 'False')
        ICP.set_param('dkt_isp_billing.dest_bank_name', 'BANK BRI')
        ICP.set_param('dkt_isp_billing.dest_account_number', '014401000343565')
        ICP.set_param('dkt_isp_billing.dest_account_name', 'WASPADA SINULINGGA')
        cls.area = cls.env['isp.area'].create({
            'name': 'Desa Uji Konfirmasi',
            'code': 'UJI_KONFIRM',
        })
        cls.router = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Uji Konfirmasi',
            'host': '192.0.2.88:18728',
            'username': 'test',
            'password': 'test',
            'active': True,
            'area_id': cls.area.id,
            'health_state': 'reachable',
            'last_test_ok': True,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Dahlina Simamora Konfirmasi',
            'customer_rank': 1,
            'area_id': cls.area.id,
            'phone': '082365083231',
            'phone_wa': '6282365083231',
        })
        cls.cpe = cls.env['isp.cpe'].create({
            'name': 'CPE Konfirmasi',
            'partner_id': cls.partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'dahlina-konfirmasi-082365083231',
            'pppoe_password': 'pwdKonf1',
            'mikrotik_config_id': cls.router.id,
            'state': 'open',
            'isp_onboarding': False,
        })
        cls.WA = cls.env['isp.whatsapp.message']
        cls.Reg = cls.env['isp.wa.registration']

    def _inbound(self, **payload):
        with patch.object(type(self.WA), 'send_inbound_ack', return_value=True) as ack:
            result = self.WA.process_fonnte_inbound(payload)
        return result, ack

    def _receipt_text(
        self,
        sender='DAHLINA SIMAMORA KONFIRMASI',
        dest_name='WASPADA SINULINGGA',
        dest_acc='0144 0100 0343 565',
        amount='200.000',
        day=None,
        ref='194723845701',
    ):
        today = fields.Date.today()
        day = day or today
        date_line = '%s %s %s, 18:44:47 WIB' % (
            day.day, ID_MONTHS[day.month], day.year,
        )
        return (
            'Transaksi Berhasil\n'
            '%s\n'
            'Total Transaksi\n'
            'Rp%s\n'
            'No. Ref\n'
            '%s\n'
            'Sumber Dana\n'
            '%s\n'
            'BANK BRI\n'
            '5277 **** **** 530\n'
            'Tujuan\n'
            '%s\n'
            'BANK BRI\n'
            '%s\n'
            'Jenis Transaksi\n'
            'Transfer Bank BRI\n'
            'Nominal\n'
            'Rp%s\n'
        ) % (date_line, amount, ref, sender, dest_name, dest_acc, amount)

    def _make_invoice(self, amount=200000.0, invoice_date=None, partner=None):
        product = self.env.ref(
            'dkt_isp_billing.product_template_internet_service'
        ).product_variant_id
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': (partner or self.partner).id,
            'invoice_date': invoice_date or fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'name': 'Internet uji konfirmasi',
                'quantity': 1,
                'price_unit': amount,
                'tax_ids': [Command.clear()],
                'product_id': product.id,
            })],
        })
        move.action_post()
        return move

    def _send_receipt(self, text, message='bukti transfer', member=REPORTER, name='Andi Lapangan'):
        raw = text.encode('utf-8')
        with patch.object(type(self.WA), '_download_https_file', return_value=raw):
            return self._inbound(
                sender=BILLING_ID,
                member=member,
                name=name,
                message=message,
                url='https://example.com/struk.png',
                extension='png',
                filename='bukti.txt',
            )

    def _journal_or_skip(self):
        journal = self.env['account.journal'].search([
            ('type', 'in', ('bank', 'cash')),
            ('company_id', '=', self.env.company.id),
            ('default_account_id', '!=', False),
        ], limit=1)
        if not journal:
            self.skipTest('Jurnal bank/kas belum ada di lingkungan tes')
        return journal

    def test_name_match_asks_confirm_not_paid(self):
        self._make_invoice()
        result, ack = self._send_receipt(self._receipt_text())
        self.assertTrue(result.get('confirm_pending'))
        self.assertFalse(result.get('paid'))
        proof = self.env['isp.payment.proof'].search([
            ('name', '=', result['proof_name']),
        ], limit=1)
        self.assertEqual(proof.partner_id, self.partner)
        self.assertEqual(proof.dest_match, 'ok')
        self.assertEqual(proof.wa_confirm_phone, REPORTER)
        self.assertNotEqual(proof.state, 'paid')
        self.assertFalse(proof.payment_id)
        msg = ack.call_args.kwargs['message']
        self.assertIn('@%s' % REPORTER, msg)
        self.assertIn('Dahlina Simamora Konfirmasi', msg)
        self.assertIn('Balas YA', msg)

    def test_confirm_yes_marks_paid(self):
        self._journal_or_skip()
        invoice = self._make_invoice()
        first, _ack = self._send_receipt(self._receipt_text())
        self.assertTrue(first.get('confirm_pending'))
        result, ack = self._inbound(
            sender=BILLING_ID,
            member=REPORTER,
            name='Andi Lapangan',
            message='YA',
        )
        self.assertEqual(result.get('confirm'), 'yes')
        self.assertTrue(result.get('paid'))
        proof = self.env['isp.payment.proof'].search([
            ('name', '=', result['proof_name']),
        ], limit=1)
        invoice.invalidate_recordset(['amount_residual', 'payment_state'])
        self.assertEqual(proof.state, 'paid')
        self.assertTrue(proof.payment_id)
        self.assertTrue(invoice.currency_id.is_zero(invoice.amount_residual))
        self.assertIn('lunas', ack.call_args.kwargs['message'].lower())

    def test_confirm_no_cancels(self):
        self._make_invoice()
        first, _ack = self._send_receipt(self._receipt_text(ref='194723845702'))
        self.assertTrue(first.get('confirm_pending'))
        result, ack = self._inbound(
            sender=BILLING_ID,
            member=REPORTER,
            name='Andi Lapangan',
            message='TIDAK',
        )
        self.assertEqual(result.get('confirm'), 'no')
        proof = self.env['isp.payment.proof'].search([
            ('name', '=', result['proof_name']),
        ], limit=1)
        self.assertEqual(proof.state, 'rejected')
        self.assertFalse(proof.payment_id)
        self.assertIn('dibatalkan', ack.call_args.kwargs['message'].lower())

    def test_wrong_dest_not_paid(self):
        self._make_invoice()
        text = self._receipt_text(
            dest_name='ORANG LAIN',
            dest_acc='1111 2222 3333 444',
            ref='194723845703',
        )
        result, ack = self._send_receipt(text)
        self.assertTrue(result.get('dest_mismatch'))
        self.assertFalse(result.get('confirm_pending'))
        proof = self.env['isp.payment.proof'].search([
            ('name', '=', result['proof_name']),
        ], limit=1)
        self.assertEqual(proof.dest_match, 'mismatch')
        self.assertNotEqual(proof.state, 'paid')
        self.assertFalse(proof.payment_id)
        self.assertIn('BUKAN rekening terdaftar', ack.call_args.kwargs['message'])

    def test_dest_name_wrong_blocks_autopay(self):
        self._make_invoice()
        text = self._receipt_text(
            dest_name='ORANG LAIN',
            dest_acc='0144 0100 0343 565',
            ref='194723845704',
        )
        result, _ack = self._send_receipt(text)
        self.assertTrue(result.get('dest_unsafe') or result.get('review'))
        self.assertFalse(result.get('confirm_pending'))
        proof = self.env['isp.payment.proof'].search([
            ('name', '=', result['proof_name']),
        ], limit=1)
        self.assertEqual(proof.dest_match, 'ok')
        dest_ok, _why = proof._dest_safe_for_autopay()
        self.assertFalse(dest_ok)
        self.assertNotEqual(proof.state, 'paid')

    def test_period_mismatch_goes_review(self):
        old = fields.Date.today() - timedelta(days=80)
        self._make_invoice(invoice_date=old)
        result, ack = self._send_receipt(self._receipt_text(ref='194723845705'))
        self.assertTrue(result.get('review'))
        self.assertFalse(result.get('confirm_pending'))
        proof = self.env['isp.payment.proof'].search([
            ('name', '=', result['proof_name']),
        ], limit=1)
        self.assertNotEqual(proof.state, 'paid')
        self.assertFalse(proof.payment_id)
        self.assertIn('bulan tagihan', ack.call_args.kwargs['message'].lower())

    def test_ambiguous_name_asks_caption(self):
        self.env['res.partner'].create({
            'name': 'Horas Manik Billing',
            'customer_rank': 1,
        })
        self.env['res.partner'].create({
            'name': 'Horas Manik Billing',
            'customer_rank': 1,
        })
        text = self._receipt_text(
            sender='HORAS MANIK BILLING',
            ref='194723845706',
        )
        result, ack = self._send_receipt(text)
        self.assertTrue(result.get('receipt_hint'))
        self.assertFalse(result.get('proof_name'))
        self.assertIn('BAYAR username', ack.call_args.kwargs['message'])

    def test_other_phone_cannot_confirm(self):
        self._make_invoice()
        first, _ack = self._send_receipt(self._receipt_text(ref='194723845707'))
        self.assertTrue(first.get('confirm_pending'))
        result, _ack = self._inbound(
            sender=BILLING_ID,
            member='6281999000888',
            message='YA',
        )
        self.assertEqual(result.get('ignored'), 'billing_chatter')
        proof = self.env['isp.payment.proof'].search([
            ('name', '=', first['proof_name']),
        ], limit=1)
        self.assertNotEqual(proof.state, 'paid')
        self.assertEqual(proof.wa_confirm_phone, REPORTER)

    def test_bayar_caption_still_not_auto_paid(self):
        self._make_invoice()
        raw = self._receipt_text(ref='194723845708').encode('utf-8')
        with patch.object(type(self.WA), '_download_https_file', return_value=raw):
            result, ack = self._inbound(
                sender=BILLING_ID,
                member=REPORTER,
                name='Andi Lapangan',
                message='BAYAR dahlina-konfirmasi-082365083231',
                url='https://example.com/bukti.png',
                extension='png',
                filename='bukti.txt',
            )
        self.assertTrue(result.get('matched'))
        proof = self.env['isp.payment.proof'].search([
            ('name', '=', result['proof_name']),
        ], limit=1)
        self.assertNotEqual(proof.state, 'paid')
        self.assertFalse(proof.payment_id)
        self.assertIn('Belum lunas', ack.call_args.kwargs['message'])

    def test_ocr_period_and_dest_helpers(self):
        invoice = self._make_invoice()
        proof = self.env['isp.payment.proof'].create({
            'partner_id': self.partner.id,
            'invoice_id': invoice.id,
            'image': base64.b64encode(self._receipt_text(ref='194723845709').encode('utf-8')),
            'image_filename': 'bukti.txt',
        })
        self.assertEqual(proof.dest_match, 'ok')
        dest_ok, _why = proof._dest_safe_for_autopay()
        self.assertTrue(dest_ok)
        period_ok, _why = proof._ocr_period_ok(invoice)
        self.assertTrue(period_ok)
        proof.date_ocr = fields.Date.today() + timedelta(days=10)
        period_ok, why = proof._ocr_period_ok(invoice)
        self.assertFalse(period_ok)
        self.assertIn('masa depan', why)
