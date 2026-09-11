import base64
import os

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.dkt_isp_billing.models.isp_ocr import (
    extract_text_from_bytes,
    parse_ocr_text,
)

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
Catatan
-
Nominal
Rp200.000
Biaya Admin
Rp0
"""

SAMPLE_PATH = os.path.join(os.path.dirname(__file__), 'samples', 'brimo_sample.png')


@tagged('post_install', '-at_install', 'dkt_isp_brimo')
class TestIspBrimoOcr(TransactionCase):
    """Parser BRImo, validasi rekening tujuan, wajib konfirmasi."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.ocr_auto_post_enabled', 'False')
        ICP.set_param('dkt_isp_billing.dest_bank_name', 'BANK BRI')
        ICP.set_param('dkt_isp_billing.dest_account_number', '014401000343565')
        ICP.set_param('dkt_isp_billing.dest_account_name', 'WASPADA SINULINGGA')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Marcella Perawati Br Ginting',
            'customer_rank': 1,
        })

    def test_parse_brimo_sample_text(self):
        parsed = parse_ocr_text(BRIMO_TEXT)
        self.assertEqual(parsed['amount'], 200000.0)
        self.assertEqual(parsed['ref'], '194723845619')
        self.assertTrue(parsed['dest_name'])
        self.assertIn('WASPADA', parsed['dest_name'])
        self.assertEqual(parsed['date'].isoformat(), '2026-08-14')
        self.assertEqual(parsed['status'], 'success')
        self.assertEqual(parsed['dest_account'], '014401000343565')
        self.assertTrue(parsed['is_brimo'])
        self.assertGreaterEqual(parsed['confidence'], 0.7)

    def test_parse_sample_image_if_tesseract(self):
        if not os.path.isfile(SAMPLE_PATH):
            self.skipTest('Sampel BRImo tidak ada')
        with open(SAMPLE_PATH, 'rb') as handle:
            data = handle.read()
        text, engine = extract_text_from_bytes(data, 'image/png', 'brimo_sample.png')
        if engine == 'none' or not text.strip():
            self.skipTest('Tesseract belum terpasang di lingkungan tes')
        parsed = parse_ocr_text(text)
        self.assertEqual(parsed['amount'], 200000.0, text[:400])
        self.assertEqual(parsed['ref'], '194723845619', text[:400])
        self.assertTrue(parsed['dest_name'] and 'WASPADA' in parsed['dest_name'], text[:400])
        self.assertEqual(parsed['date'].isoformat(), '2026-08-14', text[:400])

    def test_proof_brimo_text_requires_confirm(self):
        proof = self.env['isp.payment.proof'].create({
            'partner_id': self.partner.id,
            'image': base64.b64encode(BRIMO_TEXT.encode('utf-8')),
            'image_filename': 'brimo.txt',
        })
        self.assertEqual(proof.amount_ocr, 200000.0)
        self.assertEqual(proof.ref_ocr, '194723845619')
        self.assertIn('WASPADA', proof.dest_name_ocr or '')
        self.assertEqual(proof.dest_match, 'ok')
        self.assertNotEqual(proof.state, 'paid')
        self.assertFalse(proof.payment_id)

    def test_wrong_destination_goes_to_review(self):
        text = BRIMO_TEXT.replace('0144 0100 0343 565', '1111 2222 3333 444')
        text = text.replace('WASPADA SINULINGGA', 'ORANG LAIN')
        proof = self.env['isp.payment.proof'].create({
            'partner_id': self.partner.id,
            'image': base64.b64encode(text.encode('utf-8')),
            'image_filename': 'salah.txt',
        })
        self.assertEqual(proof.dest_match, 'mismatch')
        self.assertTrue(proof.needs_review)
        self.assertEqual(proof.state, 'review')
        self.assertFalse(proof.payment_id)
        with self.assertRaises(UserError):
            proof.action_verify()

    def test_duplicate_ref_goes_to_review(self):
        first = self.env['isp.payment.proof'].create({
            'partner_id': self.partner.id,
            'image': base64.b64encode(BRIMO_TEXT.encode('utf-8')),
            'image_filename': 'satu.txt',
        })
        self.assertEqual(first.ref_ocr, '194723845619')
        second = self.env['isp.payment.proof'].create({
            'partner_id': self.partner.id,
            'image': base64.b64encode(BRIMO_TEXT.encode('utf-8')),
            'image_filename': 'dua.txt',
        })
        self.assertTrue(second.needs_review)
        self.assertIn('dobel', (second.review_reason or '').lower() + (second.ocr_note or '').lower())
        self.assertFalse(second.ref_ocr)

    def test_village_admin_group_exists(self):
        group = self.env.ref('dkt_isp_billing.group_isp_village_admin')
        self.assertEqual(group.name, 'Admin Desa')
        self.assertTrue(group.privilege_id)
        collector = self.env.ref('dkt_isp_billing.group_isp_collector')
        self.assertEqual(collector.name, 'Kolektor')
