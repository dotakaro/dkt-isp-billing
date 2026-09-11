import logging
import secrets
from calendar import monthrange

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    subscription_id = fields.Many2one(
        'isp.subscription', string='Langganan', tracking=True,
        domain="[('partner_id', '=', partner_id)]",
        index=True,
    )
    isp_invoice_kind = fields.Selection(
        [
            ('subscription', 'Langganan'),
            ('installation', 'Instalasi'),
        ],
        string='Jenis tagihan ISP',
        default='subscription',
        index=True,
    )
    isp_upload_token = fields.Char(
        'Token unggah bukti', copy=False, index=True, readonly=True,
    )
    isp_upload_url = fields.Char(
        'Tautan unggah bukti', compute='_compute_isp_upload_url',
    )
    isp_submitter_only = fields.Boolean(
        compute='_compute_isp_submitter_only',
    )

    @api.depends_context('uid')
    def _compute_isp_submitter_only(self):
        is_submitter = self.env.user._isp_is_submitter_only()
        for move in self:
            move.isp_submitter_only = is_submitter

    @api.depends('isp_upload_token')
    def _compute_isp_upload_url(self):
        base = self.get_base_url().rstrip('/')
        for move in self:
            if move.isp_upload_token:
                move.isp_upload_url = '%s/isp/unggah/%s' % (base, move.isp_upload_token)
            else:
                move.isp_upload_url = False

    @api.model
    def _isp_new_upload_token(self):
        return secrets.token_urlsafe(24)

    def _isp_ensure_upload_token(self):
        for move in self:
            if move.move_type == 'out_invoice' and not move.isp_upload_token:
                move.isp_upload_token = self._isp_new_upload_token()
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('move_type') == 'out_invoice' and not vals.get('isp_upload_token'):
                vals['isp_upload_token'] = self._isp_new_upload_token()
        return super().create(vals_list)

    def action_post(self):
        res = super().action_post()
        self.filtered(lambda m: m.move_type == 'out_invoice')._isp_ensure_upload_token()
        return res

    @api.onchange('partner_id')
    def _onchange_partner_subscription(self):
        if self.partner_id != self.subscription_id.partner_id:
            self.subscription_id = False

    def _isp_clear_taxes(self):
        """Pajak = 0 untuk tagihan langganan draft."""
        for move in self:
            if move.state != 'draft':
                continue
            lines = move.invoice_line_ids.filtered(lambda l: l.display_type == 'product')
            if lines:
                lines.write({'tax_ids': [Command.clear()]})

    def _isp_apply_billing_policy(self):
        """Rapikan draft langganan: pajak 0, due_date tgl 1 periode. Tidak mem-post."""
        Sub = self.env['isp.subscription']
        due_day = Sub._billing_due_day()
        apply_tax = Sub._billing_apply_tax()
        for move in self:
            if move.state != 'draft' or move.move_type != 'out_invoice':
                continue
            if not move.subscription_id or move.isp_invoice_kind == 'installation':
                continue
            period = move.invoice_date or fields.Date.context_today(self)
            period = period.replace(day=1)
            last_day = monthrange(period.year, period.month)[1]
            due = period.replace(day=min(due_day or 1, last_day))
            vals = {}
            if move.invoice_date != period:
                vals['invoice_date'] = period
            if move.invoice_date_due != due:
                vals['invoice_date_due'] = due
            if vals:
                move.write(vals)
            if not apply_tax:
                move._isp_clear_taxes()
        return True

    def _isp_is_current_period(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        start = today.replace(day=1)
        last = monthrange(today.year, today.month)[1]
        end = today.replace(day=last)
        invoice_date = self.invoice_date
        return bool(invoice_date and start <= invoice_date <= end)

    def _invoice_paid_hook(self):
        super()._invoice_paid_hook()
        for move in self:
            try:
                move._isp_try_unisolate_on_payment()
            except Exception:
                _logger.exception(
                    'Buka isolir otomatis gagal (pembayaran tetap tercatat) invoice %s',
                    move.display_name,
                )

    def _isp_try_unisolate_on_payment(self):
        """Buka isolir ke router CPE setelah tagihan bulan berjalan lunas."""
        self.ensure_one()
        if self.move_type != 'out_invoice':
            return
        if self.isp_invoice_kind == 'installation':
            return
        sub = self.subscription_id
        if not sub:
            return
        if not self._isp_is_current_period():
            return
        if self.state != 'posted':
            return
        if self.payment_state not in ('paid', 'in_payment') and not self.currency_id.is_zero(self.amount_residual):
            return
        if not sub.current_period_invoice_is_paid():
            return
        sub.try_unisolate_after_payment()

    def action_isp_pay_cash(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Bayar tunai / Kas',
            'res_model': 'isp.cash.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_invoice_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_subscription_id': self.subscription_id.id,
                'default_amount': self.amount_residual,
            },
        }

    def action_isp_upload_proof(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Unggah bukti transfer',
            'res_model': 'isp.payment.proof',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_invoice_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_subscription_id': self.subscription_id.id,
                'default_amount_ocr': self.amount_residual,
                'default_area_id': self.partner_id.area_id.id,
            },
        }

    def action_isp_copy_upload_link(self):
        self.ensure_one()
        if self.move_type != 'out_invoice':
            raise UserError('Tautan unggah hanya untuk invoice pelanggan.')
        self._isp_ensure_upload_token()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Tautan unggah bukti',
                'message': self.isp_upload_url,
                'sticky': True,
                'type': 'success',
            },
        }

    def action_register_payment(self):
        if self.env.user._isp_is_submitter_only():
            raise UserError(
                'Admin desa/kolektor tidak boleh post pembayaran lunas. '
                'Unggah bukti atau ajukan kas, admin pusat yang verifikasi.'
            )
        return super().action_register_payment()

    def _isp_ensure_journal_inbound_account(self, journal):
        """Pakai akun bank/kas jurnal, bukan 'Tanda Terima Belum Lunas'."""
        if not journal or journal.type not in ('bank', 'cash'):
            return journal
        account = journal.default_account_id
        if not account or account.account_type != 'asset_cash':
            return journal
        for line in journal.inbound_payment_method_line_ids:
            current = line.payment_account_id
            if not current or current.account_type != 'asset_cash':
                line.payment_account_id = account.id
        return journal

    def _isp_register_inbound_payment(self, journal, amount, payment_date=None, communication=None):
        """Catat pembayaran inbound standar Odoo (account.payment.register) lalu post."""
        self.ensure_one()
        if self.env.user._isp_is_submitter_only():
            raise UserError(
                'Admin desa/kolektor tidak boleh mem-post account.payment. '
                'Ajukan ke antrian, admin pusat yang verifikasi.'
            )
        if self.state != 'posted':
            raise UserError('Invoice harus dipost sebelum menerima pembayaran.')
        if self.payment_state == 'paid':
            raise UserError('Invoice sudah lunas.')
        if not journal:
            raise UserError('Jurnal pembayaran belum dipilih.')
        journal = self._isp_ensure_journal_inbound_account(journal)
        amount = amount or self.amount_residual
        payment_date = payment_date or fields.Date.context_today(self)
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move',
            active_ids=self.ids,
            dont_redirect_to_payments=True,
            isp_allow_payment_post=True,
            force_payment_move=True,
        ).create({
            'journal_id': journal.id,
            'amount': amount,
            'payment_date': payment_date,
            'communication': communication or self.name or '/',
        })
        payment = wizard._create_payments()
        payment._isp_finalize_inbound_payment(self)
        self.invalidate_recordset(['amount_residual', 'payment_state'])
        return payment


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    def _get_outstanding_account(self, payment_type):
        """Verifikasi HQ: inbound bank/kas langsung ke akun jurnal, bukan outstanding."""
        if payment_type == 'inbound':
            account = self._isp_inbound_liquidity_account()
            if account and account.account_type == 'asset_cash':
                return account
        return super()._get_outstanding_account(payment_type)

    def _isp_inbound_liquidity_account(self):
        """Akun kas/bank jurnal untuk pembayaran inbound yang sudah diverifikasi."""
        self.ensure_one()
        journal = self.journal_id
        if journal and journal.type in ('bank', 'cash'):
            self.env['account.move']._isp_ensure_journal_inbound_account(journal)
            account = journal.default_account_id
            if account and account.account_type == 'asset_cash':
                return account
        method_account = self.payment_method_line_id.payment_account_id
        if method_account and method_account.account_type == 'asset_cash':
            return method_account
        return journal.default_account_id if journal else self.env['account.account']

    def _isp_apply_bank_liquidity(self):
        """Pindahkan baris likuiditas dari outstanding ke akun bank/kas jurnal."""
        rec_types = self._get_valid_payment_account_types()
        for payment in self:
            if payment.payment_type != 'inbound':
                continue
            account = payment._isp_inbound_liquidity_account()
            if not account:
                raise UserError(
                    'Jurnal %s belum punya akun bank/kas. '
                    'Isi akun jurnal atau metode pembayaran inbound.'
                    % payment.journal_id.display_name
                )
            if payment.outstanding_account_id != account:
                payment._write({'outstanding_account_id': account.id})
                payment.invalidate_recordset(['outstanding_account_id'])
            if not payment.move_id:
                continue
            liquidity = payment.move_id.line_ids.filtered(
                lambda l: l.account_id
                and l.account_id.account_type not in rec_types
                and l.account_id != account
            )
            if liquidity:
                liquidity.with_context(skip_readonly_check=True).write({
                    'account_id': account.id,
                })
        return self

    def _isp_finalize_inbound_payment(self, invoice=None):
        """Jurnal ke bank/kas, rekonsiliasi piutang, status Lunas jika residual 0."""
        for payment in self:
            payment._isp_apply_bank_liquidity()
            if not payment.move_id:
                payment._generate_journal_entry()
            if payment.move_id and payment.move_id.state == 'draft':
                payment.move_id.action_post()
            payment._isp_apply_bank_liquidity()
            target = invoice or payment.reconciled_invoice_ids[:1]
            if target:
                rec_types = self._get_valid_payment_account_types()
                inv_lines = target.line_ids.filtered(
                    lambda l: l.account_id.account_type in rec_types and not l.reconciled
                )
                pay_lines = payment.move_id.line_ids.filtered(
                    lambda l: l.account_id in inv_lines.account_id and not l.reconciled
                )
                if inv_lines and pay_lines:
                    (inv_lines + pay_lines).reconcile()
                target.invalidate_recordset(['amount_residual', 'payment_state'])
            payment.invalidate_recordset([
                'state', 'is_matched', 'is_reconciled', 'outstanding_account_id',
            ])
            if (
                payment.state == 'in_process'
                and payment.outstanding_account_id
                and payment.outstanding_account_id.account_type == 'asset_cash'
            ):
                payment.state = 'paid'
            if target:
                target.invalidate_recordset(['amount_residual', 'payment_state'])
        return self


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def _create_payments(self):
        if self.env.user._isp_is_submitter_only() and not self.env.context.get('isp_allow_payment_post'):
            raise UserError(
                'Admin desa/kolektor tidak boleh membuat pembayaran lunas. '
                'Ajukan bukti transfer atau kas, admin pusat yang verifikasi.'
            )
        return super()._create_payments()
