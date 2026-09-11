import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Petakan paket komersial dari comment tersimpan. CoA dimuat terpisah."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    try:
        stats = env['isp.subscription'].remap_commercial_packages(fetch_mikrotik=False)
        _logger.info('end-migrate remap paket: %s', stats)
    except Exception:
        _logger.exception('end-migrate: gagal memetakan paket komersial.')
