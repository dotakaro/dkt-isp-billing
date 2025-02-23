from odoo import models, fields, api

class AccountMove(models.Model):
    _inherit = 'account.move'

    subscription_id = fields.Many2one('isp.subscription', string='Subscription', tracking=True,
                                    domain="[('customer_id', '=', partner_id)]")

    @api.onchange('partner_id')
    def _onchange_partner_subscription(self):
        """Reset subscription when partner changes"""
        if self.partner_id != self.subscription_id.customer_id:
            self.subscription_id = False 