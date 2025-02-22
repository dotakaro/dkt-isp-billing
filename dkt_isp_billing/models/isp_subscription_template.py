from odoo import models, fields, api

class ISPSubscriptionTemplate(models.Model):
    _name = 'isp.subscription.template'
    _description = 'ISP Subscription Template'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama Template', required=True, tracking=True)
    package_id = fields.Many2one('isp.package', string='Paket', required=True, tracking=True)
    recurring_interval = fields.Integer('Interval Berulang', default=1, required=True, tracking=True)
    recurring_rule_type = fields.Selection([
        ('daily', 'Harian'),
        ('weekly', 'Mingguan'),
        ('monthly', 'Bulanan'),
        ('yearly', 'Tahunan')
    ], string='Tipe Berulang', default='monthly', required=True, tracking=True)
    description = fields.Text('Deskripsi', tracking=True)
    active = fields.Boolean('Active', default=True, tracking=True) 