from odoo import models, fields, api
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError, ValidationError
import logging
import routeros_api

_logger = logging.getLogger(__name__)

class ISPSubscription(models.Model):
    _name = 'isp.subscription'
    _description = 'ISP Subscription'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nomor', readonly=True, copy=False)
    customer_id = fields.Many2one('isp.customer', string='Pelanggan', required=True, tracking=True)
    partner_id = fields.Many2one(related='customer_id.partner_id', store=True)
    cpe_id = fields.Many2one('isp.cpe', string='CPE', required=True, tracking=True,
                          domain="[('customer_id', '=', customer_id), ('state', 'in', ['draft', 'active'])]")
    package_id = fields.Many2one('isp.package', string='Paket', required=True, tracking=True)
    
    date_start = fields.Date('Tanggal Mulai', default=fields.Date.today, required=True, tracking=True)
    due_day = fields.Integer('Tanggal Jatuh Tempo', default=1, required=True, tracking=True,
                          help="Tanggal jatuh tempo setiap bulannya (1-31)")
    next_invoice_date = fields.Date('Tanggal Tagihan Berikutnya', compute='_compute_next_invoice_date', store=True)
    last_invoice_date = fields.Date('Tanggal Tagihan Terakhir')
    
    recurring_interval = fields.Integer('Interval Penagihan', default=1, required=True)
    recurring_rule_type = fields.Selection([
        ('monthly', 'Bulanan'),
        ('quarterly', 'Triwulan'),
    ], string='Tipe Penagihan', default='monthly', required=True)
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('isolated', 'Terisolir'),
        ('terminated', 'Terminasi')
    ], string='Status', default='draft', tracking=True)
    
    amount = fields.Float('Jumlah Tagihan', related='package_id.price', store=True)
    
    # Fields untuk diskon
    discount_id = fields.Many2one('isp.discount', string='Diskon', tracking=True)
    discount_type = fields.Selection(related='discount_id.type', string='Tipe Diskon', readonly=True)
    discount_value = fields.Float(related='discount_id.value', string='Nilai Diskon', readonly=True)
    discount_amount = fields.Float(
        'Jumlah Diskon', 
        compute='_compute_discount_amount',
        store=True,
        help="Jumlah diskon yang diberikan"
    )
    final_amount = fields.Float(
        'Total Setelah Diskon',
        compute='_compute_discount_amount',
        store=True,
        help="Jumlah tagihan setelah diskon"
    )
    
    last_notification_date = fields.Date('Tanggal Notifikasi Terakhir')
    mikrotik_user = fields.Char('Mikrotik Username', related='cpe_id.pppoe_username', readonly=True)
    
    # Fields untuk invoice
    invoice_ids = fields.One2many('account.move', 'subscription_id', string='Invoice', domain=[('move_type', '=', 'out_invoice')])
    invoice_count = fields.Integer(string='Jumlah Invoice', compute='_compute_invoice_count')
    
    # Fields untuk invoice tracking
    invoice_draft_count = fields.Integer(string='Invoice Draft', compute='_compute_invoice_stats')
    invoice_posted_count = fields.Integer(string='Invoice Posted', compute='_compute_invoice_stats')
    invoice_paid_count = fields.Integer(string='Invoice Terbayar', compute='_compute_invoice_stats')
    invoice_overdue_count = fields.Integer(string='Invoice Menunggak', compute='_compute_invoice_stats')
    total_unpaid_amount = fields.Float(string='Total Tunggakan', compute='_compute_invoice_stats')
    
    _sql_constraints = [
        ('unique_active_cpe', 'unique(cpe_id,state)',
         'CPE ini sudah memiliki subscription aktif!')
    ]
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].next_by_code('isp.subscription.sequence')
        return super().create(vals_list)
    
    @api.constrains('cpe_id', 'state')
    def _check_active_subscription(self):
        for record in self:
            if record.state == 'open':
                active_subs = self.search([
                    ('cpe_id', '=', record.cpe_id.id),
                    ('state', '=', 'open'),
                    ('id', '!=', record.id)
                ])
                if active_subs:
                    raise ValidationError(f'CPE {record.cpe_id.name} sudah memiliki subscription open!')
    
    @api.constrains('due_day')
    def _check_due_day(self):
        for record in self:
            if record.due_day < 1 or record.due_day > 31:
                raise ValidationError('Tanggal jatuh tempo harus antara 1-31!')
    
    @api.depends('date_start', 'recurring_interval', 'recurring_rule_type', 'last_invoice_date', 'due_day')
    def _compute_next_invoice_date(self):
        for record in self:
            if record.last_invoice_date:
                base_date = record.last_invoice_date
            else:
                base_date = record.date_start
                
            # Hitung bulan berikutnya
            if record.recurring_rule_type == 'monthly':
                next_date = base_date + relativedelta(months=record.recurring_interval)
            elif record.recurring_rule_type == 'quarterly':
                next_date = base_date + relativedelta(months=3*record.recurring_interval)
                
            # Sesuaikan tanggal jatuh tempo
            try:
                record.next_invoice_date = next_date.replace(day=record.due_day)
            except ValueError:  # Untuk bulan dengan tanggal < 31
                # Jika tanggal jatuh tempo > hari dalam bulan, gunakan hari terakhir bulan
                record.next_invoice_date = next_date + relativedelta(day=31)

    @api.depends('amount', 'discount_id', 'discount_type', 'discount_value')
    def _compute_discount_amount(self):
        for record in self:
            if not record.discount_id:
                record.discount_amount = 0.0
                record.final_amount = record.amount
            else:
                if record.discount_type == 'percentage':
                    record.discount_amount = record.amount * (record.discount_value / 100)
                else:  # fixed
                    record.discount_amount = record.discount_value
                record.final_amount = record.amount - record.discount_amount

    def _prepare_invoice_values(self):
        """Menyiapkan nilai untuk pembuatan invoice dengan diskon"""
        self.ensure_one()
        
        # Dapatkan jurnal penjualan
        sale_journal = self.env['account.journal'].search([('type', '=', 'sale')], limit=1)
        if not sale_journal:
            raise ValidationError('Tidak ditemukan jurnal penjualan. Silakan buat jurnal penjualan terlebih dahulu.')
            
        # Dapatkan akun pendapatan dari produk/paket
        income_account = self.package_id.product_id.property_account_income_id or \
                        self.package_id.product_id.categ_id.property_account_income_categ_id
        if not income_account:
            raise ValidationError('Tidak ditemukan akun pendapatan pada produk/paket. Silakan atur akun pendapatan pada produk atau kategori produk.')
        
        # Siapkan line invoice utama
        invoice_line = {
            'name': f'Subscription {self.name} - {self.package_id.name}',
            'quantity': 1,
            'price_unit': self.amount,
            'account_id': income_account.id,
        }
        
        # Jika ada diskon, tambahkan line diskon
        if self.discount_id and self.discount_amount > 0:
            invoice_line_discount = {
                'name': f'Diskon: {self.discount_id.name}',
                'quantity': 1,
                'price_unit': -self.discount_amount,  # Nilai negatif untuk pengurangan
                'account_id': self.discount_id.account_id.id,
            }
            invoice_lines = [(0, 0, invoice_line), (0, 0, invoice_line_discount)]
        else:
            invoice_lines = [(0, 0, invoice_line)]
            
        return {
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'invoice_date': fields.Date.today(),
            'subscription_id': self.id,
            'journal_id': sale_journal.id,
            'invoice_line_ids': invoice_lines,
        }

    def _check_existing_invoice(self):
        """Cek apakah sudah ada invoice di bulan berjalan"""
        self.ensure_one()
        today = fields.Date.today()
        existing_invoice = self.env['account.move'].search([
            ('subscription_id', '=', self.id),
            ('move_type', '=', 'out_invoice'),
            ('invoice_date', '>=', today.replace(day=1)),  # awal bulan
            ('invoice_date', '<=', (today.replace(day=1) + relativedelta(months=1, days=-1))),  # akhir bulan
        ], limit=1)
        return existing_invoice

    def unlink_draft_invoice(self):
        """Hapus invoice yang masih draft"""
        self.ensure_one()
        draft_invoices = self.invoice_ids.filtered(lambda i: i.state == 'draft')
        if draft_invoices:
            draft_invoices.unlink()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sukses',
                    'message': 'Invoice draft berhasil dihapus',
                    'type': 'success',
                }
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Peringatan',
                'message': 'Tidak ada invoice draft yang dapat dihapus',
                'type': 'warning',
            }
        }

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for record in self:
            record.invoice_count = len(record.invoice_ids)

    @api.depends('invoice_ids', 'invoice_ids.state', 'invoice_ids.payment_state', 'invoice_ids.amount_residual')
    def _compute_invoice_stats(self):
        """Hitung statistik invoice"""
        for record in self:
            # Reset counter
            record.invoice_draft_count = 0
            record.invoice_posted_count = 0
            record.invoice_paid_count = 0
            record.invoice_overdue_count = 0
            record.total_unpaid_amount = 0.0
            
            for invoice in record.invoice_ids:
                if invoice.state == 'draft':
                    record.invoice_draft_count += 1
                elif invoice.state == 'posted':
                    record.invoice_posted_count += 1
                    if invoice.payment_state == 'paid':
                        record.invoice_paid_count += 1
                    elif invoice.payment_state in ['not_paid', 'partial'] and invoice.invoice_date_due < fields.Date.today():
                        record.invoice_overdue_count += 1
                        record.total_unpaid_amount += invoice.amount_residual

    def generate_invoice(self):
        """Generate invoice for subscription"""
        self.ensure_one()
        _logger.info(f'Generating invoice for subscription {self.name}')
        
        if not self.partner_id:
            raise ValidationError('Partner/Contact harus diisi!')
            
        if self.state not in ['open', 'isolated']:
            raise ValidationError('Hanya subscription dengan status open atau terisolir yang dapat membuat invoice!')

        # Cek invoice bulan berjalan
        existing_invoice = self._check_existing_invoice()
        if existing_invoice:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Peringatan',
                    'message': f'Invoice untuk bulan {fields.Date.today().strftime("%B %Y")} sudah dibuat dengan nomor {existing_invoice.name}',
                    'type': 'warning',
                    'sticky': True,
                }
            }
            
        # Buat invoice baru
        invoice_vals = self._prepare_invoice_values()
        
        _logger.info(f'Creating invoice with values: {invoice_vals}')
        try:
            invoice = self.env['account.move'].create(invoice_vals)
            _logger.info(f'Invoice created with ID: {invoice.id}')
            
            # Update tanggal tagihan terakhir
            self.write({
                'last_invoice_date': fields.Date.today(),
                'next_invoice_date': fields.Date.today() + relativedelta(months=self.recurring_interval)
            })
            
            # Post message di chatter dengan link ke invoice
            msg = f'Invoice <a href="#" data-oe-model="account.move" data-oe-id="{invoice.id}">{invoice.name}</a> berhasil dibuat'
            self.message_post(body=msg, message_type='notification')
            _logger.info(f'Invoice {invoice.name} berhasil dibuat')
            
            # Tampilkan notifikasi sukses
            self.env['bus.bus']._sendone(
                self.env.user.partner_id,
                'simple_notification',
                {
                    'title': 'Sukses',
                    'message': f'Invoice {invoice.name} berhasil dibuat',
                    'type': 'success',
                }
            )
            
            # Return action untuk membuka invoice
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'res_id': invoice.id,
                'view_mode': 'form',
                'target': 'current',
            }
            
        except Exception as e:
            error_msg = f'Gagal membuat invoice: {str(e)}'
            _logger.error(error_msg)
            self.message_post(body=error_msg)
            raise ValidationError(error_msg)

    def cron_generate_invoices(self):
        """Cron job untuk membuat invoice otomatis"""
        today = fields.Date.today()
        subscriptions = self.search([
            ('state', '=', 'open'),
            ('next_invoice_date', '<=', today)
        ])
        for subscription in subscriptions:
            subscription.generate_invoice()

    def _check_whatsapp_enabled(self):
        """Cek apakah fitur WhatsApp aktif di pengaturan Odoo"""
        return self.env['ir.config_parameter'].sudo().get_param('whatsapp.enable_enterprise_whatsapp')

    def _send_whatsapp_notification(self, template_name, **kwargs):
        """Helper method untuk mengirim notifikasi WhatsApp"""
        self.ensure_one()
        
        # Skip jika pelanggan tidak punya nomor HP
        if not self.customer_id.mobile:
            _logger.info('Pelanggan tidak memiliki nomor HP')
            return False

        try:
            # Template mapping
            template_mapping = {
                'activation': 'whatsapp_template_activation',
                'isolation': 'whatsapp_template_isolation',
                'due_reminder': 'whatsapp_template_due_reminder',
                'overdue_reminder': 'whatsapp_template_overdue_reminder'
            }

            template = self.env.ref(f'dkt_isp_billing.{template_mapping[template_name]}')
            if not template:
                _logger.warning(f'Template WhatsApp {template_name} tidak ditemukan')
                return False

            # Siapkan data untuk template
            values = {
                'customer_name': self.customer_id.name,
                'package_name': self.package_id.name,
                'amount': "{:,.2f}".format(self.amount),
                'due_date': self.next_invoice_date.strftime('%d-%m-%Y') if self.next_invoice_date else '',
            }

            # Kirim WhatsApp menggunakan API Odoo Enterprise
            return self.env['whatsapp.composer'].with_context(
                active_model=self._name,
                active_id=self.id,
            ).create({
                'template_id': template.id,
                'phone_number': self.customer_id.mobile,
                'composition_mode': 'comment',
                'res_model': self._name,
                'res_id': self.id,
                'template_variables': values,
            }).action_send_whatsapp_message()

        except Exception as e:
            _logger.error(f'Error saat mengirim notifikasi WhatsApp: {str(e)}')
            return False

    def action_open(self):
        """Aktivasi subscription dan enable PPPoE secret"""
        self.ensure_one()
        
        # Validasi data
        if not self.cpe_id:
            raise ValidationError('CPE harus diisi!')
            
        if not self.cpe_id.pppoe_username:
            raise ValidationError('CPE belum memiliki PPPoE username!')
            
        if not self.package_id:
            raise ValidationError('Paket harus diisi!')
            
        if not self.package_id.profile_id:
            raise ValidationError('Paket belum memiliki PPPoE Profile!')
            
        if not self.package_id.profile_id.name:
            raise ValidationError('PPPoE Profile belum memiliki nama!')
            
        # Update profile di Mikrotik sesuai paket
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
            
        try:
            # Parse host dan port
            host, port = mikrotik._parse_host_port()
            
            # Buat koneksi ke Mikrotik
            connection = routeros_api.RouterOsApiPool(
                host=str(host),
                username=str(mikrotik.username),
                password=str(mikrotik.password),
                port=int(port),
                plaintext_login=True
            )
            
            api = connection.get_api()
            if not api:
                raise ValidationError('Gagal terhubung ke Mikrotik!')
                
            try:
                # Pastikan username dalam bentuk string
                pppoe_username = str(self.cpe_id.pppoe_username or '')
                profile_name = str(self.package_id.profile_id.name or '')
                
                # Cek PPPoE secret
                secret_api = api.get_resource('/ppp/secret')
                secrets = secret_api.get(name=pppoe_username)
                
                if not secrets:
                    raise ValidationError(f'PPPoE secret {pppoe_username} tidak ditemukan di Mikrotik!')
                    
                _logger.info(f'Secret response from Mikrotik: {secrets}')
                
                # Update profile dan enable secret
                secret = secrets[0]
                if not secret:
                    raise ValidationError(f'Gagal mendapatkan data secret untuk {pppoe_username}')
                
                # Coba ambil ID dengan berbagai kemungkinan key
                secret_id = secret.get('.id') or secret.get('id') or secret.get('.uid')
                if not secret_id:
                    # Jika tidak ada ID, coba list semua key yang ada
                    available_keys = list(secret.keys())
                    _logger.info(f'Available keys in secret: {available_keys}')
                    raise ValidationError(f'Gagal mendapatkan ID secret untuk {pppoe_username}. Available keys: {available_keys}')
                
                _logger.info(f'Found secret ID: {secret_id}')
                
                # Update profile dan enable secret
                update_data = {
                    'id': str(secret_id),
                    'profile': profile_name,
                    'disabled': 'no'
                }
                _logger.info(f'Updating secret with data: {update_data}')
                
                secret_api.set(**update_data)
                
                # Update status
                self.write({'state': 'open'})
                
                # Update status CPE jika belum aktif
                if self.cpe_id.state != 'open':
                    self.cpe_id.write({'state': 'open'})
                    
                # Update status pelanggan jika belum aktif
                if self.customer_id.state != 'open':
                    self.customer_id.write({'state': 'open'})
                    
                # Tampilkan notifikasi sukses
                self.env['bus.bus']._sendone(
                    self.env.user.partner_id,
                    'simple_notification',
                    {
                        'title': 'Sukses',
                        'message': 'Subscription berhasil diaktifkan',
                        'type': 'success',
                    }
                )
                
                return {
                    'type': 'ir.actions.client',
                    'tag': 'reload',
                }
            except Exception as e:
                _logger.error(f'Error saat mengaktifkan subscription: {str(e)}')
                raise ValidationError(f'Gagal mengaktifkan subscription: {str(e)}')
            finally:
                if api:
                    connection.disconnect()
        except Exception as e:
            _logger.error(f'Error saat koneksi ke Mikrotik: {str(e)}')
            raise ValidationError(f'Gagal terhubung ke Mikrotik: {str(e)}')
    
    def action_isolate(self):
        """Isolir subscription dan disable PPPoE secret"""
        self.ensure_one()
        if self.state != 'open':
            raise ValidationError('Hanya subscription open yang dapat diisolir!')
            
        if not self.cpe_id:
            raise ValidationError('CPE harus diisi!')
            
        if not self.cpe_id.pppoe_username:
            raise ValidationError('CPE belum memiliki PPPoE username!')
            
        # Update profile di Mikrotik sesuai paket
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
            
        try:
            # Parse host dan port
            host, port = mikrotik._parse_host_port()
            
            # Buat koneksi ke Mikrotik
            connection = routeros_api.RouterOsApiPool(
                host=str(host),
                username=str(mikrotik.username),
                password=str(mikrotik.password),
                port=int(port),
                plaintext_login=True
            )
            
            api = connection.get_api()
            if not api:
                raise ValidationError('Gagal terhubung ke Mikrotik!')
                
            try:
                # Pastikan username dalam bentuk string
                pppoe_username = str(self.cpe_id.pppoe_username or '')
                
                # Cek PPPoE secret
                secret_api = api.get_resource('/ppp/secret')
                secrets = secret_api.get(name=pppoe_username)
                
                if not secrets:
                    raise ValidationError(f'PPPoE secret {pppoe_username} tidak ditemukan di Mikrotik!')
                    
                _logger.info(f'Secret response from Mikrotik: {secrets}')
                
                # Update profile dan enable secret
                secret = secrets[0]
                if not secret:
                    raise ValidationError(f'Gagal mendapatkan data secret untuk {pppoe_username}')
                
                # Coba ambil ID dengan berbagai kemungkinan key
                secret_id = secret.get('.id') or secret.get('id') or secret.get('.uid')
                if not secret_id:
                    # Jika tidak ada ID, coba list semua key yang ada
                    available_keys = list(secret.keys())
                    _logger.info(f'Available keys in secret: {available_keys}')
                    raise ValidationError(f'Gagal mendapatkan ID secret untuk {pppoe_username}. Available keys: {available_keys}')
                
                _logger.info(f'Found secret ID: {secret_id}')
                
                # Disable secret
                update_data = {
                    'id': str(secret_id),
                    'disabled': 'yes'
                }
                _logger.info(f'Updating secret with data: {update_data}')
                
                secret_api.set(**update_data)
                
                # Update status
                self.write({'state': 'isolated'})
                
                # Cek apakah masih ada subscription open untuk CPE ini
                active_subs = self.search([
                    ('cpe_id', '=', self.cpe_id.id),
                    ('state', '=', 'open'),
                    ('id', '!=', self.id)
                ])
                
                if not active_subs:
                    # Jika tidak ada subscription open lain, isolir CPE
                    self.cpe_id.write({'state': 'isolated'})
                    
                    # Cek apakah masih ada CPE open untuk pelanggan ini
                    active_cpes = self.env['isp.cpe'].search([
                        ('customer_id', '=', self.customer_id.id),
                        ('state', '=', 'open')
                    ])
                    
                    if not active_cpes:
                        # Jika tidak ada CPE open lain, isolir pelanggan
                        self.customer_id.write({'state': 'isolated'})
                
                # Tampilkan notifikasi sukses
                self.env['bus.bus']._sendone(
                    self.env.user.partner_id,
                    'simple_notification',
                    {
                        'title': 'Sukses',
                        'message': 'Subscription berhasil diisolir',
                        'type': 'success',
                    }
                )
                
                return {
                    'type': 'ir.actions.client',
                    'tag': 'reload',
                }
            except Exception as e:
                _logger.error(f'Error saat mengisolir subscription: {str(e)}')
                raise ValidationError(f'Gagal mengisolir subscription: {str(e)}')
            finally:
                if api:
                    connection.disconnect()
        except Exception as e:
            _logger.error(f'Error saat koneksi ke Mikrotik: {str(e)}')
            raise ValidationError(f'Gagal terhubung ke Mikrotik: {str(e)}')
    
    def action_terminate(self):
        """Terminasi subscription dan hapus PPPoE secret"""
        self.ensure_one()
        if self.state not in ['open', 'isolated']:
            raise ValidationError('Hanya subscription open atau terisolir yang dapat diterminasi!')
            
        # Update profile di Mikrotik sesuai paket
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise ValidationError('Konfigurasi Mikrotik tidak ditemukan!')
            
        try:
            # Parse host dan port
            host, port = mikrotik._parse_host_port()
            
            # Buat koneksi ke Mikrotik
            connection = routeros_api.RouterOsApiPool(
                host=str(host),
                username=str(mikrotik.username),
                password=str(mikrotik.password),
                port=int(port),
                plaintext_login=True
            )
            
            api = connection.get_api()
            if not api:
                raise ValidationError('Gagal terhubung ke Mikrotik!')
                
            try:
                # Pastikan username dalam bentuk string
                pppoe_username = str(self.cpe_id.pppoe_username or '')
                
                # Cek PPPoE secret
                secret_api = api.get_resource('/ppp/secret')
                secrets = secret_api.get(name=pppoe_username)
                
                if secrets:
                    # Hapus secret dari Mikrotik
                    secret = secrets[0]
                    secret_id = secret.get('.id') or secret.get('id') or secret.get('.uid')
                    if secret_id:
                        secret_api.remove(id=str(secret_id))
                
                # Update status subscription
                self.write({'state': 'terminated'})
                
                # Cek apakah masih ada subscription aktif untuk CPE ini
                active_subs = self.search([
                    ('cpe_id', '=', self.cpe_id.id),
                    ('state', 'in', ['open', 'isolated']),
                    ('id', '!=', self.id)
                ])
                
                if not active_subs:
                    # Reset CPE ke draft
                    self.cpe_id.write({
                        'state': 'draft',
                        'pppoe_username': False,
                        'pppoe_password': False
                    })
                    
                    # Cek apakah masih ada CPE aktif untuk pelanggan ini
                    active_cpes = self.env['isp.cpe'].search([
                        ('customer_id', '=', self.customer_id.id),
                        ('state', 'in', ['open', 'isolated'])
                    ])
                    
                    if not active_cpes:
                        # Reset pelanggan ke draft
                        self.customer_id.write({'state': 'draft'})
                
                # Tampilkan notifikasi sukses
                self.env['bus.bus']._sendone(
                    self.env.user.partner_id,
                    'simple_notification',
                    {
                        'title': 'Sukses',
                        'message': 'Subscription berhasil diterminasi',
                        'type': 'success',
                    }
                )
                
                return {
                    'type': 'ir.actions.client',
                    'tag': 'reload',
                }
            except Exception as e:
                _logger.error(f'Error saat terminasi subscription: {str(e)}')
                raise ValidationError(f'Gagal terminasi subscription: {str(e)}')
            finally:
                if api:
                    connection.disconnect()
        except Exception as e:
            _logger.error(f'Error saat koneksi ke Mikrotik: {str(e)}')
            raise ValidationError(f'Gagal terhubung ke Mikrotik: {str(e)}')

    def action_view_invoices(self):
        """Tampilkan invoice subscription"""
        self.ensure_one()
        action = {
            'name': 'Invoice',
            'view_mode': 'tree,form',
            'res_model': 'account.move',
            'type': 'ir.actions.act_window',
            'domain': [('subscription_id', '=', self.id), ('move_type', '=', 'out_invoice')],
            'context': {'default_subscription_id': self.id, 'default_move_type': 'out_invoice'},
        }
        
        # Tambahkan filter berdasarkan status
        action['context'].update({
            'search_default_draft': 1,
            'search_default_posted': 1,
            'search_default_unpaid': 1,
            'search_default_overdue': 1,
        })
        
        return action 