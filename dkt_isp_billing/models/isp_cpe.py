from odoo import models, fields, api
from odoo.exceptions import ValidationError
import random
import string
import logging

_logger = logging.getLogger(__name__)

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
    notes = fields.Text('Catatan', tracking=True)
    
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

    def _check_mikrotik_secret(self):
        """
        Mengecek apakah secret/user sudah ada di Mikrotik
        Returns: (exists, user_id, error_message)
        """
        self.ensure_one()
        if not self.pppoe_username:
            return False, False, 'PPPoE Username belum diisi!'
            
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            return False, False, 'Konfigurasi Mikrotik tidak ditemukan!'
            
        api = mikrotik.get_connection()
        if not api:
            return False, False, 'Gagal terhubung ke Mikrotik!'
            
        try:
            user_api = api.get_resource('/ppp/secret')
            _logger.info(f'Checking secret for username: {self.pppoe_username}')
            
            # Coba get dengan exact match
            try:
                existing_user = user_api.get(name=self.pppoe_username)
                _logger.info(f'Found exact match: {existing_user}')
            except Exception as e:
                _logger.error(f'Error getting exact match: {str(e)}')
                # Jika error, coba dengan print dan filter manual
                try:
                    all_secrets = user_api.get()
                    existing_user = [s for s in all_secrets if s.get('name') == self.pppoe_username]
                    _logger.info(f'Found after manual filter: {existing_user}')
                except Exception as e2:
                    _logger.error(f'Error getting all secrets: {str(e2)}')
                    return False, False, str(e2)
            
            if existing_user and len(existing_user) > 0:
                # Coba dapatkan ID dengan beberapa kemungkinan key
                user_id = None
                for key in ['.id', 'id', 'ret']:
                    user_id = existing_user[0].get(key)
                    if user_id:
                        break
                
                _logger.info(f'Found user_id: {user_id}')
                if user_id:
                    # Cek apakah secret sudah digunakan oleh pelanggan lain di Odoo
                    existing_customer = self.env['isp.customer'].search([
                        ('mikrotik_user_id', '=', user_id),
                        ('id', '!=', self.customer_id.id if self.customer_id else False)
                    ], limit=1)
                    
                    if existing_customer:
                        return True, user_id, f'Secret/User PPPoE {self.pppoe_username} sudah digunakan oleh pelanggan: {existing_customer.name} ({existing_customer.customer_id})'
                    
                    # Secret ada tapi belum digunakan siapa-siapa
                    return True, user_id, False
                    
            return False, False, False
            
        except Exception as e:
            _logger.error(f'Error checking secret: {str(e)}')
            return False, False, str(e)
        finally:
            if api:
                try:
                    api.disconnect()
                except:
                    pass

    def adopt_mikrotik_secret(self):
        """
        Mengadopsi secret/user yang sudah ada di Mikrotik
        Returns: True jika berhasil, False jika gagal
        """
        self.ensure_one()
        if not self.customer_id or not self.customer_id.package_id:
            raise ValidationError('Pelanggan dan Paket harus diisi terlebih dahulu!')
            
        exists, user_id, error = self._check_mikrotik_secret()
        _logger.info(f'Checking before adopt: exists={exists}, user_id={user_id}, error={error}')
        
        if not exists or not user_id:
            return False
            
        if error:
            raise ValidationError(error)
            
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
            
        api = mikrotik.get_connection()
        if not api:
            raise ValidationError('Gagal terhubung ke Mikrotik!')
            
        try:
            user_api = api.get_resource('/ppp/secret')
            
            # Pastikan secret masih ada
            try:
                existing_user = user_api.get(name=self.pppoe_username)
                if not existing_user or len(existing_user) == 0:
                    raise ValidationError(f'Secret {self.pppoe_username} tidak ditemukan di Mikrotik')
            except Exception as e:
                _logger.error(f'Error verifying secret before update: {str(e)}')
                raise ValidationError(f'Gagal memverifikasi secret: {str(e)}')
            
            # Update profile dan informasi lainnya
            _logger.info(f'Updating secret {self.pppoe_username} with ID {user_id}')
            update_data = {
                'id': user_id,
                'profile': self.customer_id.package_id.profile_id.name,
                'password': self.pppoe_password,
                'service': 'pppoe',
                'comment': f'Customer: {self.customer_id.name} - {self.customer_id.customer_id}',
                'disabled': 'no'
            }
            
            if self.ip_address:
                update_data['remote-address'] = self.ip_address
                
            _logger.info(f'Update data: {update_data}')
            user_api.set(**update_data)
            
            # Update status pelanggan
            customer_vals = {
                'mikrotik_user_id': user_id,
                'state': 'active',
                'is_adopted_secret': True
            }
            _logger.info(f'Updating customer with: {customer_vals}')
            self.env.cr.commit()  # Commit transaksi sebelum update customer
            self.customer_id.with_context(force_update=True).write(customer_vals)
            
            # Update status CPE
            cpe_vals = {'state': 'active'}
            _logger.info(f'Updating CPE with: {cpe_vals}')
            self.write(cpe_vals)
            
            # Verifikasi update status
            self.env.cr.commit()  # Commit transaksi setelah semua update
            
            # Reload record untuk memastikan perubahan tersimpan
            self.customer_id.invalidate_cache()
            self.invalidate_cache()
            
            _logger.info(f'Status after update - Customer: {self.customer_id.state}, Is Adopted: {self.customer_id.is_adopted_secret}, CPE: {self.state}')
            
            return True
            
        except Exception as e:
            _logger.error(f'Error adopting secret: {str(e)}')
            raise ValidationError(f'Gagal mengadopsi secret: {str(e)}')
        finally:
            if api:
                try:
                    api.disconnect()
                except:
                    pass 