from odoo import api, fields, models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    isp_wa_provider = fields.Selection(
        [('fonnte', 'Fonnte.id'), ('meta', 'Meta Cloud API')],
        string='Provider WhatsApp',
        config_parameter='dkt_isp_billing.wa_provider',
        default='fonnte',
    )
    isp_wa_dry_run = fields.Boolean(
        string='Dry-run WhatsApp (jangan kirim nyata)',
        config_parameter='dkt_isp_billing.wa_dry_run',
        default=True,
    )
    isp_wa_fonnte_token = fields.Char(string='Token Fonnte')
    isp_wa_fonnte_token_set = fields.Boolean(compute='_compute_isp_wa_token_set')
    isp_wa_meta_token = fields.Char(string='Token Meta')
    isp_wa_meta_token_set = fields.Boolean(compute='_compute_isp_wa_token_set')
    isp_wa_meta_phone_number_id = fields.Char(
        string='Phone Number ID (Meta)',
        config_parameter='dkt_isp_billing.wa_meta_phone_number_id',
    )
    isp_wa_meta_api_version = fields.Char(
        string='Versi Graph API',
        config_parameter='dkt_isp_billing.wa_meta_api_version',
        default='v21.0',
    )
    isp_wa_meta_template_lang = fields.Char(
        string='Bahasa template Meta',
        config_parameter='dkt_isp_billing.wa_meta_template_lang',
        default='id',
        help='Kode bahasa template Approved, biasanya id.',
    )
    isp_wa_meta_template_invoice = fields.Char(
        string='Nama template Meta: tagihan',
        config_parameter='dkt_isp_billing.wa_meta_template_invoice',
        help='Nama persis di WhatsApp Manager. Variabel: {{1}} nama {{2}} paket {{3}} periode {{4}} nominal {{5}} jatuh tempo.',
    )
    isp_wa_meta_template_due = fields.Char(
        string='Nama template Meta: jatuh tempo',
        config_parameter='dkt_isp_billing.wa_meta_template_due',
        help='Nama persis template Approved untuk pengingat jatuh tempo.',
    )
    isp_wa_meta_template_late = fields.Char(
        string='Nama template Meta: telat',
        config_parameter='dkt_isp_billing.wa_meta_template_late',
        help='Nama persis template Approved untuk tagihan telat.',
    )
    isp_wa_rate_delay = fields.Float(string='Jeda antar pesan (detik)', default=0.5)
    isp_cash_journal_id = fields.Many2one(
        'account.journal', string='Jurnal Kas',
        domain="[('type', '=', 'cash')]",
    )
    isp_transfer_journal_id = fields.Many2one(
        'account.journal', string='Jurnal Transfer / Bank',
        domain="[('type', '=', 'bank')]",
    )
    isp_ocr_auto_post_threshold = fields.Float(
        string='Ambang auto-post OCR',
        default=0.85,
        help='Hanya dipakai jika auto-post diaktifkan. Default: wajib konfirmasi admin.',
    )
    isp_ocr_auto_post_enabled = fields.Boolean(
        string='Izinkan auto-post OCR (dinonaktifkan)',
        config_parameter='dkt_isp_billing.ocr_auto_post_enabled',
        default=False,
        help='Selalu mati. Hanya admin pusat yang mem-post pembayaran setelah cek bukti.',
    )
    isp_wa_inbound_enabled = fields.Boolean(
        string='Terima bukti transfer dari WhatsApp',
        config_parameter='dkt_isp_billing.wa_inbound_enabled',
        default=True,
        help='Webhook Fonnte membuat antrian isp.payment.proof. Bukan lunas otomatis.',
    )
    isp_wa_register_enabled = fields.Boolean(
        string='Bot pendaftaran WhatsApp',
        config_parameter='dkt_isp_billing.wa_register_enabled',
        default=False,
        help='Default mati. /daftar di chat 1:1, /pasang dan eviden hanya di grup aktivasi.',
    )
    isp_wa_register_mode = fields.Selection(
        [
            ('queue', 'Antrian + admin pusat'),
            ('tech_auto', 'Teknisi boleh langsung create'),
        ],
        string='Mode create user',
        config_parameter='dkt_isp_billing.wa_register_mode',
        default='queue',
        help='Pelanggan /daftar selalu antri. Teknisi /pasang create langsung hanya jika mode ini.',
    )
    isp_wa_register_group_id = fields.Char(
        string='ID grup aktivasi Fonnte',
        config_parameter='dkt_isp_billing.wa_register_group_id',
        help='Nilai sender webhook saat pesan dari grup. Grup lain tetap diabaikan.',
    )
    isp_wa_billing_group_id = fields.Char(
        string='ID grup Billing-dkt',
        config_parameter='dkt_isp_billing.wa_billing_group_id',
        help='Semua anggota grup ini boleh BAYAR dan /status. Bukan lunas otomatis.',
    )
    isp_wa_evidence_required = fields.Boolean(
        string='Wajib eviden + cabut 24 jam',
        config_parameter='dkt_isp_billing.wa_evidence_required',
        default=False,
        help='Setelah connect pertama, teknisi wajib KTP/rumah/lokasi. Default mati. Tidak berlaku mundur.',
    )
    isp_wa_evidence_hours = fields.Integer(
        string='Batas eviden (jam)',
        config_parameter='dkt_isp_billing.wa_evidence_hours',
        default=24,
    )
    isp_wa_register_note = fields.Text(
        string='Catatan bot pendaftaran',
        readonly=True,
        default=(
            'Chat 1:1: /daftar (antrian, belum ada secret). '
            'Grup aktivasi: semua anggota boleh /pasang, /eviden, foto KTP/RUMAH, pin lokasi. '
            'BAYAR pelanggan tetap 1:1. Staf laporkan bayar di grup Billing-dkt. '
            'Cabut 24 jam = matikan secret, bukan isolir nunggak. '
            'Pelanggan lama tidak kena. Invoice tidak dibuat otomatis.'
        ),
    )
    isp_wa_public_base_url = fields.Char(
        string='URL publik (opsional)',
        config_parameter='dkt_isp_billing.wa_public_base_url',
        help='Contoh https://billing.contoh.co.id jika web.base.url masih localhost.',
    )
    isp_wa_webhook_url = fields.Char(
        string='URL webhook Fonnte',
        compute='_compute_isp_wa_webhook',
    )
    isp_wa_webhook_is_public = fields.Boolean(compute='_compute_isp_wa_webhook')
    isp_wa_inbound_note = fields.Text(
        string='Cara unggah bukti WhatsApp',
        compute='_compute_isp_wa_webhook',
    )
    isp_dest_bank_name = fields.Char(
        string='Bank rekening tujuan',
        config_parameter='dkt_isp_billing.dest_bank_name',
        default='BANK BRI',
    )
    isp_dest_account_number = fields.Char(
        string='No. rekening tujuan',
        config_parameter='dkt_isp_billing.dest_account_number',
        default='014401000343565',
        help='Tanpa spasi. Dipakai memvalidasi struk transfer (contoh BRI 014401000343565).',
    )
    isp_dest_account_name = fields.Char(
        string='Nama rekening tujuan',
        config_parameter='dkt_isp_billing.dest_account_name',
        default='WASPADA SINULINGGA',
    )
    isp_auto_isolate_enabled = fields.Boolean(
        string='Aktifkan isolir otomatis (Radius + fintech)',
        config_parameter='dkt_isp_billing.auto_isolate_enabled',
        default=False,
        help=(
            'Jangan nyalakan sebelum RADIUS dan pembayaran fintech siap. '
            'Saat ini isolir = disable secret di router CPE, hanya lewat Isolir massal.'
        ),
    )
    isp_auto_isolate_backend = fields.Selection(
        [
            ('mikrotik_secret', 'MikroTik secret (sekarang)'),
            ('radius', 'RADIUS (nanti)'),
        ],
        string='Backend isolir',
        config_parameter='dkt_isp_billing.auto_isolate_backend',
        default='mikrotik_secret',
        help='Cron isolir otomatis hanya boleh jalan jika backend = RADIUS. Secret MikroTik tetap manual.',
    )
    isp_auto_isolate_trigger = fields.Selection(
        [
            ('late_day', 'Setelah telat tanggal 21'),
            ('invoice_overdue', 'Setelah invoice overdue'),
        ],
        string='Pemicu isolir otomatis',
        config_parameter='dkt_isp_billing.auto_isolate_trigger',
        default='late_day',
        help='Rencana masa depan. Tidak dipakai selama isolir otomatis OFF.',
    )
    isp_radius_enabled = fields.Boolean(
        string='Sinkron ke FreeRADIUS',
        config_parameter='dkt_isp_billing.radius_enabled',
        default=False,
        help='Tulis user ke DB radius. Isolir otomatis tetap OFF.',
    )
    isp_radius_db_host = fields.Char(
        string='Host DB RADIUS',
        config_parameter='dkt_isp_billing.radius_db_host',
        default='dkt-isp-db',
    )
    isp_radius_db_name = fields.Char(
        string='Nama DB RADIUS',
        config_parameter='dkt_isp_billing.radius_db_name',
        default='radius',
    )
    isp_radius_db_user = fields.Char(
        string='User DB RADIUS',
        config_parameter='dkt_isp_billing.radius_db_user',
        default='radius',
    )
    isp_radius_db_password = fields.Char(string='Password DB RADIUS')
    isp_radius_landing_url = fields.Char(
        string='URL halaman isolir',
        config_parameter='dkt_isp_billing.radius_landing_url',
        default='https://billing.dotakaro.com/isp/isolir',
    )
    isp_radius_note = fields.Text(
        string='Catatan RADIUS',
        readonly=True,
        default=(
            'FreeRADIUS di stack terpisah (/opt/dkt-radius). Restart Odoo tidak mematikannya. '
            'User + paket (Mikrotik-Group) dual-write ke RADIUS; secret lokal tetap cadangan. '
            'User tidak ada di RADIUS / timeout FreeRADIUS → MikroTik pakai secret lokal. '
            'Isolir = ganti profile isolir + landing, bukan Reject. Isolir otomatis tetap OFF.'
        ),
    )
    isp_auto_isolate_note = fields.Text(
        string='Peringatan isolir otomatis',
        readonly=True,
        default=(
            'Jangan nyalakan sebelum RADIUS dan pembayaran fintech (QRIS/VA) siap. '
            'Sekarang isolir hanya wizard Isolir massal (disable secret di router CPE). '
            'Buka isolir otomatis setelah lunas bulan berjalan sudah ada. '
            'Pembayaran fintech nanti memakai jalur on-paid yang sama.'
        ),
    )
    isp_wa_meta_note = fields.Text(
        string='Catatan Meta',
        readonly=True,
        default=(
            'Meta production wajib template yang sudah Approved di WhatsApp Manager. '
            'Isi nama template di bawah (huruf kecil/underscore, persis seperti di Meta). '
            'Urutan variabel body: {{1}} nama, {{2}} paket, {{3}} periode, {{4}} nominal, {{5}} jatuh tempo. '
            'Tanpa nama template, Meta mengirim teks bebas (hanya jendela 24 jam). '
            'Fonnte memakai teks template modul (tidak perlu approve Meta). '
            'Token hanya di Settings (password), jangan di XML/git. '
            'Tanpa token atau dry-run aktif, tombol kirim hanya menulis log.'
        ),
    )

    @api.depends()
    def _compute_isp_wa_token_set(self):
        ICP = self.env['ir.config_parameter'].sudo()
        fonnte = bool(ICP.get_param('dkt_isp_billing.wa_fonnte_token'))
        meta = bool(ICP.get_param('dkt_isp_billing.wa_meta_token'))
        for rec in self:
            rec.isp_wa_fonnte_token_set = fonnte
            rec.isp_wa_meta_token_set = meta

    @api.depends('isp_wa_public_base_url')
    def _compute_isp_wa_webhook(self):
        WA = self.env['isp.whatsapp.message']
        for rec in self:
            info = WA.get_webhook_info(public_base=rec.isp_wa_public_base_url)
            if info['is_public']:
                reach = (
                    'URL ini terlihat publik. Isi persis di dashboard Fonnte: Device → Edit → Webhook. '
                    'Aktifkan Autoread. Fonnte hanya POST (update Januari 2026).'
                )
            else:
                reach = (
                    'localhost:8196 tidak bisa diterima Fonnte dari internet. '
                    'Pasang domain/tunnel HTTPS publik, lalu isi URL publik di atas '
                    'atau ubah web.base.url. Endpoint sudah aktif di Odoo; inbound live '
                    'setelah URL publik diisi di dashboard Fonnte.'
                )
            rec.isp_wa_webhook_url = info['url']
            rec.isp_wa_webhook_is_public = info['is_public']
            rec.isp_wa_inbound_note = (
                'Nomor WhatsApp device dipakai campur, bukan kotak khusus tagihan. '
                'Odoo HANYA menelan gambar jika caption memakai format:\n'
                'BAYAR username   atau   BAYAR 08xxxxxxxxxx\n'
                'Foto chat / struk dana lain / tanpa format diabaikan diam-diam '
                '(tidak diunduh, tidak dibalas, tidak masuk antrian).\n\n'
                'Setelah admin pusat verifikasi & post, ucapan terima kasih dikirim ke '
                'nomor pelanggan. Jika bukti dikirim dari HP lain, pengirim juga dapat salinan.\n\n'
                'URL webhook:\n%s\n\n%s'
            ) % (info['url'], reach)

    def action_test_fonnte_send(self):
        """Kirim 1 pesan tes ke nomor tes saja."""
        result = self.env['isp.whatsapp.message'].test_send()
        if result.get('ok'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Tes WhatsApp',
                    'message': 'Terkirim ke nomor tes. ID: %s' % (result.get('external_id') or '-'),
                    'type': 'success',
                    'sticky': False,
                },
            }
        raise UserError(result.get('error') or 'Tes WhatsApp gagal.')

    def get_values(self):
        res = super().get_values()
        ICP = self.env['ir.config_parameter'].sudo()
        res['isp_wa_fonnte_token'] = False
        res['isp_wa_meta_token'] = False
        cash = ICP.get_param('dkt_isp_billing.cash_journal_id', '')
        transfer = ICP.get_param('dkt_isp_billing.transfer_journal_id', '')
        res['isp_cash_journal_id'] = int(cash) if cash and str(cash).isdigit() else False
        res['isp_transfer_journal_id'] = int(transfer) if transfer and str(transfer).isdigit() else False
        try:
            res['isp_wa_rate_delay'] = float(ICP.get_param('dkt_isp_billing.wa_rate_delay', '0.5') or 0.5)
        except (TypeError, ValueError):
            res['isp_wa_rate_delay'] = 0.5
        try:
            res['isp_ocr_auto_post_threshold'] = float(
                ICP.get_param('dkt_isp_billing.ocr_auto_post_threshold', '0.85') or 0.85
            )
        except (TypeError, ValueError):
            res['isp_ocr_auto_post_threshold'] = 0.85
        res['isp_ocr_auto_post_enabled'] = ICP.get_param(
            'dkt_isp_billing.ocr_auto_post_enabled', 'False',
        ) in ('True', 'true', '1')
        res['isp_dest_bank_name'] = ICP.get_param(
            'dkt_isp_billing.dest_bank_name', 'BANK BRI',
        ) or 'BANK BRI'
        res['isp_dest_account_number'] = ICP.get_param(
            'dkt_isp_billing.dest_account_number', '014401000343565',
        ) or '014401000343565'
        res['isp_dest_account_name'] = ICP.get_param(
            'dkt_isp_billing.dest_account_name', 'WASPADA SINULINGGA',
        ) or 'WASPADA SINULINGGA'
        res['isp_radius_db_password'] = False
        return res

    def set_values(self):
        super().set_values()
        ICP = self.env['ir.config_parameter'].sudo()
        if self.isp_wa_fonnte_token:
            ICP.set_param('dkt_isp_billing.wa_fonnte_token', self.isp_wa_fonnte_token)
        if self.isp_wa_meta_token:
            ICP.set_param('dkt_isp_billing.wa_meta_token', self.isp_wa_meta_token)
        ICP.set_param('dkt_isp_billing.cash_journal_id', self.isp_cash_journal_id.id or '')
        ICP.set_param('dkt_isp_billing.transfer_journal_id', self.isp_transfer_journal_id.id or '')
        ICP.set_param('dkt_isp_billing.wa_rate_delay', str(self.isp_wa_rate_delay or 0.5))
        ICP.set_param(
            'dkt_isp_billing.ocr_auto_post_threshold',
            str(self.isp_ocr_auto_post_threshold or 0.85),
        )
        ICP.set_param('dkt_isp_billing.ocr_auto_post_enabled', 'False')
        ICP.set_param('dkt_isp_billing.dest_bank_name', self.isp_dest_bank_name or '')
        ICP.set_param(
            'dkt_isp_billing.dest_account_number',
            (self.isp_dest_account_number or '').replace(' ', ''),
        )
        ICP.set_param('dkt_isp_billing.dest_account_name', self.isp_dest_account_name or '')
        if self.isp_radius_db_password:
            ICP.set_param('dkt_isp_billing.radius_db_password', self.isp_radius_db_password)
