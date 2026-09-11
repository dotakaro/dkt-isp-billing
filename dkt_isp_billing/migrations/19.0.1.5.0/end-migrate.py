import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Petakan token 150/600 ke paket baru, lalu buat draft tagihan mereka."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    try:
        review = env['isp.subscription'].search([
            ('billing_review_needed', '=', True),
            ('state', 'in', ['open', 'isolated', 'draft']),
        ])
        stats = review.remap_commercial_packages(fetch_mikrotik=False)
        _logger.info('end-migrate remap paket 150/600: %s', stats)
        mapped = env['isp.subscription'].search([
            ('package_id.code', 'in', ['PAKET_150', 'PAKET_600']),
            ('billing_review_needed', '=', False),
            ('state', 'in', ['open', 'isolated']),
        ])
        env['isp.subscription']._ensure_indonesian_accounting()
        result = mapped.with_context(generate_invoice_silent=True).generate_invoice()
        created = result.get('created') if isinstance(result, dict) else False
        _logger.info(
            'end-migrate draft 150/600: dibuat=%s dilewati=%s gagal=%s',
            len(created) if created else 0,
            len(result.get('skipped') or []) if isinstance(result, dict) else 0,
            len(result.get('errors') or []) if isinstance(result, dict) else 0,
        )
    except Exception:
        _logger.exception('end-migrate: gagal memetakan paket 150/600 atau membuat draft.')
