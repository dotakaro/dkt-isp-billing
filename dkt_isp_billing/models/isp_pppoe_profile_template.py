import ipaddress
import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

DEFAULT_PPPOE_POOL_NAME = 'pppoe-pool'
SAPU_PROFILE_NAME = 'SAPU-JAGAD'
CANONICAL_PROFILE_NAMES = (
    '150-7M', '200-10M', '200-15M', '200-20M', '250-15M',
    '300-20M', '350-25M', '500-40M', '600-50M', 'isolir',
)
# Subnet 10.10.x yang sudah dipakai desa lain / OLT, jangan dipakai pool baru.
_RESERVED_POOL_RANGES = (
    '10.10.0.0/24',
    '10.10.1.0/24',
    '10.10.5.0/24',
    '10.10.100.0/24',
)


def profile_address_vals(profile):
    """Ambil local/remote dari dict profile MikroTik. Kosong jika keduanya kosong."""
    if not profile:
        return {}
    local = (profile.get('local-address') or '').strip()
    remote = (profile.get('remote-address') or '').strip()
    if not local and not remote:
        return {}
    return {
        'local-address': local or remote,
        'remote-address': remote or local,
    }


def parse_pool_range_parts(ranges_str):
    """Ubah spec pool MikroTik jadi daftar (start_int, end_int)."""
    result = []
    for part in (ranges_str or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            if '/' in part:
                net = ipaddress.ip_network(part, strict=False)
                result.append((int(net.network_address), int(net.broadcast_address)))
            elif '-' in part:
                start, end = [item.strip() for item in part.split('-', 1)]
                result.append((
                    int(ipaddress.ip_address(start)),
                    int(ipaddress.ip_address(end)),
                ))
            else:
                ip_int = int(ipaddress.ip_address(part))
                result.append((ip_int, ip_int))
        except ValueError:
            continue
    return result


def pick_new_pppoe_pool_ranges(existing_range_specs):
    """Pilih range 10.10.x yang tidak nabrak pool/IP yang sudah ada."""
    occupied = []
    for spec in list(existing_range_specs or []) + list(_RESERVED_POOL_RANGES):
        occupied.extend(parse_pool_range_parts(spec))
    for third in (8, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 30, 40, 50, 60, 70, 80, 90):
        spec = '10.10.%s.2-10.10.%s.254' % (third, third)
        candidate = parse_pool_range_parts(spec)[0]
        if any(candidate[0] <= item[1] and item[0] <= candidate[1] for item in occupied):
            continue
        return spec
    raise ValueError('Tidak ada range kosong untuk pppoe-pool.')


class ISPPppoeProfileTemplate(models.Model):
    _name = 'isp.pppoe.profile.template'
    _description = 'Template Profile PPPoE (kanonik)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, name'

    name = fields.Char(
        'Nama Profile',
        required=True,
        tracking=True,
        help='Nama yang di-push ke /ppp/profile di semua router. Harus identik '
             'antar desa agar migrasi RADIUS nanti mulus.',
    )
    sequence = fields.Integer('Urutan', default=10)
    rate_limit = fields.Char(
        'Rate Limit',
        tracking=True,
        help='Format MikroTik simetris, contoh: 10M/10M. Kosongkan untuk unlimited/rebutan.',
    )
    is_unlimited = fields.Boolean(
        'Unlimited / rebutan',
        default=False,
        tracking=True,
        help='Tidak mengirim rate-limit ke MikroTik.',
    )
    local_address = fields.Char('Local Address', tracking=True)
    remote_address = fields.Char('Remote Address', tracking=True)
    parent_queue = fields.Char('Parent Queue', tracking=True)
    only_one = fields.Boolean('Only One', default=True, tracking=True)
    is_isolir = fields.Boolean(
        'Profile Isolir',
        default=False,
        tracking=True,
        help='Dipakai saat pelanggan nunggak (bukan paket jual).',
    )
    auto_push = fields.Boolean(
        'Push otomatis saat simpan',
        default=True,
        help='Ubah template lalu push ke semua router yang password API-nya sudah diisi.',
    )
    active = fields.Boolean('Aktif', default=True, tracking=True)
    note = fields.Text('Catatan')
    package_ids = fields.One2many('isp.package', 'profile_template_id', string='Paket')
    router_profile_ids = fields.One2many(
        'isp.mikrotik.profile', 'template_id', string='Profile per router',
    )
    package_count = fields.Integer(compute='_compute_package_count')
    router_profile_count = fields.Integer(compute='_compute_router_profile_count')

    _name_uniq = models.Constraint(
        'UNIQUE(name)',
        'Nama template profile harus unik.',
    )

    @api.depends('package_ids')
    def _compute_package_count(self):
        for rec in self:
            rec.package_count = len(rec.package_ids)

    @api.depends('router_profile_ids')
    def _compute_router_profile_count(self):
        for rec in self:
            rec.router_profile_count = len(rec.router_profile_ids)

    @api.constrains('is_isolir')
    def _check_single_isolir(self):
        for rec in self:
            if rec.is_isolir:
                other = self.search([
                    ('is_isolir', '=', True),
                    ('id', '!=', rec.id),
                ], limit=1)
                if other:
                    raise ValidationError(
                        'Hanya boleh ada satu template isolir. Yang sudah ada: %s.' % other.name
                    )

    BANDWIDTH_CAP_RATE = '7M/7M'

    def _prepare_mikrotik_vals(self, router=None):
        self.ensure_one()
        vals = {
            'name': self.name,
            'only-one': 'yes' if self.only_one else 'no',
            'comment': 'Odoo template %s' % self.name,
        }
        rate = self._applied_rate_limit(router)
        if rate:
            vals['rate-limit'] = rate
        elif self.is_unlimited:
            vals['rate-limit'] = ''
        if self.local_address:
            vals['local-address'] = self.local_address
        if self.remote_address:
            vals['remote-address'] = self.remote_address
        if self.parent_queue:
            vals['parent-queue'] = self.parent_queue
        if self.is_isolir:
            vals['address-list'] = 'isolir'
        return vals

    def _applied_rate_limit(self, router=None):
        """Rate yang ditulis ke router. Hemat 7M kecuali isolir / router exempt."""
        self.ensure_one()
        if self.is_isolir:
            return self.rate_limit or '64k/64k'
        if router and not router.is_bandwidth_cap_exempt() and self._bandwidth_cap_enabled():
            return router.bandwidth_cap_rate_applied() or self.BANDWIDTH_CAP_RATE
        if not self.is_unlimited and self.rate_limit:
            return self.rate_limit
        return ''

    @api.model
    def _bandwidth_cap_enabled(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'dkt_isp_billing.bandwidth_cap_7m', 'False',
        ) in ('True', 'true', '1')

    def action_apply_bandwidth_savings_7m(self):
        """Cap 7M/7M untuk semua paket, kecuali Sinaman dan Bulan Jahe.

        Tidak kick sesi, tidak isolir, tidak mengubah secret/disabled.
        Sesi yang sudah online baru dapat 7M setelah reconnect sendiri.
        """
        Router = self.env['isp.mikrotik.config']
        exempt = Router.search([
            ('name', 'in', list(Router._BANDWIDTH_CAP_EXEMPT_NAMES)),
        ])
        if exempt:
            exempt.write({'bandwidth_cap_exempt': True})
        self.env['ir.config_parameter'].sudo().set_param(
            'dkt_isp_billing.bandwidth_cap_7m', 'True',
        )
        routers = Router.search([('active', '=', True)])
        cap_results = routers.apply_bandwidth_cap_7m()
        radius_ok, radius_err = self.env['isp.radius'].sync_package_groups()
        updated = sum(item.get('updated') or 0 for item in cap_results)
        exempt_names = [
            item['name'] for item in cap_results if item.get('skipped') == 'exempt'
        ]
        errors = [
            '%s: %s' % (item['name'], item['error'])
            for item in cap_results if item.get('error')
        ]
        parts = [
            'Cap 7M/7M pada %s profile di router hemat.' % updated,
        ]
        if exempt_names:
            parts.append('Dilewati (bebas cap): %s.' % ', '.join(exempt_names))
        if radius_ok:
            parts.append('RADIUS group ikut 7M (kecuali isolir).')
        elif radius_err:
            parts.append('RADIUS: %s' % radius_err)
        if errors:
            parts.append('Gagal: %s' % '; '.join(errors[:8]))
            if len(errors) > 8:
                parts.append('(+%s error lain)' % (len(errors) - 8))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Hemat bandwidth 7M',
                'message': ' '.join(parts),
                'type': 'success' if updated and not errors else 'warning',
                'sticky': True,
            },
        }

    @api.model
    def _ensure_router_pppoe_pool(self, api):
        """Pool per router: pakai SAPU-JAGAD, atau buat/ pakai pppoe-pool.

        Tidak mengubah secret, tidak kick sesi.
        """
        profile_api = api.get_resource('/ppp/profile')
        profiles = {item.get('name'): item for item in (profile_api.get() or [])}
        sapu_vals = profile_address_vals(profiles.get(SAPU_PROFILE_NAME))
        if sapu_vals:
            return sapu_vals

        pool_api = api.get_resource('/ip/pool')
        pools = {item.get('name'): item for item in (pool_api.get() or [])}
        if DEFAULT_PPPOE_POOL_NAME not in pools:
            existing_specs = [item.get('ranges') or '' for item in pools.values()]
            for address in api.get_resource('/ip/address').get() or []:
                iface = address.get('interface') or ''
                if iface.startswith('<pppoe-'):
                    continue
                existing_specs.append(address.get('address') or '')
            ranges = pick_new_pppoe_pool_ranges(existing_specs)
            pool_api.add(
                name=DEFAULT_PPPOE_POOL_NAME,
                ranges=ranges,
                comment='Created by Odoo',
            )
            _logger.info(
                'Membuat pool %s ranges=%s', DEFAULT_PPPOE_POOL_NAME, ranges,
            )
        pool_vals = {
            'local-address': DEFAULT_PPPOE_POOL_NAME,
            'remote-address': DEFAULT_PPPOE_POOL_NAME,
        }
        sapu = profiles.get(SAPU_PROFILE_NAME)
        if sapu:
            sapu_id = sapu.get('.id') or sapu.get('id')
            if sapu_id:
                profile_api.set(id=str(sapu_id), **pool_vals)
        return pool_vals

    @api.model
    def _apply_pool_to_named_profiles(self, api, pool_vals, names=None):
        """Set local/remote saja pada profile yang sudah ada. Tidak kick."""
        if not pool_vals:
            return 0
        names = tuple(names or CANONICAL_PROFILE_NAMES)
        profile_api = api.get_resource('/ppp/profile')
        updated = 0
        for item in profile_api.get() or []:
            name = item.get('name') or ''
            if name not in names:
                continue
            current = profile_address_vals(item)
            if (
                current.get('local-address') == pool_vals.get('local-address')
                and current.get('remote-address') == pool_vals.get('remote-address')
            ):
                continue
            profile_id = item.get('.id') or item.get('id')
            if not profile_id:
                continue
            profile_api.set(id=str(profile_id), **pool_vals)
            updated += 1
        return updated

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get('install_mode'):
            records.filtered('auto_push').action_push_to_all_routers()
        return records

    def write(self, vals):
        res = super().write(vals)
        push_fields = {
            'name', 'rate_limit', 'is_unlimited', 'only_one', 'local_address',
            'remote_address', 'parent_queue', 'active',
        }
        if not self.env.context.get('install_mode') and push_fields.intersection(vals):
            self.filtered('auto_push').action_push_to_all_routers()
        return res

    CANONICAL_PROFILES = (
        ('7M', '150-7M', '7M/7M', 7),
        ('10M', '200-10M', '10M/10M', 10),
        ('15M', '250-15M', '15M/15M', 15),
        ('20M', '300-20M', '20M/20M', 20),
        ('25M', '350-25M', '25M/25M', 25),
        ('40M', '500-40M', '40M/40M', 40),
        ('50M', '600-50M', '50M/50M', 50),
        (False, '200-15M', '15M/15M', 11),
        (False, '200-20M', '20M/20M', 12),
    )

    @api.model
    def _ensure_canonical_profiles(self):
        """Samakan nama profile: harga-bandwidth, plus varian 200-15M / 200-20M."""
        Template = self.sudo()
        for old_name, new_name, rate, sequence in self.CANONICAL_PROFILES:
            rec = Template.search([('name', '=', new_name)], limit=1)
            if not rec and old_name:
                rec = Template.search([('name', '=', old_name)], limit=1)
            vals = {
                'name': new_name,
                'rate_limit': rate,
                'is_unlimited': False,
                'only_one': False,
                'auto_push': False,
                'active': True,
                'sequence': sequence,
            }
            if rec:
                rec.with_context(install_mode=True).write(vals)
            else:
                Template.with_context(install_mode=True).create(vals)

    def action_allow_multisession_all(self):
        """Toleransi multi-sesi: only-one=no di semua template aktif, lalu push."""
        templates = self.search([('active', '=', True)]) or self
        templates.with_context(install_mode=True).write({'only_one': False})
        return templates.action_push_to_all_routers()

    def action_push_to_all_routers(self):
        routers = self.env['isp.mikrotik.config'].search([('active', '=', True)])
        return self._push_to_routers(routers)

    def action_assign_router_pools(self):
        """Isi local/remote profile kanonik dari SAPU-JAGAD, atau buat pppoe-pool."""
        routers = self.env['isp.mikrotik.config'].search([('active', '=', True)])
        skipped = routers.filtered(
            lambda router: not router.password or (router.host or '').startswith('127.')
        )
        targets = routers - skipped
        updated = 0
        errors = []
        for router in targets:
            api = router.get_connection()
            if not api or api is True:
                errors.append('%s: gagal koneksi API' % router.name)
                continue
            try:
                pool_vals = self._ensure_router_pppoe_pool(api)
                updated += self._apply_pool_to_named_profiles(api, pool_vals)
            except Exception as exc:
                errors.append('%s: %s' % (router.name, exc))
                _logger.exception('Gagal assign pool di %s', router.name)
            finally:
                if api and hasattr(api, 'connection_pool'):
                    api.connection_pool.disconnect()
        parts = ['%s profile diisi pool-nya.' % updated]
        if skipped:
            parts.append(
                'Lewati: %s.' % ', '.join(skipped.mapped('name'))
            )
        if errors:
            parts.append('Gagal: %s' % '; '.join(errors[:8]))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Assign pool PPPoE',
                'message': ' '.join(parts),
                'type': 'success' if updated and not errors else 'warning',
                'sticky': bool(errors),
            },
        }

    def _push_to_routers(self, routers):
        """Create/update /ppp/profile di router yang diberi, plus cache Odoo."""
        if not self:
            raise ValidationError('Tidak ada template profile untuk di-push.')
        if not routers:
            raise ValidationError('Tidak ada router tujuan.')

        skipped = routers.filtered(lambda r: not r.password or (r.host or '').startswith('127.'))
        targets = routers - skipped
        success = 0
        errors = []
        Profile = self.env['isp.mikrotik.profile']

        for router in targets:
            api = router.get_connection()
            if not api or api is True:
                errors.append('%s: gagal koneksi API' % router.name)
                continue
            try:
                profile_api = api.get_resource('/ppp/profile')
                existing = {p.get('name'): p for p in profile_api.get()}
                router_pool = self._ensure_router_pppoe_pool(api)
                for template in self:
                    payload = template._prepare_mikrotik_vals(router)
                    if not template.local_address and not template.remote_address:
                        payload.update(router_pool)
                    remote = existing.get(template.name)
                    try:
                        if remote:
                            remote_id = remote.get('.id') or remote.get('id')
                            profile_api.set(id=remote_id, **payload)
                            mikrotik_id = remote_id
                        else:
                            result = profile_api.add(**payload)
                            if isinstance(result, list) and result:
                                mikrotik_id = result[0].get('.id') or result[0].get('id')
                            else:
                                mikrotik_id = False
                        local = Profile.search([
                            ('mikrotik_config_id', '=', router.id),
                            ('name', '=', template.name),
                        ], limit=1)
                        local_vals = {
                            'name': template.name,
                            'template_id': template.id,
                            'mikrotik_config_id': router.id,
                            'mikrotik_id': mikrotik_id or False,
                            'rate_limit': template._applied_rate_limit(router),
                            'local_address': (
                                template.local_address
                                or router_pool.get('local-address')
                                or False
                            ),
                            'remote_address': (
                                template.remote_address
                                or router_pool.get('remote-address')
                                or False
                            ),
                            'parent_queue': template.parent_queue or False,
                            'only_one': template.only_one,
                            'active': True,
                            'sync_state': 'synced',
                            'sync_error': False,
                            'last_sync': fields.Datetime.now(),
                        }
                        if local:
                            local.write(local_vals)
                        else:
                            Profile.create(local_vals)
                        success += 1
                    except Exception as exc:
                        msg = '%s / %s: %s' % (router.name, template.name, exc)
                        errors.append(msg)
                        _logger.exception(msg)
                        local = Profile.search([
                            ('mikrotik_config_id', '=', router.id),
                            ('name', '=', template.name),
                        ], limit=1)
                        err_vals = {
                            'name': template.name,
                            'template_id': template.id,
                            'mikrotik_config_id': router.id,
                            'rate_limit': template._applied_rate_limit(router),
                            'sync_state': 'error',
                            'sync_error': str(exc),
                            'last_sync': fields.Datetime.now(),
                            'active': True,
                        }
                        if local:
                            local.write(err_vals)
                        else:
                            Profile.create(err_vals)
            finally:
                if api and hasattr(api, 'connection_pool'):
                    api.connection_pool.disconnect()

        parts = []
        if success:
            parts.append('Berhasil push %s profile.' % success)
        if skipped:
            parts.append(
                'Lewati (password API kosong): %s.' % ', '.join(skipped.mapped('name'))
            )
        if errors:
            parts.append('Gagal: %s' % '; '.join(errors[:8]))
            if len(errors) > 8:
                parts.append('(+%s error lain, cek log)' % (len(errors) - 8))

        message = ' '.join(parts) or 'Tidak ada yang di-push.'
        notif_type = 'success' if success and not errors else ('warning' if success else 'danger')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Push profile PPPoE',
                'message': message,
                'type': notif_type,
                'sticky': bool(errors or skipped),
            },
        }

    def action_assign_all_secrets(self):
        """Set semua /ppp/secret di semua router aktif ke profile template ini."""
        self.ensure_one()
        routers = self.env['isp.mikrotik.config'].search([('active', '=', True)])
        routers = routers.filtered(lambda r: r.password and not (r.host or '').startswith('127.'))
        updated = 0
        skipped_routers = []
        errors = []
        for router in routers:
            api = router.get_connection()
            if not api or api is True:
                skipped_routers.append(router.name)
                continue
            try:
                secret_api = api.get_resource('/ppp/secret')
                secrets = secret_api.get() or []
                for secret in secrets:
                    secret_id = secret.get('.id') or secret.get('id')
                    if not secret_id:
                        continue
                    if secret.get('profile') == self.name:
                        continue
                    secret_api.set(id=secret_id, profile=self.name)
                    updated += 1
            except Exception as exc:
                errors.append('%s: %s' % (router.name, exc))
                _logger.exception('Gagal assign secret di %s', router.name)
            finally:
                if api and hasattr(api, 'connection_pool'):
                    api.connection_pool.disconnect()

        parts = ['%s secret diubah ke profile %s.' % (updated, self.name)]
        if skipped_routers:
            parts.append('Router gagal koneksi: %s.' % ', '.join(skipped_routers))
        if errors:
            parts.append('Error: %s' % '; '.join(errors[:8]))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Migrasi secret PPPoE',
                'message': ' '.join(parts),
                'type': 'success' if updated and not errors else 'warning',
                'sticky': True,
            },
        }
