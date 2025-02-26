from odoo import models, fields, api
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

class ISPInstallationFee(models.Model):
    _name = 'isp.installation.fee'
    _description = 'Installation Fee'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc'

    name = fields.Char('Nomor', readonly=True, copy=False)
    partner_id = fields.Many2one('res.partner', string='Pelanggan', required=True, tracking=True,
                                domain=[('customer_rank', '>', 0)])
    installation_type_id = fields.Many2one('isp.installation.type', string='Tipe Instalasi', required=True, tracking=True)
    date = fields.Date('Tanggal', default=fields.Date.today, required=True, tracking=True)
    amount = fields.Float('Jumlah', required=True, tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Terkonfirmasi'),
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
                # Buat invoice terlebih dahulu
                invoice = record.create_invoice()
                # Update state menjadi confirmed
                record.state = 'confirmed'
                # Log untuk debugging
                _logger.info(f'Biaya instalasi {record.name} dikonfirmasi dengan invoice {invoice.name if invoice else "tidak ada"}')
                
    def action_set_paid(self):
        for record in self:
            if record.state in ['draft', 'confirmed']:
                record.state = 'paid'
                _logger.info(f'Biaya instalasi {record.name} ditandai sebagai lunas')

    def create_invoice(self):
        self.ensure_one()
        # Cari journal penjualan
        sale_journal = self.env['account.journal'].search([('type', '=', 'sale')], limit=1)
        if not sale_journal:
            raise ValidationError('Tidak ditemukan jurnal penjualan. Silakan buat jurnal penjualan terlebih dahulu.')
        
        # Cari akun pendapatan
        revenue_account = self.env.ref('dkt_isp_billing.revenue_account', raise_if_not_found=False)
        if not revenue_account:
            revenue_account = self.env['account.account'].search([
                ('account_type', '=', 'income')
            ], limit=1)
            
        if not revenue_account:
            raise ValidationError('Tidak ditemukan akun pendapatan. Silakan buat akun pendapatan terlebih dahulu.')
            
        invoice_vals = {
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'invoice_date': self.date,
            'journal_id': sale_journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': f'Biaya Instalasi - {self.installation_type_id.name}',
                'quantity': 1,
                'price_unit': self.amount,
                'account_id': revenue_account.id,
            })],
        }
        
        _logger.info(f"Membuat invoice dengan nilai: {invoice_vals}")
        invoice = self.env['account.move'].create(invoice_vals)
        self.invoice_id = invoice.id
        
        # Log untuk debugging
        _logger.info(f'Invoice dibuat: {invoice.name} untuk biaya instalasi {self.name}')
        
        return invoice 