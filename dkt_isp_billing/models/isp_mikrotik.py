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
    host = fields.Char('Host/IP', required=True, tracking=True)
    port = fields.Integer('Port', default=8728, tracking=True)
    username = fields.Char('Username', required=True, tracking=True)
    password = fields.Char('Password', required=True, tracking=True)
    active = fields.Boolean('Active', default=True, tracking=True)

    def test_connection(self):
        self.ensure_one()
        try:
            connection = routeros_api.RouterOsApiPool(
                host=self.host,
                username=self.username,
                password=self.password,
                port=self.port,
                plaintext_login=True
            )
            api = connection.get_api()
            api.get_resource('/system/identity').get()
            connection.disconnect()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sukses',
                    'message': 'Koneksi ke Mikrotik berhasil',
                    'type': 'success',
                }
            }
        except Exception as e:
            raise ValidationError(f'Koneksi gagal: {str(e)}')

    def get_connection(self):
        self.ensure_one()
        try:
            connection = routeros_api.RouterOsApiPool(
                host=self.host,
                username=self.username,
                password=self.password,
                port=self.port,
                plaintext_login=True
            )
            return connection.get_api()
        except Exception as e:
            _logger.error(f'Mikrotik connection error: {str(e)}')
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
    
    def _create_mikrotik_user(self):
        self.ensure_one()
        if not self.package_id or not self.cpe_ids:
            raise ValidationError('Paket dan CPE harus diisi terlebih dahulu!')
            
        # Pastikan CPE memiliki PPPoE credentials
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
            # Buat user di Mikrotik
            user_api = api.get_resource('/ppp/secret')
            result = user_api.add(
                name=cpe.pppoe_username,
                password=cpe.pppoe_password,
                service='pppoe',
                profile=self.package_id.profile_id.name,
                remote_address=cpe.ip_address,
                comment=f'Customer: {self.name} - {self.customer_id}'
            )
            
            # Cek hasil penambahan user
            if isinstance(result, list) and len(result) > 0:
                # Simpan ID dari user yang baru dibuat
                self.mikrotik_user_id = result[0].get('.id')
                return True
            return False
            
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
        self.ensure_one()
        if self.state == 'draft':
            if self._create_mikrotik_user():
                self.state = 'active'
                # Aktifkan juga CPE
                if self.cpe_ids:
                    self.cpe_ids[0].state = 'active'

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