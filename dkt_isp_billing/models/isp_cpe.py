from odoo import models, fields, api
from odoo.exceptions import ValidationError
import random
import string
import logging

_logger = logging.getLogger(__name__)

class ISPCPE(models.Model):
    _name = 'isp.cpe'
    _description = 'Customer Premise Equipment'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama Perangkat', required=True, tracking=True)
    customer_id = fields.Many2one('isp.customer', string='Pelanggan', required=True, tracking=True)
    mac_address = fields.Char('MAC Address', tracking=True)
    ip_address = fields.Char('IP Address', tracking=True)
    outdoor_unit = fields.Char('Outdoor Unit')
    router = fields.Char('Router')
    pppoe_username = fields.Char('PPPoE Username', required=True, tracking=True)
    pppoe_password = fields.Char('PPPoE Password', required=True, tracking=True)
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
    
    _sql_constraints = [
        ('mac_address_uniq', 'unique(mac_address)', 'MAC Address harus unik!'),
        ('pppoe_username_uniq', 'unique(pppoe_username)', 'PPPoE Username harus unik!'),
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

    def generate_pppoe_credentials(self):
        """Generate PPPoE username dan password"""
        self.ensure_one()
        if self.pppoe_username and self.pppoe_password:
            raise ValidationError('PPPoE credentials sudah ada!')
            
        # Generate username dari nama pelanggan
        if not self.pppoe_username:
            base_username = self.customer_id.name.lower().replace(' ', '-')
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
            return False, f'Secret {self.pppoe_username} sudah dipakai oleh CPE {existing_cpe.name} (Customer: {existing_cpe.customer_id.name}). Silakan gunakan username PPPoE yang lain.'
            
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
                'comment': f'Customer: {self.customer_id.name} ({self.customer_id.customer_id})',
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
            
        if self.subscription_id:
            self.subscription_id.action_terminate()
            
        # Hapus secret di Mikrotik
        exists, user_id, error, secret_data = self._check_mikrotik_secret()
        if error:
            raise ValidationError(error)
            
        if exists:
            mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
            if not mikrotik:
                raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
                
            api = mikrotik.get_connection()
            if not api:
                raise ValidationError('Gagal terhubung ke Mikrotik!')
                
            try:
                secret_api = api.get_resource('/ppp/secret')
                secret_api.remove(id=user_id)
            except Exception as e:
                raise ValidationError(f'Gagal menghapus secret di Mikrotik: {str(e)}')
            finally:
                api.disconnect()
                
        self.write({'state': 'terminated'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'CPE berhasil diterminasi',
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
                'default_customer_id': self.customer_id.id,
                'default_cpe_id': self.id
            }
        } 