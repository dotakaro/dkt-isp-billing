import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Kunci kebijakan: due_day=1, pajak 0, due_date tgl 1. Jangan post, jangan isolir."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()
    ICP.set_param('dkt_isp_billing.due_day', '1')
    ICP.set_param('dkt_isp_billing.late_day', '21')
    ICP.set_param('dkt_isp_billing.apply_tax', 'False')
    ICP.set_param('dkt_isp_billing.apply_package_rate_limit', 'False')
    Sub = env['isp.subscription']
    try:
        due_day = Sub._billing_due_day()
        wrong = Sub.search([('due_day', '!=', due_day)])
        if wrong:
            wrong.write({'due_day': due_day})
        _logger.info('end-migrate due_day=%s pada %s langganan.', due_day, len(wrong))
    except Exception:
        _logger.exception('end-migrate: gagal merapikan due_day.')

    try:
        drafts = env['account.move'].search([
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'draft'),
            ('subscription_id', '!=', False),
            ('isp_invoice_kind', '!=', 'installation'),
        ])
        drafts._isp_apply_billing_policy()
        _logger.info(
            'end-migrate draft tagihan: %s diperbaiki (pajak 0, due tgl 1). Tidak di-post.',
            len(drafts),
        )
    except Exception:
        _logger.exception('end-migrate: gagal merapikan draft invoice.')

    for xmlid in (
        'dkt_isp_billing.ir_cron_subscription_due_notice',
        'dkt_isp_billing.ir_cron_subscription_overdue_notice',
    ):
        cron = env.ref(xmlid, raise_if_not_found=False)
        if cron and cron.active:
            cron.active = False
            _logger.info('end-migrate: cron notifikasi %s dimatikan.', xmlid)

    try:
        active = Sub.search([('state', 'in', ['open', 'isolated'])])
        active._compute_is_special_treatment()
        active._compute_is_late()
        _logger.info('end-migrate: hitung ulang is_late / perlakuan khusus (%s).', len(active))
    except Exception:
        _logger.exception('end-migrate: gagal hitung ulang flag telat.')
