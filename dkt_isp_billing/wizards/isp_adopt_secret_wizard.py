from odoo import models, fields, api
from odoo.exceptions import ValidationError

class ISPAdoptSecretWizard(models.TransientModel):
    _name = 'isp.adopt.secret.wizard'
    _description = 'Wizard untuk mengadopsi PPPoE secret yang sudah ada'
    
    partner_id = fields.Many2one('res.partner', string='Pelanggan', required=True,
                                domain=[('customer_rank', '>', 0)])
    cpe_id = fields.Many2one('isp.cpe', string='CPE', required=True)
    pppoe_username = fields.Char('PPPoE Username', required=True)
    pppoe_password = fields.Char('PPPoE Password', required=True)
    
    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            # Filter CPE berdasarkan pelanggan
            return {'domain': {'cpe_id': [('partner_id', '=', self.partner_id.id)]}}
            
    def action_adopt_secret(self):
        self.ensure_one()
        if not self.cpe_id:
            raise ValidationError('Silakan pilih CPE terlebih dahulu!')
            
        # Update PPPoE credentials di CPE
        self.cpe_id.write({
            'pppoe_username': self.pppoe_username,
            'pppoe_password': self.pppoe_password
        })
        
        # Coba adopsi secret
        success, message = self.cpe_id.adopt_mikrotik_secret()
        if not success:
            raise ValidationError(message)
            
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': message,
                'type': 'success',
            }
        }

    def action_cancel(self):
        """Batalkan adopsi secret"""
        return {'type': 'ir.actions.act_window_close'} 