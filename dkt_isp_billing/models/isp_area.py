from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ISPArea(models.Model):
    _name = 'isp.area'
    _description = 'Area / Desa ISP'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    name = fields.Char('Nama Area', required=True, tracking=True)
    code = fields.Char('Kode', required=True, tracking=True, index=True)
    active = fields.Boolean('Aktif', default=True, tracking=True)
    note = fields.Text('Catatan')
    mikrotik_config_ids = fields.One2many(
        'isp.mikrotik.config', 'area_id', string='Router MikroTik',
    )
    router_count = fields.Integer(compute='_compute_router_count')
    partner_ids = fields.One2many('res.partner', 'area_id', string='Pelanggan')
    partner_count = fields.Integer(compute='_compute_partner_count')
    area_price_ids = fields.One2many(
        'isp.package.area.price', 'area_id', string='Harga Paket',
    )
    admin_user_ids = fields.Many2many(
        'res.users',
        'isp_area_res_users_rel',
        'area_id',
        'user_id',
        string='Admin Desa',
        help='User Admin Desa atau Kolektor yang boleh kelola pelanggan dan bukti di area ini.',
    )

    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        'Kode area harus unik.',
    )
    _name_uniq = models.Constraint(
        'UNIQUE(name)',
        'Nama area harus unik.',
    )

    @api.depends('mikrotik_config_ids')
    def _compute_router_count(self):
        for rec in self:
            rec.router_count = len(rec.mikrotik_config_ids)

    @api.depends('partner_ids')
    def _compute_partner_count(self):
        for rec in self:
            rec.partner_count = len(rec.partner_ids)

    @api.onchange('name')
    def _onchange_name_code(self):
        if self.name and not self.code:
            self.code = self.name.upper().replace(' ', '_')

    def action_view_routers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Router Area',
            'res_model': 'isp.mikrotik.config',
            'view_mode': 'list,form',
            'domain': [('area_id', '=', self.id)],
            'context': {'default_area_id': self.id},
        }

    def action_view_partners(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Pelanggan Area',
            'res_model': 'res.partner',
            'view_mode': 'list,form',
            'domain': [('area_id', '=', self.id), ('customer_rank', '>', 0)],
            'context': {
                'default_area_id': self.id,
                'default_customer_rank': 1,
            },
        }

    def action_push_standard_profiles(self):
        """Push template profile kanonik ke semua router di area ini."""
        routers = self.mapped('mikrotik_config_ids').filtered('active')
        if not routers:
            raise ValidationError('Area ini belum punya router aktif.')
        templates = self.env['isp.pppoe.profile.template'].search([('active', '=', True)])
        if not templates:
            raise ValidationError('Belum ada template profile PPPoE.')
        return templates._push_to_routers(routers)

    def action_import_secrets_to_odoo(self):
        """Import /ppp/secret dari router area ini ke Odoo."""
        routers = self.mapped('mikrotik_config_ids')
        if not routers:
            raise ValidationError('Area ini belum punya router.')
        return routers.action_import_secrets_to_odoo()
