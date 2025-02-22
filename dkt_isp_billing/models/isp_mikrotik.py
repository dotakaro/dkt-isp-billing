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
            return connection.get_api()
                
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

    @api.model
    def get_default_config(self):
        """Mendapatkan konfigurasi Mikrotik default (aktif)"""
        return self.search([('active', '=', True)], limit=1)

class ISPCustomer(models.Model):
    _inherit = 'isp.customer'

    mikrotik_user_id = fields.Char('Mikrotik User ID', readonly=True)
    is_adopted_secret = fields.Boolean('Is Adopted Secret', default=False, help='Menandakan bahwa secret/user ini diadopsi dari Mikrotik yang sudah ada')
    
    def _check_existing_mikrotik_user(self, username, user_id):
        """
        Mengecek apakah user Mikrotik sudah terkait dengan pelanggan lain
        Returns: customer yang menggunakan secret tersebut atau False
        """
        existing_customer = self.search([
            ('mikrotik_user_id', '=', user_id),
            ('id', '!=', self.id)
        ], limit=1)
        return existing_customer

    def action_adopt_secret(self):
        """
        Mengadopsi secret/user yang sudah ada di Mikrotik
        """
        self.ensure_one()
        if not self.package_id or not self.cpe_ids:
            raise ValidationError('Paket dan CPE harus diisi terlebih dahulu!')
            
        cpe = self.cpe_ids[0]
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
            
        api = mikrotik.get_connection()
        if not api:
            raise ValidationError('Gagal terhubung ke Mikrotik!')
            
        try:
            user_api = api.get_resource('/ppp/secret')
            existing_user = user_api.get(name=cpe.pppoe_username)
            
            if existing_user and len(existing_user) > 0:
                user_id = existing_user[0].get('.id')
                # Update profile dan informasi lainnya
                user_api.set(
                    id=user_id,
                    profile=self.package_id.profile_id.name,
                    password=cpe.pppoe_password,
                    service='pppoe',
                    remote_address=cpe.ip_address,
                    comment=f'Customer: {self.name} - {self.customer_id}',
                    disabled='no'
                )
                self.write({
                    'mikrotik_user_id': user_id,
                    'state': 'active',
                    'is_adopted_secret': True
                })
                if self.cpe_ids:
                    self.cpe_ids[0].state = 'active'
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Sukses',
                        'message': f'Secret/User PPPoE {cpe.pppoe_username} berhasil diadopsi',
                        'type': 'success',
                    }
                }
        except Exception as e:
            raise ValidationError(f'Gagal mengadopsi secret: {str(e)}')
        finally:
            if api:
                try:
                    api.disconnect()
                except:
                    pass

    def _create_mikrotik_user(self):
        self.ensure_one()
        if not self.package_id or not self.cpe_ids:
            raise ValidationError('Paket dan CPE harus diisi terlebih dahulu!')
            
        cpe = self.cpe_ids[0]
        if not cpe.pppoe_username or not cpe.pppoe_password:
            raise ValidationError('PPPoE Username dan Password harus diisi di CPE!')
            
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
            
        api = mikrotik.get_connection()
        if not api:
            raise ValidationError('Gagal terhubung ke Mikrotik!')
            
        try:
            # Cek apakah secret sudah ada di Mikrotik
            exists, user_id, error = cpe._check_mikrotik_secret()
            _logger.info(f'Checking secret {cpe.pppoe_username}: exists={exists}, user_id={user_id}, error={error}')
            
            if exists:
                if error:
                    raise ValidationError(error)
                # Adopsi secret yang ada
                if cpe.adopt_mikrotik_secret():
                    # Verifikasi status setelah adopsi
                    self.env.cr.commit()
                    self.invalidate_cache()
                    cpe.invalidate_cache()
                    
                    _logger.info(f'Status after adopt - Customer: {self.state}, Is Adopted: {self.is_adopted_secret}, CPE: {cpe.state}')
                    
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Sukses',
                            'message': f'Secret/User PPPoE {cpe.pppoe_username} berhasil diadopsi',
                            'type': 'success',
                        }
                    }
                else:
                    raise ValidationError(f'Gagal mengadopsi secret {cpe.pppoe_username}')
            
            # Jika secret belum ada, buat baru
            user_api = api.get_resource('/ppp/secret')
            _logger.info(f'Creating new PPPoE secret for {cpe.pppoe_username}')
            try:
                result = user_api.add(
                    name=cpe.pppoe_username,
                    password=cpe.pppoe_password,
                    service='pppoe',
                    profile=self.package_id.profile_id.name,
                    remote_address=cpe.ip_address,
                    comment=f'Customer: {self.name} - {self.customer_id}',
                    disabled='no'
                )
                
                if isinstance(result, list) and len(result) > 0:
                    # Update status pelanggan
                    vals = {
                        'mikrotik_user_id': result[0].get('ret'),
                        'state': 'active'
                    }
                    _logger.info(f'Updating customer status after create: {vals}')
                    self.with_context(force_update=True).write(vals)
                    
                    # Update status CPE
                    if self.cpe_ids:
                        self.cpe_ids[0].write({'state': 'active'})
                        
                    # Verifikasi status
                    self.env.cr.commit()
                    self.invalidate_cache()
                    cpe.invalidate_cache()
                    
                    _logger.info(f'Status after create - Customer: {self.state}, CPE: {cpe.state}')
                    
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Sukses',
                            'message': f'User PPPoE {cpe.pppoe_username} berhasil dibuat',
                            'type': 'success',
                        }
                    }
                return False
                
            except Exception as e:
                _logger.error(f'Error creating PPPoE secret: {str(e)}')
                if 'already exists' in str(e).lower():
                    # Jika error karena secret sudah ada, coba adopsi lagi
                    exists, user_id, error = cpe._check_mikrotik_secret()
                    if exists:
                        if error:
                            raise ValidationError(error)
                        if cpe.adopt_mikrotik_secret():
                            # Verifikasi status setelah adopsi
                            self.env.cr.commit()
                            self.invalidate_cache()
                            cpe.invalidate_cache()
                            
                            _logger.info(f'Status after retry adopt - Customer: {self.state}, Is Adopted: {self.is_adopted_secret}, CPE: {cpe.state}')
                            
                            return {
                                'type': 'ir.actions.client',
                                'tag': 'display_notification',
                                'params': {
                                    'title': 'Sukses',
                                    'message': f'Secret/User PPPoE {cpe.pppoe_username} berhasil diadopsi',
                                    'type': 'success',
                                }
                            }
                raise ValidationError(f'Gagal membuat user di Mikrotik: {str(e)}')
            
        except Exception as e:
            _logger.error(f'Create Mikrotik user error: {str(e)}')
            raise ValidationError(f'Gagal membuat user di Mikrotik: {str(e)}')
        finally:
            if api:
                try:
                    api.disconnect()
                except:
                    pass

    def action_activate(self):
        """Aktivasi pelanggan"""
        self.ensure_one()
        if self.state == 'draft':
            return self._create_mikrotik_user()

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
                user_api = api.get_resource('/ppp/secret')
                user_api.set(id=self.mikrotik_user_id, disabled='yes')
                self.state = 'isolated'
            except Exception as e:
                raise ValidationError(f'Gagal isolir: {str(e)}')
            finally:
                api.disconnect()

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
                user_api = api.get_resource('/ppp/secret')
                user_api.set(id=self.mikrotik_user_id, disabled='no')
                self.state = 'active'
            except Exception as e:
                raise ValidationError(f'Gagal buka isolir: {str(e)}')
            finally:
                api.disconnect() 