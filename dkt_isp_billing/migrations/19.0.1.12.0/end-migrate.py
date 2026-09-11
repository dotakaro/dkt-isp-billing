import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Set rekening tujuan, kunci konfirmasi OCR. Jangan kirim WA, isolir, atau post draft."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()
    ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
    ICP.set_param('dkt_isp_billing.auto_isolate_enabled', 'False')
    ICP.set_param('dkt_isp_billing.apply_tax', 'False')
    ICP.set_param('dkt_isp_billing.ocr_auto_post_enabled', 'False')
    if not ICP.get_param('dkt_isp_billing.dest_bank_name'):
        ICP.set_param('dkt_isp_billing.dest_bank_name', 'BANK BRI')
    if not ICP.get_param('dkt_isp_billing.dest_account_number'):
        ICP.set_param('dkt_isp_billing.dest_account_number', '014401000343565')
    if not ICP.get_param('dkt_isp_billing.dest_account_name'):
        ICP.set_param('dkt_isp_billing.dest_account_name', 'WASPADA SINULINGGA')
    cron = env.ref('dkt_isp_billing.ir_cron_isp_auto_isolate', raise_if_not_found=False)
    if cron and cron.active:
        cron.active = False
        _logger.info('end-migrate: cron isolir otomatis dimatikan.')
    _logger.info(
        'end-migrate 19.0.1.12.0: rekening tujuan default BRI, auto-post OCR=False, '
        'pajak=0, WA dry-run, isolir=False. Tidak post draft, tidak kirim WA.',
    )
