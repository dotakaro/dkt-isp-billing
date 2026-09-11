import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Backfill phone dari username PPPoE. Jangan kirim WA, isolir, atau post draft."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()
    ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
    ICP.set_param('dkt_isp_billing.auto_isolate_enabled', 'False')
    ICP.set_param('dkt_isp_billing.apply_package_rate_limit', 'False')
    cron = env.ref('dkt_isp_billing.ir_cron_isp_auto_isolate', raise_if_not_found=False)
    if cron and cron.active:
        cron.active = False
        _logger.info('end-migrate: cron isolir otomatis dimatikan.')
    stats = env['res.partner']._backfill_phone_from_pppoe_username()
    _logger.info(
        'end-migrate 19.0.1.11.0: parse username=%s skip=%s phone_filled=%s '
        'contoh_gagal=%s. WA dry-run=True, isolir=False, rate-limit=False.',
        stats.get('parsed'),
        stats.get('skipped'),
        stats.get('filled'),
        (stats.get('skip_examples') or [])[:3],
    )
