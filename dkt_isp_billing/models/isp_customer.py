from odoo import models, fields, api
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

class ISPCustomer(models.Model):
    _name = 'isp.customer'
    _description = 'ISP Customer'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama Pelanggan', required=True, tracking=True)
    customer_id = fields.Char('ID Pelanggan', readonly=True, tracking=True)
    identity_number = fields.Char('No KTP', required=True, tracking=True)
    address = fields.Text('Alamat', required=True, tracking=True)
    mobile = fields.Char('No HP', required=True, tracking=True)
    email = fields.Char('Email', tracking=True)
    
    package_id = fields.Many2one('isp.package', string='Paket Layanan', tracking=True)
    cpe_ids = fields.One2many('isp.cpe', 'customer_id', string='CPE')
    installation_fee_ids = fields.One2many('isp.installation.fee', 'customer_id', string='Biaya Instalasi')
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Aktif'),
        ('isolated', 'Terisolir'),
        ('terminated', 'Terminasi')
    ], default='draft', string='Status', tracking=True)
    
    partner_id = fields.Many2one('res.partner', string='Contact', required=True, tracking=True)
    subscription_ids = fields.One2many('isp.subscription', 'customer_id', string='Subscriptions')
    subscription_count = fields.Integer(compute='_compute_subscription_count')
    subscription_template_id = fields.Many2one('isp.subscription.template', string='Template Berlangganan')
    
    mikrotik_user_id = fields.Char('Mikrotik User ID', readonly=True)
    is_adopted_secret = fields.Boolean('Is Adopted Secret', default=False, 
                                     help='Menandakan bahwa secret/user ini diadopsi dari Mikrotik yang sudah ada')
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('customer_id'):
                vals['customer_id'] = self.env['ir.sequence'].next_by_code('isp.customer.sequence')
        return super().create(vals_list)
    
    def _compute_subscription_count(self):
        for record in self:
            record.subscription_count = len(record.subscription_ids)
    
    def action_create_subscription(self):
        self.ensure_one()
        if not self.package_id:
            raise ValidationError('Pilih paket layanan terlebih dahulu!')
            
        vals = {
            'customer_id': self.id,
            'package_id': self.package_id.id,
            'recurring_interval': 1,
            'recurring_rule_type': 'monthly',
        }
        subscription = self.env['isp.subscription'].create(vals)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'isp.subscription',
            'res_id': subscription.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_subscriptions(self):
        self.ensure_one()
        return {
            'name': 'Subscriptions',
            'type': 'ir.actions.act_window',
            'res_model': 'isp.subscription',
            'view_mode': 'tree,form',
            'domain': [('customer_id', '=', self.id)],
            'context': {'default_customer_id': self.id}
        }

    def action_activate(self):
        """Aktivasi pelanggan baru"""
        self.ensure_one()
        
        # Validasi data pelanggan
        if not self.identity_number or not self.mobile or not self.address:
            raise ValidationError('Data pelanggan (KTP, No HP, Alamat) harus diisi lengkap!')
            
        # Validasi CPE
        if not self.cpe_ids:
            raise ValidationError('Minimal satu CPE harus didaftarkan!')
            
        active_cpe = self.cpe_ids.filtered(lambda c: c.active)
        if not active_cpe:
            raise ValidationError('Minimal satu CPE harus aktif!')
            
        # Validasi paket layanan
        if not self.package_id:
            raise ValidationError('Pilih paket layanan terlebih dahulu!')
            
        # Buat subscription baru
        subscription_vals = {
            'customer_id': self.id,
            'package_id': self.package_id.id,
            'recurring_interval': 1,
            'recurring_rule_type': 'monthly',
            'date_start': fields.Date.today(),
        }
        
        if self.subscription_template_id:
            subscription_vals.update({
                'template_id': self.subscription_template_id.id,
                'recurring_interval': self.subscription_template_id.recurring_interval,
                'recurring_rule_type': self.subscription_template_id.recurring_rule_type,
            })
            
        subscription = self.env['isp.subscription'].create(subscription_vals)
        
        # Buat PPPoE secret di Mikrotik untuk setiap CPE aktif
        for cpe in active_cpe:
            if not cpe.pppoe_username or not cpe.pppoe_password:
                cpe.generate_pppoe_credentials()
                
            # Buat user di Mikrotik
            mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
            if not mikrotik:
                raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
                
            api = mikrotik.get_connection()
            if not api:
                raise ValidationError('Gagal terhubung ke Mikrotik!')
                
            try:
                secret_api = api.get_resource('/ppp/secret')
                secret_data = {
                    'name': cpe.pppoe_username,
                    'password': cpe.pppoe_password,
                    'profile': self.package_id.profile_id.name,
                    'service': 'pppoe',
                }
                secret_api.add(**secret_data)
            except Exception as e:
                raise ValidationError(f'Gagal membuat PPPoE secret di Mikrotik: {str(e)}')
                
        # Update status pelanggan
        self.write({'state': 'active'})
        
        # Tampilkan notifikasi sukses
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'Pelanggan berhasil diaktifkan',
                'type': 'success',
            }
        }

    def action_isolate(self):
        """Isolir pelanggan"""
        self.ensure_one()
        if self.state != 'active':
            raise ValidationError('Hanya pelanggan aktif yang dapat diisolir!')
            
        # Nonaktifkan PPPoE secret di Mikrotik
        active_cpe = self.cpe_ids.filtered(lambda c: c.active)
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        
        if mikrotik and active_cpe:
            api = mikrotik.get_connection()
            if api:
                try:
                    secret_api = api.get_resource('/ppp/secret')
                    for cpe in active_cpe:
                        secrets = secret_api.get(name=cpe.pppoe_username)
                        if secrets:
                            secret_id = secrets[0].get('id', '')
                            if secret_id:
                                secret_api.set(id=secret_id, disabled='true')
                except Exception as e:
                    raise ValidationError(f'Gagal meng-isolir PPPoE secret di Mikrotik: {str(e)}')
                    
        self.write({'state': 'isolated'})
        return True

    def action_reactivate(self):
        """Aktifkan kembali pelanggan yang terisolir"""
        self.ensure_one()
        if self.state != 'isolated':
            raise ValidationError('Hanya pelanggan terisolir yang dapat diaktifkan kembali!')
            
        # Aktifkan kembali PPPoE secret di Mikrotik
        active_cpe = self.cpe_ids.filtered(lambda c: c.active)
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        
        if mikrotik and active_cpe:
            api = mikrotik.get_connection()
            if api:
                try:
                    secret_api = api.get_resource('/ppp/secret')
                    for cpe in active_cpe:
                        secrets = secret_api.get(name=cpe.pppoe_username)
                        if secrets:
                            secret_id = secrets[0].get('id', '')
                            if secret_id:
                                secret_api.set(id=secret_id, disabled='false')
                except Exception as e:
                    raise ValidationError(f'Gagal mengaktifkan PPPoE secret di Mikrotik: {str(e)}')
                    
        self.write({'state': 'active'})
        return True

    def action_terminate(self):
        """Terminasi pelanggan"""
        self.ensure_one()
        if self.state not in ['active', 'isolated']:
            raise ValidationError('Hanya pelanggan aktif atau terisolir yang dapat diterminasi!')
            
        # Hapus PPPoE secret di Mikrotik
        active_cpe = self.cpe_ids.filtered(lambda c: c.active)
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        
        if mikrotik and active_cpe:
            api = mikrotik.get_connection()
            if api:
                try:
                    secret_api = api.get_resource('/ppp/secret')
                    for cpe in active_cpe:
                        secrets = secret_api.get(name=cpe.pppoe_username)
                        if secrets:
                            secret_id = secrets[0].get('id', '')
                            if secret_id:
                                secret_api.remove(id=secret_id)
                except Exception as e:
                    raise ValidationError(f'Gagal menghapus PPPoE secret di Mikrotik: {str(e)}')
                    
        # Nonaktifkan subscription
        active_subs = self.subscription_ids.filtered(lambda s: s.state == 'active')
        active_subs.write({'state': 'terminated'})
        
        self.write({'state': 'terminated'})
        return True

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
            user_api = api.get_resource('/ppp/secret')
            
            # Cek apakah user sudah ada di Mikrotik
            try:
                existing_user = user_api.get(name=cpe.pppoe_username)
            except Exception as e:
                existing_user = []
                
            if existing_user and len(existing_user) > 0:
                user_id = existing_user[0].get('.id')
                if user_id:
                    # Cek apakah secret sudah digunakan oleh pelanggan lain di Odoo
                    existing_customer = self._check_existing_mikrotik_user(cpe.pppoe_username, user_id)
                    if existing_customer:
                        raise ValidationError(
                            f'Secret/User PPPoE {cpe.pppoe_username} sudah digunakan oleh pelanggan: '
                            f'{existing_customer.name} ({existing_customer.customer_id})\n'
                            f'Silahkan gunakan username PPPoE yang lain.'
                        )
                    
                    # Jika belum digunakan di Odoo, tampilkan wizard konfirmasi adopsi
                    return {
                        'type': 'ir.actions.act_window',
                        'name': 'Konfirmasi Adopsi Secret',
                        'res_model': 'isp.adopt.secret.wizard',
                        'view_mode': 'form',
                        'target': 'new',
                        'context': {
                            'default_customer_id': self.id,
                            'default_secret_name': cpe.pppoe_username,
                            'default_secret_id': user_id,
                        }
                    }
            
            # Jika user belum ada di Mikrotik, buat baru
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
                    self.write({
                        'mikrotik_user_id': result[0].get('.id'),
                        'state': 'active'
                    })
                    if self.cpe_ids:
                        self.cpe_ids[0].state = 'active'
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
                if 'already exists' in str(e):
                    # Jika error karena secret sudah ada, coba cek lagi dan tampilkan wizard adopsi
                    try:
                        existing_user = user_api.get(name=cpe.pppoe_username)
                        if existing_user and len(existing_user) > 0:
                            user_id = existing_user[0].get('.id')
                            if user_id:
                                existing_customer = self._check_existing_mikrotik_user(cpe.pppoe_username, user_id)
                                if existing_customer:
                                    raise ValidationError(
                                        f'Secret/User PPPoE {cpe.pppoe_username} sudah digunakan oleh pelanggan: '
                                        f'{existing_customer.name} ({existing_customer.customer_id})\n'
                                        f'Silahkan gunakan username PPPoE yang lain.'
                                    )
                                
                                return {
                                    'type': 'ir.actions.act_window',
                                    'name': 'Konfirmasi Adopsi Secret',
                                    'res_model': 'isp.adopt.secret.wizard',
                                    'view_mode': 'form',
                                    'target': 'new',
                                    'context': {
                                        'default_customer_id': self.id,
                                        'default_secret_name': cpe.pppoe_username,
                                        'default_secret_id': user_id,
                                    }
                                }
                    except Exception as e2:
                        _logger.error(f'Error checking existing secret after add failed: {str(e2)}')
                raise ValidationError(f'Gagal membuat user PPPoE di Mikrotik: {str(e)}')
        except Exception as e:
            raise ValidationError(f'Gagal membuat user PPPoE di Mikrotik: {str(e)}')
        finally:
            if api:
                try:
                    api.disconnect()
                except:
                    pass 