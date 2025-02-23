from odoo import models, fields, api
from odoo.exceptions import ValidationError
import routeros_api
import socket
import logging

_logger = logging.getLogger(__name__)

class ISPMikrotikConfig(models.Model):
    _name = 'isp.mikrotik.config'
    _description = 'Mikrotik Configuration'
    _inherit = ['mail.thread']

    name = fields.Char('Nama', required=True, tracking=True)
    host = fields.Char('Host/IP', required=True, tracking=True, help='Format: host:port (contoh: 192.168.1.1:8728)')
    port = fields.Integer('Port', default=8728, tracking=True, help='Deprecated: Gunakan format host:port pada field Host/IP')
    username = fields.Char('Username', required=True, tracking=True)
    password = fields.Char('Password', required=True, tracking=True)
    active = fields.Boolean('Active', default=True, tracking=True)

    @api.constrains('host')
    def _check_host_format(self):
        for record in self:
            if not record.host:
                raise ValidationError('Host/IP tidak boleh kosong')
            
            try:
                host_parts = record.host.split(':')
                if len(host_parts) != 2:
                    raise ValidationError('Format Host/IP harus host:port (contoh: 192.168.1.1:8728)')
                
                # Validasi port
                try:
                    port = int(host_parts[1])
                    if port <= 0 or port > 65535:
                        raise ValidationError('Port harus antara 1-65535')
                except ValueError:
                    raise ValidationError('Port harus berupa angka')
                
                # Validasi host/IP
                host = host_parts[0].strip()
                try:
                    socket.gethostbyname(host)
                except socket.gaierror:
                    raise ValidationError('Host/IP tidak valid')
            except Exception as e:
                if not isinstance(e, ValidationError):
                    raise ValidationError(f'Format Host/IP tidak valid: {str(e)}')
                raise

    def _parse_host_port(self):
        """Helper method untuk parsing host:port"""
        try:
            host_parts = self.host.split(':')
            if len(host_parts) != 2:
                raise ValidationError('Format Host/IP harus host:port')
            
            host = host_parts[0].strip()
            port = int(host_parts[1])
            
            return host, port
        except (ValueError, IndexError):
            raise ValidationError('Format Host/IP tidak valid')

    def test_connection(self):
        self.ensure_one()
        try:
            host, port = self._parse_host_port()
            _logger.info(f'Mencoba koneksi ke {host}:{port} dengan user {self.username}')
            
            # Cek dulu apakah host bisa di-ping
            try:
                socket.create_connection((host, port), timeout=5)
                _logger.info('Koneksi socket berhasil')
            except (socket.timeout, socket.gaierror, ConnectionRefusedError) as e:
                raise ValidationError(f'Tidak dapat terhubung ke {host}:{port} - {str(e)}')
            
            # Koneksi non-SSL
            connection = routeros_api.RouterOsApiPool(
                host=host,
                username=self.username,
                password=self.password,
                port=port,
                plaintext_login=True
            )
            
            try:
                api = connection.get_api()
                identity = api.get_resource('/system/identity').get()
                _logger.info(f'Berhasil mendapatkan identity: {identity}')
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Sukses',
                        'message': f'Koneksi ke Mikrotik berhasil',
                        'type': 'success',
                    }
                }
            except Exception as e:
                _logger.error(f'Error saat mengambil identity: {str(e)}')
                if 'cannot log in' in str(e).lower():
                    raise ValidationError('Username atau password salah')
                raise ValidationError(f'Gagal mengakses API: {str(e)}')
            finally:
                try:
                    connection.disconnect()
                    _logger.info('Koneksi berhasil ditutup')
                except:
                    _logger.warning('Gagal menutup koneksi')
                    
        except ValidationError as e:
            _logger.error(f'Validation error: {str(e)}')
            raise
        except Exception as e:
            _logger.error(f'Test connection error: {str(e)}', exc_info=True)
            if not str(e):
                raise ValidationError('Koneksi timeout atau ditolak oleh server')
            raise ValidationError(f'Koneksi gagal: {str(e)}')

    def get_connection(self):
        self.ensure_one()
        try:
            host, port = self._parse_host_port()
            _logger.info(f'Membuat koneksi ke {host}:{port}')
            
            connection = routeros_api.RouterOsApiPool(
                host=host,
                username=self.username,
                password=self.password,
                port=port,
                plaintext_login=True
            )
            api = connection.get_api()
            # Simpan connection pool di api object untuk digunakan saat disconnect
            api.connection_pool = connection
            return api
                
        except Exception as e:
            _logger.error(f'Get connection error: {str(e)}', exc_info=True)
            return False

    def enable_user(self, username):
        """Mengaktifkan user PPPoE di Mikrotik"""
        self.ensure_one()
        api = self.get_connection()
        if not api:
            raise ValidationError('Gagal terhubung ke Mikrotik!')
            
        try:
            secret_api = api.get_resource('/ppp/secret')
            secrets = secret_api.get(name=username)
            if secrets:
                secret_id = secrets[0].get('id', '')
                if secret_id:
                    secret_api.set(id=secret_id, disabled='no')
                    return True
            return False
        except Exception as e:
            raise ValidationError(f'Gagal mengaktifkan user di Mikrotik: {str(e)}')
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect()

    def disable_user(self, username):
        """Menonaktifkan user PPPoE di Mikrotik"""
        self.ensure_one()
        api = self.get_connection()
        if not api:
            raise ValidationError('Gagal terhubung ke Mikrotik!')
            
        try:
            secret_api = api.get_resource('/ppp/secret')
            secrets = secret_api.get(name=username)
            if secrets:
                secret_id = secrets[0].get('id', '')
                if secret_id:
                    secret_api.set(id=secret_id, disabled='yes')
                    return True
            return False
        except Exception as e:
            raise ValidationError(f'Gagal menonaktifkan user di Mikrotik: {str(e)}')
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect()

    @api.model
    def get_default_config(self):
        """Mendapatkan konfigurasi Mikrotik default (aktif)"""
        return self.search([('active', '=', True)], limit=1)

