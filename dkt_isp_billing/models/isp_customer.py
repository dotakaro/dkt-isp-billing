from odoo import models, fields, api
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

class ISPCustomer(models.Model):
    _name = 'isp.customer'
    _description = 'Customer'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama', required=True, tracking=True)
    customer_id = fields.Char('ID Pelanggan', readonly=True, copy=False)
    identity_number = fields.Char('No KTP', tracking=True)
    mobile = fields.Char('No HP', tracking=True)
    email = fields.Char('Email', tracking=True)
    address = fields.Text('Alamat', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Contact', tracking=True)
    subscription_template_id = fields.Many2one('isp.subscription.template', string='Template Berlangganan')
    
    cpe_ids = fields.One2many('isp.cpe', 'customer_id', string='CPE')
    installation_fee_ids = fields.One2many('isp.installation.fee', 'customer_id', string='Biaya Instalasi')
    subscription_ids = fields.One2many('isp.subscription', 'customer_id', string='Subscription')
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('isolated', 'Terisolir'),
        ('terminated', 'Terminasi')
    ], string='Status', default='draft', tracking=True)
    
    active = fields.Boolean('Active', default=True)
    is_adopted_secret = fields.Boolean('Is Adopted Secret', default=False)
    
    # Computed fields untuk status
    cpe_count = fields.Integer(compute='_compute_cpe_stats', string='Total CPE')
    active_cpe_count = fields.Integer(compute='_compute_cpe_stats', string='CPE Aktif')
    subscription_count = fields.Integer(compute='_compute_subscription_stats', string='Total Subscription')
    active_subscription_count = fields.Integer(compute='_compute_subscription_stats', string='Subscription Aktif')
    
    # Status detail untuk ditampilkan di form
    cpe_status_details = fields.Text(compute='_compute_status_details', string='Status Detail')
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('customer_id'):
                vals['customer_id'] = self.env['ir.sequence'].next_by_code('isp.customer.sequence')
        return super().create(vals_list)
    
    @api.depends('cpe_ids', 'cpe_ids.state')
    def _compute_cpe_stats(self):
        for record in self:
            record.cpe_count = len(record.cpe_ids)
            record.active_cpe_count = len(record.cpe_ids.filtered(lambda c: c.state == 'open'))
    
    @api.depends('subscription_ids', 'subscription_ids.state')
    def _compute_subscription_stats(self):
        for record in self:
            record.subscription_count = len(record.subscription_ids)
            record.active_subscription_count = len(record.subscription_ids.filtered(lambda s: s.state == 'open'))
    
    @api.depends('cpe_ids', 'cpe_ids.state', 'cpe_ids.subscription_ids', 'cpe_ids.subscription_ids.state')
    def _compute_status_details(self):
        for record in self:
            details = []
            for cpe in record.cpe_ids:
                active_subs = cpe.subscription_ids.filtered(lambda s: s.state == 'open')
                details.append(f"CPE: {cpe.name} ({cpe.state})")
                if active_subs:
                    for sub in active_subs:
                        details.append(f"  └ {sub.package_id.name} ({sub.state})")
                else:
                    details.append("  └ Tidak ada subscription aktif")
            record.cpe_status_details = '\n'.join(details) if details else 'Tidak ada CPE'
    
    def action_create_subscription(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'isp.subscription',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_customer_id': self.id,
            }
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
        """Aktivasi pelanggan"""
        self.ensure_one()
        
        # Validasi data pelanggan
        if not self.identity_number or not self.mobile or not self.address:
            raise ValidationError('Data pelanggan (KTP, No HP, Alamat) harus diisi lengkap!')
            
        # Validasi CPE
        if not self.active_cpe_count:
            raise ValidationError('Pelanggan harus memiliki minimal satu CPE aktif!')
            
        # Validasi Subscription
        if not self.active_subscription_count:
            raise ValidationError('Pelanggan harus memiliki minimal satu subscription aktif!')
            
        self.write({'state': 'open'})
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
        if self.state != 'open':
            raise ValidationError('Hanya pelanggan open yang dapat diisolir!')
            
        # Isolir semua subscription aktif
        active_subs = self.subscription_ids.filtered(lambda s: s.state == 'open')
        for sub in active_subs:
            sub.action_isolate()
            
        self.write({'state': 'isolated'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'Pelanggan berhasil diisolir',
                'type': 'success',
            }
        }

    def action_terminate(self):
        """Terminasi pelanggan"""
        self.ensure_one()
        if self.state not in ['open', 'isolated']:
            raise ValidationError('Hanya pelanggan open atau terisolir yang dapat diterminasi!')
            
        # Cancel semua subscription
        active_subs = self.subscription_ids.filtered(lambda s: s.state in ['open', 'isolated'])
        for sub in active_subs:
            sub.action_terminate()
            
        # Non-aktifkan semua CPE
        active_cpes = self.cpe_ids.filtered(lambda c: c.state in ['open', 'isolated'])
        for cpe in active_cpes:
            cpe.action_terminate()
            
        self.write({'state': 'terminated'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'Pelanggan berhasil diterminasi',
                'type': 'success',
            }
        }

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
        if not self.cpe_ids:
            raise ValidationError('Pelanggan harus memiliki minimal satu CPE!')
            
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
                    profile=cpe.profile_id.name,
                    password=cpe.pppoe_password,
                    service='pppoe',
                    remote_address=cpe.ip_address,
                    comment=f'Customer: {self.name} - {self.customer_id}',
                    disabled='no'
                )
                self.write({
                    'mikrotik_user_id': user_id,
                    'state': 'open',
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
        if not self.cpe_ids:
            raise ValidationError('Pelanggan harus memiliki minimal satu CPE!')
            
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
                    profile=cpe.profile_id.name,
                    remote_address=cpe.ip_address,
                    comment=f'Customer: {self.name} - {self.customer_id}',
                    disabled='no'
                )
                
                if isinstance(result, list) and len(result) > 0:
                    self.write({
                        'mikrotik_user_id': result[0].get('.id'),
                        'state': 'open'
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