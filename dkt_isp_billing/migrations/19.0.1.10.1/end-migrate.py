import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Kunci isolir otomatis OFF. Jangan isolir, jangan kirim WA, jangan post draft."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()
    ICP.set_param('dkt_isp_billing.auto_isolate_enabled', 'False')
    ICP.set_param(
        'dkt_isp_billing.auto_isolate_backend',
        ICP.get_param('dkt_isp_billing.auto_isolate_backend') or 'mikrotik_secret',
    )
    ICP.set_param(
        'dkt_isp_billing.auto_isolate_trigger',
        ICP.get_param('dkt_isp_billing.auto_isolate_trigger') or 'late_day',
    )
    ICP.set_param('dkt_isp_billing.apply_package_rate_limit', 'False')
    ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
    cron = env.ref('dkt_isp_billing.ir_cron_isp_auto_isolate', raise_if_not_found=False)
    if cron and cron.active:
        cron.active = False
        _logger.info('end-migrate: cron isolir otomatis dimatikan.')
    _logger.info(
        'end-migrate 19.0.1.10.1: auto_isolate_enabled=False. '
        'Tidak isolir pelanggan, tidak kirim WA, tidak post draft.',
    )
