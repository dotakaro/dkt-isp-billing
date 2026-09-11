from odoo import api, fields, models
from odoo.exceptions import UserError


class IspCashPaymentWizard(models.TransientModel):
    _name = 'isp.cash.payment.wizard'
    _description = 'Bayar tunai / Kas'

    partner_id = fields.Many2one(
        'res.partner', string='Pelanggan', required=True,
        domain=[('customer_rank', '>', 0)],
    )
    subscription_id = fields.Many2one(
        'isp.subscription', string='Langganan',
        domain="[('partner_id', '=', partner_id)]",
    )
    invoice_id = fields.Many2one(
        'account.move', string='Invoice',
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), "
               "('partner_id', '=', partner_id), "
               "('payment_state', 'in', ('not_paid', 'partial', 'in_payment'))]",
    )
    journal_id = fields.Many2one(
        'account.journal', string='Jurnal Kas',
        domain="[('type', '=', 'cash')]",
    )
    amount = fields.Monetary('Jumlah', currency_field='currency_id')
    payment_date = fields.Date('Tanggal', required=True, default=fields.Date.context_today)
    communication = fields.Char('Keterangan', default='Bayar tunai / Kas')
    currency_id = fields.Many2one(
        'res.currency', compute='_compute_currency_id',
    )
    amount_residual = fields.Monetary(
        related='invoice_id.amount_residual', string='Sisa invoice', readonly=True,
    )
    is_submitter_only = fields.Boolean(compute='_compute_is_submitter_only')
    note = fields.Text('Kebijakan', readonly=True)

    @api.depends('invoice_id', 'invoice_id.currency_id')
    def _compute_currency_id(self):
        for wiz in self:
            wiz.currency_id = wiz.invoice_id.currency_id or self.env.company.currency_id

    @api.depends_context('uid')
    def _compute_is_submitter_only(self):
        is_sub = self.env.user._isp_is_submitter_only()
        for wiz in self:
            wiz.is_submitter_only = is_sub

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if not res.get('journal_id'):
            journal = self._default_cash_journal()
            if journal:
                res['journal_id'] = journal.id
        if self.env.user._isp_is_submitter_only():
            res['note'] = (
                'Kas diajukan ke antrian verifikasi. Admin pusat yang cek dan post jurnal. '
                'Bukan lunas otomatis.'
            )
        else:
            res['note'] = (
                'Admin pusat boleh post langsung ke jurnal Kas. '
                'Admin desa/kolektor hanya mengajukan antrian.'
            )
        return res

    @api.model
    def _default_cash_journal(self):
        ICP = self.env['ir.config_parameter'].sudo()
        raw = ICP.get_param('dkt_isp_billing.cash_journal_id', '')
        if raw and str(raw).isdigit():
            journal = self.env['account.journal'].browse(int(raw))
            if journal.exists() and journal.type == 'cash':
                return journal
        return self.env['account.journal'].search([
            ('type', '=', 'cash'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.subscription_id and self.subscription_id.partner_id != self.partner_id:
            self.subscription_id = False
        if self.invoice_id and self.invoice_id.partner_id != self.partner_id:
            self.invoice_id = False
        if self.partner_id and not self.subscription_id:
            self.subscription_id = self.partner_id.subscription_ids[:1]
        if self.partner_id and not self.invoice_id:
            self._suggest_invoice()

    @api.onchange('subscription_id')
    def _onchange_subscription_id(self):
        if self.subscription_id:
            self.partner_id = self.subscription_id.partner_id
            self._suggest_invoice()

    @api.onchange('invoice_id')
    def _onchange_invoice_id(self):
        if self.invoice_id:
            self.amount = self.invoice_id.amount_residual
            self.partner_id = self.invoice_id.partner_id
            if self.invoice_id.subscription_id:
                self.subscription_id = self.invoice_id.subscription_id

    def _suggest_invoice(self):
        invoice = False
        if self.subscription_id:
            invoice = self.subscription_id._current_period_invoice()
            if invoice and invoice.payment_state == 'paid':
                invoice = False
        if not invoice and self.partner_id:
            invoice = self.env['account.move'].search([
                ('partner_id', '=', self.partner_id.id),
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('payment_state', 'in', ('not_paid', 'partial', 'in_payment')),
            ], order='invoice_date desc', limit=1)
        if invoice:
            self.invoice_id = invoice
            self.amount = invoice.amount_residual

    def _validate(self):
        self.ensure_one()
        if not self.invoice_id:
            raise UserError('Pilih invoice yang akan dibayar.')
        if not self.journal_id or self.journal_id.type != 'cash':
            raise UserError('Pilih jurnal Kas.')
        if self.amount <= 0:
            raise UserError('Jumlah harus lebih dari 0.')

    def action_submit(self):
        """Desa/kolektor: antrian kas, belum lunas."""
        self._validate()
        proof = self.env['isp.payment.proof'].create({
            'proof_type': 'cash',
            'partner_id': self.partner_id.id,
            'subscription_id': self.subscription_id.id,
            'invoice_id': self.invoice_id.id,
            'area_id': self.partner_id.area_id.id,
            'amount_manual': self.amount,
            'date_ocr': self.payment_date,
            'journal_id': self.journal_id.id,
            'ocr_note': self.communication or 'Pengajuan kas tunai.',
            'source': self.env.user._isp_submitter_source(),
        })
        proof._action_submit_queue(source=self.env.user._isp_submitter_source())
        return {
            'type': 'ir.actions.act_window',
            'name': 'Pengajuan kas',
            'res_model': 'isp.payment.proof',
            'res_id': proof.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_confirm(self):
        self._validate()
        if self.env.user._isp_is_submitter_only():
            return self.action_submit()
        payment = self.invoice_id._isp_register_inbound_payment(
            self.journal_id,
            self.amount,
            self.payment_date,
            self.communication or 'Bayar tunai / Kas',
        )
        return {
            'type': 'ir.actions.act_window',
            'name': 'Pembayaran',
            'res_model': 'account.payment',
            'res_id': payment.id,
            'view_mode': 'form',
            'target': 'current',
        }
