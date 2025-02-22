from odoo import models, fields, api

class ISPDeviceHistory(models.Model):
    _name = 'isp.device.history'
    _description = 'Device Replacement History'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc'

    name = fields.Char('Referensi', readonly=True)
    date = fields.Date('Tanggal', required=True, default=fields.Date.context_today, tracking=True)
    cpe_id = fields.Many2one('isp.cpe', string='Perangkat', required=True, tracking=True)
    customer_id = fields.Many2one(related='cpe_id.customer_id', store=True)
    device_name = fields.Char('Nama Alat', required=True, tracking=True)
    replacement_reason = fields.Text('Alasan Pergantian', required=True, tracking=True)
    ownership = fields.Selection([
        ('owned', 'Milik Sendiri'),
        ('borrowed', 'Dipinjamkan')
    ], string='Status Kepemilikan', required=True, tracking=True)
    technician_id = fields.Many2one('res.users', string='Petugas', required=True, tracking=True)
    cost = fields.Float('Biaya', tracking=True)
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].next_by_code('isp.device.history.sequence')
        return super().create(vals_list) 