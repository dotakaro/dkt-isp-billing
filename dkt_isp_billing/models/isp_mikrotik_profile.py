from odoo import models, fields, api
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

class ISPMikrotikProfile(models.Model):
    _name = 'isp.mikrotik.profile'
    _description = 'Mikrotik PPPoE Profile'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama Profile', required=True, tracking=True)
    mikrotik_id = fields.Char('ID di Mikrotik', readonly=True)
    mikrotik_config_id = fields.Many2one('isp.mikrotik.config', string='Router Mikrotik', 
                                        required=True, tracking=True,
                                        help="Router Mikrotik tempat profile ini dibuat")
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
        """Sinkronisasi profile dari semua Mikrotik Router yang terdaftar (sinkronisasi 1:1)"""
        # Ambil semua router Mikrotik yang aktif
        mikrotik_configs = self.env['isp.mikrotik.config'].search([('active', '=', True)])
        if not mikrotik_configs:
            raise ValidationError('Tidak ada konfigurasi Mikrotik yang aktif!')
        
        success_count = 0
        error_messages = []
        
        # Iterasi untuk setiap router Mikrotik
        for mikrotik in mikrotik_configs:
            try:
                api = mikrotik.get_connection()
                if not api:
                    error_messages.append(f"Gagal terhubung ke router {mikrotik.name}")
                    continue
                
                try:
                    # Ambil semua profile dari Mikrotik
                    profile_api = api.get_resource('/ppp/profile')
                    mikrotik_profiles = profile_api.get()
                    
                    # Ambil semua profile di Odoo untuk router ini
                    odoo_profiles = self.search([('mikrotik_config_id', '=', mikrotik.id)])
                    
                    # Buat dictionary untuk memetakan nama profile di Mikrotik
                    mikrotik_profile_names = {profile['name']: profile for profile in mikrotik_profiles}
                    
                    # Nonaktifkan profile yang ada di Odoo tapi tidak ada di Mikrotik
                    for odoo_profile in odoo_profiles:
                        if odoo_profile.name not in mikrotik_profile_names:
                            odoo_profile.write({
                                'active': False,
                                'mikrotik_id': False,
                            })
                            _logger.info(f"Profile {odoo_profile.name} dinonaktifkan karena tidak ada di router {mikrotik.name}")
                    
                    # Update atau buat profile yang ada di Mikrotik
                    for profile_name, profile in mikrotik_profile_names.items():
                        existing = self.search([
                            ('name', '=', profile_name), 
                            ('mikrotik_config_id', '=', mikrotik.id)
                        ], limit=1)
                        
                        vals = {
                            'name': profile_name,
                            'mikrotik_id': profile.get('.id', '') or profile.get('id', ''),
                            'mikrotik_config_id': mikrotik.id,
                            'rate_limit': profile.get('rate-limit', ''),
                            'local_address': profile.get('local-address', ''),
                            'remote_address': profile.get('remote-address', ''),
                            'parent_queue': profile.get('parent-queue', ''),
                            'only_one': profile.get('only-one', 'no') == 'yes',
                            'active': True,  # Pastikan profile aktif
                        }
                        
                        if existing:
                            existing.write(vals)
                        else:
                            self.create(vals)
                    
                    success_count += 1
                    _logger.info(f"Berhasil sinkronisasi profile dari router {mikrotik.name}")
                    
                except Exception as e:
                    error_msg = f"Gagal sinkronisasi profile dari router {mikrotik.name}: {str(e)}"
                    error_messages.append(error_msg)
                    _logger.error(error_msg)
                finally:
                    if api and hasattr(api, 'connection_pool'):
                        api.connection_pool.disconnect()
            except Exception as e:
                error_msg = f"Error tidak terduga saat sinkronisasi dari router {mikrotik.name}: {str(e)}"
                error_messages.append(error_msg)
                _logger.error(error_msg)
        
        # Tampilkan notifikasi hasil sinkronisasi
        if success_count > 0:
            message = f"Berhasil sinkronisasi profile dari {success_count} router"
            if error_messages:
                message += f". Terdapat {len(error_messages)} router yang gagal."
            
            notification_type = 'success' if not error_messages else 'warning'
            
            # Jika ada error, tambahkan ke log chatter
            if error_messages:
                # Catat error di chatter untuk model ini (menggunakan record pertama sebagai referensi)
                first_profile = self.search([], limit=1)
                if first_profile:
                    error_log = "<br/>".join(error_messages)
                    first_profile.message_post(
                        body=f"<b>Error saat sinkronisasi profile PPPoE:</b><br/>{error_log}",
                        subject="Sinkronisasi Profile PPPoE - Error Log"
                    )
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sinkronisasi Profile PPPoE',
                    'message': message,
                    'type': notification_type,
                    'sticky': bool(error_messages),
                }
            }
        else:
            error_message = "Gagal sinkronisasi profile dari semua router:<br/>" + "<br/>".join(error_messages)
            raise ValidationError(error_message)

    def action_create_in_mikrotik(self):
        """Membuat profile baru di Mikrotik Router"""
        self.ensure_one()
        mikrotik = self.mikrotik_config_id or self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
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
                profile_data['only-one'] = 'yes'
                
            result = profile_api.add(**profile_data)
            if isinstance(result, list) and len(result) > 0:
                self.mikrotik_id = result[0].get('.id')
            
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
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect() 