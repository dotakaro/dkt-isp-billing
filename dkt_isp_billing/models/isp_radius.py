import logging
import os

from odoo import api, models

_logger = logging.getLogger(__name__)


class IspRadius(models.AbstractModel):
    """Sinkron user/NAS ke DB FreeRADIUS. Gagal SQL tidak boleh menggagalkan bayar."""

    _name = 'isp.radius'
    _description = 'Sinkron FreeRADIUS'

    @api.model
    def get_config(self):
        ICP = self.env['ir.config_parameter'].sudo()
        enabled = ICP.get_param('dkt_isp_billing.radius_enabled', 'False') in (
            'True', 'true', '1',
        )
        return {
            'enabled': enabled,
            'host': ICP.get_param('dkt_isp_billing.radius_db_host', 'dkt-isp-db') or 'dkt-isp-db',
            'port': int(ICP.get_param('dkt_isp_billing.radius_db_port', '5432') or 5432),
            'dbname': ICP.get_param('dkt_isp_billing.radius_db_name', 'radius') or 'radius',
            'user': ICP.get_param('dkt_isp_billing.radius_db_user', 'radius') or 'radius',
            'password': ICP.get_param('dkt_isp_billing.radius_db_password', '') or '',
        }

    @api.model
    def landing_url(self):
        ICP = self.env['ir.config_parameter'].sudo()
        custom = (ICP.get_param('dkt_isp_billing.radius_landing_url', '') or '').strip()
        if custom:
            return custom.rstrip('/')
        base = (
            ICP.get_param('dkt_isp_billing.wa_public_base_url', '')
            or ICP.get_param('web.base.url', '')
            or ''
        ).strip().rstrip('/')
        return '%s/isp/isolir' % (base or 'https://billing.dotakaro.com')

    @api.model
    def isolir_profile_name(self):
        template = self.env['isp.pppoe.profile.template'].sudo().search([
            ('is_isolir', '=', True),
        ], limit=1)
        return template.name if template else 'isolir'

    @api.model
    def open_profile_name(self, cpe=None, fallback=None):
        if cpe and cpe.subscription_id:
            try:
                return cpe.subscription_id._get_assigned_profile_name()
            except Exception:
                pass
        if fallback:
            return fallback
        default = self.env['isp.package'].sudo().get_default_package()
        if default and default.profile_template_id:
            return default.profile_template_id.name
        return 'SAPU-JAGAD'

    @api.model
    def _radius_db_hosts(self, configured):
        """Produksi resolve Postgres sebagai `db`; XML default lokal `dkt-isp-db`."""
        hosts = []
        for host in (
            configured,
            (os.environ.get('HOST') or '').strip(),
            'db',
            'odoo19-dkt-db',
        ):
            if host and host not in hosts:
                hosts.append(host)
        return hosts

    @api.model
    def _connect(self):
        cfg = self.get_config()
        if not cfg['enabled'] or not cfg['password']:
            return None
        try:
            import psycopg2
        except ImportError:
            _logger.warning('psycopg2 tidak tersedia; sinkron RADIUS dilewati.')
            return None
        last_exc = None
        for host in self._radius_db_hosts(cfg['host']):
            try:
                return psycopg2.connect(
                    host=host,
                    port=cfg['port'],
                    dbname=cfg['dbname'],
                    user=cfg['user'],
                    password=cfg['password'],
                    connect_timeout=5,
                )
            except Exception as exc:
                last_exc = exc
                continue
        _logger.warning('Koneksi DB RADIUS gagal: %s', last_exc)
        return None

    @api.model
    def _execute(self, statements):
        """Jalankan daftar (sql, params). Return (ok, error)."""
        conn = self._connect()
        if conn is None:
            return False, 'RADIUS DB tidak aktif atau tidak terjangkau.'
        try:
            with conn:
                with conn.cursor() as cr:
                    for sql, params in statements:
                        cr.execute(sql, params)
            return True, None
        except Exception as exc:
            _logger.warning('SQL RADIUS gagal: %s', exc)
            return False, str(exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @api.model
    def _replace_check(self, username, attribute, op, value):
        return [
            (
                'DELETE FROM radcheck WHERE username = %s AND attribute = %s',
                (username, attribute),
            ),
            (
                'INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, %s, %s, %s)',
                (username, attribute, op, value),
            ),
        ]

    @api.model
    def _delete_check(self, username, attribute):
        return [(
            'DELETE FROM radcheck WHERE username = %s AND attribute = %s',
            (username, attribute),
        )]

    @api.model
    def _replace_reply(self, username, attribute, op, value):
        return [
            (
                'DELETE FROM radreply WHERE username = %s AND attribute = %s',
                (username, attribute),
            ),
            (
                'INSERT INTO radreply (username, attribute, op, value) VALUES (%s, %s, %s, %s)',
                (username, attribute, op, value),
            ),
        ]

    @api.model
    def _delete_reply(self, username, attribute):
        return [(
            'DELETE FROM radreply WHERE username = %s AND attribute = %s',
            (username, attribute),
        )]

    @api.model
    def _replace_usergroup(self, username, groupname):
        return [
            ('DELETE FROM radusergroup WHERE username = %s', (username,)),
            (
                'INSERT INTO radusergroup (username, groupname, priority) '
                'VALUES (%s, %s, %s)',
                (username, groupname, 1),
            ),
        ]

    @api.model
    def _replace_group_reply(self, groupname, attribute, op, value):
        return [
            (
                'DELETE FROM radgroupreply WHERE groupname = %s AND attribute = %s',
                (groupname, attribute),
            ),
            (
                'INSERT INTO radgroupreply (groupname, attribute, op, value) '
                'VALUES (%s, %s, %s, %s)',
                (groupname, attribute, op, value),
            ),
        ]

    @api.model
    def nas_identifier(self, router):
        if not router:
            return ''
        return (
            (router.radius_nas_identifier or '').strip()
            or (router.identity_name or '').strip()
            or (router.name or '').strip()
        )

    @api.model
    def sync_nas(self, router):
        """Tulis baris `nas` dari router. Secret kosong = skip."""
        if not router or not router.radius_secret or not router.radius_nas_ip:
            return False, 'radius_nas_ip / radius_secret belum diisi.'
        stmts = [
            ('DELETE FROM nas WHERE nasname = %s OR shortname = %s', (
                router.radius_nas_ip, (router.name or '')[:32],
            )),
            (
                'INSERT INTO nas (nasname, shortname, type, secret, description) '
                'VALUES (%s, %s, %s, %s, %s)',
                (
                    router.radius_nas_ip,
                    (router.name or 'nas')[:32],
                    'other',
                    router.radius_secret,
                    self.nas_identifier(router)[:200],
                ),
            ),
        ]
        return self._execute(stmts)

    @api.model
    def _bandwidth_cap_rate_for_radius(self):
        """Pintu Angin memakai RADIUS: cap 7M jika hemat aktif dan NAS tidak exempt."""
        Template = self.env['isp.pppoe.profile.template']
        if not Template._bandwidth_cap_enabled():
            return ''
        routers = self.env['isp.mikrotik.config'].sudo().search([
            ('active', '=', True),
            ('session_backend', '=', 'radius'),
        ])
        if routers and any(not router.is_bandwidth_cap_exempt() for router in routers):
            return Template.BANDWIDTH_CAP_RATE
        return ''

    @api.model
    def sync_package_groups(self):
        """Daftarkan profile/paket ke radgroupreply. Tidak menyentuh secret lokal."""
        templates = self.env['isp.pppoe.profile.template'].sudo().search([
            ('active', '=', True),
        ])
        cap_rate = self._bandwidth_cap_rate_for_radius()
        stmts = []
        names = set()
        for template in templates:
            name = (template.name or '').strip()
            if not name:
                continue
            names.add(name)
            stmts.extend(self._replace_group_reply(
                name, 'Mikrotik-Group', '=', name,
            ))
            rate = ''
            if template.is_isolir:
                rate = template.rate_limit or '64k/64k'
            elif cap_rate:
                rate = cap_rate
            elif not template.is_unlimited and template.rate_limit:
                rate = template.rate_limit
            if rate:
                stmts.extend(self._replace_group_reply(
                    name, 'Mikrotik-Rate-Limit', '=', rate,
                ))
            else:
                stmts.append((
                    'DELETE FROM radgroupreply '
                    'WHERE groupname = %s AND attribute = %s',
                    (name, 'Mikrotik-Rate-Limit'),
                ))
        if not names:
            return False, 'Tidak ada template profile.'
        return self._execute(stmts)

    @api.model
    def sync_user(self, username, password, profile, identifier='', isolated=False):
        """Tulis satu user RADIUS. Tidak menghapus secret MikroTik, tidak kick."""
        username = (username or '').strip()
        password = password or ''
        profile = (profile or 'SAPU-JAGAD').strip() or 'SAPU-JAGAD'
        if not username or not password:
            return False, 'Username/password PPPoE kosong.'
        stmts = self._replace_check(
            username, 'Cleartext-Password', ':=', password,
        )
        if identifier:
            stmts.extend(self._replace_check(
                username, 'NAS-Identifier', '==', identifier,
            ))
        stmts.extend(self._replace_usergroup(username, profile))
        if isolated:
            stmts.extend(self._replace_reply(
                username, 'Mikrotik-Group', '=', self.isolir_profile_name(),
            ))
        else:
            stmts.extend(self._delete_reply(username, 'Mikrotik-Group'))
        return self._execute(stmts)

    @api.model
    def sync_cpe(self, cpe, isolated=None):
        """Dual-write ke RADIUS. Secret lokal tetap; isolir lewat profile, bukan Reject."""
        if not cpe or not cpe.pppoe_username or not cpe.pppoe_password:
            return False, 'CPE belum punya username/password PPPoE.'
        if isolated is None:
            isolated = cpe.state == 'isolated'
        ok, err = self.sync_user(
            cpe.pppoe_username,
            cpe.pppoe_password,
            self.open_profile_name(cpe),
            identifier=self.nas_identifier(cpe.mikrotik_config_id),
            isolated=isolated,
        )
        if ok and cpe.mikrotik_config_id:
            self.sync_nas(cpe.mikrotik_config_id)
        return ok, err

    @api.model
    def set_isolated(self, cpe, isolated):
        return self.sync_cpe(cpe, isolated=isolated)

    @api.model
    def push_router_users(self, router, include_local_secrets=True):
        """Push user+paket router RADIUS. Tidak hapus secret, tidak kick.

        Secret lokal tetap jadi cadangan jika user belum ada di RADIUS atau
        FreeRADIUS timeout / tidak menjawab.
        """
        if not router or not router.uses_radius_isolir():
            return {
                'ok': 0, 'skipped': 0, 'errors': ['Router bukan backend RADIUS.'],
            }
        stats = {'ok': 0, 'skipped': 0, 'errors': []}
        group_ok, group_err = self.sync_package_groups()
        if not group_ok:
            stats['errors'].append(group_err or 'Gagal daftar paket RADIUS.')
        nas_ok, nas_err = self.sync_nas(router)
        if not nas_ok:
            stats['errors'].append(nas_err or 'Gagal sync NAS.')
        identifier = self.nas_identifier(router)
        seen = set()
        cpes = self.env['isp.cpe'].sudo().search([
            ('mikrotik_config_id', '=', router.id),
            ('pppoe_username', '!=', False),
            ('pppoe_password', '!=', False),
        ])
        for cpe in cpes:
            username = (cpe.pppoe_username or '').strip()
            if not username:
                stats['skipped'] += 1
                continue
            ok, err = self.sync_cpe(cpe)
            if ok:
                stats['ok'] += 1
                seen.add(username)
            else:
                stats['errors'].append('%s: %s' % (username, err or 'gagal'))
        if not include_local_secrets:
            return stats
        api = router.get_connection()
        if not api or api is True:
            stats['errors'].append(
                'API %s tidak terjangkau; secret lokal tidak dilengkapi.' % router.name
            )
            return stats
        try:
            secrets = api.get_resource('/ppp/secret').get() or []
            for secret in secrets:
                username = (secret.get('name') or '').strip()
                password = secret.get('password') or ''
                if not username or username in seen or not password:
                    continue
                profile = (secret.get('profile') or 'SAPU-JAGAD').strip() or 'SAPU-JAGAD'
                isolated = (secret.get('profile') or '') == self.isolir_profile_name()
                ok, err = self.sync_user(
                    username, password, profile,
                    identifier=identifier, isolated=isolated,
                )
                if ok:
                    stats['ok'] += 1
                else:
                    stats['errors'].append('%s: %s' % (username, err or 'gagal'))
        except Exception as exc:
            stats['errors'].append('%s secret lokal: %s' % (router.name, exc))
            _logger.warning('Lengkapi secret lokal RADIUS %s gagal: %s', router.name, exc)
        finally:
            if api and hasattr(api, 'connection_pool'):
                try:
                    api.connection_pool.disconnect()
                except Exception:
                    pass
        return stats

    @api.model
    def action_push_all_radius_routers(self):
        """Push user+paket ke semua router backend RADIUS. Tidak kick."""
        routers = self.env['isp.mikrotik.config'].search([
            ('active', '=', True),
            ('session_backend', '=', 'radius'),
        ])
        ok = skipped = 0
        errors = []
        for router in routers:
            stats = self.push_router_users(router)
            ok += stats.get('ok') or 0
            skipped += stats.get('skipped') or 0
            errors.extend(stats.get('errors') or [])
        parts = ['%s user ditulis ke RADIUS. Dilewati: %s.' % (ok, skipped)]
        if errors:
            parts.append('Catatan: %s' % '; '.join(errors[:8]))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Push user RADIUS',
                'message': ' '.join(parts),
                'type': 'success' if ok and not errors else 'warning',
                'sticky': bool(errors),
            },
        }
