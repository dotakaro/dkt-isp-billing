from odoo import models, fields, api
from odoo.exceptions import ValidationError

class ISPAdoptSecretWizard(models.TransientModel):
    _name = 'isp.adopt.secret.wizard'
    _description = 'Wizard Adopsi Secret'

    customer_id = fields.Many2one('isp.customer', string='Pelanggan', required=True)
    secret_name = fields.Char('PPPoE Username', readonly=True)
    secret_id = fields.Char('Secret ID', readonly=True)
    confirm = fields.Boolean('Konfirmasi', help='Konfirmasi untuk mengadopsi secret ini')
    
    def action_confirm(self):
        """Konfirmasi adopsi secret"""
        self.ensure_one()
        if not self.confirm:
            raise ValidationError('Silakan centang konfirmasi terlebih dahulu!')
        return self.customer_id.action_adopt_secret()
        
    def action_cancel(self):
        """Batalkan adopsi secret"""
        return {'type': 'ir.actions.act_window_close'} 