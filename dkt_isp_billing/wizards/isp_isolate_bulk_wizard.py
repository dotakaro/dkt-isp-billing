import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ISPIsolateBulkWizard(models.TransientModel):
    _name = 'isp.isolate.bulk.wizard'
    _description = 'Isolir massal per area / CPE'

    area_id = fields.Many2one('isp.area', string='Area / Desa')
    cpe_id = fields.Many2one(
        'isp.cpe',
        string='CPE',
        domain="[('state', '=', 'open')]",
    )
    line_ids = fields.One2many(
        'isp.isolate.bulk.wizard.line', 'wizard_id', string='Kandidat',
    )
    selected_count = fields.Integer(
        'Jumlah terpilih', compute='_compute_selected_count',
    )
    candidate_count = fields.Integer('Jumlah kandidat', readonly=True)
    isolated_count = fields.Integer('Berhasil isolir', readonly=True)
    failed_count = fields.Integer('Gagal', readonly=True)
    skipped_count = fields.Integer('Dilewati', readonly=True)
    log = fields.Text('Hasil', readonly=True)
    note = fields.Text(
        'Kebijakan',
        readonly=True,
        default=(
            'Isolir HANYA untuk yang Anda pilih. Tidak ada isolir otomatis tanggal 21. '
            'Perlakuan khusus (tanpa harga) dikecualikan. Setiap CPE diisolir ke router-nya sendiri.'
        ),
    )

    @api.depends('line_ids.selected')
    def _compute_selected_count(self):
        for wiz in self:
            wiz.selected_count = len(wiz.line_ids.filtered('selected'))

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids') or []
        if active_model == 'isp.subscription' and active_ids:
            vals['log'] = 'Memuat %s langganan terpilih. Klik Muat kandidat.' % len(active_ids)
        elif active_model == 'isp.cpe' and active_ids:
            vals['log'] = 'Memuat %s CPE terpilih. Klik Muat kandidat.' % len(active_ids)
        return vals

    def _check_village_admin_blocked(self):
        user = self.env.user
        if user._isp_is_submitter_only():
            raise UserError(
                'Admin desa/kolektor tidak boleh isolir massal. Isolir tetap lewat aturan existing, bukan dari unggah bukti.'
            )

    def action_load(self):
        self.ensure_one()
        self._check_village_admin_blocked()
        subs = self._find_candidates()
        self.line_ids.unlink()
        lines = [(0, 0, {
            'subscription_id': sub.id,
            'partner_id': sub.partner_id.id,
            'cpe_id': sub.cpe_id.id,
            'area_id': sub.area_id.id,
            'selected': True,
            'is_late': sub.is_late,
            'router_id': sub.cpe_id.mikrotik_config_id.id,
        }) for sub in subs]
        self.write({
            'line_ids': lines,
            'candidate_count': len(subs),
            'isolated_count': 0,
            'failed_count': 0,
            'skipped_count': 0,
            'log': 'Kandidat telat/nunggak: %s. Perlakuan khusus tidak masuk daftar.' % len(subs),
        })
        return self._reopen()

    def _find_candidates(self):
        self.ensure_one()
        domain = [
            ('state', '=', 'open'),
            ('is_special_treatment', '=', False),
            '|',
            ('is_late', '=', True),
            ('overdue_marked', '=', True),
        ]
        if self.area_id:
            domain.append(('area_id', '=', self.area_id.id))
        if self.cpe_id:
            domain.append(('cpe_id', '=', self.cpe_id.id))

        ctx_model = self.env.context.get('active_model')
        ctx_ids = self.env.context.get('active_ids') or []
        if ctx_model == 'isp.subscription' and ctx_ids and not self.area_id and not self.cpe_id:
            domain = [
                ('id', 'in', ctx_ids),
                ('state', '=', 'open'),
                ('is_special_treatment', '=', False),
            ]
        elif ctx_model == 'isp.cpe' and ctx_ids and not self.cpe_id:
            domain.append(('cpe_id', 'in', ctx_ids))
        return self.env['isp.subscription'].search(domain)

    def action_confirm_isolate(self):
        """Isolir yang dipilih ke router CPE masing-masing. Tidak massal otomatis."""
        self.ensure_one()
        self._check_village_admin_blocked()
        selected = self.line_ids.filtered('selected')
        if not selected:
            raise UserError('Tidak ada baris yang dipilih.')
        ok = 0
        failed = 0
        skipped = 0
        lines = [
            'Isolir massal: %s CPE dipilih. Setiap secret ke router CPE-nya.' % len(selected),
        ]
        for line in selected:
            sub = line.subscription_id
            if not sub or sub.is_special_treatment:
                skipped += 1
                lines.append('- Dilewati (perlakuan khusus): %s' % (sub.display_name if sub else '-'))
                continue
            if sub.state == 'isolated':
                skipped += 1
                lines.append('- Sudah terisolir: %s' % sub.display_name)
                continue
            try:
                with self.env.cr.savepoint():
                    success, err = sub.isolate_safe()
                if success:
                    ok += 1
                    lines.append('- OK %s → %s' % (
                        sub.display_name,
                        sub.cpe_id.mikrotik_config_id.display_name or 'router CPE',
                    ))
                else:
                    failed += 1
                    lines.append('- GAGAL %s: %s' % (sub.display_name, err))
            except Exception as exc:
                failed += 1
                _logger.exception('Isolir massal gagal %s', sub.display_name)
                lines.append('- GAGAL %s: %s' % (sub.display_name, exc))
        self.write({
            'isolated_count': ok,
            'failed_count': failed,
            'skipped_count': skipped,
            'log': '\n'.join(lines),
        })
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }


class ISPIsolateBulkWizardLine(models.TransientModel):
    _name = 'isp.isolate.bulk.wizard.line'
    _description = 'Baris isolir massal'

    wizard_id = fields.Many2one('isp.isolate.bulk.wizard', required=True, ondelete='cascade')
    selected = fields.Boolean('Pilih', default=True)
    subscription_id = fields.Many2one('isp.subscription', string='Langganan', required=True)
    partner_id = fields.Many2one('res.partner', string='Pelanggan')
    cpe_id = fields.Many2one('isp.cpe', string='CPE')
    area_id = fields.Many2one('isp.area', string='Area')
    router_id = fields.Many2one('isp.mikrotik.config', string='Router CPE')
    state = fields.Selection(related='subscription_id.state', string='Status')
    is_late = fields.Boolean('Telat')
