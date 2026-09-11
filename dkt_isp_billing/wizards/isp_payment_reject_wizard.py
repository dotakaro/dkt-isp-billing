from odoo import fields, models
from odoo.exceptions import UserError


class IspPaymentRejectWizard(models.TransientModel):
    _name = 'isp.payment.reject.wizard'
    _description = 'Tolak bukti pembayaran'

    proof_ids = fields.Many2many('isp.payment.proof', string='Bukti')
    reason = fields.Text('Alasan penolakan', required=True)

    def action_reject(self):
        self.ensure_one()
        if not self.env.user._isp_can_verify_payment():
            raise UserError('Hanya admin pusat yang boleh menolak bukti.')
        if not self.reason or not self.reason.strip():
            raise UserError('Isi alasan penolakan.')
        self.proof_ids.action_reject(reason=self.reason.strip())
        return {'type': 'ir.actions.act_window_close'}
