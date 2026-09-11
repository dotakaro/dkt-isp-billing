import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Kunci dry-run WA. Jangan kirim WA, jangan isolir, rate-limit paket mati."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()
    ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
    ICP.set_param('dkt_isp_billing.auto_isolate_enabled', 'False')
    ICP.set_param('dkt_isp_billing.apply_package_rate_limit', 'False')
    ICP.set_param(
        'dkt_isp_billing.wa_provider',
        ICP.get_param('dkt_isp_billing.wa_provider') or 'fonnte',
    )
    ICP.set_param(
        'dkt_isp_billing.wa_meta_template_lang',
        ICP.get_param('dkt_isp_billing.wa_meta_template_lang') or 'id',
    )
    ICP.set_param(
        'dkt_isp_billing.wa_meta_api_version',
        ICP.get_param('dkt_isp_billing.wa_meta_api_version') or 'v21.0',
    )
    cron = env.ref('dkt_isp_billing.ir_cron_isp_auto_isolate', raise_if_not_found=False)
    if cron and cron.active:
        cron.active = False
        _logger.info('end-migrate: cron isolir otomatis dimatikan.')
    _logger.info(
        'end-migrate 19.0.1.10.2: WA dry-run=True, isolir otomatis=False, '
        'rate-limit paket=False. Tidak mengirim WhatsApp, tidak isolir, tidak post draft.',
    )
