from odoo import models, fields, api
from datetime import date, datetime, timedelta
from calendar import monthrange
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError, ValidationError
import logging
import routeros_api

_logger = logging.getLogger(__name__)

class ISPSubscription(models.Model):
    _name = 'isp.subscription'
    _description = 'ISP Subscription'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nomor Subscription', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Pelanggan', required=True, tracking=True,
                                domain=[('customer_rank', '>', 0)])
    cpe_id = fields.Many2one('isp.cpe', string='CPE', required=True, tracking=True,
                            domain="[('partner_id', '=', partner_id)]")
    package_id = fields.Many2one('isp.package', string='Paket', required=True, tracking=True)
    profile_template_id = fields.Many2one(
        'isp.pppoe.profile.template',
        string='Profile khusus',
        tracking=True,
        domain="[('active', '=', True), ('is_isolir', '=', False)]",
        help='Kosong = profile default paket. Isi jika bayar paket ini '
             'tapi dapat bandwidth lain, contoh Paket 200 dengan 15M.',
    )
    
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
    
    amount = fields.Float(
        'Jumlah Tagihan',
        compute='_compute_amount',
        store=True,
        help='Harga area jika ada, else harga default paket.',
    )
    area_id = fields.Many2one(
        'isp.area',
        string='Area',
        related='cpe_id.area_id',
        store=True,
        index=True,
    )
    
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
    currency_id = fields.Many2one(
        'res.currency', string='Mata Uang',
        default=lambda self: self.env.company.currency_id,
    )
    billing_review_needed = fields.Boolean(
        'Perlu review tarif',
        default=False,
        tracking=True,
        help='Harga/paket komersial tidak ditemukan di comment MikroTik. Jangan ditagih otomatis.',
    )
    billing_review_note = fields.Char('Catatan review tarif')
    is_overdue = fields.Boolean(
        'Nunggak',
        compute='_compute_is_overdue',
        store=True,
        help='Ada invoice posted yang jatuh tempo dan belum lunas.',
    )
    overdue_marked = fields.Boolean(
        'Ditandai nunggak',
        default=False,
        tracking=True,
        help='Penanda operasional. Isolir tetap manual — tidak otomatis memutus pelanggan.',
    )
    is_late = fields.Boolean(
        'Telat',
        compute='_compute_is_late',
        store=True,
        help='Invoice posted belum lunas dan hari sudah melewati tanggal 21 periode.',
    )
    is_special_treatment = fields.Boolean(
        'Perlakuan khusus',
        compute='_compute_is_special_treatment',
        store=True,
        help='Belum ada harga/paket komersial. Jangan ditagih normal, jangan isolir nunggak.',
    )
    unisolate_failed = fields.Boolean(
        'Gagal buka isolir otomatis',
        default=False,
        tracking=True,
    )
    unisolate_error = fields.Char('Error buka isolir')
    
    _unique_active_cpe = models.Constraint(
        'UNIQUE(cpe_id, state)',
        'CPE ini sudah memiliki subscription aktif!',
    )
    
    @api.model_create_multi
    def create(self, vals_list):
        due_day = self._billing_due_day()
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].next_by_code('isp.subscription.sequence')
            vals['due_day'] = due_day
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

    @api.model
    def _billing_due_day(self):
        try:
            return max(1, min(31, int(self.env['ir.config_parameter'].sudo().get_param(
                'dkt_isp_billing.due_day', '1',
            ) or 1)))
        except (TypeError, ValueError):
            return 1

    @api.model
    def _billing_late_day(self):
        try:
            return max(1, min(31, int(self.env['ir.config_parameter'].sudo().get_param(
                'dkt_isp_billing.late_day', '21',
            ) or 21)))
        except (TypeError, ValueError):
            return 21

    @api.model
    def _billing_apply_tax(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'dkt_isp_billing.apply_tax', 'False',
        ) in ('True', 'true', '1')

    @api.model
    def is_date_late_for_period(self, period_date, today=None, due_date=None, late_day=None):
        """Telat jika due sudah lewat DAN today >= tanggal late_day periode invoice."""
        if not period_date:
            return False
        today = today or fields.Date.context_today(self)
        late_day = late_day or self._billing_late_day()
        last_day = monthrange(period_date.year, period_date.month)[1]
        late_on = period_date.replace(day=min(late_day, last_day))
        due = due_date or period_date.replace(day=min(self._billing_due_day(), last_day))
        return due < today and today >= late_on
    
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
        """Menyiapkan nilai invoice draft langganan (tidak di-post)."""
        self.ensure_one()
        journal = self._get_sale_journal()
        product = self._get_internet_service_product()
        period_date = fields.Date.today().replace(day=1)
        due_date = self._get_invoice_due_date(period_date)
        month_label = self._period_label(period_date)
        line_name = 'Langganan %s - %s (%s)' % (
            self.package_id.display_name, self.partner_id.display_name, month_label,
        )
        price_unit = self.amount
        invoice_line = {
            'name': line_name,
            'quantity': 1,
            'price_unit': price_unit,
            'display_type': 'product',
            'tax_ids': [(6, 0, [])],
        }
        if product:
            invoice_line['product_id'] = product.id
            if product.uom_id:
                invoice_line['product_uom_id'] = product.uom_id.id
        else:
            account = self._get_isp_income_account()
            if account:
                invoice_line['account_id'] = account.id

        lines = [(0, 0, invoice_line)]
        if self.discount_id and self.discount_amount > 0:
            discount_line = {
                'name': 'Diskon: %s' % self.discount_id.name,
                'quantity': 1,
                'price_unit': -self.discount_amount,
                'display_type': 'product',
                'tax_ids': [(6, 0, [])],
            }
            if self.discount_id.account_id:
                discount_line['account_id'] = self.discount_id.account_id.id
            lines.append((0, 0, discount_line))

        return {
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'invoice_date': period_date,
            'invoice_date_due': due_date,
            'subscription_id': self.id,
            'isp_invoice_kind': 'subscription',
            'journal_id': journal.id,
            'fiscal_position_id': False,
            'invoice_line_ids': lines,
            'narration': 'Tagihan ISP %s periode %s. Draft — review pajak/jurnal sebelum post.' % (
                self.name, month_label,
            ),
        }

    def _get_invoice_due_date(self, period_date):
        self.ensure_one()
        last_day = monthrange(period_date.year, period_date.month)[1]
        day = min(self._billing_due_day(), last_day)
        return period_date.replace(day=day)

    @api.model
    def _period_label(self, period_date):
        months = (
            '', 'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
            'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember',
        )
        return '%s %s' % (months[period_date.month], period_date.year)

    @api.model
    def _get_sale_journal(self):
        self._ensure_indonesian_accounting()
        company = self.env.company
        journal = self.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', company.id),
        ], limit=1)
        if not journal:
            raise ValidationError(
                'Tidak ditemukan jurnal penjualan. Muat bagan akun Indonesia (l10n_id) '
                'atau buat jurnal penjualan terlebih dahulu.'
            )
        return journal

    @api.model
    def _get_internet_service_product(self):
        tmpl = self.env.ref(
            'dkt_isp_billing.product_template_internet_service', raise_if_not_found=False,
        )
        if tmpl:
            return tmpl.product_variant_id
        product = self.env['product.product'].search([
            ('default_code', '=', 'ISP-SVC'),
        ], limit=1)
        return product

    def _get_isp_income_account(self):
        account = self.env.ref('dkt_isp_billing.revenue_account', raise_if_not_found=False)
        if account:
            return account
        company = self.env.company
        if company.income_account_id:
            return company.income_account_id
        if self.package_id.product_id:
            product = self.package_id.product_id
            return product.property_account_income_id or product.categ_id.property_account_income_categ_id
        return self.env['account.account'].search([
            ('account_type', '=', 'income'),
        ], limit=1)

    def _check_existing_invoice(self):
        """Cek apakah sudah ada invoice di bulan berjalan."""
        self.ensure_one()
        today = fields.Date.today()
        month_start = today.replace(day=1)
        month_end = month_start + relativedelta(months=1, days=-1)
        return self.env['account.move'].search([
            ('subscription_id', '=', self.id),
            ('move_type', '=', 'out_invoice'),
            ('state', '!=', 'cancel'),
            ('isp_invoice_kind', '!=', 'installation'),
            ('invoice_date', '>=', month_start),
            ('invoice_date', '<=', month_end),
        ], limit=1)

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

    @api.depends(
        'package_id', 'package_id.price', 'package_id.area_price_ids',
        'package_id.area_price_ids.price', 'package_id.area_price_ids.area_id',
        'cpe_id.area_id', 'partner_id.area_id',
    )
    def _compute_amount(self):
        for record in self:
            area = record.cpe_id.area_id or record.partner_id.area_id
            record.amount = record.package_id.get_price_for_area(area) if record.package_id else 0.0

    def _get_area_mikrotik(self):
        self.ensure_one()
        return self.env['isp.mikrotik.config'].require_cpe_router(self.cpe_id)

    def _get_assigned_profile_name(self):
        """Profile MikroTik langganan: override dulu, baru default paket."""
        self.ensure_one()
        if self.profile_template_id:
            return self.profile_template_id.name
        if self.package_id:
            return self.package_id.get_assigned_profile_name()
        return 'SAPU-JAGAD'

    def _get_pppoe_profile_name(self):
        self.ensure_one()
        if not self.env['isp.package']._apply_package_rate_limit():
            return self.package_id.get_pppoe_profile_name()
        return self._get_assigned_profile_name()

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
                    elif invoice.payment_state in ['not_paid', 'partial', 'in_payment']:
                        record.total_unpaid_amount += invoice.amount_residual
                        if record._invoice_is_late(invoice):
                            record.invoice_overdue_count += 1

    @api.depends(
        'billing_review_needed', 'package_id', 'package_id.is_default', 'amount',
    )
    def _compute_is_special_treatment(self):
        for record in self:
            record.is_special_treatment = bool(
                record.billing_review_needed
                or (record.package_id and record.package_id.is_default)
                or (record.amount or 0) <= 0
            )

    @api.depends(
        'invoice_ids', 'invoice_ids.state', 'invoice_ids.payment_state',
        'invoice_ids.invoice_date', 'invoice_ids.invoice_date_due',
        'invoice_ids.isp_invoice_kind', 'is_special_treatment',
    )
    def _compute_is_late(self):
        today = fields.Date.context_today(self)
        late_day = self._billing_late_day()
        for record in self:
            if record.is_special_treatment:
                record.is_late = False
                continue
            record.is_late = any(
                record._invoice_is_late(inv, today, late_day)
                for inv in record.invoice_ids
            )

    def _invoice_is_late(self, invoice, today=None, late_day=None):
        if invoice.state != 'posted':
            return False
        if invoice.isp_invoice_kind == 'installation':
            return False
        if invoice.payment_state not in ('not_paid', 'partial', 'in_payment'):
            return False
        period = invoice.invoice_date or invoice.invoice_date_due
        return self.is_date_late_for_period(
            period, today=today, due_date=invoice.invoice_date_due, late_day=late_day,
        )

    @api.depends('is_late')
    def _compute_is_overdue(self):
        for record in self:
            record.is_overdue = record.is_late

    def generate_invoice(self):
        """Buat invoice draft untuk langganan open/terisolir. Tidak mem-post."""
        silent = self.env.context.get('generate_invoice_silent')
        created = self.env['account.move']
        skipped = []
        errors = []
        for record in self:
            try:
                move = record._generate_invoice_one()
                if isinstance(move, dict) and move.get('skipped'):
                    skipped.append(move)
                elif move:
                    created |= move
            except Exception as exc:
                _logger.exception('Gagal membuat tagihan %s', record.name)
                errors.append({'subscription': record, 'error': str(exc)})
                record.message_post(body='Gagal membuat tagihan: %s' % exc)
                if not silent and len(self) == 1:
                    raise ValidationError('Gagal membuat invoice: %s' % exc) from exc

        if silent:
            return {'created': created, 'skipped': skipped, 'errors': errors}

        if len(self) == 1:
            if created:
                return {
                    'type': 'ir.actions.act_window',
                    'res_model': 'account.move',
                    'res_id': created.id,
                    'view_mode': 'form',
                    'target': 'current',
                }
            reason = (skipped[0].get('reason') if skipped else 'Tagihan tidak dibuat.')
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Tagihan',
                    'message': reason,
                    'type': 'warning',
                    'sticky': True,
                },
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Buat tagihan',
                'message': 'Draft dibuat: %s. Dilewati: %s. Gagal: %s.' % (
                    len(created), len(skipped), len(errors),
                ),
                'type': 'success' if created else 'warning',
                'sticky': True,
            },
        }

    def _generate_invoice_one(self):
        self.ensure_one()
        if not self.partner_id:
            raise ValidationError('Partner/Contact harus diisi!')
        if self.state not in ['open', 'isolated']:
            raise ValidationError(
                'Hanya subscription dengan status open atau terisolir yang dapat membuat invoice!'
            )
        if self.is_special_treatment or self.billing_review_needed or (
            self.package_id and self.package_id.is_default
        ):
            return {
                'skipped': True,
                'reason': 'Perlakuan khusus / paket belum dipetakan. Tagihan dilewati.',
                'subscription': self,
            }
        if (self.final_amount or self.amount) <= 0:
            return {
                'skipped': True,
                'reason': 'Jumlah tagihan 0. Tidak membuat invoice.',
                'subscription': self,
            }
        existing_invoice = self._check_existing_invoice()
        if existing_invoice:
            return {
                'skipped': True,
                'reason': 'Invoice bulan %s sudah ada (%s).' % (
                    self._period_label(fields.Date.today()),
                    existing_invoice.display_name,
                ),
                'subscription': self,
                'invoice': existing_invoice,
            }

        invoice_vals = self._prepare_invoice_values()
        invoice = self.env['account.move'].create(invoice_vals)
        if not self._billing_apply_tax():
            invoice._isp_clear_taxes()
        self.write({
            'last_invoice_date': invoice.invoice_date,
        })
        self.message_post(
            body='Draft tagihan <a href="#" data-oe-model="account.move" data-oe-id="%s">%s</a> dibuat. Belum di-post.' % (
                invoice.id, invoice.display_name,
            ),
            message_type='notification',
        )
        return invoice

    def cron_generate_invoices(self):
        """Cron: buat draft tagihan bulan berjalan. Tidak mem-post, tidak isolir."""
        self._ensure_indonesian_accounting()
        subscriptions = self.search([
            ('state', 'in', ['open', 'isolated']),
            ('is_special_treatment', '=', False),
            ('billing_review_needed', '=', False),
            ('amount', '>', 0),
        ])
        result = subscriptions.with_context(generate_invoice_silent=True).generate_invoice()
        created = result.get('created') if isinstance(result, dict) else False
        _logger.info(
            'Cron tagihan: dibuat=%s dilewati=%s gagal=%s',
            len(created) if created else 0,
            len(result.get('skipped') or []) if isinstance(result, dict) else 0,
            len(result.get('errors') or []) if isinstance(result, dict) else 0,
        )
        return result

    def _check_whatsapp_enabled(self):
        """Cek apakah fitur WhatsApp aktif di pengaturan Odoo"""
        return self.env['ir.config_parameter'].sudo().get_param('whatsapp.enable_enterprise_whatsapp')

    def _send_whatsapp_notification(self, template_name, **kwargs):
        """Dimatikan: pemberitahuan tidak dikirim otomatis."""
        _logger.info('Pengiriman WhatsApp dimatikan (kebijakan pemberitahuan).')
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

        profile_name = str(self._get_pppoe_profile_name() or '')
        mikrotik = self._get_area_mikrotik()
            
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
                profile_name = str(self._get_pppoe_profile_name() or '')
                
                # Cek PPPoE secret
                secret_api = api.get_resource('/ppp/secret')
                secrets = secret_api.get(name=pppoe_username)
                
                if not secrets:
                    # Secret tidak ditemukan, buat baru
                    _logger.info(f'PPPoE secret {pppoe_username} tidak ditemukan di Mikrotik. Membuat secret baru.')
                    
                    # Buat PPPoE secret baru
                    secret_data = {
                        'name': pppoe_username,
                        'password': self.cpe_id.pppoe_password,
                        'service': 'pppoe',
                        'profile': profile_name,
                        'comment': mikrotik.format_secret_comment(self.partner_id),
                        'disabled': 'no'  # Langsung aktif
                    }
                    
                    _logger.info(f'Creating new secret with data: {secret_data}')
                    secret_api.add(**secret_data)
                    
                    # Update status
                    self.write({'state': 'open'})
                    
                    # Update status CPE jika belum aktif
                    if self.cpe_id.state != 'open':
                        self.cpe_id.write({'state': 'open'})
                        
                    # Update status pelanggan jika belum aktif
                    if self.partner_id.state != 'active':
                        self.partner_id.write({'state': 'active'})
                    
                    # Tampilkan notifikasi sukses
                    self.env['bus.bus']._sendone(
                        self.env.user.partner_id,
                        'simple_notification',
                        {
                            'title': 'Sukses',
                            'message': f'Subscription berhasil diaktifkan dengan membuat secret baru untuk {pppoe_username}',
                            'type': 'success',
                        }
                    )
                    
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'reload',
                    }
                    
                _logger.info(f'Secret response from Mikrotik: {secrets}')
                
                # Secret ditemukan, update profile dan enable secret
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
                if self.partner_id.state != 'active':
                    self.partner_id.write({'state': 'active'})
                    
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
    
    def _apply_pppoe_disabled(self, disabled):
        """Set secret di router CPE. Returns (ok, error). Tidak fallback router default."""
        self.ensure_one()
        if not self.cpe_id:
            return False, 'CPE harus diisi.'
        if not self.cpe_id.pppoe_username:
            return False, 'CPE belum memiliki PPPoE username.'
        try:
            mikrotik = self._get_area_mikrotik()
        except Exception as exc:
            return False, str(exc)
        if mikrotik.uses_radius_isolir():
            return mikrotik.apply_isolir_profile(self.cpe_id, isolated=disabled)
        return mikrotik.set_secret_disabled(self.cpe_id.pppoe_username, disabled)

    def _mark_isolated_locally(self):
        self.ensure_one()
        self.write({'state': 'isolated'})
        active_subs = self.search([
            ('cpe_id', '=', self.cpe_id.id),
            ('state', '=', 'open'),
            ('id', '!=', self.id),
        ])
        if not active_subs and self.cpe_id.state != 'isolated':
            self.cpe_id.write({'state': 'isolated'})
            active_cpes = self.env['isp.cpe'].search([
                ('partner_id', '=', self.partner_id.id),
                ('state', '=', 'open'),
            ])
            if not active_cpes:
                self.partner_id.write({'state': 'isolated'})

    def _mark_open_locally(self):
        self.ensure_one()
        self.write({
            'state': 'open',
            'unisolate_failed': False,
            'unisolate_error': False,
            'overdue_marked': False,
        })
        if self.cpe_id.state == 'isolated':
            self.cpe_id.write({'state': 'open'})
        if self.partner_id.state == 'isolated':
            isolated_cpes = self.env['isp.cpe'].search([
                ('partner_id', '=', self.partner_id.id),
                ('state', '=', 'isolated'),
            ])
            if not isolated_cpes:
                self.partner_id.write({'state': 'active'})

    def isolate_safe(self):
        """Isolir ke router CPE. Idempotent. Tidak raise."""
        self.ensure_one()
        if self.state == 'isolated':
            return True, None
        if self.state != 'open':
            return False, 'Hanya langganan open yang dapat diisolir.'
        if self.is_special_treatment and not self.env.context.get('isp_allow_special_isolate'):
            return False, 'Perlakuan khusus: jangan isolir karena nunggak.'
        ok, err = self._apply_pppoe_disabled(True)
        if not ok:
            return False, err
        self._mark_isolated_locally()
        self.message_post(body='Diisolir ke router CPE %s.' % (
            self.cpe_id.mikrotik_config_id.display_name or '-',
        ))
        return True, None

    def enable_safe(self):
        """Buka isolir ke router CPE. Idempotent. Tidak raise."""
        self.ensure_one()
        if self.state == 'open':
            return True, None
        if self.state != 'isolated':
            return False, 'Hanya langganan terisolir yang dapat dibuka isolirnya.'
        ok, err = self._apply_pppoe_disabled(False)
        if not ok:
            return False, err
        self._mark_open_locally()
        self.message_post(body='Isolir dibuka di router CPE %s.' % (
            self.cpe_id.mikrotik_config_id.display_name or '-',
        ))
        return True, None

    def current_period_invoice_is_paid(self):
        self.ensure_one()
        invoice = self._current_period_invoice()
        if not invoice or invoice.state != 'posted':
            return False
        if invoice.payment_state == 'paid':
            return True
        return invoice.currency_id.is_zero(invoice.amount_residual)

    def _current_period_invoice(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        start = today.replace(day=1)
        last = monthrange(today.year, today.month)[1]
        end = today.replace(day=last)
        return self.env['account.move'].search([
            ('subscription_id', '=', self.id),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('isp_invoice_kind', '!=', 'installation'),
            ('invoice_date', '>=', start),
            ('invoice_date', '<=', end),
        ], limit=1)

    def try_unisolate_after_payment(self):
        """Buka isolir setelah lunas bulan berjalan. Gagal API tidak menggagalkan pembayaran."""
        self.ensure_one()
        if self.state != 'isolated':
            return True
        try:
            with self.env.cr.savepoint():
                ok, err = self.enable_safe()
            if ok:
                self.message_post(
                    body='Isolir dibuka otomatis setelah pelunasan tagihan bulan berjalan.',
                )
                self._notify_restored_whatsapp()
                return True
            self.write({
                'unisolate_failed': True,
                'unisolate_error': (err or 'Gagal buka isolir')[:512],
            })
            self.message_post(
                body='Gagal buka isolir otomatis: %s. Pembayaran tetap tercatat.' % err,
            )
            _logger.warning('Buka isolir otomatis gagal %s: %s', self.name, err)
            return False
        except Exception as exc:
            _logger.exception('Buka isolir otomatis exception %s', self.name)
            self.write({
                'unisolate_failed': True,
                'unisolate_error': str(exc)[:512],
            })
            self.message_post(
                body='Gagal buka isolir otomatis: %s. Pembayaran tetap tercatat.' % exc,
            )
            return False

    def _notify_restored_whatsapp(self):
        """Info khusus: koneksi sudah aktif lagi. Gagal WA tidak membatalkan buka isolir."""
        self.ensure_one()
        if not self.partner_id:
            return False
        try:
            invoice = self._current_period_invoice()
            self.env['isp.whatsapp.message'].queue_and_send(
                self.partner_id,
                template_kind='restored',
                invoice=invoice,
            )
        except Exception as exc:
            _logger.warning('WA buka isolir dilewati %s: %s', self.name, exc)
            return False
        return True

    def action_isolate(self):
        """Isolir subscription dan disable PPPoE secret di router CPE."""
        self.ensure_one()
        ok, err = self.with_context(isp_allow_special_isolate=True).isolate_safe()
        if not ok:
            raise ValidationError(err or 'Gagal mengisolir subscription.')
        if not self.env.context.get('isp_silent_isolate'):
            self.env['bus.bus']._sendone(
                self.env.user.partner_id,
                'simple_notification',
                {
                    'title': 'Sukses',
                    'message': 'Subscription berhasil diisolir',
                    'type': 'success',
                },
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_terminate(self):
        """Terminasi subscription dan hapus PPPoE secret"""
        self.ensure_one()
        if self.state not in ['open', 'isolated']:
            raise ValidationError('Hanya subscription open atau terisolir yang dapat diterminasi!')
            
        # Simpan username terlebih dahulu jika ada
        has_pppoe = False
        pppoe_username = False
        
        if self.cpe_id and self.cpe_id.pppoe_username:
            has_pppoe = True
            pppoe_username = str(self.cpe_id.pppoe_username)

        # Set flag terminasi pada CPE untuk bypass validasi PPPoE
        if self.cpe_id:
            self.cpe_id.is_terminating = True
            
        try:
            # Update status subscription ke draft (bukan terminated)
            # agar history subscription tetap terjaga
            self.write({'state': 'draft'})
            
            # Update status CPE dengan SQL langsung untuk bypass constraint
            if self.cpe_id:
                self.env.cr.execute(
                    """UPDATE isp_cpe SET state = 'draft' WHERE id = %s""", 
                    (self.cpe_id.id,)
                )
                # Force refresh dari database
                self.env['isp.cpe'].invalidate_model()
                
            # Hanya lakukan operasi Mikrotik jika ada username PPPoE
            if has_pppoe and pppoe_username:
                # Update profile di Mikrotik sesuai paket
                mikrotik = self.cpe_id.mikrotik_config_id
                if not mikrotik:
                    # Jika tidak ada konfigurasi Mikrotik, lanjutkan tanpa error
                    _logger.warning('Konfigurasi Mikrotik tidak ditemukan!')
                else:
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
                        if api:
                            try:
                                # Cek PPPoE secret dan hapus jika ada
                                secret_api = api.get_resource('/ppp/secret')
                                secrets = secret_api.get(name=pppoe_username)
                                
                                if secrets:
                                    # Hapus secret dari Mikrotik
                                    secret = secrets[0]
                                    secret_id = secret.get('.id') or secret.get('id') or secret.get('.uid')
                                    if secret_id:
                                        secret_api.remove(id=str(secret_id))
                            except Exception as e:
                                _logger.error(f'Error saat menghapus PPPoE secret: {str(e)}')
                            finally:
                                connection.disconnect()
                    except Exception as e:
                        _logger.error(f'Error saat koneksi ke Mikrotik: {str(e)}')
            
            # Cek apakah masih ada subscription aktif untuk CPE ini
            active_subs = self.search([
                ('cpe_id', '=', self.cpe_id.id),
                ('state', 'in', ['open', 'isolated']),
                ('id', '!=', self.id)
            ])
            
            if not active_subs:
                # Reset CPE ke draft tapi TETAP pertahankan username & password PPPoE
                # agar history tetap terjaga
                self.env.cr.execute(
                    """UPDATE isp_cpe SET state = 'draft' WHERE id = %s""", 
                    (self.cpe_id.id,)
                )
                
                # Cek apakah masih ada CPE aktif untuk pelanggan ini
                active_cpes = self.env['isp.cpe'].search([
                    ('partner_id', '=', self.partner_id.id),
                    ('state', 'in', ['open', 'isolated'])
                ])
                
                if not active_cpes:
                    # Reset pelanggan ke draft
                    self.partner_id.write({'state': 'draft'})
        finally:
            # Pastikan flag terminasi direset meskipun ada error
            if self.cpe_id:
                self.cpe_id.is_terminating = False
        
        # Tampilkan notifikasi sukses
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'Subscription berhasil diterminasi dan diubah ke status draft',
                'type': 'success',
            }
        }

    def action_enable(self):
        """Buka isolir subscription dan enable PPPoE secret di router CPE."""
        self.ensure_one()
        ok, err = self.enable_safe()
        if not ok:
            raise ValidationError(err or 'Gagal membuka isolir subscription.')
        if not self.env.context.get('isp_silent_isolate'):
            self.env['bus.bus']._sendone(
                self.env.user.partner_id,
                'simple_notification',
                {
                    'title': 'Sukses',
                    'message': 'Subscription berhasil dibuka isolirnya',
                    'type': 'success',
                },
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_view_invoices(self):
        """Tampilkan invoice subscription"""
        self.ensure_one()
        action = {
            'name': 'Invoice',
            'view_mode': 'list,form',
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

    @api.onchange('package_id', 'cpe_id')
    def _onchange_package_id(self):
        if self.package_id:
            area = self.cpe_id.area_id or self.partner_id.area_id
            self.amount = self.package_id.get_price_for_area(area)

    def write(self, vals):
        """Ubah paket untuk tagihan tidak menulis profile MikroTik (rate-limit mati)."""
        if 'due_day' in vals:
            vals['due_day'] = self._billing_due_day()
        res = super().write(vals)
        if {'package_id', 'profile_template_id', 'state', 'cpe_id'} & set(vals):
            self.mapped('cpe_id')._sync_radius_safe()
        return res

    @api.model
    def _ensure_indonesian_accounting(self):
        """Muat CoA l10n_id, IDR, dan jurnal penjualan jika belum ada."""
        company = self.env.company
        indonesia = self.env.ref('base.id', raise_if_not_found=False)
        idr = self.env.ref('base.IDR', raise_if_not_found=False)
        if indonesia and not company.country_id:
            company.country_id = indonesia
        posted = self.env['account.move'].search_count([
            ('company_id', '=', company.id),
            ('state', '=', 'posted'),
        ])
        if idr and company.currency_id != idr and not posted:
            company.currency_id = idr
        if not company.chart_template:
            _logger.info('Memuat bagan akun Indonesia (l10n_id) untuk %s.', company.display_name)
            self.env['account.chart.template'].try_loading('id', company, install_demo=False)
        journal = self.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', company.id),
        ], limit=1)
        if not journal:
            journal = self.env['account.journal'].create({
                'name': 'Tagihan Pelanggan',
                'code': 'INV',
                'type': 'sale',
                'company_id': company.id,
            })
        self._configure_isp_income_product(company)
        self._rebind_legacy_account_xmlids(company)
        return journal

    @api.model
    def _rebind_legacy_account_xmlids(self, company):
        """Kembalikan xmlid akun lama ke akun CoA agar env.ref tidak pecah."""
        Data = self.env['ir.model.data']
        Account = self.env['account.account']

        def _account_by_code(code):
            rec = Account.search([('code_store', 'ilike', code)], limit=1)
            if rec:
                return rec
            return Account.search([('name', 'ilike', code)], limit=1)

        def _bind(xmlid_name, record):
            if not record:
                return
            existing = Data.search([
                ('module', '=', 'dkt_isp_billing'),
                ('name', '=', xmlid_name),
            ], limit=1)
            if existing:
                if existing.res_id != record.id:
                    existing.res_id = record.id
                return
            Data.create({
                'module': 'dkt_isp_billing',
                'name': xmlid_name,
                'model': record._name,
                'res_id': record.id,
                'noupdate': True,
            })

        sales = self.env.ref('account.%s_l10n_id_41000010' % company.id, raise_if_not_found=False) or _account_by_code('41000010')
        receivable = self.env.ref('account.%s_l10n_id_11210010' % company.id, raise_if_not_found=False) or _account_by_code('11210010')
        other_exp = self.env.ref('account.%s_l10n_id_69000000' % company.id, raise_if_not_found=False)
        maint = self.env.ref('account.%s_l10n_id_65110080' % company.id, raise_if_not_found=False)
        device = self.env.ref('account.%s_l10n_id_63110120' % company.id, raise_if_not_found=False)
        misc = self.env['account.journal'].search([
            ('type', '=', 'general'),
            ('company_id', '=', company.id),
        ], limit=1)
        _bind('revenue_account', sales)
        _bind('installation_revenue_account', sales)
        _bind('sales_revenue_account', sales)
        _bind('receivable_account', receivable)
        _bind('expense_account', other_exp)
        _bind('device_expense_account', device)
        _bind('maintenance_expense_account', maint)
        _bind('financial_report_journal', misc)

    @api.model
    def _configure_isp_income_product(self, company):
        tmpl = self.env.ref(
            'dkt_isp_billing.product_template_internet_service', raise_if_not_found=False,
        )
        if not tmpl:
            return
        income = company.income_account_id or self.env.ref(
            'dkt_isp_billing.revenue_account', raise_if_not_found=False,
        )
        if income and tmpl.categ_id and not tmpl.categ_id.property_account_income_categ_id:
            tmpl.categ_id.property_account_income_categ_id = income
        if not tmpl.taxes_id:
            tmpl.taxes_id = [(5, 0, 0)]

    def _comment_from_cpe_notes(self):
        self.ensure_one()
        notes = (self.cpe_id.notes or '').strip()
        marker = 'Comment MikroTik:'
        if marker in notes:
            return notes.split(marker, 1)[1].strip()
        return notes or ''

    @api.model
    def _fetch_secret_comment_map(self):
        """Baca comment /ppp/secret (read-only). Key: (router_id, username)."""
        result = {}
        routers = self.env['isp.mikrotik.config'].search([('active', '=', True)])
        for router in routers:
            skip = router._import_skip_reason()
            if skip:
                _logger.info('Remap skip router %s: %s', router.name, skip)
                continue
            secrets, err = router._list_ppp_secrets()
            if err:
                _logger.warning('Remap: gagal baca secret %s: %s', router.name, err)
                continue
            for secret in secrets or []:
                username = (secret.get('name') or '').strip()
                if username:
                    result[(router.id, username)] = secret.get('comment') or ''
        return result

    @api.model
    def remap_commercial_packages(self, fetch_mikrotik=False):
        """Petakan package_id komersial dari comment. Tidak mengubah profile MikroTik."""
        stats = {
            'PAKET_150': 0,
            'PAKET_200': 0,
            'PAKET_250': 0,
            'PAKET_300': 0,
            'PAKET_350': 0,
            'PAKET_500': 0,
            'PAKET_600': 0,
            'unknown': 0,
            'unchanged': 0,
            'updated': 0,
        }
        secret_map = self._fetch_secret_comment_map() if fetch_mikrotik else {}
        domain = [('state', 'in', ['open', 'isolated', 'draft'])]
        if self:
            records = self
        else:
            records = self.search(domain)
        Mikrotik = self.env['isp.mikrotik.config']
        for rec in records:
            comment = rec._comment_from_cpe_notes()
            username = rec.cpe_id.pppoe_username or ''
            router_id = rec.cpe_id.mikrotik_config_id.id
            live_comment = secret_map.get((router_id, username))
            if live_comment:
                if not comment:
                    rec.cpe_id.notes = 'Comment MikroTik: %s' % live_comment
                comment = live_comment
            area = rec.cpe_id.area_id or rec.partner_id.area_id
            package, parsed = Mikrotik._package_from_secret_comment(comment, area=area)
            if package:
                stats[package.code] = stats.get(package.code, 0) + 1
                vals = {
                    'billing_review_needed': False,
                    'billing_review_note': False,
                }
                if rec.package_id != package:
                    vals['package_id'] = package.id
                    stats['updated'] += 1
                    rec.message_post(
                        body='Paket tagihan dipetakan ke %s dari comment (profile MikroTik tidak diubah).' % (
                            package.display_name,
                        ),
                    )
                else:
                    stats['unchanged'] += 1
                rec.write(vals)
            else:
                stats['unknown'] += 1
                note = Mikrotik._billing_review_note(parsed) or 'Comment tanpa harga/paket. Perlu review.'
                rec.write({
                    'billing_review_needed': True,
                    'billing_review_note': note,
                })
        _logger.info('Remap paket komersial: %s', stats)
        return stats

    def action_remap_commercial_packages(self):
        stats = (self or self.search([])).remap_commercial_packages(fetch_mikrotik=False)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Pemetaan paket',
                'message': (
                    '150: %(PAKET_150)s, 200: %(PAKET_200)s, 250: %(PAKET_250)s, '
                    '300: %(PAKET_300)s, 350: %(PAKET_350)s, 500: %(PAKET_500)s, '
                    '600: %(PAKET_600)s, tidak dikenal: %(unknown)s, diubah: %(updated)s.'
                ) % stats,
                'type': 'success',
                'sticky': True,
            },
        }

    def action_generate_invoices(self):
        """Tombol list/form: buat draft tagihan bulan ini."""
        records = self.filtered(lambda s: s.state in ('open', 'isolated'))
        if not records:
            records = self.search([('state', 'in', ['open', 'isolated'])]) if not self else self
        return records.generate_invoice()

    def action_mark_overdue(self):
        """Tandai nunggak. Tidak isolir."""
        marked = self.env['isp.subscription']
        for rec in self:
            if rec.state not in ('open', 'isolated'):
                continue
            if rec.is_special_treatment:
                continue
            rec.overdue_marked = True
            rec.message_post(
                body='Ditandai telat/nunggak. Isolir tidak otomatis — gunakan Isolir massal jika perlu.',
            )
            marked |= rec
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Telat',
                'message': '%s langganan ditandai telat. Isolir tetap manual.' % len(marked),
                'type': 'warning',
                'sticky': False,
            },
        }

    def cron_mark_overdue(self):
        """Hitung ulang telat (tgl 21). Hanya menandai, tidak isolir, tidak kirim notif."""
        subs = self.search([('state', 'in', ['open', 'isolated'])])
        subs._compute_is_special_treatment()
        subs._compute_is_late()
        late = subs.filtered(lambda s: s.is_late and not s.is_special_treatment)
        to_mark = late.filtered(lambda s: not s.overdue_marked)
        if to_mark:
            to_mark.action_mark_overdue()
        to_clear = subs.filtered(lambda s: s.overdue_marked and not s.is_late)
        if to_clear:
            to_clear.write({'overdue_marked': False})
        _logger.info(
            'Cron tanda telat: ditandai=%s dibersihkan=%s. Isolir tidak dijalankan.',
            len(to_mark), len(to_clear),
        )
        return True

    @api.model
    def _auto_isolate_config(self):
        ICP = self.env['ir.config_parameter'].sudo()
        enabled = ICP.get_param('dkt_isp_billing.auto_isolate_enabled', 'False') in (
            'True', 'true', '1',
        )
        backend = ICP.get_param('dkt_isp_billing.auto_isolate_backend', 'mikrotik_secret') or 'mikrotik_secret'
        trigger = ICP.get_param('dkt_isp_billing.auto_isolate_trigger', 'late_day') or 'late_day'
        return {
            'enabled': enabled,
            'backend': backend,
            'trigger': trigger,
        }

    @api.model
    def _cron_auto_isolate_overdue(self):
        """Kerangka isolir otomatis. No-op kecuali flag ON dan backend RADIUS siap."""
        cfg = self._auto_isolate_config()
        if not cfg['enabled']:
            _logger.info(
                'Isolir otomatis OFF. Isolir tetap wizard bulk manual. Tidak ada pelanggan diisolir.',
            )
            return True
        if cfg['backend'] != 'radius':
            _logger.info(
                'Isolir otomatis ON tetapi backend=%s (bukan radius). No-op. '
                'Jangan isolir secret MikroTik dari cron.',
                cfg['backend'],
            )
            return True
        _logger.info(
            'Isolir otomatis RADIUS belum diimplementasikan (trigger=%s). No-op.',
            cfg['trigger'],
        )
        return True

    def action_open_isolate_bulk(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Isolir massal',
            'res_model': 'isp.isolate.bulk.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'isp.subscription',
                'active_ids': self.ids,
            },
        }

    def action_prepare_notification(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Siapkan pemberitahuan',
            'res_model': 'isp.notification.prepare.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'isp.subscription',
                'active_ids': self.ids,
            },
        }

    def action_isp_pay_cash(self):
        self.ensure_one()
        invoice = self._current_period_invoice()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Bayar tunai / Kas',
            'res_model': 'isp.cash.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_subscription_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_invoice_id': invoice.id if invoice else False,
                'default_amount': invoice.amount_residual if invoice else self.final_amount,
            },
        }

    def action_isp_upload_proof(self):
        self.ensure_one()
        invoice = self._current_period_invoice()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Unggah bukti transfer',
            'res_model': 'isp.payment.proof',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_subscription_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_invoice_id': invoice.id if invoice else False,
            },
        }

    def action_retry_unisolate(self):
        """Coba lagi buka isolir setelah gagal otomatis."""
        failed = 0
        for rec in self:
            ok = rec.try_unisolate_after_payment()
            if not ok:
                failed += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Buka isolir',
                'message': 'Selesai. Gagal: %s dari %s.' % (failed, len(self)),
                'type': 'success' if not failed else 'warning',
            },
        }

    def _cron_send_due_notice(self):
        _logger.info('Pemberitahuan otomatis dimatikan. Gunakan wizard Siapkan pemberitahuan.')
        return True

    def _cron_send_overdue_notice(self):
        _logger.info('Pemberitahuan otomatis dimatikan. Gunakan wizard Siapkan pemberitahuan.')
        return True

    def cron_check_due_date(self):
        return self.cron_mark_overdue()
 