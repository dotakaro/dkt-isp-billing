from odoo import models, fields, api
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class ISPSubscription(models.Model):
    _name = 'isp.subscription'
    _description = 'ISP Subscription'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nomor', readonly=True, copy=False)
    customer_id = fields.Many2one('isp.customer', string='Pelanggan', required=True, tracking=True)
    partner_id = fields.Many2one(related='customer_id.partner_id', store=True)
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
        ('active', 'Aktif'),
        ('isolated', 'Terisolir'),
        ('cancelled', 'Dibatalkan')
    ], default='draft', string='Status', tracking=True)
    
    amount = fields.Float('Jumlah Tagihan', related='package_id.price', store=True)
    
    # Tambahkan field-field ini setelah field amount
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
    mikrotik_user = fields.Char('Mikrotik Username', related='customer_id.cpe_ids.pppoe_username', readonly=True)
    
    # Tambahkan field untuk melacak invoice
    invoice_ids = fields.One2many('account.move', 'subscription_id', string='Invoice', domain=[('move_type', '=', 'out_invoice')])
    invoice_count = fields.Integer(string='Jumlah Invoice', compute='_compute_invoice_count')
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].next_by_code('isp.subscription.sequence')
        return super().create(vals_list)
    
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
        income_account = self.package_id.product_tmpl_id.property_account_income_id or \
                        self.package_id.product_tmpl_id.categ_id.property_account_income_categ_id
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

    def generate_invoice(self):
        """Generate invoice for subscription"""
        self.ensure_one()
        _logger.info(f'Generating invoice for subscription {self.name}')
        
        if not self.partner_id:
            raise ValidationError('Partner/Contact harus diisi!')

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
            ('state', '=', 'active'),
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
            
    def _isolate_mikrotik(self):
        """Helper method untuk isolir di Mikrotik"""
        self.ensure_one()
        if not self.mikrotik_user:
            return False
            
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise UserError('Konfigurasi Mikrotik belum diatur!')
            
        return mikrotik.disable_user(self.mikrotik_user)
    
    def _activate_mikrotik(self):
        """Helper method untuk aktivasi di Mikrotik"""
        self.ensure_one()
        if not self.mikrotik_user:
            return False
            
        mikrotik = self.env['isp.mikrotik.config'].search([('active', '=', True)], limit=1)
        if not mikrotik:
            raise UserError('Konfigurasi Mikrotik belum diatur!')
            
        return mikrotik.enable_user(self.mikrotik_user)
    
    def action_activate(self):
        """Aktivasi subscription"""
        self.ensure_one()
        
        # Validasi pelanggan dan paket
        if not self.customer_id or not self.package_id:
            raise ValidationError('Pelanggan dan Paket harus diisi!')
            
        # Validasi status pelanggan
        if self.customer_id.state != 'active':
            raise ValidationError('Pelanggan harus dalam status aktif sebelum subscription dapat diaktifkan!')
            
        # Validasi CPE
        if not self.customer_id.cpe_ids:
            raise ValidationError('Pelanggan belum memiliki CPE yang terdaftar!')
            
        active_cpe = self.customer_id.cpe_ids.filtered(lambda c: c.state == 'active')
        if not active_cpe:
            raise ValidationError('Pelanggan belum memiliki CPE yang aktif!')
            
        # Validasi PPPoE username
        if not active_cpe.pppoe_username or not active_cpe.pppoe_password:
            raise ValidationError('CPE belum memiliki kredensial PPPoE yang valid!')
            
        # Aktifkan di Mikrotik
        if not self._activate_mikrotik():
            raise UserError('Gagal mengaktifkan user di Mikrotik!')
        
        # Update status
        self.write({
            'state': 'active',
            'date_start': fields.Date.today(),
        })
        
        # Kirim notifikasi WhatsApp (akan diskip jika tidak aktif)
        self._send_whatsapp_notification('activation', 
            customer_name=self.customer_id.name,
            package_name=self.package_id.name
        )
        
        # Buat invoice pertama
        self.generate_invoice()
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'Subscription berhasil diaktifkan',
                'type': 'success',
            }
        }

    def action_isolate(self):
        """Isolir subscription"""
        self.ensure_one()
        if self.state != 'active':
            raise ValidationError('Hanya subscription aktif yang dapat diisolir!')
            
        # Isolir di Mikrotik
        self._isolate_mikrotik()
        
        # Update status
        self.write({'state': 'isolated'})
        
        # Kirim notifikasi WhatsApp (akan diskip jika tidak aktif)
        self._send_whatsapp_notification('isolation',
            customer_name=self.customer_id.name,
            due_amount=self.amount
        )
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'Subscription berhasil diisolir',
                'type': 'success',
            }
        }
    
    def cron_check_due_date(self):
        """Cron job untuk mengecek jatuh tempo dan mengirim notifikasi"""
        today = fields.Date.today()
        
        # Notifikasi 3 hari sebelum jatuh tempo
        due_soon = self.search([
            ('state', '=', 'active'),
            ('next_invoice_date', '=', today + timedelta(days=3)),
            ('last_notification_date', '!=', today)
        ])
        for subscription in due_soon:
            subscription._send_whatsapp_notification('due_reminder',
                customer_name=subscription.customer_id.name,
                due_date=subscription.next_invoice_date,
                amount=subscription.amount
            )
            subscription.last_notification_date = today
        
        # Notifikasi 3 hari setelah jatuh tempo
        overdue = self.search([
            ('state', '=', 'active'),
            ('next_invoice_date', '=', today - timedelta(days=3)),
            ('last_notification_date', '!=', today)
        ])
        for subscription in overdue:
            subscription._send_whatsapp_notification('overdue_reminder',
                customer_name=subscription.customer_id.name,
                due_date=subscription.next_invoice_date,
                amount=subscription.amount
            )
            subscription.last_notification_date = today

    def action_cancel(self):
        self.ensure_one()
        self.state = 'cancelled'

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for record in self:
            record.invoice_count = len(record.invoice_ids)
    
    def action_view_invoices(self):
        self.ensure_one()
        return {
            'name': 'Invoice',
            'view_mode': 'tree,form',
            'res_model': 'account.move',
            'type': 'ir.actions.act_window',
            'domain': [('subscription_id', '=', self.id), ('move_type', '=', 'out_invoice')],
            'context': {'default_subscription_id': self.id, 'default_move_type': 'out_invoice'},
        } 