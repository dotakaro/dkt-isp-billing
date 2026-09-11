import base64
import logging

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


class IspPublicUploadController(http.Controller):

    def _invoice_from_token(self, token):
        token = (token or '').strip()
        if not token or len(token) < 16:
            return request.env['account.move']
        return request.env['account.move'].sudo().search([
            ('isp_upload_token', '=', token),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
        ], limit=1)

    def _dest_info(self):
        ICP = request.env['ir.config_parameter'].sudo()
        return {
            'bank': ICP.get_param('dkt_isp_billing.dest_bank_name', '') or '',
            'account': ICP.get_param('dkt_isp_billing.dest_account_number', '') or '',
            'name': ICP.get_param('dkt_isp_billing.dest_account_name', '') or '',
        }

    def _render(self, invoice, token, error=None, success=False):
        dest = self._dest_info()
        return request.render('dkt_isp_billing.public_upload_page', {
            'invoice': invoice,
            'token': token,
            'error': error,
            'success': success,
            'dest_bank': dest['bank'],
            'dest_account': dest['account'],
            'dest_name': dest['name'],
            'company_name': request.env.company.name,
            'amount_due': invoice.amount_residual if invoice else 0.0,
            'currency': invoice.currency_id if invoice else request.env.company.currency_id,
            'can_upload': bool(
                invoice
                and invoice.payment_state in ('not_paid', 'partial', 'in_payment')
            ),
            'csrf_token': request.csrf_token(),
        })

    @http.route(
        '/isp/unggah/<string:token>',
        type='http',
        auth='public',
        methods=['GET', 'POST'],
        csrf=True,
        website=True,
    )
    def public_upload(self, token, **post):
        invoice = self._invoice_from_token(token)
        if not invoice:
            return self._render(invoice, token, error='Tautan tidak valid atau sudah tidak berlaku.')
        if request.httprequest.method != 'POST':
            return self._render(invoice, token)
        if invoice.payment_state in ('paid', 'reversed'):
            return self._render(invoice, token, error='Tagihan ini sudah lunas.')
        upload = request.httprequest.files.get('isp_proof')
        public_name = (post.get('public_name') or '').strip()
        public_phone = (post.get('public_phone') or '').strip()
        if not public_name or not public_phone:
            return self._render(invoice, token, error='Isi nama dan nomor HP.')
        if not upload:
            return self._render(invoice, token, error='Pilih file bukti transfer.')
        raw = upload.read()
        filename = upload.filename or 'bukti.png'
        try:
            Proof = request.env['isp.payment.proof'].sudo()
            Proof._validate_upload_payload(raw, filename)
            Proof.create_from_customer_upload({
                'invoice_id': invoice.id,
                'partner_id': invoice.partner_id.id,
                'subscription_id': invoice.subscription_id.id,
                'area_id': invoice.partner_id.area_id.id,
                'image': base64.b64encode(raw),
                'image_filename': filename,
                'public_name': public_name[:80],
                'public_phone': public_phone[:40],
            }, source='public', ip=request.httprequest.remote_addr, token=token)
        except UserError as exc:
            return self._render(invoice, token, error=str(exc))
        except Exception:
            _logger.exception('Unggah publik gagal token invoice %s', invoice.id)
            return self._render(invoice, token, error='Unggah gagal. Coba lagi nanti.')
        return self._render(invoice, token, success=True)