class ISPCustomer(models.Model):
    _inherit = 'isp.customer'

    is_adopted_secret = fields.Boolean('Is Adopted Secret', default=False, help='Menandakan bahwa secret/user ini diadopsi dari Mikrotik yang sudah ada')
    
    def _check_existing_mikrotik_user(self, username, user_id):
        """
        Mengecek apakah user Mikrotik sudah terkait dengan pelanggan lain
        Returns: customer yang menggunakan secret tersebut atau False
        """
        existing_customer = self.search([
            ('cpe_ids.pppoe_username', '=', username),
            ('id', '!=', self.id)
        ], limit=1)
        return existing_customer

    def action_adopt_secret(self):
        """
        Mengadopsi secret/user yang sudah ada di Mikrotik
        """
        self.ensure_one()
        if not self.cpe_ids:
            raise ValidationError('CPE harus diisi terlebih dahulu!')
            
        cpe = self.cpe_ids[0]
        if not cpe.pppoe_username or not cpe.pppoe_password:
            raise ValidationError('PPPoE Username dan Password harus diisi di CPE!')
            
        if cpe.adopt_mikrotik_secret():
            self.write({
                'state': 'active',
                'is_adopted_secret': True
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sukses',
                    'message': f'Secret/User PPPoE {cpe.pppoe_username} berhasil diadopsi',
                    'type': 'success',
                }
            }
        raise ValidationError(f'Gagal mengadopsi secret {cpe.pppoe_username}')

    def create_mikrotik_user(self, cpe):
        """Buat user di Mikrotik"""
        if not cpe:
            return False, 'CPE tidak ditemukan'
            
        # Cek apakah secret sudah ada
        exists, user_id, error, secret_data = cpe._check_mikrotik_secret()
        _logger.info(f'Checking secret {cpe.pppoe_username}: exists={exists}, user_id={user_id}, error={error}')
        
        if error:
            return False, error
            
        if exists:
            # Coba adopsi secret yang ada
            success, message = cpe.adopt_mikrotik_secret()
            if not success:
                _logger.error(f'Create Mikrotik user error: {message}')
                return False, message
            return True, message
            
        # Buat secret baru
        try:
            api = self.get_connection()
            if not api:
                return False, 'Gagal terhubung ke Mikrotik'
                
            secret_api = api.get_resource('/ppp/secret')
            secret_data = {
                'name': cpe.pppoe_username,
                'password': cpe.pppoe_password,
                'service': 'pppoe',
                'profile': cpe.active_subscription_id.package_id.profile_id.name if cpe.active_subscription_id else 'default',
                'comment': f'Customer: {cpe.customer_id.name} ({cpe.customer_id.customer_id})'
            }
            
            secret_api.add(**secret_data)
            return True, 'Secret berhasil dibuat'
        except Exception as e:
            _logger.error(f'Create Mikrotik user error: {str(e)}', exc_info=True)
            return False, str(e)
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect()

    def action_activate(self):
        """Aktivasi pelanggan"""
        self.ensure_one()
        if self.state == 'draft':
            return self.create_mikrotik_user(self.cpe_ids[0])

    def action_isolate(self):
        self.ensure_one()
        if self.state == 'active':
            mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
            if not mikrotik:
                raise ValidationError('Konfigurasi Mikrotik tidak ditemukan')
                
            api = mikrotik.get_connection()
            if not api:
                raise ValidationError('Gagal terhubung ke Mikrotik')
                
            try:
                # Isolir semua CPE aktif
                for cpe in self.cpe_ids.filtered(lambda c: c.state == 'active'):
                    user_api = api.get_resource('/ppp/secret')
                    secrets = user_api.get(name=cpe.pppoe_username)
                    if secrets:
                        user_id = secrets[0].get('.id')
                        user_api.set(id=user_id, disabled='yes')
                        cpe.state = 'isolated'
                self.state = 'isolated'
            except Exception as e:
                raise ValidationError(f'Gagal isolir: {str(e)}')
            finally:
                if api and hasattr(api, 'connection_pool'):
                    api.connection_pool.disconnect()

    def action_enable(self):
        self.ensure_one()
        if self.state == 'isolated':
            mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
            if not mikrotik:
                raise ValidationError('Konfigurasi Mikrotik tidak ditemukan')
                
            api = mikrotik.get_connection()
            if not api:
                raise ValidationError('Gagal terhubung ke Mikrotik')
                
            try:
                # Aktifkan semua CPE yang terisolir
                for cpe in self.cpe_ids.filtered(lambda c: c.state == 'isolated'):
                    user_api = api.get_resource('/ppp/secret')
                    secrets = user_api.get(name=cpe.pppoe_username)
                    if secrets:
                        user_id = secrets[0].get('.id')
                        user_api.set(id=user_id, disabled='no')
                        cpe.state = 'active'
                self.state = 'active'
            except Exception as e:
                raise ValidationError(f'Gagal buka isolir: {str(e)}')
            finally:
                if api and hasattr(api, 'connection_pool'):
                    api.connection_pool.disconnect() 