from odoo import models, fields, api
from odoo.exceptions import ValidationError

class ISPInstallationFee(models.Model):
    _name = 'isp.installation.fee'
    _description = 'Installation Fee'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc'

    name = fields.Char('Nomor', readonly=True, copy=False)
    customer_id = fields.Many2one('isp.customer', string='Pelanggan', required=True, tracking=True)
    installation_type_id = fields.Many2one('isp.installation.type', string='Jenis Instalasi', required=True, tracking=True)
    date = fields.Date('Tanggal', default=fields.Date.today, required=True, tracking=True)
    amount = fields.Float('Jumlah', required=True, tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('paid', 'Lunas'),
        ('cancelled', 'Dibatalkan')
    ], string='Status', default='draft', tracking=True)
    
    invoice_id = fields.Many2one('account.move', string='Invoice', tracking=True)
    technician_id = fields.Many2one('res.users', string='Teknisi', tracking=True)
    notes = fields.Text('Catatan', tracking=True)

    @api.depends('installation_type_id')
    def _compute_amount(self):
        for record in self:
            record.amount = record.installation_type_id.price if record.installation_type_id else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].next_by_code('isp.installation.fee.sequence')
        return super().create(vals_list)

    def action_confirm(self):
        for record in self:
            if record.state == 'draft':
                record.state = 'confirmed'
                # Buat invoice
                record._create_invoice()

    def _create_invoice(self):
        self.ensure_one()
        invoice_vals = {
            'move_type': 'out_invoice',
            'partner_id': self.customer_id.partner_id.id,
            'invoice_date': self.date,
            'invoice_line_ids': [(0, 0, {
                'name': f'Biaya Instalasi - {self.installation_type_id.name}',
                'quantity': 1,
                'price_unit': self.amount,
            })],
        }
        invoice = self.env['account.move'].create(invoice_vals)
        self.invoice_id = invoice.id 