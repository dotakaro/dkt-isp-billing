from odoo import models, fields, api
from odoo.exceptions import ValidationError

class ISPDiscount(models.Model):
    _name = 'isp.discount'
    _description = 'ISP Discount'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    name = fields.Char('Nama Diskon', required=True, tracking=True)
    code = fields.Char('Kode Diskon', required=True, tracking=True)
    type = fields.Selection([
        ('percentage', 'Persentase'),
        ('fixed', 'Nominal Tetap'),
    ], string='Tipe Diskon', required=True, default='percentage', tracking=True)
    
    value = fields.Float(
        'Nilai Diskon', 
        required=True, 
        tracking=True,
        help="Untuk persentase, masukkan nilai antara 0-100. Untuk nominal tetap, masukkan nilai dalam rupiah."
    )
    
    account_id = fields.Many2one(
        'account.account', 
        string='Akun Diskon',
        required=True,
        tracking=True,
        domain="[('account_type', '=', 'expense'), ('internal_group', '=', 'expense')]",
        help="Akun yang akan digunakan untuk pencatatan diskon"
    )
    
    journal_id = fields.Many2one(
        'account.journal',
        string='Jurnal Diskon',
        required=True,
        tracking=True,
        domain="[('type', '=', 'general')]",
        default=lambda self: self.env['account.journal'].search([('type', '=', 'general')], limit=1),
        help="Jurnal yang akan digunakan untuk pencatatan diskon"
    )
    
    active = fields.Boolean('Aktif', default=True)
    description = fields.Text('Deskripsi')
    
    @api.constrains('type', 'value')
    def _check_value(self):
        for record in self:
            if record.type == 'percentage' and (record.value < 0 or record.value > 100):
                raise ValidationError('Nilai persentase diskon harus antara 0-100!')
            elif record.type == 'fixed' and record.value < 0:
                raise ValidationError('Nilai diskon tidak boleh negatif!')

    @api.model
    def create(self, vals):
        # Jika tidak ada akun yang dipilih, cari akun diskon yang sudah ada
        if not vals.get('account_id'):
            # Coba dapatkan akun diskon bawaan
            account = self.env['account.account'].search([
                '|',
                ('name', 'ilike', 'Sales Discount'),
                ('name', 'ilike', 'Discount'),
                ('account_type', '=', 'expense'),
                ('internal_group', '=', 'expense')
            ], limit=1)
            
            if account:
                vals['account_id'] = account.id
            else:
                raise ValidationError('Tidak ditemukan akun diskon yang sesuai. Silakan pilih akun secara manual.')
        
        # Jika tidak ada jurnal yang dipilih, gunakan jurnal Miscellaneous
        if not vals.get('journal_id'):
            journal = self.env['account.journal'].search([
                ('type', '=', 'general')
            ], limit=1)
            
            if journal:
                vals['journal_id'] = journal.id
            else:
                raise ValidationError('Tidak ditemukan jurnal yang sesuai. Silakan pilih jurnal secara manual.')
        
        return super().create(vals) 