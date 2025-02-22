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
    
    last_notification_date = fields.Date('Tanggal Notifikasi Terakhir')
    mikrotik_user = fields.Char('Mikrotik Username', related='customer_id.cpe_ids.pppoe_username', readonly=True)
    
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

    def _prepare_invoice_values(self):
        """Menyiapkan nilai untuk pembuatan invoice"""
        self.ensure_one()
        return {
            'partner_id': self.partner_id.id,
            'move_type': 'out_invoice',
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [(0, 0, {
                'name': f'Layanan Internet {self.package_id.name}',
                'quantity': 1,
                'price_unit': self.amount,
            })],
        }

    def generate_invoice(self):
        """Membuat invoice untuk subscription"""
        self.ensure_one()
        invoice_values = self._prepare_invoice_values()
        invoice = self.env['account.move'].create(invoice_values)
        self.last_invoice_date = fields.Date.today()
        return invoice

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