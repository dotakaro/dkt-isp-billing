import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Kunci dry-run WA, jangan kirim, jangan isolir, jangan post draft, rate-limit mati."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()
    ICP.set_param('dkt_isp_billing.due_day', '1')
    ICP.set_param('dkt_isp_billing.late_day', '21')
    ICP.set_param('dkt_isp_billing.apply_tax', 'False')
    ICP.set_param('dkt_isp_billing.apply_package_rate_limit', 'False')
    ICP.set_param('dkt_isp_billing.auto_isolate_enabled', 'False')
    ICP.set_param(
        'dkt_isp_billing.auto_isolate_backend',
        ICP.get_param('dkt_isp_billing.auto_isolate_backend') or 'mikrotik_secret',
    )
    ICP.set_param(
        'dkt_isp_billing.auto_isolate_trigger',
        ICP.get_param('dkt_isp_billing.auto_isolate_trigger') or 'late_day',
    )
    ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
    ICP.set_param('dkt_isp_billing.wa_provider', ICP.get_param('dkt_isp_billing.wa_provider') or 'fonnte')
    ICP.set_param('dkt_isp_billing.wa_rate_delay', ICP.get_param('dkt_isp_billing.wa_rate_delay') or '0.5')
    ICP.set_param(
        'dkt_isp_billing.ocr_auto_post_threshold',
        ICP.get_param('dkt_isp_billing.ocr_auto_post_threshold') or '0.85',
    )
    ICP.set_param(
        'dkt_isp_billing.wa_meta_api_version',
        ICP.get_param('dkt_isp_billing.wa_meta_api_version') or 'v21.0',
    )
    _logger.info(
        'end-migrate 19.0.1.10.0: WA dry-run=True, isolir otomatis=False, '
        'rate-limit paket=False. Tidak mengirim WhatsApp, tidak isolir, tidak post draft.'
    )
