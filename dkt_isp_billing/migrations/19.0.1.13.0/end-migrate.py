import logging
import secrets

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Kunci auto-post, isi token unggah invoice, jangan kirim WA/isolir/post draft."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()
    ICP.set_param('dkt_isp_billing.ocr_auto_post_enabled', 'False')
    ICP.set_param('dkt_isp_billing.wa_dry_run', 'True')
    ICP.set_param('dkt_isp_billing.auto_isolate_enabled', 'False')
    invoices = env['account.move'].search([
        ('move_type', '=', 'out_invoice'),
        ('isp_upload_token', '=', False),
    ])
    for move in invoices:
        move.isp_upload_token = secrets.token_urlsafe(24)
    _logger.info(
        'end-migrate 19.0.1.13.0: auto-post OCR=False, %s token unggah invoice, '
        'WA dry-run, isolir=False. Tidak post draft, tidak kirim WA.',
        len(invoices),
    )
