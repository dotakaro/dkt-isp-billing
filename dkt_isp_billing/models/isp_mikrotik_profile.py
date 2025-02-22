from odoo import models, fields, api
from odoo.exceptions import ValidationError

class ISPMikrotikProfile(models.Model):
    _name = 'isp.mikrotik.profile'
    _description = 'Mikrotik PPPoE Profile'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama Profile', required=True, tracking=True)
    mikrotik_id = fields.Char('ID di Mikrotik', readonly=True)
    rate_limit = fields.Char('Rate Limit', required=True, tracking=True,
                          help="Format: [upload]M/[download]M (contoh: 10M/20M)")
    local_address = fields.Char('Local Address', tracking=True)
    remote_address = fields.Char('Remote Address', tracking=True)
    parent_queue = fields.Char('Parent Queue', tracking=True)
    only_one = fields.Boolean('Only One', tracking=True)
    active = fields.Boolean('Active', default=True, tracking=True)
    package_ids = fields.One2many('isp.package', 'profile_id', string='Paket Terkait')
    package_count = fields.Integer(compute='_compute_package_count')
    
    @api.depends('package_ids')
    def _compute_package_count(self):
        for record in self:
            record.package_count = len(record.package_ids)

    def action_sync_from_mikrotik(self):
        """Sinkronisasi profile dari Mikrotik Router"""
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
            
        api = mikrotik.get_connection()
        if not api:
            raise ValidationError('Gagal terhubung ke Mikrotik!')
            
        try:
            profile_api = api.get_resource('/ppp/profile')
            profiles = profile_api.get()
            
            for profile in profiles:
                existing = self.search([('name', '=', profile['name'])], limit=1)
                vals = {
                    'name': profile['name'],
                    'mikrotik_id': profile.get('id', ''),
                    'rate_limit': profile.get('rate-limit', ''),
                    'local_address': profile.get('local-address', ''),
                    'remote_address': profile.get('remote-address', ''),
                    'parent_queue': profile.get('parent-queue', ''),
                    'only_one': profile.get('only-one', False),
                }
                
                if existing:
                    existing.write(vals)
                else:
                    self.create(vals)
                    
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sukses',
                    'message': 'Profile berhasil disinkronkan dari Mikrotik',
                    'type': 'success',
                }
            }
        except Exception as e:
            raise ValidationError(f'Gagal sinkronisasi profile dari Mikrotik: {str(e)}')

    def action_create_in_mikrotik(self):
        """Membuat profile baru di Mikrotik Router"""
        self.ensure_one()
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
            
        api = mikrotik.get_connection()
        if not api:
            raise ValidationError('Gagal terhubung ke Mikrotik!')
            
        try:
            profile_api = api.get_resource('/ppp/profile')
            profile_data = {
                'name': self.name,
                'rate-limit': self.rate_limit,
            }
            
            if self.local_address:
                profile_data['local-address'] = self.local_address
            if self.remote_address:
                profile_data['remote-address'] = self.remote_address
            if self.parent_queue:
                profile_data['parent-queue'] = self.parent_queue
            if self.only_one:
                profile_data['only-one'] = self.only_one
                
            result = profile_api.add(**profile_data)
            self.mikrotik_id = result['ret']
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sukses',
                    'message': f'Profile {self.name} berhasil dibuat di Mikrotik',
                    'type': 'success',
                }
            }
        except Exception as e:
            raise ValidationError(f'Gagal membuat profile di Mikrotik: {str(e)}') 