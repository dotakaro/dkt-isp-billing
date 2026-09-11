import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class IspIsolirLandingController(http.Controller):

    @http.route('/isp/isolir', type='http', auth='public', website=True, sitemap=False)
    def isolir_landing(self, **kwargs):
        ICP = request.env['ir.config_parameter'].sudo()
        dest = {
            'bank': ICP.get_param('dkt_isp_billing.dest_bank_name', '') or '',
            'account': ICP.get_param('dkt_isp_billing.dest_account_number', '') or '',
            'name': ICP.get_param('dkt_isp_billing.dest_account_name', '') or '',
        }
        company = request.env.company.sudo()
        return request.render('dkt_isp_billing.isolir_landing_page', {
            'company_name': company.name or 'DOTAKARO',
            'dest_bank': dest['bank'],
            'dest_account': dest['account'],
            'dest_name': dest['name'],
        })
