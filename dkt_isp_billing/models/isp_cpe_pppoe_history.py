from datetime import timedelta
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

PPPOE_HISTORY_RETENTION_DAYS = 90


class ISPCpePppoeHistory(models.Model):
    _name = 'isp.cpe.pppoe.history'
    _description = 'Riwayat Status PPPoE CPE'
    _order = 'timestamp desc, id desc'

    cpe_id = fields.Many2one(
        'isp.cpe', string='CPE', required=True, ondelete='cascade', index=True,
    )
    partner_id = fields.Many2one(
        related='cpe_id.partner_id', store=True, string='Pelanggan',
    )
    timestamp = fields.Datetime(
        'Waktu', required=True, default=fields.Datetime.now, index=True,
    )
    old_status = fields.Selection(
        [
            ('connected', 'Connected'),
            ('disconnected', 'Disconnected'),
            ('unknown', 'Unknown'),
        ],
        string='Status lama',
    )
    new_status = fields.Selection(
        [
            ('connected', 'Connected'),
            ('disconnected', 'Disconnected'),
            ('unknown', 'Unknown'),
        ],
        string='Status baru',
        required=True,
    )
    event_type = fields.Selection(
        [
            ('status_change', 'Perubahan status'),
            ('multisession_enter', 'Masuk multi-sesi'),
            ('multisession_leave', 'Keluar multi-sesi'),
        ],
        string='Jenis event',
        default='status_change',
        required=True,
        index=True,
    )
    session_count = fields.Integer('Jumlah sesi', default=0)
    uptime = fields.Char('Uptime')
    caller_id = fields.Char('Caller ID')
    ip_address = fields.Char('IP')
    router_id = fields.Many2one('isp.mikrotik.config', string='Router')
    source = fields.Selection(
        [
            ('cron', 'Cron'),
            ('manual', 'Manual'),
            ('monitor', 'Monitor'),
        ],
        string='Sumber',
        default='cron',
        required=True,
    )

    @api.autovacuum
    def _gc_pppoe_history(self):
        """Hapus riwayat lebih dari 90 hari (ir.autovacuum harian)."""
        limit = fields.Datetime.now() - timedelta(days=PPPOE_HISTORY_RETENTION_DAYS)
        old = self.search([('timestamp', '<', limit)])
        if old:
            _logger.info(
                'Hapus %s riwayat PPPoE lebih dari %s hari',
                len(old), PPPOE_HISTORY_RETENTION_DAYS,
            )
            old.unlink()
