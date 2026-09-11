from odoo import api, fields, models
from odoo.exceptions import UserError

from .isp_whatsapp_message import TEMPLATE_KIND_SELECTION, WA_TEMPLATES


class _SafeFormat(dict):
    def __missing__(self, key):
        return '{%s}' % key


class IspWhatsappTemplate(models.Model):
    _name = 'isp.whatsapp.template'
    _description = 'Template pesan WhatsApp ISP'
    _order = 'sequence, id'

    name = fields.Char('Nama', required=True)
    code = fields.Selection(
        TEMPLATE_KIND_SELECTION,
        string='Kode',
        required=True,
        index=True,
    )
    sequence = fields.Integer('Urutan', default=10)
    active = fields.Boolean('Aktif', default=True)
    body = fields.Text(
        'Isi pesan',
        required=True,
        help=(
            'Variabel: {name} {package} {period} {amount} {due_date} '
            '{late_date} {username} {dest_bank} {dest_account} {dest_name}'
        ),
    )
    placeholder_help = fields.Text(compute='_compute_placeholder_help')
    preview = fields.Text('Pratinjau', compute='_compute_preview')

    _code_uniq = models.Constraint(
        'unique(code)',
        'Kode template harus unik.',
    )

    @api.depends('body')
    def _compute_placeholder_help(self):
        text = (
            '{name} nama pelanggan\n'
            '{package} paket\n'
            '{period} periode tagihan\n'
            '{amount} nominal\n'
            '{due_date} jatuh tempo (tgl 1)\n'
            '{late_date} tanggal telat (tgl 21)\n'
            '{username} username PPPoE\n'
            '{isolir_url} halaman isolir\n'
            '{dest_bank} {dest_account} {dest_name} rekening tujuan'
        )
        for rec in self:
            rec.placeholder_help = text

    @api.depends('body', 'code')
    def _compute_preview(self):
        WA = self.env['isp.whatsapp.message']
        partner = self.env['res.partner'].search([('customer_rank', '>', 0)], limit=1)
        for rec in self:
            rec.preview = rec.render_for(partner) if rec.body else ''

    def render_for(self, partner, invoice=None, subscription=None):
        self.ensure_one()
        ctx = self.env['isp.whatsapp.message'].get_template_context(
            self.code, partner, invoice, subscription,
        )
        return (self.body or '').format_map(_SafeFormat(ctx))

    @api.model
    def get_body(self, code):
        rec = self.sudo().search([('code', '=', code), ('active', '=', True)], limit=1)
        if rec:
            return rec.body
        return WA_TEMPLATES.get(code) or WA_TEMPLATES['invoice']

    def init(self):
        self.ensure_defaults()

    @api.model
    def ensure_defaults(self):
        """Buat template yang belum ada. Tidak menimpa yang sudah diedit."""
        labels = dict(TEMPLATE_KIND_SELECTION)
        sequence = 10
        for code, body in WA_TEMPLATES.items():
            existing = self.sudo().search([('code', '=', code)], limit=1)
            if existing:
                continue
            self.sudo().create({
                'name': labels.get(code) or code,
                'code': code,
                'body': body,
                'sequence': sequence,
            })
            sequence += 10
        return True

    def action_reset_default(self):
        for rec in self:
            default = WA_TEMPLATES.get(rec.code)
            if not default:
                raise UserError('Tidak ada teks bawaan untuk kode %s.' % rec.code)
            rec.body = default
        return True
