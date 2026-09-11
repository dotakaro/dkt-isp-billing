import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Aktifkan inbound WA. Token Fonnte tidak disentuh. Tidak blast."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()
    ICP.set_param('dkt_isp_billing.wa_inbound_enabled', 'True')
    ICP.set_param('dkt_isp_billing.ocr_auto_post_enabled', 'False')
    ICP.set_param('dkt_isp_billing.auto_isolate_enabled', 'False')
    _logger.info(
        'end-migrate 19.0.1.14.0: inbound WA=True, auto-post=False, isolir=False. '
        'Token tidak diubah. Tidak kirim WA, tidak post draft.',
    )
