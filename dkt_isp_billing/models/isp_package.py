from odoo import models, fields, api
from odoo.exceptions import ValidationError

class ISPPackage(models.Model):
    _name = 'isp.package'
    _description = 'ISP Package'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    
    name = fields.Char('Nama Paket', required=True, tracking=True)
    code = fields.Char('Kode', compute='_compute_code', store=True, readonly=False,
                    help="Kode paket yang digunakan di Mikrotik")
    profile_id = fields.Many2one('isp.mikrotik.profile', string='PPPoE Profile',
                              required=True, tracking=True,
                              help="Profile PPPoE yang digunakan di Mikrotik")
    price = fields.Float('Harga', required=True, tracking=True)
    description = fields.Text('Deskripsi', tracking=True)
    active = fields.Boolean('Active', default=True, tracking=True)
    
    subscription_ids = fields.One2many('isp.subscription', 'package_id', string='Subscriptions')
    subscription_count = fields.Integer(compute='_compute_subscription_count')
    
    # Fields yang diambil dari profile
    bandwidth_up = fields.Integer('Bandwidth Upload (Mbps)', compute='_compute_bandwidth', store=True)
    bandwidth_down = fields.Integer('Bandwidth Download (Mbps)', compute='_compute_bandwidth', store=True)
    
    # Field untuk produk
    product_id = fields.Many2one('product.template', string='Produk', readonly=True,
                              help="Produk yang terkait dengan paket ini")
    
    _sql_constraints = [
        ('profile_uniq', 'unique(profile_id)', 'Profile PPPoE sudah digunakan di paket lain!'),
        ('code_uniq', 'unique(code)', 'Kode paket harus unik!')
    ]
    
    @api.depends('subscription_ids')
    def _compute_subscription_count(self):
        for record in self:
            record.subscription_count = len(record.subscription_ids)
    
    @api.model_create_multi
    def create(self, vals_list):
        # Buat produk untuk setiap paket
        for vals in vals_list:
            # Buat produk baru
            product_vals = {
                'name': vals.get('name', ''),
                'type': 'service',
                'list_price': vals.get('price', 0.0),
                'standard_price': 0.0,
                'default_code': vals.get('code', ''),
                'description': vals.get('description', ''),
                'detailed_type': 'service',
                'invoice_policy': 'order',
            }
            product = self.env['product.template'].create(product_vals)
            vals['product_id'] = product.id
            
        return super().create(vals_list)
    
    def write(self, vals):
        # Update produk terkait jika ada perubahan
        if any(field in vals for field in ['name', 'price', 'code', 'description']):
            for record in self:
                if record.product_id:
                    product_vals = {}
                    if 'name' in vals:
                        product_vals['name'] = vals['name']
                    if 'price' in vals:
                        product_vals['list_price'] = vals['price']
                    if 'code' in vals:
                        product_vals['default_code'] = vals['code']
                    if 'description' in vals:
                        product_vals['description'] = vals['description']
                    record.product_id.write(product_vals)
        return super().write(vals)
    
    def unlink(self):
        # Hapus produk terkait saat paket dihapus
        products = self.mapped('product_id')
        result = super().unlink()
        if products:
            products.unlink()
        return result

    @api.depends('profile_id', 'profile_id.rate_limit')
    def _compute_bandwidth(self):
        for record in self:
            if record.profile_id and record.profile_id.rate_limit:
                try:
                    # Format rate limit: "10M/20M"
                    up, down = record.profile_id.rate_limit.split('/')
                    record.bandwidth_up = int(up.strip('M'))
                    record.bandwidth_down = int(down.strip('M'))
                except:
                    record.bandwidth_up = 0
                    record.bandwidth_down = 0
            else:
                record.bandwidth_up = 0
                record.bandwidth_down = 0

    @api.depends('name')
    def _compute_code(self):
        for record in self:
            if not record.code:
                # Buat kode dari nama paket (hapus spasi dan ubah ke uppercase)
                record.code = record.name.upper().replace(' ', '_') if record.name else False

    def _prepare_mikrotik_profile_data(self):
        """Prepare data untuk membuat/update profile di Mikrotik."""
        self.ensure_one()
        return {
            'name': self.name,
            'rate-limit': f'{self.bandwidth_up}M/{self.bandwidth_down}M',
            'comment': f'Created by Odoo - {self.name}'
        }

    def action_sync_to_mikrotik(self):
        """Sinkronkan paket ke Mikrotik sebagai profile."""
        self.ensure_one()
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
            
        api = mikrotik.get_connection()
        if not api:
            raise ValidationError('Gagal terhubung ke Mikrotik!')
            
        try:
            profile_api = api.get_resource('/ppp/profile')
            profile_data = self._prepare_mikrotik_profile_data()
            
            # Cek apakah profile sudah ada
            existing_profile = profile_api.get(name=self.name)
            
            if existing_profile:
                # Update profile yang ada
                profile_api.set(id=existing_profile[0]['id'], **profile_data)
            else:
                # Buat profile baru
                result = profile_api.add(**profile_data)
                
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sukses',
                    'message': f'Profile {self.name} berhasil disinkronkan ke Mikrotik',
                    'type': 'success',
                }
            }
        except Exception as e:
            raise ValidationError(f'Gagal sinkronisasi profile ke Mikrotik: {str(e)}')
        finally:
            api.disconnect()

    def write(self, vals):
        """Override write untuk update profile di Mikrotik jika ada perubahan bandwidth."""
        res = super(ISPPackage, self).write(vals)
        if any(field in vals for field in ['bandwidth_up', 'bandwidth_down', 'name']):
            for record in self:
                record.action_sync_to_mikrotik()
        return res

    def action_view_subscriptions(self):
        """Tampilkan subscription paket"""
        self.ensure_one()
        return {
            'name': 'Subscriptions',
            'type': 'ir.actions.act_window',
            'res_model': 'isp.subscription',
            'view_mode': 'tree,form',
            'domain': [('package_id', '=', self.id)],
            'context': {'default_package_id': self.id}
        } 