from odoo import api, fields, models


class ISPParsePhoneWizard(models.TransientModel):
    _name = 'isp.parse.phone.wizard'
    _description = 'Parse nomor dari secret PPPoE'

    overwrite_manual = fields.Boolean(
        'Timpa nomor manual yang berbeda',
        default=False,
        help='Default mati. Nomor yang sudah diisi manual dan berbeda tidak ditimpa.',
    )
    parsed_count = fields.Integer('Username ter-parse', readonly=True)
    skipped_count = fields.Integer('Username tanpa nomor', readonly=True)
    filled_count = fields.Integer('Phone diisi', readonly=True)
    updated_count = fields.Integer('Diperbarui (dari parser)', readonly=True)
    unchanged_count = fields.Integer('Sudah sama', readonly=True)
    skip_manual_count = fields.Integer('Dilewati (nomor manual)', readonly=True)
    partner_count = fields.Integer('Partner diproses', readonly=True)
    log = fields.Text('Hasil', readonly=True)
    note = fields.Text(
        'Kebijakan',
        readonly=True,
        default=(
            'Parser membaca username PPPoE (secret name), bukan comment '
            '"Nama - Area: harga". Pola: nama-08xxxxxxxxxx. '
            'Phone lokal 08…, WhatsApp 62…. Username tanpa nomor dilewati. '
            'Username yang sama di banyak CPE = satu partner. '
            'Tidak mengirim WA, tidak isolir, tidak post invoice.'
        ),
    )

    @api.model
    def _partners_from_context(self):
        ctx_model = self.env.context.get('active_model')
        ctx_ids = self.env.context.get('active_ids') or []
        if ctx_model == 'res.partner' and ctx_ids:
            return self.env['res.partner'].browse(ctx_ids)
        if ctx_model == 'isp.cpe' and ctx_ids:
            return self.env['isp.cpe'].browse(ctx_ids).mapped('partner_id')
        if ctx_model == 'isp.subscription' and ctx_ids:
            return self.env['isp.subscription'].browse(ctx_ids).mapped('partner_id')
        return self.env['res.partner']

    def action_parse(self):
        self.ensure_one()
        partners = self._partners_from_context()
        stats = self.env['res.partner']._backfill_phone_from_pppoe_username(
            partners=partners or None,
            overwrite_manual=self.overwrite_manual,
        )
        examples = stats.get('skip_examples') or []
        lines = [
            'Username berhasil di-parse: %s' % stats['parsed'],
            'Username dilewati (tanpa nomor): %s' % stats['skipped'],
            'Partner diproses: %s' % stats['partners'],
            'Phone diisi: %s' % stats['filled'],
            'Diperbarui (sumber parser): %s' % stats['updated'],
            'Sudah sama: %s' % stats['unchanged'],
            'Dilewati karena nomor manual berbeda: %s' % stats['skip_manual'],
            'Contoh gagal (tersamarkan): %s' % (', '.join(examples[:3]) or '-'),
            'Tidak mengirim WhatsApp. Tidak isolir. Tidak post invoice.',
        ]
        self.write({
            'parsed_count': stats['parsed'],
            'skipped_count': stats['skipped'],
            'filled_count': stats['filled'],
            'updated_count': stats['updated'],
            'unchanged_count': stats['unchanged'],
            'skip_manual_count': stats['skip_manual'],
            'partner_count': stats['partners'],
            'log': '\n'.join(lines),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }
