from odoo import models, fields, api
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Fields untuk ISP
    identity_number = fields.Char('Nomor Identitas')
    cpe_ids = fields.One2many('isp.cpe', 'partner_id', string='CPE')
    cpe_count = fields.Integer('Jumlah CPE', compute='_compute_cpe_count')
    active_cpe_count = fields.Integer('CPE Aktif', compute='_compute_cpe_count')
    subscription_ids = fields.One2many('isp.subscription', 'partner_id', string='Subscription')
    subscription_count = fields.Integer('Jumlah Subscription', compute='_compute_subscription_count')
    active_subscription_count = fields.Integer('Subscription Aktif', compute='_compute_subscription_count')
    invoice_ids = fields.One2many('account.move', 'partner_id', string='Invoice', domain=[('move_type', '=', 'out_invoice')])
    invoice_count = fields.Integer('Jumlah Invoice', compute='_compute_invoice_count')
    invoice_draft_count = fields.Integer('Invoice Draft', compute='_compute_invoice_count')
    total_outstanding = fields.Monetary('Total Outstanding', compute='_compute_total_outstanding')
    emergency_contact = fields.Char('Kontak Darurat')
    emergency_phone = fields.Char('Telepon Darurat')
    notes = fields.Text('Catatan')
    is_adopted_secret = fields.Boolean('Is Adopted Secret', default=False, help='Menandakan bahwa secret/user ini diadopsi dari Mikrotik yang sudah ada')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Aktif'),
        ('inactive', 'Non-Aktif'),
        ('isolated', 'Terisolir'),
        ('terminated', 'Terminasi')
    ], string='Status', default='draft', tracking=True)

    @api.depends('cpe_ids', 'cpe_ids.state')
    def _compute_cpe_count(self):
        for record in self:
            record.cpe_count = len(record.cpe_ids)
            record.active_cpe_count = len(record.cpe_ids.filtered(lambda c: c.state == 'open'))

    @api.depends('subscription_ids', 'subscription_ids.state')
    def _compute_subscription_count(self):
        for record in self:
            record.subscription_count = len(record.subscription_ids)
            record.active_subscription_count = len(record.subscription_ids.filtered(lambda s: s.state == 'open'))

    @api.depends('invoice_ids', 'invoice_ids.state')
    def _compute_invoice_count(self):
        for record in self:
            record.invoice_count = len(record.invoice_ids)
            record.invoice_draft_count = len(record.invoice_ids.filtered(lambda i: i.state == 'draft'))

    @api.depends('invoice_ids')
    def _compute_total_outstanding(self):
        for record in self:
            record.total_outstanding = sum(record.invoice_ids.filtered(lambda i: i.state == 'posted' and i.payment_state != 'paid').mapped('amount_residual'))

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

    def action_view_subscriptions(self):
        """Tampilkan subscription pelanggan"""
        self.ensure_one()
        return {
            'name': 'Subscriptions',
            'type': 'ir.actions.act_window',
            'res_model': 'isp.subscription',
            'view_mode': 'tree,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id}
        }

    def action_view_invoices(self):
        """Tampilkan invoice pelanggan"""
        self.ensure_one()
        return {
            'name': 'Invoices',
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'tree,form',
            'domain': [('partner_id', '=', self.id), ('move_type', '=', 'out_invoice')],
            'context': {'default_partner_id': self.id, 'default_move_type': 'out_invoice'}
        }

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
            mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
            if not mikrotik:
                return False, 'Konfigurasi Mikrotik tidak ditemukan'
                
            api = mikrotik.get_connection()
            if not api:
                return False, 'Gagal terhubung ke Mikrotik'
                
            secret_api = api.get_resource('/ppp/secret')
            secret_data = {
                'name': cpe.pppoe_username,
                'password': cpe.pppoe_password,
                'service': 'pppoe',
                'profile': cpe.subscription_id.package_id.profile_id.name if cpe.subscription_id else 'default',
                'comment': f'Customer: {cpe.partner_id.name}'
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