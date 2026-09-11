import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Petakan status lama ke antrian pusat sebelum selection baru dimuat."""
    cr.execute("SELECT to_regclass('isp_payment_proof')")
    if not cr.fetchone()[0]:
        return
    cr.execute("""
        UPDATE isp_payment_proof
           SET state = CASE
                WHEN state IN ('ocr', 'failed', 'manual') THEN 'review'
                WHEN state = 'matched' THEN 'submitted'
                ELSE state
           END
         WHERE state IN ('ocr', 'failed', 'manual', 'matched')
    """)
    _logger.info('pre-migrate 19.0.1.13.0: status bukti lama dipetakan ke submitted/review.')
