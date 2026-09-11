import base64
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

BRIMO_TEXT = """
Transaksi Berhasil
14 Agustus 2026, 18:44:47 WIB
Total Transaksi
Rp200.000
No. Ref
194723845619
Sumber Dana
MARCELLA PERAWATI BR GINTING
BANK BRI
5277 **** **** 530
Tujuan
WASPADA SINULINGGA
BANK BRI
0144 0100 0343 565
Jenis Transaksi
Transfer Bank BRI
Nominal
Rp200.000
"""


@tagged('post_install', '-at_install', 'dkt_isp_payment_verify')
class TestIspPaymentVerify(TransactionCase):
    """Desa/kolektor ajukan, pusat verifikasi, pelanggan masuk antrian."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.ocr_auto_post_enabled', 'False')
        ICP.set_param('dkt_isp_billing.dest_bank_name', 'BANK BRI')
        ICP.set_param('dkt_isp_billing.dest_account_number', '014401000343565')
        ICP.set_param('dkt_isp_billing.dest_account_name', 'WASPADA SINULINGGA')
        cls.area = cls.env['isp.area'].create({
            'name': 'Desa Uji Verifikasi',
            'code': 'UJI_VERIF',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Marcella Perawati Br Ginting',
            'customer_rank': 1,
            'area_id': cls.area.id,
        })
        village_group = cls.env.ref('dkt_isp_billing.group_isp_village_admin')
        collector_group = cls.env.ref('dkt_isp_billing.group_isp_collector')
        cls.village_user = cls.env['res.users'].create({
            'name': 'Admin Desa Verifikasi',
            'login': 'desa_uji_verifikasi',
            'group_ids': [Command.set([village_group.id])],
            'isp_area_ids': [Command.set([cls.area.id])],
        })
        cls.collector_user = cls.env['res.users'].create({
            'name': 'Kolektor Verifikasi',
            'login': 'kolektor_uji_verifikasi',
            'group_ids': [Command.set([collector_group.id])],
            'isp_area_ids': [Command.set([cls.area.id])],
        })

    def _make_proof(self, **vals):
        values = {
            'partner_id': self.partner.id,
            'area_id': self.area.id,
            'image': base64.b64encode(BRIMO_TEXT.encode('utf-8')),
            'image_filename': 'brimo.txt',
        }
        values.update(vals)
        return self.env['isp.payment.proof'].create(values)

    def _make_invoice(self, amount=200000.0):
        product = self.env.ref(
            'dkt_isp_billing.product_template_internet_service'
        ).product_variant_id
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'name': 'Internet uji',
                'quantity': 1,
                'price_unit': amount,
                'tax_ids': [Command.clear()],
                'product_id': product.id,
            })],
        })
        move.action_post()
        return move

    def test_collector_group_exists(self):
        group = self.env.ref('dkt_isp_billing.group_isp_collector')
        self.assertEqual(group.name, 'Kolektor')
        self.assertTrue(self.collector_user._isp_is_submitter_only())
        self.assertFalse(self.collector_user._isp_can_verify_payment())

    def test_ocr_stays_in_queue_not_paid(self):
        proof = self._make_proof()
        self.assertNotEqual(proof.state, 'paid')
        self.assertFalse(proof.payment_id)
        self.assertFalse(proof._can_auto_post())
        self.assertIn(proof.state, ('draft', 'review'))

    def test_village_can_submit_cannot_verify(self):
        proof = self._make_proof()
        proof.with_user(self.village_user).action_submit()
        self.assertIn(proof.state, ('submitted', 'review'))
        self.assertEqual(proof.submitted_by, self.village_user)
        self.assertTrue(proof.submitted_at)
        with self.assertRaises(UserError):
            proof.with_user(self.village_user).action_verify()
        with self.assertRaises(UserError):
            proof.with_user(self.village_user).action_reject(reason='Tidak boleh')
        self.assertFalse(proof.payment_id)

    def test_collector_cannot_verify(self):
        proof = self._make_proof()
        proof.with_user(self.collector_user).action_submit()
        with self.assertRaises(UserError):
            proof.with_user(self.collector_user).action_confirm_payment()

    def test_hq_can_reject(self):
        proof = self._make_proof()
        proof.action_submit()
        proof.action_reject(reason='Rekening tujuan tidak jelas')
        self.assertEqual(proof.state, 'rejected')
        self.assertIn('tidak jelas', proof.reject_reason)

    def test_customer_upload_enters_submitted_queue(self):
        invoice = self._make_invoice()
        self.assertTrue(invoice.isp_upload_token)
        self.assertIn('/isp/unggah/', invoice.isp_upload_url)
        proof = self.env['isp.payment.proof'].create_from_customer_upload({
            'invoice_id': invoice.id,
            'partner_id': self.partner.id,
            'area_id': self.area.id,
            'image': base64.b64encode(BRIMO_TEXT.encode('utf-8')),
            'image_filename': 'brimo.txt',
            'public_name': 'Marcella',
            'public_phone': '081234567890',
        }, source='public', ip='203.0.113.10', token=invoice.isp_upload_token)
        self.assertIn(proof.state, ('submitted', 'review'))
        self.assertEqual(proof.source, 'public')
        self.assertFalse(proof.payment_id)
        self.assertNotEqual(proof.state, 'paid')

    def test_village_cash_submits_not_posts(self):
        invoice = self._make_invoice()
        journal = self.env['account.journal'].search([
            ('type', '=', 'cash'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        if not journal:
            self.skipTest('Jurnal kas belum ada di lingkungan tes')
        wiz = self.env['isp.cash.payment.wizard'].with_user(self.village_user).create({
            'partner_id': self.partner.id,
            'invoice_id': invoice.id,
            'journal_id': journal.id,
            'amount': invoice.amount_residual,
        })
        action = wiz.action_confirm()
        self.assertEqual(action['res_model'], 'isp.payment.proof')
        proof = self.env['isp.payment.proof'].browse(action['res_id'])
        self.assertEqual(proof.proof_type, 'cash')
        self.assertIn(proof.state, ('submitted', 'review'))
        self.assertFalse(proof.payment_id)
        self.assertEqual(invoice.payment_state, 'not_paid')

    def test_village_cannot_register_payment(self):
        invoice = self._make_invoice()
        with self.assertRaises(UserError):
            invoice.with_user(self.village_user).action_register_payment()

    def test_hq_verify_full_payment_clears_invoice(self):
        invoice = self._make_invoice(200000)
        journal = self.env['account.journal'].search([
            ('type', 'in', ('bank', 'cash')),
            ('company_id', '=', self.env.company.id),
            ('default_account_id', '!=', False),
        ], limit=1)
        if not journal:
            self.skipTest('Jurnal bank/kas belum ada di lingkungan tes')
        proof = self._make_proof(
            invoice_id=invoice.id,
            amount_ocr=200000,
            dest_match='ok',
            status_ocr='success',
        )
        proof.action_submit()
        proof.action_verify()
        invoice.invalidate_recordset(['amount_residual', 'payment_state'])
        self.assertTrue(proof.payment_id)
        self.assertTrue(proof.payment_id.move_id)
        self.assertEqual(proof.payment_id.move_id.state, 'posted')
        self.assertTrue(invoice.currency_id.is_zero(invoice.amount_residual))
        self.assertEqual(invoice.payment_state, 'paid')
        self.assertEqual(proof.payment_id.state, 'paid')
        self.assertEqual(proof.payment_id.outstanding_account_id.account_type, 'asset_cash')
        liquidity = proof.payment_id._seek_for_lines()[0]
        self.assertTrue(liquidity)
        self.assertTrue(all(line.account_id.account_type == 'asset_cash' for line in liquidity))
        self.assertFalse(proof.is_underpayment)
        leftover = self.env['account.move'].search(
            self.env['isp.dashboard']._domain_invoice_outstanding() + [('id', '=', invoice.id)]
        )
        self.assertFalse(leftover)

    def test_underpayment_attaches_invoice_for_review(self):
        invoice = self._make_invoice(200000)
        proof = self._make_proof()
        proof.write({
            'amount_ocr': 100000,
            'amount_manual': 100000,
            'dest_match': 'ok',
            'status_ocr': 'success',
        })
        proof._match_and_maybe_pay()
        self.assertTrue(proof.is_underpayment)
        self.assertEqual(proof.invoice_id, invoice)
        self.assertEqual(proof.state, 'review')
        self.assertIn('KURANG', proof.review_reason or '')

    def test_overpayment_needs_review_without_underpayment_flag(self):
        invoice = self._make_invoice(200000)
        proof = self._make_proof()
        proof.write({
            'amount_ocr': 250000,
            'amount_manual': 250000,
            'dest_match': 'ok',
            'status_ocr': 'success',
        })
        proof._match_and_maybe_pay()
        self.assertFalse(proof.is_underpayment)
        self.assertEqual(proof.invoice_id, invoice)
        self.assertIn('LEBIH', proof.review_reason or '')

    def test_exact_amount_is_not_underpayment(self):
        invoice = self._make_invoice(200000)
        proof = self._make_proof()
        proof.write({
            'amount_ocr': 200000,
            'amount_manual': 200000,
            'dest_match': 'ok',
            'status_ocr': 'success',
        })
        proof._match_and_maybe_pay()
        self.assertFalse(proof.is_underpayment)
        self.assertEqual(proof.invoice_id, invoice)
        self.assertIn('cocok', (proof.review_reason or '').lower())

    def test_rate_limit_public_upload(self):
        invoice = self._make_invoice()
        Proof = self.env['isp.payment.proof']
        for idx in range(8):
            Proof.create({
                'partner_id': self.partner.id,
                'upload_ip': '198.51.100.9',
                'image_filename': 'x%s.txt' % idx,
            })
        with self.assertRaises(UserError):
            Proof._check_upload_rate_limit('198.51.100.9', invoice.isp_upload_token)

    def test_finalize_moves_outstanding_receipts_to_bank(self):
        invoice = self._make_invoice(200000)
        journal = self.env['account.journal'].search([
            ('type', 'in', ('bank', 'cash')),
            ('company_id', '=', self.env.company.id),
            ('default_account_id', '!=', False),
        ], limit=1)
        if not journal or journal.default_account_id.account_type != 'asset_cash':
            self.skipTest('Jurnal bank/kas belum ada di lingkungan tes')
        outstanding = self.env['account.chart.template'].ref(
            'account_journal_payment_debit_account_id', raise_if_not_found=False,
        )
        if not outstanding or outstanding.account_type == 'asset_cash':
            outstanding = self.env['account.account'].search([
                ('account_type', '=', 'asset_current'),
                ('reconcile', '=', True),
            ], limit=1)
        if not outstanding:
            self.skipTest('Akun outstanding receipts belum ada')
        payment = invoice._isp_register_inbound_payment(
            journal, invoice.amount_residual,
        )
        liquidity = payment._seek_for_lines()[0]
        self.assertTrue(liquidity)
        liquidity.with_context(skip_readonly_check=True).write({
            'account_id': outstanding.id,
        })
        payment._write({'outstanding_account_id': outstanding.id})
        payment.invalidate_recordset(['state', 'is_matched', 'outstanding_account_id'])
        invoice.invalidate_recordset(['payment_state', 'amount_residual'])
        self.assertEqual(payment.state, 'in_process')
        payment._isp_finalize_inbound_payment(invoice)
        invoice.invalidate_recordset(['amount_residual', 'payment_state'])
        payment.invalidate_recordset(['state', 'outstanding_account_id'])
        self.assertEqual(payment.state, 'paid')
        self.assertEqual(invoice.payment_state, 'paid')
        self.assertEqual(payment.outstanding_account_id.account_type, 'asset_cash')
        liquidity = payment._seek_for_lines()[0]
        self.assertTrue(all(line.account_id.account_type == 'asset_cash' for line in liquidity))

    def test_paid_zero_residual_not_in_outstanding_kpi(self):
        invoice = self._make_invoice(200000)
        invoice.payment_state = 'in_payment'
        self.env.cr.execute(
            'UPDATE account_move SET amount_residual = 0 WHERE id = %s',
            [invoice.id],
        )
        invoice.invalidate_recordset(['amount_residual', 'payment_state'])
        domain = self.env['isp.dashboard']._domain_invoice_outstanding()
        leftover = self.env['account.move'].search(domain + [('id', '=', invoice.id)])
        self.assertFalse(leftover)

    def test_verify_explains_missing_outstanding_invoice(self):
        proof = self._make_proof(amount_ocr=200000, dest_match='ok', status_ocr='success')
        with self.assertRaises(UserError) as ctx:
            proof._invoice_for_verify()
        msg = str(ctx.exception)
        self.assertIn(self.partner.name, msg)
        self.assertIn('belum punya invoice posted', msg)

    def test_verify_auto_picks_single_outstanding_invoice(self):
        invoice = self._make_invoice(200000)
        proof = self._make_proof(amount_ocr=200000, dest_match='ok', status_ocr='success')
        self.assertFalse(proof.invoice_id)
        picked = proof._invoice_for_verify()
        self.assertEqual(picked, invoice)
        self.assertEqual(proof.invoice_id, invoice)

    def test_verify_rejects_draft_invoice_on_proof(self):
        product = self.env.ref(
            'dkt_isp_billing.product_template_internet_service'
        ).product_variant_id
        draft = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'name': 'Internet draft',
                'quantity': 1,
                'price_unit': 200000,
                'tax_ids': [Command.clear()],
                'product_id': product.id,
            })],
        })
        proof = self._make_proof(
            invoice_id=draft.id,
            amount_ocr=200000,
            dest_match='ok',
            status_ocr='success',
        )
        with self.assertRaises(UserError) as ctx:
            proof._invoice_for_verify()
        self.assertIn('masih draft', str(ctx.exception))

    def test_hq_post_without_wa_pays_cash_and_skips_thanks(self):
        invoice = self._make_invoice(150000)
        journal = self.env['account.journal'].search([
            ('type', '=', 'cash'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        if not journal:
            self.skipTest('Jurnal kas belum ada di lingkungan tes')
        proof = self.env['isp.payment.proof'].create({
            'proof_type': 'cash',
            'source': 'backend',
            'partner_id': self.partner.id,
            'invoice_id': invoice.id,
            'amount_manual': 150000,
            'journal_id': journal.id,
            'inbound_note': 'Tes rekap Excel tanpa WA',
        })
        with patch.object(
            type(self.env['isp.whatsapp.message']), 'queue_and_send',
        ) as send:
            proof.action_hq_post_without_wa()
        send.assert_not_called()
        invoice.invalidate_recordset(['payment_state', 'amount_residual'])
        self.assertEqual(proof.state, 'paid')
        self.assertTrue(proof.payment_id)
        self.assertEqual(invoice.payment_state, 'paid')
        self.assertFalse(proof.needs_review)

    def test_group_paid_notify_uses_billing_group(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.wa_billing_group_id', '120363billinguji')
        proof = self._make_proof(source='whatsapp_group')
        with patch.object(
            type(self.env['isp.wa.registration']), '_reply', return_value=True,
        ) as reply:
            sent = proof._notify_paid_group_whatsapp()
        self.assertTrue(sent)
        self.assertEqual(reply.call_args.args[0], '120363billinguji')
        self.assertIn(proof.name, reply.call_args.args[1])
