from odoo import fields, models


class ISPBillingRunWizard(models.TransientModel):
    _name = 'isp.billing.run.wizard'
    _description = 'Proses tagihan ISP'

    fetch_mikrotik = fields.Boolean(
        'Baca comment dari MikroTik',
        default=True,
        help='Hanya membaca /ppp/secret. Tidak mengubah profile atau secret.',
    )
    log = fields.Text('Hasil', readonly=True)
    mapped_150 = fields.Integer('Paket 150', readonly=True)
    mapped_200 = fields.Integer('Paket 200', readonly=True)
    mapped_250 = fields.Integer('Paket 250', readonly=True)
    mapped_300 = fields.Integer('Paket 300', readonly=True)
    mapped_350 = fields.Integer('Paket 350', readonly=True)
    mapped_500 = fields.Integer('Paket 500', readonly=True)
    mapped_600 = fields.Integer('Paket 600', readonly=True)
    mapped_unknown = fields.Integer('Belum diketahui', readonly=True)
    invoices_created = fields.Integer('Draft dibuat', readonly=True)
    invoices_skipped = fields.Integer('Dilewati', readonly=True)
    invoices_failed = fields.Integer('Gagal', readonly=True)
    drafts_fixed = fields.Integer('Draft dirapikan', readonly=True)
    policy_note = fields.Text(
        'Kebijakan',
        readonly=True,
        default=(
            'Periode = bulan berjalan. Jatuh tempo tanggal 1. Telat mulai tanggal 21. '
            'Pajak 0. Draft tidak di-post otomatis. Isolir hanya lewat Isolir massal. '
            'Pemberitahuan tidak dikirim. Profile MikroTik tetap SAPU-JAGAD.'
        ),
    )

    def action_remap(self):
        self.ensure_one()
        Sub = self.env['isp.subscription']
        stats = Sub.remap_commercial_packages(fetch_mikrotik=self.fetch_mikrotik)
        self.write({
            'mapped_150': stats.get('PAKET_150', 0),
            'mapped_200': stats.get('PAKET_200', 0),
            'mapped_250': stats.get('PAKET_250', 0),
            'mapped_300': stats.get('PAKET_300', 0),
            'mapped_350': stats.get('PAKET_350', 0),
            'mapped_500': stats.get('PAKET_500', 0),
            'mapped_600': stats.get('PAKET_600', 0),
            'mapped_unknown': stats.get('unknown', 0),
            'log': self._format_remap_log(stats),
        })
        return self._reopen()

    def action_generate_drafts(self):
        self.ensure_one()
        Sub = self.env['isp.subscription']
        Sub._ensure_indonesian_accounting()
        result = Sub.cron_generate_invoices() or {}
        created = result.get('created')
        skipped = result.get('skipped') or []
        errors = result.get('errors') or []
        lines = [
            'Draft tagihan bulan berjalan (tidak di-post).',
            'Dibuat: %s' % (len(created) if created else 0),
            'Dilewati: %s' % len(skipped),
            'Gagal: %s' % len(errors),
        ]
        for item in skipped[:20]:
            sub = item.get('subscription')
            lines.append('- %s: %s' % (sub.display_name if sub else '-', item.get('reason')))
        for item in errors[:10]:
            sub = item.get('subscription')
            lines.append('- GAGAL %s: %s' % (sub.display_name if sub else '-', item.get('error')))
        self.write({
            'invoices_created': len(created) if created else 0,
            'invoices_skipped': len(skipped),
            'invoices_failed': len(errors),
            'log': '\n'.join(lines),
        })
        return self._reopen()

    def action_fix_drafts(self):
        self.ensure_one()
        drafts = self.env['account.move'].search([
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'draft'),
            ('subscription_id', '!=', False),
            ('isp_invoice_kind', '!=', 'installation'),
        ])
        drafts._isp_apply_billing_policy()
        self.write({
            'drafts_fixed': len(drafts),
            'log': (
                'Draft dirapikan: %s. Pajak 0, jatuh tempo tanggal 1 periode. Tidak di-post.'
            ) % len(drafts),
        })
        return self._reopen()

    def _format_remap_log(self, stats):
        return '\n'.join([
            'Pemetaan paket komersial (profile MikroTik tidak diubah).',
            'Paket 150: %s' % stats.get('PAKET_150', 0),
            'Paket 200: %s' % stats.get('PAKET_200', 0),
            'Paket 250: %s' % stats.get('PAKET_250', 0),
            'Paket 300: %s' % stats.get('PAKET_300', 0),
            'Paket 350: %s' % stats.get('PAKET_350', 0),
            'Paket 500: %s' % stats.get('PAKET_500', 0),
            'Paket 600: %s' % stats.get('PAKET_600', 0),
            'Tidak dikenal / tanpa harga: %s' % stats.get('unknown', 0),
            'Langganan diubah: %s' % stats.get('updated', 0),
            'Sudah sesuai: %s' % stats.get('unchanged', 0),
        ])

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
