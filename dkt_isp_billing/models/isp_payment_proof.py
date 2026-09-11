import base64
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .isp_ocr import (
    accounts_match,
    extract_text_from_bytes,
    name_similarity_score,
    names_similar,
    normalize_account_number,
    parse_ocr_text,
)

_logger = logging.getLogger(__name__)

SUBMIT_STATES = ('draft', 'review', 'rejected')
VERIFY_STATES = ('draft', 'submitted', 'review')
PUBLIC_MAX_BYTES = 5 * 1024 * 1024
PUBLIC_RATE_IP = 8
PUBLIC_RATE_TOKEN = 5
PUBLIC_RATE_MINUTES = 15


class IspPaymentProof(models.Model):
    _name = 'isp.payment.proof'
    _description = 'Bukti transfer / pengajuan kas pelanggan'
    _order = 'id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nomor', default='New', copy=False, readonly=True)
    proof_type = fields.Selection(
        [('transfer', 'Transfer'), ('cash', 'Kas tunai')],
        string='Jenis',
        default='transfer',
        required=True,
        tracking=True,
        index=True,
    )
    source = fields.Selection(
        [
            ('backend', 'Backend'),
            ('village', 'Admin desa'),
            ('collector', 'Kolektor'),
            ('portal', 'Portal pelanggan'),
            ('public', 'Tautan unggah'),
            ('whatsapp', 'WhatsApp'),
            ('whatsapp_group', 'Grup Billing-dkt'),
        ],
        string='Sumber',
        default='backend',
        tracking=True,
        index=True,
        help='WhatsApp: foto masuk lewat webhook Fonnte. Bukan lunas otomatis.',
    )
    partner_id = fields.Many2one(
        'res.partner', string='Pelanggan', tracking=True, index=True,
        domain=[('customer_rank', '>', 0)],
    )
    subscription_id = fields.Many2one(
        'isp.subscription', string='Langganan', tracking=True, index=True,
        domain="[('partner_id', '=', partner_id)]",
    )
    cpe_id = fields.Many2one(
        'isp.cpe', string='CPE', tracking=True, index=True,
        domain="[('partner_id', '=', partner_id)]",
    )
    area_id = fields.Many2one('isp.area', string='Area / Desa', tracking=True, index=True)
    invoice_id = fields.Many2one(
        'account.move', string='Invoice', tracking=True, index=True,
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), "
               "('payment_state', 'in', ('not_paid', 'partial', 'in_payment'))]",
    )
    image = fields.Binary('Bukti transfer', attachment=True)
    image_filename = fields.Char('Nama file')
    amount_ocr = fields.Monetary('Jumlah OCR', currency_field='currency_id', tracking=True)
    amount_manual = fields.Monetary('Jumlah manual', currency_field='currency_id')
    date_ocr = fields.Date('Tanggal OCR')
    ref_ocr = fields.Char('Referensi OCR', index=True)
    status_ocr = fields.Selection(
        [('success', 'Transaksi berhasil'), ('failed', 'Transaksi gagal')],
        string='Status struk',
        readonly=True,
    )
    sender_name_ocr = fields.Char('Pengirim (OCR)', readonly=True)
    sender_bank_ocr = fields.Char('Bank pengirim', readonly=True)
    sender_account_ocr = fields.Char('Rekening pengirim (tersamar)', readonly=True)
    dest_name_ocr = fields.Char('Penerima (OCR)', readonly=True)
    dest_bank_ocr = fields.Char('Bank tujuan (OCR)', readonly=True)
    dest_account_ocr = fields.Char('Rekening tujuan (OCR)', readonly=True)
    dest_match = fields.Selection(
        [
            ('ok', 'Rekening tujuan cocok'),
            ('mismatch', 'Rekening tujuan salah'),
            ('unknown', 'Belum bisa divalidasi'),
        ],
        string='Validasi tujuan',
        default='unknown',
        tracking=True,
        index=True,
    )
    dest_match_note = fields.Char('Catatan tujuan')
    currency_id = fields.Many2one(
        'res.currency', string='Mata uang',
        default=lambda self: self.env.company.currency_id,
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('submitted', 'Diajukan'),
            ('review', 'Perlu review'),
            ('verified', 'Terverifikasi'),
            ('paid', 'Lunas'),
            ('rejected', 'Ditolak'),
        ],
        string='Status',
        default='draft',
        required=True,
        tracking=True,
        index=True,
    )
    ocr_engine = fields.Char('Mesin OCR', readonly=True)
    ocr_raw = fields.Text('Teks OCR', readonly=True)
    ocr_note = fields.Text('Catatan OCR', readonly=True)
    confidence = fields.Float('Keyakinan OCR', readonly=True)
    needs_review = fields.Boolean('Perlu review', default=False, index=True)
    is_underpayment = fields.Boolean(
        'Pembayaran kurang',
        default=False,
        index=True,
        tracking=True,
        help='Nominal bukti lebih kecil dari sisa tagihan. Bisa dicatat sebagian; isolir tidak dibuka sampai lunas.',
    )
    review_reason = fields.Char('Alasan review')
    reject_reason = fields.Text('Alasan penolakan', tracking=True)
    submitted_by = fields.Many2one('res.users', string='Diajukan oleh', readonly=True, tracking=True)
    submitted_at = fields.Datetime('Waktu pengajuan', readonly=True)
    verified_by = fields.Many2one('res.users', string='Diverifikasi oleh', readonly=True, tracking=True)
    verified_at = fields.Datetime('Waktu verifikasi', readonly=True)
    public_name = fields.Char('Nama pengunggah')
    public_phone = fields.Char('HP pengunggah')
    upload_ip = fields.Char('IP unggah', readonly=True, index=True)
    inbound_note = fields.Char(
        'Catatan inbound',
        help='Nomor pengirim / inbox Fonnte. Jangan dibalas ke pelanggan sebagai data invoice.',
    )
    wa_match = fields.Selection(
        [
            ('phone', 'Nomor WA terdaftar'),
            ('format', 'Format BAYAR'),
            ('name', 'Nama pengirim rekening'),
            ('none', 'Belum cocok'),
        ],
        string='Cocok WhatsApp',
        default='none',
        index=True,
        tracking=True,
        help='Nomor pengirim diutamakan. Format BAYAR username/HP hanya jika nomor belum terdaftar.',
    )
    wa_confirm_phone = fields.Char(
        'HP konfirmasi grup',
        index=True,
        help='Nomor pelapor yang harus membalas YA/TIDAK di grup Billing-dkt.',
    )
    wa_confirm_deadline = fields.Datetime('Batas konfirmasi grup')
    payment_id = fields.Many2one('account.payment', string='Pembayaran', readonly=True)
    journal_id = fields.Many2one(
        'account.journal', string='Jurnal',
        domain="[('type', 'in', ('bank', 'cash'))]",
    )
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('isp.payment.proof') or 'TRF'
            if not vals.get('source'):
                user = self.env.user
                if not user._is_public() and hasattr(user, '_isp_submitter_source'):
                    vals['source'] = user._isp_submitter_source()
        records = super().create(vals_list)
        if not self.env.context.get('isp_skip_ocr'):
            for rec, vals in zip(records, vals_list):
                if vals.get('image') and rec.proof_type == 'transfer':
                    rec.with_context(isp_skip_ocr=True)._run_ocr_and_match()
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get('image') and not self.env.context.get('isp_skip_ocr'):
            to_ocr = self.filtered(
                lambda r: r.proof_type == 'transfer' and r.state not in ('paid', 'verified')
            )
            to_ocr.with_context(isp_skip_ocr=True)._run_ocr_and_match()
        return res

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id and self.subscription_id.partner_id != self.partner_id:
            self.subscription_id = False
            self.invoice_id = False
            self.cpe_id = False
        if self.partner_id:
            if not self.area_id and self.partner_id.area_id:
                self.area_id = self.partner_id.area_id
            if not self.subscription_id:
                self.subscription_id = self.partner_id.subscription_ids[:1]
            if not self.cpe_id and self.partner_id.cpe_ids:
                self.cpe_id = self.partner_id.cpe_ids[:1]

    @api.onchange('subscription_id')
    def _onchange_subscription_id(self):
        if self.subscription_id:
            self.partner_id = self.subscription_id.partner_id
            if self.subscription_id.cpe_id:
                self.cpe_id = self.subscription_id.cpe_id
            if self.subscription_id.area_id:
                self.area_id = self.subscription_id.area_id
            invoice = self.subscription_id._current_period_invoice()
            if invoice and invoice.payment_state != 'paid':
                self.invoice_id = invoice

    @api.onchange('cpe_id')
    def _onchange_cpe_id(self):
        if self.cpe_id:
            self.partner_id = self.cpe_id.partner_id
            if self.cpe_id.area_id:
                self.area_id = self.cpe_id.area_id
            sub = self.cpe_id.subscription_ids[:1] if 'subscription_ids' in self.cpe_id._fields else False
            if not sub:
                sub = self.env['isp.subscription'].search([
                    ('cpe_id', '=', self.cpe_id.id),
                ], limit=1)
            if sub:
                self.subscription_id = sub

    @api.onchange('area_id')
    def _onchange_area_id(self):
        if self.partner_id and self.partner_id.area_id and self.area_id:
            if self.partner_id.area_id != self.area_id:
                self.partner_id = False
                self.subscription_id = False
                self.cpe_id = False
                self.invoice_id = False

    def _auto_post_threshold(self):
        try:
            return float(self.env['ir.config_parameter'].sudo().get_param(
                'dkt_isp_billing.ocr_auto_post_threshold', '0.85',
            ) or 0.85)
        except (TypeError, ValueError):
            return 0.85

    def _auto_post_enabled(self):
        return False

    def _amount_tolerance(self, invoice):
        residual = invoice.amount_residual or 0.0
        return max(100.0, residual * 0.01)

    def _default_transfer_journal(self):
        ICP = self.env['ir.config_parameter'].sudo()
        raw = ICP.get_param('dkt_isp_billing.transfer_journal_id', '')
        if raw and str(raw).isdigit():
            journal = self.env['account.journal'].browse(int(raw))
            if journal.exists() and journal.type == 'bank':
                return journal
        return self.env['account.journal'].search([
            ('type', '=', 'bank'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)

    def _default_cash_journal(self):
        ICP = self.env['ir.config_parameter'].sudo()
        raw = ICP.get_param('dkt_isp_billing.cash_journal_id', '')
        if raw and str(raw).isdigit():
            journal = self.env['account.journal'].browse(int(raw))
            if journal.exists() and journal.type == 'cash':
                return journal
        return self.env['account.journal'].search([
            ('type', '=', 'cash'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)

    @api.model
    def _company_dest_config(self):
        ICP = self.env['ir.config_parameter'].sudo()
        return {
            'bank': (ICP.get_param('dkt_isp_billing.dest_bank_name', '') or '').strip(),
            'account': normalize_account_number(
                ICP.get_param('dkt_isp_billing.dest_account_number', '') or '',
            ),
            'name': (ICP.get_param('dkt_isp_billing.dest_account_name', '') or '').strip(),
        }

    def _check_can_verify(self):
        if not self.env.user._isp_can_verify_payment():
            raise UserError(
                'Hanya admin pusat yang boleh verifikasi atau menolak bukti. '
                'Admin desa/kolektor hanya mengajukan antrian.'
            )

    def action_run_ocr(self):
        for rec in self:
            rec.with_context(isp_skip_ocr=True)._run_ocr_and_match()
        return True

    def action_revalidate_dest(self):
        for rec in self:
            dest_match, dest_note = rec._eval_dest_match({
                'dest_account': rec.dest_account_ocr,
                'dest_name': rec.dest_name_ocr,
                'dest_bank': rec.dest_bank_ocr,
            })
            rec.write({
                'dest_match': dest_match,
                'dest_match_note': dest_note,
            })
            if dest_match == 'mismatch' and rec.state not in ('paid', 'verified'):
                rec.write({
                    'state': 'review',
                    'needs_review': True,
                    'review_reason': dest_note or 'Rekening tujuan tidak cocok.',
                })
        return True

    def action_use_chatter_attachment(self):
        self.ensure_one()
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
        ], order='id desc')
        attachments = attachments.filtered(
            lambda a: a.datas and (a.mimetype or '').startswith(('image/', 'application/pdf', 'text/'))
        )
        if not attachments:
            raise UserError('Tidak ada lampiran gambar/PDF di chatter.')
        att = attachments[0]
        self.with_context(isp_skip_ocr=True).write({
            'image': att.datas,
            'image_filename': att.name,
        })
        self.with_context(isp_skip_ocr=True)._run_ocr_and_match()
        return True

    def _run_ocr_and_match(self):
        for rec in self:
            rec._process_one()

    def _process_one(self):
        self.ensure_one()
        if self.proof_type == 'cash':
            return
        if not self.image:
            self.write({
                'state': 'draft' if self.state not in ('submitted', 'paid', 'verified') else self.state,
                'needs_review': False,
                'ocr_note': 'Belum ada bukti. Unggah gambar/PDF atau lampiran chatter.',
            })
            return
        data = base64.b64decode(self.image)
        mimetype = ''
        attachment = self.env['ir.attachment'].search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
            ('res_field', '=', 'image'),
        ], limit=1)
        if attachment:
            mimetype = attachment.mimetype or ''
        text, engine = extract_text_from_bytes(data, mimetype, self.image_filename)
        parsed = parse_ocr_text(text)
        dest_match, dest_note = self._eval_dest_match(parsed)
        note = []
        if engine == 'none':
            note.append(
                'Mesin OCR tidak tersedia di server (bukan Tesseract/AI). '
                'Isi jumlah dan invoice secara manual, lalu ajukan ke admin pusat.'
            )
        elif not parsed.get('amount'):
            note.append('Teks terbaca tetapi nominal tidak ditemukan. Periksa manual.')
        else:
            note.append('Nominal diekstrak otomatis. Bukan lunas — admin pusat yang memutuskan.')
        if dest_match == 'mismatch':
            note.append(dest_note or 'Rekening tujuan tidak cocok. Jangan bayar.')
        elif dest_match == 'unknown':
            note.append(dest_note or 'Rekening tujuan belum tervalidasi.')
        else:
            note.append('Rekening tujuan cocok dengan rekening perusahaan.')

        ref_value = parsed.get('ref') or False
        duplicate = self._find_duplicate_ref(ref_value)
        if duplicate:
            note.append('No.ref sama dengan bukti %s. Bukti dobel, masuk review.' % duplicate.name)
            ref_value = False

        vals = {
            'ocr_engine': engine,
            'ocr_raw': (text or '')[:8000],
            'ocr_note': ' '.join(note),
            'amount_ocr': parsed.get('amount') or 0.0,
            'date_ocr': parsed.get('date') or False,
            'ref_ocr': ref_value,
            'status_ocr': parsed.get('status') or False,
            'sender_name_ocr': parsed.get('sender_name') or False,
            'sender_bank_ocr': parsed.get('sender_bank') or False,
            'sender_account_ocr': parsed.get('sender_account') or False,
            'dest_name_ocr': parsed.get('dest_name') or False,
            'dest_bank_ocr': parsed.get('dest_bank') or False,
            'dest_account_ocr': parsed.get('dest_account') or False,
            'dest_match': dest_match,
            'dest_match_note': dest_note,
            'confidence': parsed.get('confidence') or 0.0,
        }
        if self.state not in ('submitted', 'paid', 'verified', 'rejected'):
            vals['state'] = 'draft'
        self.write(vals)
        self._match_and_maybe_pay(duplicate=duplicate)

    @api.model
    def _eval_dest_match(self, parsed):
        """Rekening penerima harus nomor terdaftar (Waspada / Settings)."""
        cfg = self._company_dest_config()
        dest_acc = normalize_account_number(parsed.get('dest_account') or '')
        dest_name = parsed.get('dest_name') or ''
        if not cfg['account'] and not cfg['name']:
            return 'unknown', 'Rekening tujuan perusahaan belum dikonfigurasi di Pengaturan ISP.'
        if dest_acc and cfg['account'] and not accounts_match(dest_acc, cfg['account']):
            return 'mismatch', 'Rekening tujuan struk tidak sama dengan rekening perusahaan. Jangan bayar.'
        if dest_acc and cfg['account'] and accounts_match(dest_acc, cfg['account']):
            note = 'Rekening tujuan cocok dengan rekening perusahaan.'
            if cfg['name'] and dest_name and not names_similar(dest_name, cfg['name'], 0.45):
                note = (
                    'Nomor rekening tujuan cocok. Nama penerima OCR kurang jelas; '
                    'periksa screenshot sebelum verifikasi.'
                )
            return 'ok', note
        if not dest_acc:
            return 'unknown', 'Nomor rekening tujuan tidak terbaca. Perlu review sebelum bayar.'
        return 'unknown', 'Rekening tujuan belum bisa dipastikan. Perlu review.'

    def _find_duplicate_ref(self, ref_value, only_paid=False):
        ref = (ref_value or '').strip()
        if not ref:
            return self.env['isp.payment.proof']
        domain = [
            ('ref_ocr', '=', ref),
            ('id', '!=', self.id if self.id else 0),
            ('company_id', '=', self.company_id.id),
        ]
        if only_paid:
            domain.append(('state', 'in', ('paid', 'verified')))
        return self.search(domain, limit=1)

    @api.model
    def _suggest_partner_from_sender_name(self, name, area_id=False):
        """Cocokkan nama pemilik rekening pengirim ke pelanggan. Kosong jika ambigu."""
        if not name:
            return self.env['res.partner']
        domain = [('customer_rank', '>', 0)]
        if area_id:
            domain.append(('area_id', '=', area_id))
        tokens = [tok for tok in str(name).split() if len(tok) > 2][:3]
        if tokens:
            domain = domain + [('name', 'ilike', tokens[0])]
        partners = self.env['res.partner'].search(domain, limit=80)
        if not partners:
            partners = self.env['res.partner'].search([
                ('customer_rank', '>', 0),
            ] + ([('area_id', '=', area_id)] if area_id else []), limit=80)
        scored = []
        for partner in partners:
            score = name_similarity_score(name, partner.name)
            if score >= 0.72:
                scored.append((score, partner))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return self.env['res.partner']
        if len(scored) == 1 or scored[0][0] >= 0.9:
            if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08:
                return scored[0][1]
        return self.env['res.partner']

    def _suggest_partner_from_sender(self):
        self.ensure_one()
        return self._suggest_partner_from_sender_name(
            self.sender_name_ocr,
            self.area_id.id if self.area_id else False,
        )

    def _outstanding_invoices(self):
        self.ensure_one()
        domain = [
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial', 'in_payment')),
            ('amount_residual', '>', 0),
        ]
        if self.partner_id:
            domain.append(('partner_id', '=', self.partner_id.id))
        elif self.area_id:
            domain.append(('partner_id.area_id', '=', self.area_id.id))
        elif self.cpe_id:
            domain.append(('partner_id', '=', self.cpe_id.partner_id.id))
        invoices = self.env['account.move'].search(domain, order='invoice_date desc', limit=80)
        if self.subscription_id:
            preferred = invoices.filtered(lambda inv: inv.subscription_id == self.subscription_id)
            if preferred:
                invoices = preferred | (invoices - preferred)
        return invoices

    def _invoice_for_verify(self):
        """Invoice posted + residual > 0. Satu outstanding → dipasang otomatis."""
        self.ensure_one()
        invoice = self.invoice_id
        if invoice:
            if invoice.state != 'posted':
                raise UserError(
                    'Invoice %s masih %s, bukan posted. Post invoice itu dulu, '
                    'lalu verifikasi lagi.' % (invoice.name, invoice.state)
                )
            if invoice.payment_state == 'paid' or invoice.amount_residual <= 0:
                raise UserError(
                    'Invoice %s sudah lunas, bukan outstanding. '
                    'Pilih invoice yang masih ada sisanya.' % invoice.name
                )
            return invoice

        outstanding = self._outstanding_invoices()
        if len(outstanding) == 1:
            self.invoice_id = outstanding[0]
            return outstanding[0]
        if len(outstanding) > 1:
            raise UserError(
                'Ada %s invoice outstanding untuk %s. '
                'Pilih salah satu di field Invoice, lalu verifikasi.' % (
                    len(outstanding),
                    self.partner_id.display_name,
                )
            )
        raise UserError(self._missing_outstanding_invoice_reason())

    def _missing_outstanding_invoice_reason(self):
        """Pesan jika pelanggan belum punya invoice posted yang masih outstanding."""
        self.ensure_one()
        partner = self.partner_id
        name = partner.display_name if partner else 'pelanggan ini'
        parts = [
            'Pelanggan %s belum punya invoice posted yang masih outstanding '
            '(sisa > 0). Konfirmasi langganan saja tidak cukup — buat tagihan, '
            'post invoice-nya, lalu pilih di field Invoice pada bukti ini.' % name,
        ]
        if partner:
            drafts = self.env['account.move'].search([
                ('partner_id', '=', partner.id),
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'draft'),
            ], limit=5)
            if drafts:
                parts.append(
                    'Ada invoice draft: %s. Post dulu, baru verifikasi.' % (
                        ', '.join(drafts.mapped('name')),
                    )
                )
            subs = partner.subscription_ids.filtered(
                lambda s: s.state in ('open', 'isolated')
            )
            if subs:
                sub = subs[0]
                pkg = sub.package_id
                if sub.billing_review_needed or (pkg and pkg.is_default):
                    parts.append(
                        'Langganan %s masih paket %s (harga Rp %s) dan perlu '
                        'review paket/harga, jadi tagihan otomatis tidak dibuat.' % (
                            sub.name,
                            pkg.display_name if pkg else '-',
                            '{:,.0f}'.format(sub.amount or 0).replace(',', '.'),
                        )
                    )
                elif (sub.final_amount or sub.amount) <= 0:
                    parts.append(
                        'Langganan %s berharga 0 jadi invoice tidak dibuat.' % sub.name
                    )
        return ' '.join(parts)

    def _match_and_maybe_pay(self, duplicate=False):
        """Cocokkan invoice. Tidak pernah auto-post / auto-lunas."""
        self.ensure_one()
        if not self.partner_id and self.sender_name_ocr:
            partner = self._suggest_partner_from_sender()
            if partner:
                self.partner_id = partner.id
                if partner.area_id and not self.area_id:
                    self.area_id = partner.area_id.id

        invoices = self._outstanding_invoices()
        amount = self.amount_manual or self.amount_ocr
        matches = self.env['account.move']
        if amount and invoices:
            matches = invoices.filtered(
                lambda inv: abs(inv.amount_residual - amount) <= self._amount_tolerance(inv)
            )
        if self.invoice_id and self.invoice_id in invoices and not matches:
            if amount and abs(self.invoice_id.amount_residual - amount) <= self._amount_tolerance(self.invoice_id):
                matches = self.invoice_id
        reason = False
        needs_review = True
        underpayment = False
        invoice = self.invoice_id
        confidence = self.confidence or 0.0
        queued = self.state in ('submitted', 'paid', 'verified', 'rejected')

        if self.dest_match == 'mismatch':
            reason = self.dest_match_note or 'Rekening tujuan tidak cocok. Jangan bayar.'
        elif self.status_ocr == 'failed':
            reason = 'Status struk bukan transaksi berhasil.'
        elif duplicate:
            reason = 'No.ref sudah ada pada bukti lain. Cegah pembayaran dobel.'
        elif not amount:
            reason = 'Nominal OCR kosong / gagal dibaca.'
        elif not invoices and not self.partner_id:
            reason = 'Pelanggan belum terpilih. Pilih pelanggan/CPE/area, lalu ajukan ke pusat.'
        elif not invoices:
            reason = 'Tidak ada invoice outstanding untuk pelanggan ini.'
        elif len(matches) == 1:
            invoice = matches[0]
            if not self.partner_id:
                self.partner_id = invoice.partner_id.id
            confidence = min(0.95, confidence + 0.15)
            reason = 'Nominal cocok dengan satu invoice. Admin pusat wajib cek screenshot sebelum lunas.'
            needs_review = self.dest_match != 'ok' or confidence < 0.85
            if needs_review and self.dest_match == 'ok':
                reason = 'Invoice cocok. Keyakinan OCR belum tinggi — pusat review manual.'
            elif self.dest_match != 'ok':
                reason = 'Invoice cocok tetapi rekening tujuan belum pasti. Perlu review pusat.'
        elif len(matches) > 1:
            reason = 'Beberapa invoice residual sama. Pilih invoice lalu ajukan.'
            invoice = matches[0]
        else:
            under = self.env['account.move']
            if amount and invoices:
                under = invoices.filtered(
                    lambda inv: amount + self._amount_tolerance(inv) < inv.amount_residual
                )
            if len(invoices) == 1 and amount and amount + self._amount_tolerance(invoices[0]) < invoices[0].amount_residual:
                invoice = invoices[0]
                underpayment = True
                reason = (
                    'Pembayaran KURANG dari sisa tagihan (bukti Rp %s, sisa Rp %s). '
                    'Invoice tetap dipasangkan. Pusat boleh verifikasi sebagai pelunasan sebagian. '
                    'Isolir tidak dibuka sampai lunas.'
                ) % (
                    '{:,.0f}'.format(amount).replace(',', '.'),
                    '{:,.0f}'.format(invoice.amount_residual).replace(',', '.'),
                )
                needs_review = True
            elif len(under) == 1:
                invoice = under[0]
                underpayment = True
                reason = (
                    'Pembayaran KURANG dari sisa tagihan (bukti Rp %s, sisa Rp %s). '
                    'Invoice tetap dipasangkan. Pusat boleh verifikasi sebagai pelunasan sebagian. '
                    'Isolir tidak dibuka sampai lunas.'
                ) % (
                    '{:,.0f}'.format(amount).replace(',', '.'),
                    '{:,.0f}'.format(invoice.amount_residual).replace(',', '.'),
                )
                needs_review = True
            elif amount and invoices and any(
                amount > inv.amount_residual + self._amount_tolerance(inv) for inv in invoices
            ):
                reason = (
                    'Nominal bukti LEBIH besar dari sisa tagihan. Masuk review; '
                    'pusat pilih invoice atau pecah pembayaran.'
                )
                invoice = invoices[0]
            else:
                reason = 'Nominal tidak cocok dengan sisa invoice. Masuk daftar review.'

        vals = {
            'invoice_id': invoice.id if invoice else False,
            'is_underpayment': underpayment,
            'subscription_id': (
                invoice.subscription_id.id if invoice and invoice.subscription_id
                else self.subscription_id.id
            ),
            'area_id': (
                self.area_id.id
                or (self.partner_id.area_id.id if self.partner_id and self.partner_id.area_id else False)
            ),
            'confidence': confidence,
            'needs_review': needs_review,
            'review_reason': reason,
        }
        if not queued:
            vals['state'] = 'review' if needs_review or self.dest_match == 'mismatch' else 'draft'
        elif self.state == 'submitted' and (needs_review or self.dest_match == 'mismatch'):
            vals['state'] = 'review'
        self.write(vals)

    def _can_auto_post(self):
        """Auto-post OCR selalu mati. Hanya admin pusat yang mem-post."""
        return False

    def _dest_safe_for_autopay(self):
        """Nomor rekening tujuan wajib terdaftar. Nama penerima dicek jika terbaca."""
        self.ensure_one()
        if self.dest_match != 'ok':
            return False, self.dest_match_note or 'Rekening tujuan belum pasti. Jangan bayar.'
        cfg = self._company_dest_config()
        dest_acc = normalize_account_number(self.dest_account_ocr or '')
        if not dest_acc or not cfg['account'] or not accounts_match(dest_acc, cfg['account']):
            return False, 'Nomor rekening tujuan tidak cocok dengan rekening terdaftar.'
        dest_name = self.dest_name_ocr or ''
        if dest_name and cfg['name'] and not names_similar(dest_name, cfg['name'], 0.45):
            return False, (
                'Nama penerima bukan rekening terdaftar '
                '(Waspada Sinulingga / Pengaturan ISP).'
            )
        return True, ''

    def _ocr_period_ok(self, invoice=None):
        """Tanggal transfer OCR + bulan tagihan invoice harus wajar."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        if not self.date_ocr:
            return False, 'Tanggal transfer tidak terbaca dari struk.'
        if self.date_ocr > today + timedelta(days=1):
            return False, 'Tanggal transfer di masa depan. Perlu review.'
        if self.date_ocr < today - timedelta(days=45):
            return False, 'Tanggal transfer terlalu lama. Perlu review.'
        invoice = invoice or self.invoice_id
        if not invoice or not invoice.invoice_date:
            return False, 'Invoice atau periode tagihan belum jelas.'
        same_month = (
            invoice.invoice_date.year == self.date_ocr.year
            and invoice.invoice_date.month == self.date_ocr.month
        )
        if same_month:
            return True, ''
        if self.date_ocr >= invoice.invoice_date:
            delta = (self.date_ocr - invoice.invoice_date).days
            if delta <= 45:
                return True, ''
        if invoice.invoice_date >= self.date_ocr:
            delta = (invoice.invoice_date - self.date_ocr).days
            if delta <= 10:
                return True, ''
        return False, 'Bulan tagihan tidak cocok dengan tanggal transfer struk.'

    def _group_autopay_blockers(self):
        """Alasan menolak lunas otomatis setelah YA di grup billing."""
        self.ensure_one()
        blockers = []
        dest_ok, dest_why = self._dest_safe_for_autopay()
        if not dest_ok:
            blockers.append(dest_why)
        if self.status_ocr != 'success':
            blockers.append('Status struk bukan transaksi berhasil.')
        if self.state in ('paid', 'verified', 'rejected'):
            blockers.append('Bukti sudah selesai.')
        paid_dup = self._find_duplicate_ref(self.ref_ocr, only_paid=True)
        if paid_dup:
            blockers.append('No.ref sudah dipakai pembayaran %s.' % paid_dup.name)
        if not self.partner_id:
            blockers.append('Pelanggan tidak unik dari nama pengirim rekening.')
        amount = self.amount_manual or self.amount_ocr
        if not amount:
            blockers.append('Nominal tidak terbaca.')
        if self.is_underpayment:
            blockers.append('Nominal kurang dari tagihan. Perlu review.')
        invoice = self.invoice_id
        if not invoice:
            blockers.append('Invoice outstanding tidak ketemu atau tidak cocok.')
        elif amount and abs(invoice.amount_residual - amount) > self._amount_tolerance(invoice):
            blockers.append('Nominal tidak sama dengan sisa tagihan.')
        if invoice:
            period_ok, period_why = self._ocr_period_ok(invoice)
            if not period_ok:
                blockers.append(period_why)
        return blockers

    def _clear_wa_confirm(self):
        self.filtered('wa_confirm_phone').write({
            'wa_confirm_phone': False,
            'wa_confirm_deadline': False,
        })

    def _start_wa_confirm(self, phone):
        self.ensure_one()
        phone = (phone or '').strip()
        if not phone:
            return False
        others = self.search([
            ('wa_confirm_phone', '=', phone),
            ('id', '!=', self.id),
            ('state', 'in', ('draft', 'review', 'submitted')),
        ])
        others._clear_wa_confirm()
        self.write({
            'wa_confirm_phone': phone,
            'wa_confirm_deadline': fields.Datetime.now() + timedelta(minutes=30),
            'state': 'review',
            'needs_review': True,
            'review_reason': 'Menunggu konfirmasi YA di grup Billing-dkt.',
        })
        return True

    @api.model
    def _find_pending_wa_confirm(self, phone):
        phone = (phone or '').strip()
        if not phone:
            return self.browse()
        now = fields.Datetime.now()
        expired = self.search([
            ('wa_confirm_phone', '=', phone),
            ('wa_confirm_deadline', '!=', False),
            ('wa_confirm_deadline', '<', now),
            ('state', 'in', ('draft', 'review', 'submitted')),
        ])
        if expired:
            expired.write({
                'wa_confirm_phone': False,
                'wa_confirm_deadline': False,
                'review_reason': 'Konfirmasi YA kedaluwarsa. Kirim ulang foto struk.',
            })
        return self.search([
            ('wa_confirm_phone', '=', phone),
            ('wa_confirm_deadline', '>', now),
            ('state', 'in', ('draft', 'review', 'submitted')),
            ('source', '=', 'whatsapp_group'),
        ], limit=1, order='id desc')

    def _try_post_from_group_confirm(self, actor_phone):
        """Setelah YA: post lunas jika dest, tanggal, dan periode lolos. Bukan action_verify."""
        self.ensure_one()
        blockers = self._group_autopay_blockers()
        if blockers:
            self.write({
                'state': 'review',
                'needs_review': True,
                'review_reason': 'Konfirmasi YA tapi tidak auto-lunas: %s' % '; '.join(blockers),
            })
            self._clear_wa_confirm()
            return False, blockers[0]
        self.sudo()._create_transfer_payment(post=True)
        self.sudo().write({
            'verified_at': fields.Datetime.now(),
        })
        self.sudo().message_post(
            body='Dikonfirmasi YA di grup Billing-dkt oleh %s. Dipost otomatis.' % (
                actor_phone or '-',
            ),
        )
        self._clear_wa_confirm()
        try:
            self.sudo()._notify_paid_group_whatsapp()
        except Exception as exc:
            _logger.warning(
                'WA grup lunas dilewati setelah konfirmasi proof=%s: %s',
                self.name, exc,
            )
        return True, ''

    def action_mark_manual(self):
        self.filtered(lambda r: r.state not in ('paid', 'verified')).write({
            'state': 'review',
            'needs_review': True,
            'review_reason': 'Diisi manual, menunggu verifikasi admin pusat.',
        })
        return True

    def action_submit(self):
        """Desa/kolektor/pelanggan mengajukan antrian. Tidak mem-post payment."""
        return self._action_submit_queue()

    def _action_submit_queue(self, source=None):
        for rec in self:
            if rec.state in ('paid', 'verified'):
                continue
            if rec.proof_type == 'transfer' and not rec.image:
                raise UserError('Unggah bukti transfer terlebih dahulu.')
            if rec.proof_type == 'cash' and not rec.invoice_id:
                raise UserError('Pilih invoice untuk pengajuan kas.')
            if rec.proof_type == 'cash' and not (rec.amount_manual or rec.amount_ocr):
                raise UserError('Isi jumlah kas yang diterima.')
            state = 'submitted'
            if rec.proof_type == 'transfer' and (
                rec.dest_match == 'mismatch'
                or rec.status_ocr == 'failed'
                or rec.needs_review
            ):
                state = 'review'
            submit_uid = False if self.env.user._is_public() else self.env.user.id
            rec.write({
                'state': state,
                'submitted_by': submit_uid,
                'submitted_at': fields.Datetime.now(),
                'source': source or rec.source or self.env.user._isp_submitter_source(),
                'reject_reason': False,
            })
            rec.message_post(body='Diajukan ke antrian verifikasi admin pusat. Belum lunas.')
        return True

    def action_hq_post_without_wa(self):
        """Admin pusat: lunaskan bukti kas/transfer tanpa kirim WA.

        Untuk rekapan lapangan/Excel. Jangan pakai action_verify: itu memaksa
        ucapan terima kasih (force_dry_run=False).
        """
        self._check_can_verify()
        for rec in self:
            if rec.state in ('paid', 'verified') and rec.payment_id:
                continue
            if rec.state == 'rejected':
                raise UserError('Bukti ditolak. Ajukan ulang sebelum posting.')
            if rec.proof_type == 'transfer' and rec.dest_match == 'mismatch':
                raise UserError(
                    'Rekening tujuan tidak cocok dengan rekening perusahaan. Tidak boleh bayar.'
                )
            amount = rec.amount_manual or rec.amount_ocr
            if not rec.partner_id:
                raise UserError('Pilih pelanggan terlebih dahulu.')
            rec._invoice_for_verify()
            if not amount:
                raise UserError('Isi jumlah OCR atau jumlah manual.')
            if rec.proof_type == 'cash':
                rec._create_cash_payment()
            else:
                rec._create_transfer_payment(post=True)
            rec.write({
                'verified_by': self.env.user.id,
                'verified_at': fields.Datetime.now(),
            })
        return True

    def action_verify(self):
        """Admin pusat: cek bukti lalu post account.payment."""
        self._check_can_verify()
        for rec in self:
            if rec.state in ('paid', 'verified') and rec.payment_id:
                continue
            if rec.state == 'rejected':
                raise UserError('Bukti ditolak. Ajukan ulang sebelum verifikasi.')
            if rec.proof_type == 'transfer' and rec.dest_match == 'mismatch':
                raise UserError(
                    'Rekening tujuan tidak cocok dengan rekening perusahaan. Tidak boleh bayar.'
                )
            if rec.proof_type == 'transfer' and rec.status_ocr == 'failed':
                raise UserError('Status struk gagal. Tidak boleh bayar.')
            paid_dup = rec._find_duplicate_ref(rec.ref_ocr, only_paid=True)
            if paid_dup:
                raise UserError(
                    'No.ref sudah dipakai pembayaran %s. Bukti dobel ditolak.' % paid_dup.name
                )
            amount = rec.amount_manual or rec.amount_ocr
            if not rec.partner_id:
                raise UserError('Pilih pelanggan terlebih dahulu.')
            rec._invoice_for_verify()
            if not amount:
                raise UserError('Isi jumlah OCR atau jumlah manual.')
            if rec.proof_type == 'cash':
                rec._create_cash_payment()
            else:
                rec._create_transfer_payment(post=True)
            rec.write({
                'verified_by': self.env.user.id,
                'verified_at': fields.Datetime.now(),
            })
            if rec.source == 'whatsapp_group':
                rec._notify_paid_group_whatsapp()
            else:
                rec._notify_thanks_whatsapp()
        return True

    def _notify_thanks_whatsapp(self):
        """Ucapan terima kasih setelah verifikasi. Gagal WA tidak membatalkan lunas."""
        self.ensure_one()
        if not self.partner_id:
            return False
        invoice = self.invoice_id
        remain = invoice.amount_residual if invoice else 0.0
        partial = self.is_underpayment or (
            invoice and invoice.payment_state == 'partial'
        ) or (invoice and remain and not invoice.currency_id.is_zero(remain))
        body_override = None
        if partial:
            paid = self.amount_manual or self.amount_ocr or 0.0
            fmt = self.env['isp.whatsapp.message']._format_idr
            body_override = (
                'Halo {name},\n\n'
                'Pembayaran sebagian sebesar Rp %s sudah kami terima dan diverifikasi. '
                'Sisa tagihan {package} periode {period}: Rp %s. '
                'Isolir tidak dibuka sampai pelunasan penuh.\n\n'
                'DOTAKARO'
            ) % (fmt(paid), fmt(remain))
        WA = self.env['isp.whatsapp.message']
        sent_any = False
        for phone in self._thanks_target_phones():
            try:
                result = WA.queue_and_send(
                    self.partner_id,
                    template_kind='thanks',
                    invoice=self.invoice_id,
                    body_override=body_override,
                    phone_override=phone,
                    force_dry_run=False,
                )
                sent_any = sent_any or bool(result.get('sent') or result.get('dry_run'))
            except Exception as exc:
                _logger.warning(
                    'WA terima kasih dilewati proof=%s phone=%s: %s',
                    self.name, phone, exc,
                )
        return sent_any

    def _thanks_target_phones(self):
        """Nomor pelanggan, plus pengirim WA jika berbeda (kirim dari HP lain)."""
        self.ensure_one()
        WA = self.env['isp.whatsapp.message']
        phones = []
        partner_phone = WA.partner_phone(self.partner_id) if self.partner_id else False
        if partner_phone:
            phones.append(partner_phone)
        sender = WA.normalize_phone(self.public_phone)
        if sender and sender not in phones and self.source in ('whatsapp', 'whatsapp_group'):
            phones.append(sender)
        return phones

    def _notify_paid_group_whatsapp(self):
        """Kabari grup Billing-dkt setelah verifikasi lunas. Bukan blast."""
        self.ensure_one()
        if self.source != 'whatsapp_group':
            return False
        group_id = (
            self.env['ir.config_parameter'].sudo().get_param(
                'dkt_isp_billing.wa_billing_group_id', '',
            ) or ''
        ).strip()
        if not group_id:
            return False
        username = (
            self.cpe_id.pppoe_username
            if self.cpe_id and self.cpe_id.pppoe_username
            else '-'
        )
        invoice = self.invoice_id
        remain = invoice.amount_residual if invoice else 0.0
        lunas = invoice and invoice.currency_id.is_zero(remain)
        status = 'Lunas' if lunas else 'Pembayaran tercatat, sisa Rp %s' % (
            '{:,.0f}'.format(remain).replace(',', '.'),
        )
        body = (
            '%s.\n'
            'Bukti %s\n'
            'Pelanggan: %s\n'
            'User: %s\n'
            'Invoice %s'
        ) % (
            status,
            self.name,
            self.partner_id.name if self.partner_id else '-',
            username,
            invoice.name if invoice else '-',
        )
        try:
            return bool(self.env['isp.wa.registration'].sudo()._reply(group_id, body))
        except Exception as exc:
            _logger.warning(
                'WA grup lunas gagal proof=%s: %s', self.name, exc,
            )
            return False

    def action_confirm_payment(self):
        """Alias lama: hanya admin pusat."""
        return self.action_verify()

    def action_reject(self, reason=None):
        self._check_can_verify()
        for rec in self:
            if rec.state in ('paid', 'verified') and rec.payment_id:
                raise UserError('Pembayaran sudah dipost. Tidak bisa ditolak.')
            why = (reason or rec.reject_reason or '').strip()
            if not why:
                raise UserError('Isi alasan penolakan.')
            rec.write({
                'state': 'rejected',
                'reject_reason': why,
                'needs_review': False,
                'review_reason': why,
            })
            rec.message_post(body='Ditolak admin pusat: %s' % why)
        return True

    def action_open_reject_wizard(self):
        self._check_can_verify()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tolak bukti',
            'res_model': 'isp.payment.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_proof_ids': [(6, 0, self.ids)],
            },
        }

    def action_create_draft_payment(self):
        raise UserError(
            'Payment draft tidak dipakai. Admin pusat memverifikasi lalu payment langsung dipost.'
        )

    def action_mark_review(self):
        self._check_can_verify()
        self.filtered(lambda r: r.state not in ('paid', 'verified', 'rejected')).write({
            'state': 'review',
            'needs_review': True,
            'review_reason': self[:1].review_reason or 'Ditandai review oleh admin pusat.',
        })
        return True

    def _create_cash_payment(self):
        self.ensure_one()
        journal = self.journal_id if self.journal_id and self.journal_id.type == 'cash' else self._default_cash_journal()
        if not journal:
            raise UserError('Tidak ada jurnal Kas. Set di Pengaturan ISP.')
        self.journal_id = journal.id
        return self._create_transfer_payment(post=True)

    def _create_transfer_payment(self, post=True):
        self.ensure_one()
        if not post:
            raise UserError('Payment draft tidak dibuat. Verifikasi pusat mem-post langsung.')
        if self.env.user._isp_is_submitter_only():
            raise UserError('Admin desa/kolektor tidak boleh mem-post pembayaran.')
        invoice = self.invoice_id
        if not invoice:
            raise UserError('Invoice belum dipilih.')
        if self.proof_type == 'transfer' and self.dest_match == 'mismatch':
            raise UserError('Rekening tujuan tidak cocok. Tidak boleh membuat pembayaran.')
        if self.proof_type == 'cash':
            journal = self.journal_id if self.journal_id and self.journal_id.type == 'cash' else self._default_cash_journal()
        else:
            journal = self.journal_id if self.journal_id and self.journal_id.type == 'bank' else self._default_transfer_journal()
        if not journal:
            raise UserError('Tidak ada jurnal pembayaran. Set di Pengaturan ISP.')
        amount = self.amount_manual or self.amount_ocr or invoice.amount_residual
        memo = self.ref_ocr or ('Bukti %s' % self.name)
        payment = invoice._isp_register_inbound_payment(
            journal, amount, self.date_ocr or fields.Date.context_today(self), memo,
        )
        invoice.invalidate_recordset(['amount_residual', 'payment_state'])
        paid = invoice.payment_state in ('paid', 'in_payment') and (
            invoice.currency_id.is_zero(invoice.amount_residual)
            or payment.state in ('in_process', 'paid')
        )
        remain = invoice.amount_residual
        partial = bool(remain) and not invoice.currency_id.is_zero(remain)
        reason = False
        if partial:
            reason = (
                'Pelunasan sebagian tercatat. Sisa tagihan Rp %s. '
                'Isolir tidak dibuka sampai lunas.'
            ) % '{:,.0f}'.format(remain).replace(',', '.')
        elif not paid:
            reason = 'Pembayaran tercatat, belum berstatus lunas.'
        self.write({
            'payment_id': payment.id,
            'journal_id': journal.id,
            'state': 'paid' if paid else 'verified',
            'is_underpayment': partial or self.is_underpayment,
            'needs_review': not paid or partial,
            'review_reason': reason,
        })
        self.message_post(body='Pembayaran %s dipost setelah verifikasi admin pusat.' % payment.display_name)
        return payment

    @api.model
    def _check_upload_rate_limit(self, ip, token=None):
        since = fields.Datetime.now() - timedelta(minutes=PUBLIC_RATE_MINUTES)
        domain_ip = [('create_date', '>=', since)]
        if ip:
            domain_ip.append(('upload_ip', '=', ip))
            if self.search_count(domain_ip) >= PUBLIC_RATE_IP:
                raise UserError('Terlalu banyak unggahan dari jaringan ini. Coba lagi nanti.')
        if token:
            token_count = self.search_count([
                ('create_date', '>=', since),
                ('invoice_id.isp_upload_token', '=', token),
            ])
            if token_count >= PUBLIC_RATE_TOKEN:
                raise UserError('Terlalu banyak unggahan untuk tagihan ini. Coba lagi nanti.')
        return True

    @api.model
    def _validate_upload_payload(self, raw_bytes, filename, max_bytes=None):
        if not raw_bytes:
            raise UserError('Pilih file bukti transfer.')
        limit = max_bytes if max_bytes is not None else PUBLIC_MAX_BYTES
        if len(raw_bytes) > limit:
            raise UserError('Ukuran file maksimal %s MB.' % int(limit / (1024 * 1024)))
        name = (filename or '').lower()
        allowed = ('.png', '.jpg', '.jpeg', '.webp', '.pdf', '.txt')
        if name and not name.endswith(allowed):
            raise UserError('Format diizinkan: PNG, JPG, WEBP, PDF.')
        return True

    @api.model
    def create_from_customer_upload(self, vals, source='portal', ip=None, token=None):
        """Portal / tautan publik: masuk antrian submitted, bukan lunas."""
        self._check_upload_rate_limit(ip, token)
        vals = dict(vals)
        vals.setdefault('source', source)
        vals.setdefault('proof_type', 'transfer')
        if ip:
            vals['upload_ip'] = ip
        rec = self.sudo().create(vals)
        rec.sudo()._action_submit_queue(source=source)
        return rec

    @api.model
    def _recent_open_proof(self, partner, hours=24):
        """Antrian bukti yang sama dalam 24 jam. Cegah dobel rekap grup."""
        if not partner:
            return self.browse()
        since = fields.Datetime.now() - timedelta(hours=hours)
        return self.search([
            ('partner_id', '=', partner.id),
            ('state', 'in', ('draft', 'submitted', 'review')),
            ('create_date', '>=', since),
        ], limit=1, order='id desc')

    @api.model
    def create_from_whatsapp(self, vals, match_sender=True):
        """Bukti dari webhook Fonnte: antrian submitted/review, tidak auto-lunas.

        match_sender=False untuk grup billing: pelanggan dari caption BAYAR,
        bukan dari HP pelapor.
        """
        vals = dict(vals or {})
        reporter_user_id = vals.pop('reporter_user_id', False)
        vals['source'] = 'whatsapp' if match_sender else 'whatsapp_group'
        vals.setdefault('proof_type', 'transfer')
        caption = (vals.pop('caption', False) or '').strip()
        sender = (vals.get('public_phone') or '').strip()
        WA = self.env['isp.whatsapp.message']
        bayar_token = WA.parse_bayar_caption(caption)
        if match_sender:
            partner = WA.find_partner_by_phone(sender)
            match = 'phone' if partner else 'none'
        else:
            partner = self.env['res.partner']
            match = 'none'
            if not bayar_token:
                return self.browse()
        if not partner and not bayar_token:
            return self.browse()
        if not partner and bayar_token:
            partner, how = WA.find_partner_by_bayar_token(bayar_token)
            if partner:
                match = how or 'format'
        vals['wa_match'] = match
        if partner:
            vals.setdefault('partner_id', partner.id)
            if partner.area_id and not vals.get('area_id'):
                vals['area_id'] = partner.area_id.id
            if partner.subscription_ids and not vals.get('subscription_id'):
                vals['subscription_id'] = partner.subscription_ids[0].id
            if not vals.get('cpe_id'):
                cpe = partner.cpe_ids[:1] if hasattr(partner, 'cpe_ids') else False
                if not cpe and bayar_token:
                    cpe = self.env['isp.cpe'].sudo().search([
                        ('pppoe_username', '=ilike', bayar_token),
                        ('partner_id', '=', partner.id),
                    ], limit=1)
                if cpe:
                    vals['cpe_id'] = cpe.id
        rec = self.sudo().create(vals)
        if rec.image:
            rec.sudo()._action_submit_queue(source=vals['source'])
        else:
            rec.sudo().write({
                'state': 'review',
                'needs_review': True,
                'source': vals['source'],
                'review_reason': rec.inbound_note or 'Lampiran WhatsApp gagal diunduh.',
            })
        rec.sudo().write({
            'submitted_by': reporter_user_id or False,
            'wa_match': match,
        })
        if not rec.partner_id:
            hint = (
                ' Nomor WA belum terdaftar: %s. Kirim foto + teks BAYAR username '
                'atau BAYAR 08…, atau admin pusat pasangkan pelanggan.'
            ) % (sender or '-')
            if bayar_token:
                hint = (
                    ' Nomor WA belum terdaftar dan BAYAR %s tidak ketemu. '
                    'Gambar disimpan, admin pusat pasangkan pelanggan.'
                ) % bayar_token
            rec.sudo().write({
                'state': 'review',
                'needs_review': True,
                'wa_match': 'none',
                'review_reason': hint.strip(),
            })
        elif match == 'format':
            rec.sudo().write({
                'inbound_note': (
                    '%s | dicocokkan dari BAYAR %s'
                    % ((rec.inbound_note or '').strip(), bayar_token)
                )[:500],
            })
        return rec

    @api.model
    def create_from_group_receipt(self, vals):
        """Struk grup tanpa caption BAYAR. OCR dulu; partner dari nama pengirim."""
        vals = dict(vals or {})
        reporter_user_id = vals.pop('reporter_user_id', False)
        vals['source'] = 'whatsapp_group'
        vals.setdefault('proof_type', 'transfer')
        vals.setdefault('wa_match', 'name' if vals.get('partner_id') else 'none')
        rec = self.sudo().create(vals)
        if reporter_user_id:
            rec.sudo().write({'submitted_by': reporter_user_id})
        if rec.partner_id and rec.wa_match == 'none':
            rec.sudo().write({'wa_match': 'name'})
        return rec
