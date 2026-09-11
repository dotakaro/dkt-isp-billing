import base64
import logging

from odoo import http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

from odoo.addons.account.controllers.portal import PortalAccount

_logger = logging.getLogger(__name__)


class PortalIspPayment(PortalAccount):

    def _invoice_get_page_view_values(self, invoice, access_token, **kwargs):
        values = super()._invoice_get_page_view_values(invoice, access_token, **kwargs)
        proofs = request.env['isp.payment.proof'].sudo().search([
            ('invoice_id', '=', invoice.id),
        ], limit=8)
        values['isp_proofs'] = proofs
        invoice.sudo()._isp_ensure_upload_token()
        values['isp_public_upload_url'] = invoice.isp_upload_url
        values['isp_can_upload'] = (
            invoice.move_type == 'out_invoice'
            and invoice.state == 'posted'
            and invoice.payment_state in ('not_paid', 'partial', 'in_payment')
            and not request.env.user._is_public()
        )
        values['isp_upload_error'] = kwargs.get('isp_upload_error')
        values['isp_upload_ok'] = kwargs.get('isp_upload_ok')
        return values

    @http.route(
        ['/my/invoices/<int:invoice_id>/isp_upload'],
        type='http',
        auth='user',
        methods=['POST'],
        website=True,
        csrf=True,
    )
    def portal_isp_upload_proof(self, invoice_id, **post):
        try:
            invoice = self._document_check_access('account.move', invoice_id, post.get('access_token'))
        except (AccessError, MissingError):
            return request.redirect('/my')
        if invoice.payment_state in ('paid', 'reversed'):
            return request.redirect(invoice.get_portal_url())
        upload = request.httprequest.files.get('isp_proof')
        error = False
        if not upload:
            error = 'Pilih file bukti transfer.'
        else:
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
                    'public_name': request.env.user.partner_id.name,
                    'public_phone': request.env.user.partner_id.phone or request.env.user.partner_id.mobile,
                }, source='portal', ip=request.httprequest.remote_addr)
            except UserError as exc:
                error = str(exc)
            except Exception:
                _logger.exception('Portal unggah bukti gagal invoice %s', invoice_id)
                error = 'Unggah gagal. Coba lagi atau hubungi admin desa.'
        url = invoice.get_portal_url()
        if error:
            return request.redirect('%s%sisp_upload_error=1' % (url, '&' if '?' in url else '?'))
        return request.redirect('%s%sisp_upload_ok=1' % (url, '&' if '?' in url else '?'))
