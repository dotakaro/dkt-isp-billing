from odoo import models, fields, api
from odoo.exceptions import ValidationError
import random
import string
import logging
import requests

_logger = logging.getLogger(__name__)

class ISPCPE(models.Model):
    _name = 'isp.cpe'
    _description = 'Customer Premise Equipment'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama Perangkat', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Pelanggan', required=True, tracking=True,
                                domain=[('customer_rank', '>', 0)])
    mac_address = fields.Char('MAC Address', tracking=True)
    ip_address = fields.Char('IP Address', tracking=True)
    outdoor_unit = fields.Char('Outdoor Unit')
    router = fields.Char('Router')
    connection_type = fields.Selection([
        ('pppoe', 'PPPoE'),
        ('static', 'Static IP'),
        ('dhcp', 'DHCP')
    ], string='Tipe Koneksi', default='pppoe', required=True, tracking=True)
    pppoe_username = fields.Char('PPPoE Username', tracking=True)
    pppoe_password = fields.Char('PPPoE Password', tracking=True)
    ownership = fields.Selection([
        ('customer', 'Milik Pelanggan'),
        ('company', 'Milik Perusahaan')
    ], string='Kepemilikan', default='company')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('isolated', 'Terisolir'),
        ('terminated', 'Terminasi')
    ], default='draft', string='Status', tracking=True)
    active = fields.Boolean('Active', default=True, tracking=True)
    history_ids = fields.One2many('isp.device.history', 'cpe_id', string='Riwayat')
    notes = fields.Text('Catatan', tracking=True)
    
    subscription_ids = fields.One2many('isp.subscription', 'cpe_id', string='Subscription')
    subscription_id = fields.Many2one('isp.subscription', string='Active Subscription',
                                    compute='_compute_subscription', store=True)
    subscription_state = fields.Selection(related='subscription_id.state', string='Status Subscription')
    
    # Fields untuk monitoring PPPoE
    pppoe_status = fields.Selection([
        ('connected', 'Connected'),
        ('disconnected', 'Disconnected'),
        ('unknown', 'Unknown')
    ], string='Status PPPoE', default='unknown', tracking=True)
    pppoe_uptime = fields.Char('PPPoE Uptime', tracking=True)
    pppoe_last_seen = fields.Datetime('Last Seen', tracking=True)
    pppoe_caller_id = fields.Char('Caller ID', tracking=True, help='MAC Address perangkat yang terkoneksi')
    pppoe_address = fields.Char('PPPoE IP', tracking=True, help='IP Address yang diberikan ke client')
    pppoe_session_id = fields.Char('Session ID', tracking=True)
    upload_usage = fields.Float('Upload (MB)', tracking=True)
    download_usage = fields.Float('Download (MB)', tracking=True)
    upload_rate = fields.Char('Upload Rate', tracking=True)
    download_rate = fields.Char('Download Rate', tracking=True)
    signal_strength = fields.Char('Signal Strength', tracking=True)
    
    # Field untuk monitoring realtime
    is_monitoring = fields.Boolean('Is Monitoring', default=False)
    current_upload_rate = fields.Char('Current Upload Rate', readonly=True)
    current_download_rate = fields.Char('Current Download Rate', readonly=True)
    last_update = fields.Datetime('Last Update', readonly=True)
    
    # Tambahkan field untuk menandai proses terminasi sedang berlangsung
    is_terminating = fields.Boolean('Is Terminating', default=False, copy=False)
    
    _sql_constraints = [
        ('mac_address_uniq', 'unique(mac_address)', 'MAC Address harus unik!'),
        ('pppoe_username_uniq', 'unique(pppoe_username,connection_type)', 'PPPoE Username harus unik!'),
    ]

    @api.depends('subscription_ids', 'subscription_ids.state')
    def _compute_subscription(self):
        for record in self:
            # Ambil subscription yang tidak terminated
            valid_subs = record.subscription_ids.filtered(lambda s: s.state != 'terminated')
            record.subscription_id = valid_subs[0] if valid_subs else False
    
    @api.constrains('subscription_ids')
    def _check_single_subscription(self):
        for record in self:
            active_subs = record.subscription_ids.filtered(lambda s: s.state != 'terminated')
            if len(active_subs) > 1:
                raise ValidationError(f'CPE {record.name} hanya boleh memiliki satu subscription aktif!')

    @api.model
    def _generate_random_password(self, length=8):
        """Generate random password with specified length."""
        characters = string.ascii_letters + string.digits
        return ''.join(random.choice(characters) for i in range(length))

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id and not self.pppoe_username:
            # Generate username dari nama pelanggan
            base_username = self.partner_id.name.lower().replace(' ', '-')
            counter = 1
            username = base_username
            while self.search_count([('pppoe_username', '=', username)]) > 0:
                username = f"{base_username}-{counter}"
                counter += 1
            self.pppoe_username = username
            self.pppoe_password = self._generate_random_password()

    def generate_pppoe_credentials(self):
        """Generate PPPoE username dan password"""
        self.ensure_one()
        if self.pppoe_username and self.pppoe_password:
            raise ValidationError('PPPoE credentials sudah ada!')
            
        # Generate username dari nama pelanggan
        if not self.pppoe_username:
            base_username = self.partner_id.name.lower().replace(' ', '-')
            counter = 1
            username = base_username
            while self.search_count([('pppoe_username', '=', username)]) > 0:
                username = f"{base_username}-{counter}"
                counter += 1
            self.pppoe_username = username
            
        # Generate random password
        if not self.pppoe_password:
            chars = string.ascii_letters + string.digits
            self.pppoe_password = ''.join(random.choice(chars) for _ in range(8))
    
    def _check_mikrotik_secret(self):
        """
        Cek apakah secret sudah ada di Mikrotik dan statusnya di Odoo
        Returns: (exists, user_id, error, secret_data)
        """
        self.ensure_one()
        if self.connection_type != 'pppoe':
            return False, False, None, None
            
        # Jika state adalah terminated, lewati validasi username
        if self.state == 'terminated':
            return False, False, None, None
            
        if not self.pppoe_username:
            return False, False, 'PPPoE Username tidak boleh kosong!', None
            
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            return False, False, 'Konfigurasi Mikrotik tidak ditemukan!', None
            
        api = mikrotik.get_connection()
        if not api:
            return False, False, 'Gagal terhubung ke Mikrotik!', None
            
        try:
            secret_api = api.get_resource('/ppp/secret')
            secrets = secret_api.get(name=self.pppoe_username)
            if secrets:
                return True, secrets[0].get('.id'), None, secrets[0]
            return False, False, None, None
        except Exception as e:
            return False, False, str(e), None
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect()

    def adopt_mikrotik_secret(self):
        """
        Mengadopsi secret yang sudah ada di Mikrotik
        Returns: (success, message)
        """
        self.ensure_one()
        if self.connection_type != 'pppoe':
            return True, 'Bukan koneksi PPPoE'
            
        if not self.pppoe_username or not self.pppoe_password:
            return False, 'PPPoE Username dan Password harus diisi!'
            
        # Cek apakah secret sudah ada di Mikrotik
        exists, user_id, error, secret_data = self._check_mikrotik_secret()
        if error:
            return False, error
            
        if not exists:
            return False, f'Secret {self.pppoe_username} tidak ditemukan di Mikrotik'
            
        # Cek apakah secret sudah dipakai oleh CPE lain
        existing_cpe = self.env['isp.cpe'].search([
            ('id', '!=', self.id),
            ('pppoe_username', '=', self.pppoe_username),
            ('state', 'not in', ['terminated'])
        ], limit=1)
        
        if existing_cpe:
            return False, f'Secret {self.pppoe_username} sudah dipakai oleh CPE {existing_cpe.name} (Customer: {existing_cpe.partner_id.name}). Silakan gunakan username PPPoE yang lain.'
            
        # Adopsi secret yang ada
        try:
            self.write({
                'pppoe_password': secret_data.get('password', self.pppoe_password),
                'state': 'open'
            })
            return True, f'Secret {self.pppoe_username} berhasil diadopsi dari Mikrotik'
        except Exception as e:
            _logger.error(f'Error adopting secret: {str(e)}')
            return False, f'Gagal mengadopsi secret {self.pppoe_username}: {str(e)}'

    def action_activate(self):
        """Aktivasi CPE dan buat PPPoE secret"""
        self.ensure_one()
        if not self.subscription_id:
            raise ValidationError('CPE harus memiliki subscription terlebih dahulu!')
            
        if self.connection_type == 'pppoe':
            # Cek apakah sudah ada secret di Mikrotik
            exists, user_id, error, secret_data = self._check_mikrotik_secret()
            if error:
                raise ValidationError(error)
                
            if exists:
                # Coba adopsi secret yang ada
                success, message = self.adopt_mikrotik_secret()
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
                
            # Jika secret belum ada, buat baru di Mikrotik
            mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
            if not mikrotik:
                raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
                
            api = mikrotik.get_connection()
            if not api:
                raise ValidationError('Gagal terhubung ke Mikrotik!')
                
            try:
                secret_api = api.get_resource('/ppp/secret')
                secret_data = {
                    'name': self.pppoe_username,
                    'password': self.pppoe_password,
                    'service': 'pppoe',
                    'profile': self.subscription_id.package_id.profile_id.name,
                    'comment': f'Customer: {self.partner_id.name}',
                    'disabled': 'yes'  # Default disabled, akan di-enable oleh subscription
                }
                
                secret_api.add(**secret_data)
                self.write({'state': 'open'})
                
                # Aktifkan subscription jika masih draft
                if self.subscription_id.state == 'draft':
                    self.subscription_id.action_open()
                    
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Sukses',
                        'message': 'CPE berhasil diaktifkan dengan membuat secret baru',
                        'type': 'success',
                    }
                }
            except Exception as e:
                raise ValidationError(f'Gagal mengaktifkan CPE di Mikrotik: {str(e)}')
            finally:
                if api and hasattr(api, 'connection_pool'):
                    api.connection_pool.disconnect()
        else:
            # Untuk koneksi non-PPPoE, langsung aktifkan
            self.write({'state': 'open'})
            if self.subscription_id.state == 'draft':
                self.subscription_id.action_open()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sukses',
                    'message': 'CPE berhasil diaktifkan',
                    'type': 'success',
                }
            }
    
    def action_isolate(self):
        """Isolir CPE"""
        self.ensure_one()
        if self.state != 'open':
            raise ValidationError('Hanya CPE open yang dapat diisolir!')
            
        if not self.subscription_id:
            raise ValidationError('CPE tidak memiliki subscription!')
            
        # Isolir subscription yang akan otomatis disable PPPoE secret
        self.subscription_id.action_isolate()
        
        self.write({'state': 'isolated'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'CPE berhasil diisolir',
                'type': 'success',
            }
        }
    
    def action_terminate(self):
        """Terminasi CPE"""
        self.ensure_one()
        if self.state not in ['open', 'isolated']:
            raise ValidationError('Hanya CPE open atau terisolir yang dapat diterminasi!')
        
        # 1. Set flag terminasi untuk bypass validasi
        self.is_terminating = True
        
        # 2. Jika ada subscription, terminate dulu
        if self.subscription_id:
            self.subscription_id.action_terminate()
        else:
            # 3. Jika tidak ada subscription, langsung proses CPE
            
            # Simpan username jika perlu menghapus secret Mikrotik
            has_pppoe = self.connection_type == 'pppoe' and self.pppoe_username
            pppoe_username = self.pppoe_username if has_pppoe else False
            
            # Set state tanpa melewati validasi (SUPER untuk bypass ORM constraint)
            # Langsung ubah ke draft, bukan terminated agar history tetap ada
            self.env.cr.execute(
                """UPDATE isp_cpe SET state = 'draft' WHERE id = %s""", 
                (self.id,)
            )
            
            # Hapus secret di Mikrotik jika perlu
            if has_pppoe:
                # Hapus secret di Mikrotik
                exists, user_id, error, secret_data = self._check_mikrotik_secret()
                if error:
                    _logger.warning(f'Error checking secret: {error}')
                
                if exists:
                    mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
                    if not mikrotik:
                        _logger.warning('Konfigurasi Mikrotik tidak ditemukan!')
                    else:
                        api = mikrotik.get_connection()
                        if not api:
                            _logger.warning('Gagal terhubung ke Mikrotik!')
                        else:
                            try:
                                secret_api = api.get_resource('/ppp/secret')
                                secret_api.remove(id=user_id)
                            except Exception as e:
                                _logger.error(f'Gagal menghapus secret di Mikrotik: {str(e)}')
                            finally:
                                if hasattr(api, 'disconnect'):
                                    api.disconnect()
        
        # Refresh model data dari database
        self.env['isp.cpe'].invalidate_model()
        
        # Reset flag terminasi
        self.write({'is_terminating': False})
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'CPE berhasil diterminasi dan diubah ke status draft',
                'type': 'success',
            }
        }
    
    def action_view_subscriptions(self):
        """Tampilkan subscription CPE"""
        self.ensure_one()
        return {
            'name': 'Subscriptions',
            'type': 'ir.actions.act_window',
            'res_model': 'isp.subscription',
            'view_mode': 'tree,form',
            'domain': [('cpe_id', '=', self.id)],
            'context': {
                'default_partner_id': self.partner_id.id,
                'default_cpe_id': self.id
            }
        }

    def _get_pppoe_active(self, api):
        """
        Mendapatkan informasi PPPoE active dari Mikrotik
        Returns: (active_data, error)
        """
        self.ensure_one()
        try:
            active_api = api.get_resource('/ppp/active')
            # Cari berdasarkan username yang sesuai
            active = active_api.get(name=self.pppoe_username)
            if active and isinstance(active, list) and len(active) > 0:
                return active[0], None
                
            # Jika tidak ditemukan dengan username exact match, 
            # coba cari dengan contains untuk menangani kasus username dengan format berbeda
            all_active = active_api.get()
            for conn in all_active:
                if isinstance(conn, dict) and self.pppoe_username.lower() in conn.get('name', '').lower():
                    return conn, None
                    
            return None, None
        except Exception as e:
            _logger.error(f'Error getting PPPoE active status: {str(e)}')
            return None, str(e)
            
    def _get_pppoe_secret(self, api):
        """
        Mendapatkan informasi PPPoE secret dari Mikrotik
        Returns: (secret_data, error)
        """
        self.ensure_one()
        try:
            secret_api = api.get_resource('/ppp/secret')
            secret = secret_api.get(name=self.pppoe_username)
            if secret:
                return secret[0], None
            return None, None
        except Exception as e:
            return None, str(e)
            
    def _bytes_to_mb(self, bytes_val):
        """Convert bytes to MB"""
        try:
            return float(bytes_val) / (1024 * 1024)
        except:
            return 0.0
            
    def update_pppoe_status(self):
        """Update PPPoE status from Mikrotik"""
        self.ensure_one()
        
        if not self.pppoe_username:
            return False
        
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            return False
        
        api = mikrotik.get_connection()
        if not api:
            return False
        
        try:
            # Get active connection
            active, error = self._get_pppoe_active(api)
            if error:
                _logger.error(f"Error mendapatkan status PPPoE: {error}")
                return False
                
            if active and isinstance(active, dict):
                # Get interface stats
                interface = active.get('interface')
                stats = {}
                if interface:
                    interface_api = api.get_resource('/interface')
                    interface_stats = interface_api.get(name=interface)
                    if interface_stats and isinstance(interface_stats, list) and len(interface_stats) > 0:
                        stats = interface_stats[0]
                        if not isinstance(stats, dict):
                            stats = {}
                
                # Ambil nilai caller-id (MAC address) dan address (IP address)
                caller_id = active.get('caller-id', '')
                pppoe_address = active.get('address', '')
            
                # Update status
                update_vals = {
                    'pppoe_status': 'connected',
                    'pppoe_uptime': active.get('uptime', ''),
                    'pppoe_address': pppoe_address,
                    'pppoe_caller_id': caller_id,
                    'pppoe_session_id': active.get('session-id', ''),
                    'current_upload_rate': self._format_rate(stats.get('tx-byte', 0)) + ' kbps',
                    'current_download_rate': self._format_rate(stats.get('rx-byte', 0)) + ' kbps',
                    'upload_usage': float(stats.get('tx-byte', 0)) / (1024 * 1024),
                    'download_usage': float(stats.get('rx-byte', 0)) / (1024 * 1024),
                    'last_update': fields.Datetime.now()
                }
                
                # Update MAC address dan IP address jika ada nilai baru
                if caller_id and not self.mac_address:
                    update_vals['mac_address'] = caller_id
                    
                if pppoe_address and not self.ip_address:
                    update_vals['ip_address'] = pppoe_address
                
                self.write(update_vals)
            else:
                self.write({
                    'pppoe_status': 'disconnected',
                    'pppoe_uptime': '',
                    'pppoe_address': '',
                    'pppoe_caller_id': '',
                    'pppoe_session_id': '',
                    'current_upload_rate': '0 kbps',
                    'current_download_rate': '0 kbps',
                    'last_update': fields.Datetime.now()
                })
            
            return True
        except Exception as e:
            _logger.error(f'Error updating PPPoE status: {str(e)}')
            return False
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect()

    def _format_rate(self, bytes_val):
        """Format byte rate ke kbps"""
        try:
            return str(int(float(bytes_val) * 8 / 1024))
        except:
            return '0'

    def action_check_pppoe(self):
        """Action untuk mengecek status PPPoE"""
        self.ensure_one()
        
        # Update status PPPoE (juga akan update MAC dan IP jika belum diisi)
        result = self.update_pppoe_status()
        
        # Jika berhasil dan status terhubung, selalu update MAC dan IP address
        if result and self.pppoe_status == 'connected':
            # Pastikan MAC address dan IP address selalu terupdate dengan yang terbaru
            update_vals = {}
            
            if self.pppoe_caller_id and (not self.mac_address or self.mac_address != self.pppoe_caller_id):
                update_vals['mac_address'] = self.pppoe_caller_id
                
            if self.pppoe_address and (not self.ip_address or self.ip_address != self.pppoe_address):
                update_vals['ip_address'] = self.pppoe_address
                
            if update_vals:
                self.write(update_vals)
        
        # Pesan berbeda tergantung status
        if self.pppoe_status == 'connected':
            message = f'Status PPPoE: {self.pppoe_status}. MAC Address dan IP Address telah diperbarui.'
        else:
            message = f'Status PPPoE: {self.pppoe_status}.'
            
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Info',
                'message': message,
                'type': 'info',
            }
        }

    @api.model
    def _cron_update_pppoe_status(self):
        """
        Cron job untuk update status PPPoE semua CPE yang aktif
        """
        # Ambil semua CPE yang statusnya open atau isolated
        cpes = self.search([('state', 'in', ['open', 'isolated'])])
        for cpe in cpes:
            try:
                cpe.update_pppoe_status()
            except Exception as e:
                _logger.error(f'Error updating PPPoE status for CPE {cpe.name}: {str(e)}')
                continue 

    def _compute_rates(self):
        """Compute upload dan download rate"""
        for record in self:
            record.upload_rate = record.current_upload_rate or '0 kbps'
            record.download_rate = record.current_download_rate or '0 kbps'
            
    def action_start_monitoring(self):
        """Start monitoring PPPoE connection"""
        self.ensure_one()
        if self.connection_type != 'pppoe':
            raise ValidationError('Hanya koneksi PPPoE yang dapat dimonitor!')
        
        if not self.pppoe_username:
            raise ValidationError('PPPoE Username harus diisi!')
        
        # Update status monitoring
        self.write({
            'is_monitoring': True,
            'last_update': fields.Datetime.now()
        })
        
        # Ambil data awal
        self.update_pppoe_status()
        
        return True

    def action_stop_monitoring(self):
        """Stop monitoring PPPoE connection"""
        self.ensure_one()
        
        # Update status monitoring
        self.write({
            'is_monitoring': False,
            'current_upload_rate': '0 kbps',
            'current_download_rate': '0 kbps'
        })
        
        return True

    @api.model
    def _update_pppoe_stats(self, stats):
        """Update statistik PPPoE dari monitoring"""
        self.ensure_one()
        self.write({
            'current_upload_rate': stats['rate_out'] + ' kbps',
            'current_download_rate': stats['rate_in'] + ' kbps',
            'upload_usage': stats['bytes_out'],
            'download_usage': stats['bytes_in'],
            'pppoe_status': stats['status'],
            'pppoe_uptime': stats['uptime'],
            'pppoe_address': stats['address']
        })

    @api.constrains('connection_type', 'pppoe_username', 'pppoe_password', 'state')
    def _check_pppoe_fields(self):
        for record in self:
            # Jika CPE sudah dalam status terminated, lewati validasi
            if record.state == 'terminated' or record.is_terminating:
                continue
            if record.connection_type == 'pppoe':
                if not record.pppoe_username:
                    raise ValidationError('PPPoE Username harus diisi untuk koneksi PPPoE!')
                if not record.pppoe_password:
                    raise ValidationError('PPPoE Password harus diisi untuk koneksi PPPoE!') 