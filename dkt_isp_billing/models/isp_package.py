import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ISPPackage(models.Model):
    _name = 'isp.package'
    _description = 'ISP Package'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama Paket', required=True, tracking=True)
    code = fields.Char(
        'Kode', compute='_compute_code', store=True, readonly=False,
        help='Kode internal paket (bukan nama profile MikroTik).',
    )
    profile_template_id = fields.Many2one(
        'isp.pppoe.profile.template',
        string='Template Profile PPPoE',
        tracking=True,
        domain="[('active', '=', True), ('is_isolir', '=', False)]",
        help='Profile kanonik yang di-push ke semua router.',
    )
    profile_id = fields.Many2one(
        'isp.mikrotik.profile',
        string='Profile lama (per router)',
        tracking=True,
        help='Dipakai hanya untuk data lama. Paket baru memakai template profile.',
    )
    mikrotik_config_id = fields.Many2one(
        'isp.mikrotik.config',
        string='Router Mikrotik (lama)',
        related='profile_id.mikrotik_config_id',
        store=True,
        readonly=True,
    )
    price = fields.Float(
        'Harga default',
        required=True,
        tracking=True,
        help='Dipakai jika area pelanggan tidak punya harga khusus.',
    )
    is_default = fields.Boolean(
        'Paket default',
        default=False,
        tracking=True,
        help='Dipakai pasang baru dan saat bandwidth masih rebutan (SAPU-JAGAD).',
    )
    area_price_ids = fields.One2many(
        'isp.package.area.price', 'package_id', string='Harga per area',
    )
    description = fields.Text('Deskripsi', tracking=True)
    active = fields.Boolean('Active', default=True, tracking=True)
    subscription_ids = fields.One2many('isp.subscription', 'package_id', string='Subscriptions')
    subscription_count = fields.Integer(compute='_compute_subscription_count')
    bandwidth_up = fields.Integer(
        'Bandwidth Upload (Mbps)', compute='_compute_bandwidth', store=True,
    )
    bandwidth_down = fields.Integer(
        'Bandwidth Download (Mbps)', compute='_compute_bandwidth', store=True,
    )
    product_id = fields.Many2one(
        'product.template', string='Produk', readonly=True,
        help='Produk yang terkait dengan paket ini',
    )

    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        'Kode paket harus unik.',
    )

    def init(self):
        super().init()
        self.env.cr.execute(
            'ALTER TABLE isp_package DROP CONSTRAINT IF EXISTS isp_package_profile_uniq'
        )

    @api.constrains('is_default')
    def _check_single_default(self):
        for rec in self:
            if rec.is_default:
                other = self.search([
                    ('is_default', '=', True),
                    ('id', '!=', rec.id),
                ], limit=1)
                if other:
                    raise ValidationError(
                        'Hanya boleh satu paket default. Yang sudah ada: %s.' % other.display_name
                    )

    @api.model
    def get_default_package(self):
        pkg = self.search([('is_default', '=', True), ('active', '=', True)], limit=1)
        if pkg:
            return pkg
        return self.env.ref('dkt_isp_billing.isp_package_sapu_jagad', raise_if_not_found=False)

    def _apply_package_rate_limit(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'dkt_isp_billing.apply_package_rate_limit', 'False',
        ) in ('True', 'true', '1')

    @api.depends('subscription_ids')
    def _compute_subscription_count(self):
        for record in self:
            record.subscription_count = len(record.subscription_ids)

    @api.depends(
        'profile_template_id', 'profile_template_id.rate_limit',
        'profile_id', 'profile_id.rate_limit',
    )
    def _compute_bandwidth(self):
        for record in self:
            rate = False
            if record.profile_template_id and record.profile_template_id.rate_limit:
                rate = record.profile_template_id.rate_limit
            elif record.profile_id and record.profile_id.rate_limit:
                rate = record.profile_id.rate_limit
            record.bandwidth_up, record.bandwidth_down = record._parse_rate_limit(rate)

    @api.model
    def _parse_rate_limit(self, rate_limit):
        if not rate_limit:
            return 0, 0
        try:
            parts = rate_limit.split('/')
            if len(parts) != 2:
                return 0, 0

            def _to_mbps(raw):
                raw = (raw or '').strip().lower()
                digits = ''.join(c for c in raw if c.isdigit() or c == '.')
                if not digits:
                    return 0
                value = float(digits)
                if raw.endswith('k'):
                    return int(value / 1000) if value >= 1000 else 0
                return int(value)

            return _to_mbps(parts[0]), _to_mbps(parts[1])
        except Exception as exc:
            _logger.error('Error parsing rate limit %s: %s', rate_limit, exc)
            return 0, 0

    @api.depends('name')
    def _compute_code(self):
        for record in self:
            if not record.code:
                record.code = record.name.upper().replace(' ', '_') if record.name else False

    def get_assigned_profile_name(self):
        """Nama profile paket langganan, tanpa saklar rate-limit."""
        self.ensure_one()
        if self.profile_template_id:
            return self.profile_template_id.name
        if self.profile_id:
            return self.profile_id.name
        default = self.get_default_package()
        if default and default.profile_template_id:
            return default.profile_template_id.name
        return 'SAPU-JAGAD'

    def get_pppoe_profile_name(self):
        """Nama /ppp/profile yang ditulis ke MikroTik."""
        self.ensure_one()
        if not self._apply_package_rate_limit():
            default = self.get_default_package()
            if default and default.profile_template_id:
                return default.profile_template_id.name
        return self.get_assigned_profile_name()

    def action_push_and_assign_shared_profile(self):
        """Push SAPU-JAGAD ke semua router lalu pindahkan semua secret ke profile itu."""
        default = self.get_default_package()
        if not default or not default.profile_template_id:
            raise ValidationError('Paket default SAPU-JAGAD belum dikonfigurasi.')
        template = default.profile_template_id
        template.action_push_to_all_routers()
        return template.action_assign_all_secrets()

    def get_price_for_area(self, area):
        """Harga area jika ada, else harga default paket."""
        self.ensure_one()
        if area:
            line = self.area_price_ids.filtered(lambda l: l.area_id == area)[:1]
            if line:
                return line.price
        return self.price

    _COMMERCIAL_XMLIDS = {
        'PAKET_150': 'dkt_isp_billing.isp_package_150',
        'PAKET_200': 'dkt_isp_billing.isp_package_200',
        'PAKET_250': 'dkt_isp_billing.isp_package_250',
        'PAKET_300': 'dkt_isp_billing.isp_package_300',
        'PAKET_350': 'dkt_isp_billing.isp_package_350',
        'PAKET_500': 'dkt_isp_billing.isp_package_500',
        'PAKET_600': 'dkt_isp_billing.isp_package_600',
    }

    @api.model
    def match_commercial_package(self, code=None, amount=None, area=None):
        """Cari paket komersial dari kode comment atau nominal. Tidak mengarang harga."""
        if code:
            pkg = self.search([('code', '=', code), ('active', '=', True)], limit=1)
            if pkg:
                return pkg
            xmlid = self._COMMERCIAL_XMLIDS.get(code)
            if xmlid:
                pkg = self.env.ref(xmlid, raise_if_not_found=False)
                if pkg and pkg.active:
                    return pkg
        if amount:
            amount = float(amount)
            if area:
                line = self.env['isp.package.area.price'].search([
                    ('area_id', '=', area.id),
                    ('price', '=', amount),
                    ('package_id.active', '=', True),
                    ('package_id.is_default', '=', False),
                ], limit=1)
                if line:
                    return line.package_id
            pkg = self.search([
                ('price', '=', amount),
                ('active', '=', True),
                ('is_default', '=', False),
            ], limit=1)
            if pkg:
                return pkg
        return self.browse()

    def action_push_profile_to_all_routers(self):
        templates = self.mapped('profile_template_id').filtered('id')
        if not templates:
            raise ValidationError('Paket belum punya template profile untuk di-push.')
        return templates.action_push_to_all_routers()

    @api.model
    def action_apply_bandwidth_savings_7m(self):
        """Cap 7M semua paket kecuali Sinaman dan Bulan Jahe. Tidak kick."""
        return self.env['isp.pppoe.profile.template'].action_apply_bandwidth_savings_7m()

    def _link_legacy_packages_to_templates(self):
        """Paket lama Basic/Standard/Premium memakai template 7M/15M/25M."""
        Template = self.env['isp.pppoe.profile.template']
        mapping = {
            'BASIC': '150-7M',
            'STANDARD': '250-15M',
            'PREMIUM': '350-25M',
        }
        for code, profile_name in mapping.items():
            pkg = self.search([('code', '=', code)], limit=1)
            template = Template.search([('name', '=', profile_name)], limit=1)
            if pkg and template and pkg.profile_template_id != template:
                pkg.profile_template_id = template.id

    @api.model
    def action_sync_router_profiles_from_subscriptions(self):
        """Buat profile per paket, izinkan multi-sesi, secret mengikuti langganan.

        Tidak isolir, tidak kick sesi, tidak mengubah disabled secret.
        Langganan isolated dilewati.
        """
        Package = self.env['isp.package']
        Template = self.env['isp.pppoe.profile.template']
        ICP = self.env['ir.config_parameter'].sudo()
        Package._link_legacy_packages_to_templates()
        Template._ensure_canonical_profiles()
        templates = Template.search([('active', '=', True)])
        if not templates:
            raise ValidationError('Belum ada template profile PPPoE.')
        templates.with_context(install_mode=True).write({'only_one': False})
        push = templates.action_push_to_all_routers()
        ICP.set_param('dkt_isp_billing.apply_package_rate_limit', 'True')

        company_partner = self.env.company.partner_id
        subs = self.env['isp.subscription'].search([
            ('state', '=', 'open'),
            ('cpe_id', '!=', False),
            ('package_id', '!=', False),
        ])
        by_router = {}
        skipped = 0
        for sub in subs:
            cpe = sub.cpe_id
            partner = sub.partner_id
            if not cpe or not cpe.pppoe_username or not cpe.mikrotik_config_id:
                skipped += 1
                continue
            if cpe.state == 'isolated' or partner.state == 'isolated':
                skipped += 1
                continue
            if company_partner and partner == company_partner:
                skipped += 1
                continue
            router = cpe.mikrotik_config_id
            if not router.active or not router.password:
                skipped += 1
                continue
            if (router.host or '').startswith('127.'):
                skipped += 1
                continue
            profile = sub._get_assigned_profile_name()
            by_router.setdefault(router, {})[cpe.pppoe_username] = profile

        totals = {'updated': 0, 'unchanged': 0, 'missing': 0, 'errors': []}
        for router, mapping in by_router.items():
            result = router.assign_secret_profiles(mapping)
            totals['updated'] += result.get('updated') or 0
            totals['unchanged'] += result.get('unchanged') or 0
            totals['missing'] += result.get('missing') or 0
            totals['errors'].extend(result.get('errors') or [])

        push_msg = ''
        if isinstance(push, dict):
            push_msg = ((push.get('params') or {}).get('message') or '').strip()
        parts = [
            push_msg,
            'Secret diubah: %s. Sudah cocok: %s. Secret tidak ketemu: %s. Dilewati: %s.' % (
                totals['updated'], totals['unchanged'], totals['missing'], skipped,
            ),
        ]
        if totals['errors']:
            parts.append('Gagal: %s' % '; '.join(totals['errors'][:8]))
            if len(totals['errors']) > 8:
                parts.append('(+%s error lain)' % (len(totals['errors']) - 8))
        message = ' '.join(part for part in parts if part)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sinkron profile paket ke router',
                'message': message or 'Tidak ada yang diubah.',
                'type': 'success' if totals['updated'] and not totals['errors'] else 'warning',
                'sticky': True,
            },
        }

    def action_view_subscriptions(self):
        self.ensure_one()
        return {
            'name': 'Subscriptions',
            'type': 'ir.actions.act_window',
            'res_model': 'isp.subscription',
            'view_mode': 'list,form',
            'domain': [('package_id', '=', self.id)],
            'context': {'default_package_id': self.id},
        }
