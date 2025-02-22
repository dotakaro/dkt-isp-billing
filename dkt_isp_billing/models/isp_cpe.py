from odoo import models, fields, api
from odoo.exceptions import ValidationError
import random
import string

class ISPCPE(models.Model):
    _name = 'isp.cpe'
    _description = 'CPE'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama Perangkat', required=True, tracking=True)
    mac_address = fields.Char('MAC Address', required=True, tracking=True)
    customer_id = fields.Many2one('isp.customer', string='Pelanggan', tracking=True)
    ip_address = fields.Char('IP Address', tracking=True)
    outdoor_unit = fields.Char('Outdoor Unit')
    router = fields.Char('Router')
    pppoe_username = fields.Char('PPPoE Username', tracking=True, copy=False)
    pppoe_password = fields.Char('PPPoE Password', tracking=True, copy=False)
    ownership = fields.Selection([
        ('customer', 'Milik Pelanggan'),
        ('company', 'Milik Perusahaan')
    ], string='Kepemilikan', default='company')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Aktif'),
        ('inactive', 'Tidak Aktif'),
        ('broken', 'Rusak')
    ], string='Status', default='draft', tracking=True)
    active = fields.Boolean('Active', default=True, tracking=True)
    history_ids = fields.One2many('isp.device.history', 'cpe_id', string='Riwayat')
    
    _sql_constraints = [
        ('mac_address_uniq', 'unique(mac_address)', 'MAC Address harus unik!'),
        ('pppoe_username_uniq', 'unique(pppoe_username)', 'PPPoE Username harus unik!')
    ]

    @api.model
    def _generate_random_password(self, length=8):
        """Generate random password with specified length."""
        characters = string.ascii_letters + string.digits
        return ''.join(random.choice(characters) for i in range(length))

    @api.model
    def _generate_pppoe_username(self, customer_id):
        """Generate PPPoE username based on customer ID."""
        if not customer_id:
            return False
        # Format: PPP-{customer_id}
        return f"PPP-{customer_id}"

    @api.onchange('customer_id')
    def _onchange_customer_id(self):
        if self.customer_id and not self.pppoe_username:
            self.pppoe_username = self._generate_pppoe_username(self.customer_id.customer_id)
            self.pppoe_password = self._generate_random_password()

    def action_generate_pppoe_credentials(self):
        """Generate new PPPoE credentials."""
        for record in self:
            if not record.customer_id:
                raise ValidationError('Pelanggan harus diisi terlebih dahulu!')
            record.pppoe_username = self._generate_pppoe_username(record.customer_id.customer_id)
            record.pppoe_password = self._generate_random_password()
        return True 