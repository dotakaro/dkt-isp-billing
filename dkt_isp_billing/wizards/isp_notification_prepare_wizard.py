import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ISPNotificationPrepareWizard(models.TransientModel):
    _name = 'isp.notification.prepare.wizard'
    _description = 'Siapkan pemberitahuan (tanpa kirim)'

    area_id = fields.Many2one('isp.area', string='Area / Desa')
    only_late = fields.Boolean('Hanya yang telat / nunggak', default=True)
    add_auto_tag = fields.Boolean(
        'Tag notif_otomatis (nanti otomatis)',
        default=False,
        help='Hanya menandai kategori partner. Tidak mengirim WA/SMS/email.',
    )
    add_queue_tag = fields.Boolean(
        'Masukkan antrian notif_siap',
        default=True,
        help='Antrian pemberitahuan bulan ini. Tidak dikirim.',
    )
    line_ids = fields.One2many(
        'isp.notification.prepare.wizard.line', 'wizard_id', string='Pelanggan',
    )
    selected_count = fields.Integer(compute='_compute_selected_count')
    tagged_count = fields.Integer('Ditandai', readonly=True)
    log = fields.Text('Hasil', readonly=True)
    template_kind = fields.Selection(
        [
            ('invoice', 'Tagihan'),
            ('due', 'Jatuh tempo'),
            ('due_soon', 'H-1 telat (tgl 20)'),
            ('late', 'Telat'),
            ('isolir_warn', 'Peringatan isolir'),
            ('isolated', 'Sudah isolir'),
            ('thanks', 'Terima kasih bayar'),
            ('restored', 'Buka isolir'),
            ('custom', 'Blast khusus (teks bebas)'),
        ],
        string='Template WA',
        default='invoice',
        required=True,
    )
    custom_body = fields.Text(
        'Teks blast khusus',
        help=(
            'Untuk gangguan, ganti rekening, pengumuman, dll. '
            'Boleh pakai {name} {package} {period} {area} dan variabel template lain.'
        ),
    )
    preview_body = fields.Text('Contoh teks', compute='_compute_preview_body')
    wa_dry_run = fields.Boolean(
        'Dry-run (jangan kirim nyata)',
        default=True,
        help='Jika dicentang atau token kosong, hanya log. Tidak mengirim ke pelanggan.',
    )
    wa_has_credentials = fields.Boolean(compute='_compute_wa_status')
    wa_provider = fields.Selection(
        [('fonnte', 'Fonnte.id'), ('meta', 'Meta Cloud API')],
        string='Provider',
        compute='_compute_wa_status',
    )
    wa_mode_note = fields.Char(compute='_compute_wa_status')
    sent_count = fields.Integer('Terkirim / dry-run', readonly=True)
    failed_count = fields.Integer('Gagal WA', readonly=True)
    note = fields.Text(
        'Kebijakan',
        readonly=True,
        default=(
            'Tidak ada blast otomatis ke semua pelanggan. Siapkan tag/antrian dulu, '
            'lalu kirim hanya ke yang dipilih atau tag notif_siap. '
            'Template bisa diedit di Konfigurasi → Template WhatsApp. '
            'Blast khusus (gangguan, ganti rekening) pilih "Blast khusus" lalu tulis teks. '
            'Provider mengikuti Pengaturan ISP (Fonnte atau Meta). '
            'Meta production butuh nama template Approved di Settings. '
            'Tanpa token, tombol kirim tetap dry-run.'
        ),
    )

    @api.depends('line_ids.selected')
    def _compute_selected_count(self):
        for wiz in self:
            wiz.selected_count = len(wiz.line_ids.filtered('selected'))

    @api.depends('template_kind', 'custom_body', 'line_ids.partner_id')
    def _compute_preview_body(self):
        WA = self.env['isp.whatsapp.message']
        for wiz in self:
            partner = wiz.line_ids[:1].partner_id
            if not partner:
                partner = self.env['res.partner']
            invoice = WA._partner_open_invoice(partner) if partner else WA.env['account.move']
            override = wiz.custom_body if wiz.template_kind == 'custom' else None
            if wiz.template_kind == 'custom' and not (override or '').strip():
                wiz.preview_body = 'Tulis teks blast khusus di atas. Contoh: Gangguan di {name} area...'
                continue
            wiz.preview_body = WA.render_template(
                wiz.template_kind,
                partner,
                invoice,
                partner.subscription_ids[:1] if partner else None,
                body=override,
            )

    @api.depends()
    def _compute_wa_status(self):
        WA = self.env['isp.whatsapp.message']
        cfg = WA.get_wa_config()
        has_cred = WA.has_wa_credentials(cfg)
        provider = cfg.get('provider') or 'fonnte'
        for wiz in self:
            wiz.wa_has_credentials = has_cred
            wiz.wa_provider = provider
            if cfg['dry_run'] or not has_cred:
                wiz.wa_mode_note = (
                    'Provider %s. Mode dry-run: pesan hanya masuk log, tidak dikirim ke pelanggan.'
                    % (provider,)
                )
            else:
                wiz.wa_mode_note = (
                    'Provider %s. Mode live: token tersimpan. Hanya kirim jika Anda menekan Kirim WhatsApp.'
                    % (provider,)
                )

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        partners = self._partners_from_context()
        if partners:
            vals['only_late'] = False
            vals['line_ids'] = [(0, 0, {
                'partner_id': partner.id,
                'area_id': partner.area_id.id,
                'phone': partner.phone_wa or partner.phone,
                'selected': True,
            }) for partner in partners]
            vals['log'] = 'Dari seleksi: %s. Belum dikirim.' % len(partners)
        return vals

    def action_load(self):
        self.ensure_one()
        partners = self._find_partners()
        self.line_ids.unlink()
        self.write({
            'line_ids': [(0, 0, {
                'partner_id': partner.id,
                'area_id': partner.area_id.id,
                'phone': partner.phone_wa or partner.phone,
                'selected': True,
            }) for partner in partners],
            'tagged_count': 0,
            'log': 'Kandidat pemberitahuan: %s. Belum ada yang dikirim.' % len(partners),
        })
        return self._reopen()

    @api.model
    def _partners_from_context(self):
        ctx_model = self.env.context.get('active_model')
        ctx_ids = self.env.context.get('active_ids') or []
        if ctx_model == 'isp.subscription' and ctx_ids:
            subs = self.env['isp.subscription'].browse(ctx_ids).filtered(
                lambda s: not s.is_special_treatment
            )
            return subs.mapped('partner_id')
        if ctx_model == 'res.partner' and ctx_ids:
            return self.env['res.partner'].browse(ctx_ids).filtered(
                lambda p: p.customer_rank > 0
            )
        return self.env['res.partner']

    def _find_partners(self):
        self.ensure_one()
        from_ctx = self._partners_from_context()
        if from_ctx:
            return from_ctx
        domain = [
            ('state', 'in', ['open', 'isolated']),
            ('is_special_treatment', '=', False),
        ]
        if self.area_id:
            domain.append(('area_id', '=', self.area_id.id))
        if self.only_late:
            domain.extend(['|', ('is_late', '=', True), ('overdue_marked', '=', True)])
        subs = self.env['isp.subscription'].search(domain)
        return subs.mapped('partner_id')

    def action_prepare(self):
        """Tandai tag/antrian. Tidak memanggil API WA/SMS/email."""
        self.ensure_one()
        if not self.add_auto_tag and not self.add_queue_tag:
            raise UserError('Pilih minimal satu: antrian notif_siap atau tag notif_otomatis.')
        selected = self.line_ids.filtered('selected')
        if not selected:
            raise UserError('Tidak ada pelanggan yang dipilih.')
        tags = self.env['res.partner.category']
        if self.add_queue_tag:
            tags |= self.env.ref('dkt_isp_billing.partner_category_notif_siap')
        if self.add_auto_tag:
            tags |= self.env.ref('dkt_isp_billing.partner_category_notif_otomatis')
        partners = selected.mapped('partner_id')
        partners.write({'category_id': [(4, tag.id) for tag in tags]})
        now = fields.Datetime.now()
        partners.write({
            'isp_notif_prepared': True,
            'isp_notif_prepared_date': now,
        })
        _logger.info(
            'Pemberitahuan disiapkan (tanpa kirim) untuk %s partner. tag=%s',
            len(partners), tags.mapped('name'),
        )
        self.write({
            'tagged_count': len(partners),
            'log': (
                'Ditandai %s pelanggan. Tag: %s. Tidak ada pesan yang dikirim.'
            ) % (len(partners), ', '.join(tags.mapped('name'))),
        })
        return self._reopen()

    def action_load_queue(self):
        """Muat partner bertag notif_siap. Tidak mengirim."""
        self.ensure_one()
        tag = self.env.ref('dkt_isp_billing.partner_category_notif_siap')
        partners = self.env['res.partner'].search([
            ('category_id', 'in', [tag.id]),
            ('customer_rank', '>', 0),
        ])
        self.line_ids.unlink()
        self.write({
            'line_ids': [(0, 0, {
                'partner_id': partner.id,
                'area_id': partner.area_id.id,
                'phone': partner.phone_wa or partner.phone,
                'selected': True,
            }) for partner in partners],
            'tagged_count': 0,
            'sent_count': 0,
            'failed_count': 0,
            'log': 'Antrian notif_siap: %s. Belum dikirim.' % len(partners),
        })
        return self._reopen()

    def action_send_whatsapp(self):
        """Kirim ke baris terpilih. Tanpa token / dry-run: hanya log."""
        self.ensure_one()
        selected = self.line_ids.filtered('selected')
        if not selected:
            raise UserError('Tidak ada pelanggan yang dipilih. Muat kandidat atau antrian notif_siap dulu.')
        partners = selected.mapped('partner_id')
        force_dry = True if self.wa_dry_run else None
        if self.template_kind == 'custom' and not (self.custom_body or '').strip():
            raise UserError('Isi teks blast khusus terlebih dahulu.')
        result = self.env['isp.whatsapp.message'].queue_and_send(
            partners,
            template_kind=self.template_kind,
            force_dry_run=force_dry,
            body_override=self.custom_body if self.template_kind == 'custom' else None,
        )
        self.write({
            'sent_count': result['sent'] + result['dry_run'],
            'failed_count': result['failed'],
            'log': (
                'WhatsApp %s: log=%s terkirim=%s dry-run=%s gagal=%s. '
                'Tidak ada blast ke semua pelanggan.'
            ) % (
                result['mode'],
                len(result['created']),
                result['sent'],
                result['dry_run'],
                result['failed'],
            ),
        })
        _logger.info(
            'Pemberitahuan WA wizard: mode=%s sent=%s dry=%s failed=%s',
            result['mode'], result['sent'], result['dry_run'], result['failed'],
        )
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }


class ISPNotificationPrepareWizardLine(models.TransientModel):
    _name = 'isp.notification.prepare.wizard.line'
    _description = 'Baris siapkan pemberitahuan'

    wizard_id = fields.Many2one(
        'isp.notification.prepare.wizard', required=True, ondelete='cascade',
    )
    selected = fields.Boolean('Pilih', default=True)
    partner_id = fields.Many2one('res.partner', string='Pelanggan', required=True)
    area_id = fields.Many2one('isp.area', string='Area')
    phone = fields.Char('Telepon')
