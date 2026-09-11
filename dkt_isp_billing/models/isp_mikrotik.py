from odoo import models, fields, api
from odoo.exceptions import ValidationError
import os
import re
import routeros_api
import socket
import subprocess
import time
import logging
import requests
import json

from .isp_phone import parse_phone_from_username

_logger = logging.getLogger(__name__)

class ISPMikrotikConfig(models.Model):
    _name = 'isp.mikrotik.config'
    _description = 'Konfigurasi Mikrotik'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama', required=True, tracking=True)
    area_id = fields.Many2one(
        'isp.area', string='Area / Desa', tracking=True, index=True,
        help='Satu router PPPoE per area. Isolir selalu ke router ini, bukan router default.',
    )
    access_host = fields.Char(
        'Host Winbox',
        tracking=True,
        help='Port akses MikroTik (konekterus/Winbox), contoh: id01.konekterus.com:16286. '
             'Port API dihitung otomatis: port akses + 1.',
    )
    host = fields.Char(
        'Host API',
        required=True,
        tracking=True,
        compute='_compute_host',
        store=True,
        readonly=False,
        help='Format host:port API. Otomatis port Winbox + 1, contoh Winbox 16286 → API 16287.',
    )
    port = fields.Integer('Port', default=8728, tracking=True, help='Deprecated: Gunakan format host:port pada field Host API')
    username = fields.Char('Username API', required=True, tracking=True)
    password = fields.Char(
        'Password API',
        tracking=True,
        help='Isi di form Odoo. Jangan simpan di XML/git.',
    )
    session_backend = fields.Selection(
        [
            ('mikrotik_secret', 'PPPoE secret di router (sekarang)'),
            ('radius', 'RADIUS + landing isolir'),
        ],
        string='Backend sesi',
        default='mikrotik_secret',
        required=True,
        tracking=True,
        help='RADIUS: isolir ganti profile isolir (bukan disable secret). '
             'Router lain tetap secret lokal.',
    )
    radius_nas_ip = fields.Char(
        'IP sumber NAS (RADIUS)',
        tracking=True,
        help='IP yang terlihat FreeRADIUS (biasanya egress konekterus), bukan IP LAN.',
    )
    radius_nas_identifier = fields.Char(
        'NAS-Identifier',
        tracking=True,
        help='Identity MikroTik unik per desa. Mengikat user agar tidak login di desa lain.',
    )
    radius_secret = fields.Char(
        'Secret RADIUS',
        help='Shared secret NAS. Jangan simpan di XML/git.',
    )
    bandwidth_cap_exempt = fields.Boolean(
        'Bebas cap 7M',
        default=False,
        tracking=True,
        help='Sinaman dan Bulan Jahe: jangan batasi 7M/7M. Router lain di-cap untuk hemat bandwidth.',
    )
    bandwidth_cap_rate = fields.Char(
        'Rate cap router',
        tracking=True,
        help='Contoh 10M/10M untuk Samura. Kosong = 7M/7M. Tidak dipakai jika bebas cap.',
    )
    active = fields.Boolean('Active', default=True, tracking=True)
    last_test_ok = fields.Boolean('Tes koneksi terakhir OK', readonly=True)
    last_test_message = fields.Char('Pesan tes terakhir', readonly=True)
    identity_name = fields.Char('Identity MikroTik', readonly=True)
    health_state = fields.Selection(
        [
            ('reachable', 'Terjangkau'),
            ('timeout', 'Timeout'),
            ('inactive', 'Nonaktif'),
            ('skipped', 'Dilewati'),
        ],
        string='Health',
        readonly=True,
        index=True,
    )
    last_ping_ok = fields.Boolean('Ping terakhir OK', readonly=True)
    last_ping_ms = fields.Integer('Latensi TCP (ms)', readonly=True)
    last_icmp_ok = fields.Boolean('ICMP terakhir OK', readonly=True)
    last_icmp_ms = fields.Integer('Latensi ICMP (ms)', readonly=True)
    last_check = fields.Datetime('Cek health terakhir', readonly=True)
    last_error = fields.Char('Pesan health terakhir', readonly=True)

    _WINBOX_PORTS = frozenset({
        16286, 16499, 16013, 16595, 17108,
        14632, 13189, 12895, 12979, 13864,
    })

    def init(self):
        super().init()
        self._migrate_winbox_and_api_ports()
        self._fill_password_from_env()

    def _fill_password_from_env(self):
        """Isi password API dari env jika masih kosong. Jangan commit nilainya ke git."""
        password = (os.environ.get('MIKROTIK_API_PASSWORD') or '').strip()
        if not password:
            return 0
        routers = self.search([('active', '=', True)])
        to_fill = routers.filtered(lambda r: not r.password)
        if to_fill:
            to_fill.write({'password': password})
            _logger.info(
                'Password API diisi dari MIKROTIK_API_PASSWORD untuk %s router.',
                len(to_fill),
            )
        return len(to_fill)

    def _migrate_winbox_and_api_ports(self):
        """Pindahkan host yang masih port Winbox ke access_host, API = port + 1."""
        cr = self.env.cr
        cr.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'isp_mikrotik_config'
              AND column_name IN ('host', 'access_host')
        """)
        cols = {row[0] for row in cr.fetchall()}
        if 'host' not in cols or 'access_host' not in cols:
            return
        for rec in self.search([]):
            rec._ensure_access_and_api_hosts()

    def _split_host_port(self, value):
        if not value or ':' not in value:
            return False, False
        hostname, port_s = value.rsplit(':', 1)
        try:
            return hostname.strip(), int(port_s)
        except ValueError:
            return False, False

    def _api_host_from_access(self, access_host):
        hostname, port = self._split_host_port(access_host)
        if not hostname or not port:
            return False
        return '%s:%s' % (hostname, port + 1)

    def _ensure_access_and_api_hosts(self):
        self.ensure_one()
        hostname, port = self._split_host_port(self.host)
        if not hostname or not port:
            return
        vals = {}
        if port in self._WINBOX_PORTS:
            vals['access_host'] = self.host
            vals['host'] = '%s:%s' % (hostname, port + 1)
        elif (port - 1) in self._WINBOX_PORTS and not self.access_host:
            vals['access_host'] = '%s:%s' % (hostname, port - 1)
        if vals:
            self.sudo().write(vals)

    @api.depends('access_host')
    def _compute_host(self):
        for rec in self:
            api_host = rec._api_host_from_access(rec.access_host)
            if api_host:
                rec.host = api_host

    @api.constrains('host')
    def _check_host_format(self):
        for record in self:
            if not record.host:
                raise ValidationError('Host/IP tidak boleh kosong')
            
            try:
                host_parts = record.host.split(':')
                if len(host_parts) != 2:
                    raise ValidationError('Format Host/IP harus host:port (contoh: id01.konekterus.com:16287)')
                
                # Validasi port
                try:
                    port = int(host_parts[1])
                    if port <= 0 or port > 65535:
                        raise ValidationError('Port harus antara 1-65535')
                except ValueError:
                    raise ValidationError('Port harus berupa angka')
                
                host = host_parts[0].strip()
                if not host:
                    raise ValidationError('Host/IP tidak boleh kosong')
            except Exception as e:
                if not isinstance(e, ValidationError):
                    raise ValidationError(f'Format Host/IP tidak valid: {str(e)}')
                raise

    def _parse_host_port(self):
        """Helper method untuk parsing host:port"""
        try:
            host_parts = self.host.split(':')
            if len(host_parts) != 2:
                raise ValidationError('Format Host/IP harus host:port')
            
            host = host_parts[0].strip()
            port = int(host_parts[1])
            
            return host, port
        except (ValueError, IndexError):
            raise ValidationError('Format Host/IP tidak valid')

    def test_connection(self):
        self.ensure_one()
        if not self.password:
            raise ValidationError('Password API belum diisi untuk router %s.' % self.name)
        try:
            host, port = self._parse_host_port()
            _logger.info(f'Mencoba koneksi ke {host}:{port} dengan user {self.username}')
            
            # Cek dulu apakah host bisa di-ping
            try:
                socket.create_connection((host, port), timeout=5)
                _logger.info('Koneksi socket berhasil')
            except (socket.timeout, socket.gaierror, ConnectionRefusedError) as e:
                raise ValidationError(f'Tidak dapat terhubung ke {host}:{port} - {str(e)}')
            
            # Koneksi non-SSL
            connection = routeros_api.RouterOsApiPool(
                host=host,
                username=self.username,
                password=self.password,
                port=port,
                plaintext_login=True
            )
            
            try:
                api = connection.get_api()
                identity = api.get_resource('/system/identity').get()
                _logger.info(f'Berhasil mendapatkan identity: {identity}')
                identity_name = identity[0].get('name') if identity else False
                self.write({
                    'last_test_ok': True,
                    'last_test_message': 'Koneksi OK (%s)' % (identity_name or host),
                    'identity_name': identity_name or False,
                    'health_state': 'reachable',
                    'last_ping_ok': True,
                    'last_check': fields.Datetime.now(),
                    'last_error': False,
                })
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Sukses',
                        'message': 'Koneksi ke Mikrotik %s berhasil' % self.name,
                        'type': 'success',
                    }
                }
            except Exception as e:
                _logger.error(f'Error saat mengambil identity: {str(e)}')
                if 'cannot log in' in str(e).lower():
                    raise ValidationError('Username atau password salah')
                raise ValidationError(f'Gagal mengakses API: {str(e)}')
            finally:
                try:
                    connection.disconnect()
                    _logger.info('Koneksi berhasil ditutup')
                except:
                    _logger.warning('Gagal menutup koneksi')
                    
        except ValidationError as e:
            _logger.error(f'Validation error: {str(e)}')
            raise
        except Exception as e:
            _logger.error(f'Test connection error: {str(e)}', exc_info=True)
            if not str(e):
                raise ValidationError('Koneksi timeout atau ditolak oleh server')
            raise ValidationError(f'Koneksi gagal: {str(e)}')

    def get_connection(self):
        self.ensure_one()
        
        # Skip koneksi jika sedang loading data awal (init mode)
        if self.env.context.get('install_mode'):
            return True
            
        if not self.password:
            _logger.warning('Password API kosong untuk router %s', self.name)
            return False
        try:
            host, port = self._parse_host_port()
            _logger.info(f'Membuat koneksi ke {host}:{port}')
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(8)
            try:
                connection = routeros_api.RouterOsApiPool(
                    host=host,
                    username=self.username,
                    password=self.password,
                    port=port,
                    plaintext_login=True
                )
                api = connection.get_api()
            finally:
                socket.setdefaulttimeout(old_timeout)
            # Simpan connection pool di api object untuk digunakan saat disconnect
            api.connection_pool = connection
            return api
                
        except Exception as e:
            _logger.error(f'Get connection error: {str(e)}', exc_info=True)
            return False

    _BANDWIDTH_CAP_RATE = '7M/7M'
    _BANDWIDTH_CAP_RATE_BY_NAME = {
        'Samura': '10M/10M',
    }
    _BANDWIDTH_CAP_EXEMPT_NAMES = frozenset({'Sinaman', 'Bulan Jahe'})
    _BANDWIDTH_CAP_SKIP_PROFILES = frozenset({
        'default', 'default-encryption', 'isolir',
    })

    def uses_radius_isolir(self):
        return self.session_backend == 'radius'

    def is_bandwidth_cap_exempt(self):
        """Sinaman / Bulan Jahe tidak ikut hemat 7M."""
        self.ensure_one()
        if self.bandwidth_cap_exempt:
            return True
        return (self.name or '').strip() in self._BANDWIDTH_CAP_EXEMPT_NAMES

    def bandwidth_cap_rate_applied(self):
        """Rate cap router ini. Samura 10M/10M, lain 7M/7M, exempt kosong."""
        self.ensure_one()
        if self.is_bandwidth_cap_exempt():
            return ''
        custom = (self.bandwidth_cap_rate or '').strip()
        if custom:
            return custom
        named = self._BANDWIDTH_CAP_RATE_BY_NAME.get((self.name or '').strip())
        if named:
            return named
        return self._BANDWIDTH_CAP_RATE

    def apply_bandwidth_cap_7m(self):
        """Set rate cap pada profile PPP non-isolir. Tidak kick, tidak isolir.

        Router exempt dilewati. Secret dan disabled tidak diubah.
        Samura memakai 10M/10M; router hemat lain 7M/7M.
        """
        Template = self.env['isp.pppoe.profile.template']
        isolir = Template.search([('is_isolir', '=', True)], limit=1)
        skip = set(self._BANDWIDTH_CAP_SKIP_PROFILES)
        if isolir and isolir.name:
            skip.add(isolir.name)
        results = []
        for router in self:
            if router.is_bandwidth_cap_exempt():
                results.append({
                    'name': router.name, 'skipped': 'exempt', 'updated': 0,
                })
                continue
            rate = router.bandwidth_cap_rate_applied()
            if not rate:
                results.append({
                    'name': router.name, 'skipped': 'no_rate', 'updated': 0,
                })
                continue
            if (
                not router.active
                or not router.password
                or (router.host or '').startswith('127.')
            ):
                results.append({
                    'name': router.name, 'skipped': 'inactive', 'updated': 0,
                })
                continue
            api = router.get_connection()
            if not api or api is True:
                results.append({
                    'name': router.name, 'error': 'gagal koneksi API', 'updated': 0,
                })
                continue
            try:
                profile_api = api.get_resource('/ppp/profile')
                changed = []
                for item in profile_api.get() or []:
                    name = (item.get('name') or '').strip()
                    if not name or name in skip:
                        continue
                    current = (item.get('rate-limit') or '').strip()
                    if current == rate:
                        continue
                    profile_id = item.get('.id') or item.get('id')
                    if not profile_id:
                        continue
                    profile_api.set(
                        id=str(profile_id),
                        **{'rate-limit': rate},
                    )
                    changed.append(name)
                    local = self.env['isp.mikrotik.profile'].search([
                        ('mikrotik_config_id', '=', router.id),
                        ('name', '=', name),
                    ], limit=1)
                    if local:
                        local.write({
                            'rate_limit': rate,
                            'sync_state': 'synced',
                            'sync_error': False,
                            'last_sync': fields.Datetime.now(),
                        })
                results.append({
                    'name': router.name,
                    'rate': rate,
                    'updated': len(changed),
                    'profiles': changed,
                })
            except Exception as exc:
                _logger.exception('Gagal cap bandwidth di %s', router.name)
                results.append({
                    'name': router.name, 'error': str(exc), 'updated': 0,
                })
            finally:
                if api and hasattr(api, 'connection_pool'):
                    try:
                        api.connection_pool.disconnect()
                    except Exception:
                        _logger.warning(
                            'Gagal tutup API setelah cap bandwidth %s', router.name,
                        )
        return results

    def assign_secret_profiles(self, username_to_profile):
        """Ganti profile secret saja. Tidak mengubah disabled, tidak kick sesi."""
        self.ensure_one()
        mapping = {
            str(name or '').strip(): str(profile or '').strip()
            for name, profile in (username_to_profile or {}).items()
            if name and profile
        }
        if not mapping:
            return {'updated': 0, 'unchanged': 0, 'missing': 0, 'errors': []}
        api = self.get_connection()
        if not api or api is True:
            return {
                'updated': 0, 'unchanged': 0, 'missing': 0,
                'errors': ['Gagal terhubung ke router %s.' % self.name],
            }
        updated = unchanged = missing = 0
        errors = []
        try:
            secret_api = api.get_resource('/ppp/secret')
            secrets = {
                (item.get('name') or ''): item
                for item in (secret_api.get() or [])
            }
            for username, profile in mapping.items():
                secret = secrets.get(username)
                if not secret:
                    missing += 1
                    continue
                if (secret.get('profile') or '') == profile:
                    unchanged += 1
                    continue
                secret_id = secret.get('.id') or secret.get('id') or secret.get('.uid')
                if not secret_id:
                    errors.append('ID secret %s tidak ada di %s.' % (username, self.name))
                    continue
                try:
                    secret_api.set(id=str(secret_id), profile=profile)
                    updated += 1
                except Exception as exc:
                    errors.append('%s @ %s: %s' % (username, self.name, exc))
                    _logger.exception('assign_secret_profiles %s di %s', username, self.name)
        except Exception as exc:
            errors.append('%s: %s' % (self.name, exc))
            _logger.exception('assign_secret_profiles di %s', self.name)
        finally:
            if api and hasattr(api, 'connection_pool'):
                try:
                    api.connection_pool.disconnect()
                except Exception:
                    _logger.warning('Gagal menutup koneksi setelah assign profile %s', self.name)
        return {
            'updated': updated,
            'unchanged': unchanged,
            'missing': missing,
            'errors': errors,
        }

    def set_secret_profile(self, username, profile, disabled=False):
        """Ganti profile secret, biarkan enabled kecuali diminta disable."""
        self.ensure_one()
        api = self.get_connection()
        if not api:
            return False, 'Gagal terhubung ke router %s (timeout/koneksi).' % self.name
        try:
            secret_api = api.get_resource('/ppp/secret')
            secrets = secret_api.get(name=str(username or ''))
            if not secrets:
                return False, 'PPPoE secret %s tidak ditemukan di %s.' % (username, self.name)
            secret = secrets[0] or {}
            secret_id = secret.get('.id') or secret.get('id') or secret.get('.uid')
            if not secret_id:
                return False, 'ID secret %s tidak ditemukan di %s.' % (username, self.name)
            secret_api.set(
                id=str(secret_id),
                profile=str(profile or 'SAPU-JAGAD'),
                disabled='yes' if disabled else 'no',
            )
            return True, None
        except Exception as exc:
            _logger.exception('set_secret_profile %s di %s', username, self.name)
            return False, str(exc) or 'Timeout API MikroTik.'
        finally:
            if api and hasattr(api, 'connection_pool'):
                try:
                    api.connection_pool.disconnect()
                except Exception:
                    _logger.warning('Gagal menutup koneksi setelah set_secret_profile %s', self.name)

    def disconnect_pppoe_active(self, username):
        """Putus sesi /ppp/active. Tidak ada sesi = sukses."""
        self.ensure_one()
        api = self.get_connection()
        if not api:
            return False, 'Gagal terhubung ke router %s (timeout/koneksi).' % self.name
        try:
            active_api = api.get_resource('/ppp/active')
            sessions = active_api.get(name=str(username or '')) or []
            for session in sessions:
                sid = session.get('.id') or session.get('id')
                if sid:
                    active_api.remove(id=str(sid))
            return True, None
        except Exception as exc:
            _logger.exception('disconnect_pppoe_active %s di %s', username, self.name)
            return False, str(exc) or 'Timeout API MikroTik.'
        finally:
            if api and hasattr(api, 'connection_pool'):
                try:
                    api.connection_pool.disconnect()
                except Exception:
                    _logger.warning('Gagal menutup koneksi setelah disconnect %s', self.name)

    def apply_isolir_profile(self, cpe, isolated):
        """Isolir landing: profile isolir + secret enabled + kick. RADIUS tetap Accept."""
        self.ensure_one()
        username = cpe.pppoe_username if cpe else False
        if not username:
            return False, 'CPE belum memiliki PPPoE username.'
        Radius = self.env['isp.radius']
        profile = Radius.isolir_profile_name() if isolated else Radius.open_profile_name(cpe)
        ok, err = self.set_secret_profile(username, profile, disabled=False)
        if not ok:
            return False, err
        try:
            Radius.set_isolated(cpe, isolated)
        except Exception as exc:
            _logger.warning('Sinkron RADIUS dilewati CPE %s: %s', username, exc)
        kick_ok, kick_err = self.disconnect_pppoe_active(username)
        if not kick_ok:
            _logger.warning('Kick sesi %s di %s: %s', username, self.name, kick_err)
        return True, None

    def set_secret_disabled(self, username, disabled):
        """Enable/disable secret. Returns (ok, error). Tidak raise — aman untuk pelunasan."""
        self.ensure_one()
        api = self.get_connection()
        if not api:
            return False, 'Gagal terhubung ke router %s (timeout/koneksi).' % self.name
        try:
            secret_api = api.get_resource('/ppp/secret')
            secrets = secret_api.get(name=str(username or ''))
            if not secrets:
                return False, 'PPPoE secret %s tidak ditemukan di %s.' % (username, self.name)
            secret = secrets[0] or {}
            secret_id = secret.get('.id') or secret.get('id') or secret.get('.uid')
            if not secret_id:
                return False, 'ID secret %s tidak ditemukan di %s.' % (username, self.name)
            secret_api.set(id=str(secret_id), disabled='yes' if disabled else 'no')
            return True, None
        except Exception as exc:
            _logger.exception('set_secret_disabled %s di %s', username, self.name)
            return False, str(exc) or 'Timeout API MikroTik.'
        finally:
            if api and hasattr(api, 'connection_pool'):
                try:
                    api.connection_pool.disconnect()
                except Exception:
                    _logger.warning('Gagal menutup koneksi setelah set_secret_disabled %s', self.name)

    def enable_user(self, username):
        """Mengaktifkan user PPPoE di Mikrotik"""
        self.ensure_one()
        api = self.get_connection()
        if not api:
            raise ValidationError('Gagal terhubung ke Mikrotik!')
            
        try:
            secret_api = api.get_resource('/ppp/secret')
            secrets = secret_api.get(name=username)
            if secrets:
                secret_id = secrets[0].get('id', '')
                if secret_id:
                    secret_api.set(id=secret_id, disabled='no')
                    return True
            return False
        except Exception as e:
            raise ValidationError(f'Gagal mengaktifkan user di Mikrotik: {str(e)}')
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect()

    def disable_user(self, username):
        """Menonaktifkan user PPPoE di Mikrotik"""
        self.ensure_one()
        api = self.get_connection()
        if not api:
            raise ValidationError('Gagal terhubung ke Mikrotik!')
            
        try:
            secret_api = api.get_resource('/ppp/secret')
            secrets = secret_api.get(name=username)
            if secrets:
                secret_id = secrets[0].get('id', '')
                if secret_id:
                    secret_api.set(id=secret_id, disabled='yes')
                    return True
            return False
        except Exception as e:
            raise ValidationError(f'Gagal menonaktifkan user di Mikrotik: {str(e)}')
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect()

    @api.model
    def get_default_config(self):
        """Tidak ada router default di multi-area. Hanya aman jika router aktif tepat satu."""
        configs = self.search([('active', '=', True)])
        if len(configs) == 1:
            return configs
        raise ValidationError(
            'Tidak ada router default. Pilih router area pada CPE / pelanggan.'
        )

    @api.model
    def require_cpe_router(self, cpe):
        """Router wajib dari CPE. Jangan fallback ke router desa lain."""
        if not cpe:
            raise ValidationError('CPE tidak ditemukan.')
        if not cpe.mikrotik_config_id:
            raise ValidationError(
                'CPE %s belum terhubung ke router area. Pilih MikroTik pada CPE.'
                % cpe.display_name
            )
        if not cpe.mikrotik_config_id.password:
            raise ValidationError(
                'Password API router %s belum diisi. Isi di menu Konfigurasi > Mikrotik.'
                % cpe.mikrotik_config_id.name
            )
        return cpe.mikrotik_config_id

    def format_secret_comment(self, partner):
        """Comment secret: Nama - Area (harga tidak ditulis ke MikroTik)."""
        self.ensure_one()
        partner_name = partner.name if partner else ''
        area_name = self.area_id.name if self.area_id else ''
        if partner_name and area_name:
            return '%s - %s' % (partner_name, area_name)
        return partner_name or area_name or ''

    def action_push_standard_profiles(self):
        templates = self.env['isp.pppoe.profile.template'].search([('active', '=', True)])
        if not templates:
            raise ValidationError('Belum ada template profile PPPoE.')
        return templates._push_to_routers(self)

    def action_apply_bandwidth_savings_7m(self):
        """Tombol daftar router: cap 7M kecuali Sinaman/Bulan Jahe. Tidak kick."""
        return self.env['isp.pppoe.profile.template'].action_apply_bandwidth_savings_7m()

    # Liang Pangi: area tidak memakai PPPoE (bukan connection refused).
    _IMPORT_SKIP_ROUTER_NAMES = frozenset({
        'Liang Pangi',
    })
    _PRICE_SUFFIX_RE = re.compile(
        r'\s*:\s*(?:rp\.?\s*)?[\d.,]+\s*(?:rb|ribu|rbu|k)?\s*$',
        re.IGNORECASE,
    )
    _PAKET_NAME_RE = re.compile(r'paket\s*[:\-]?\s*(\d+)', re.IGNORECASE)
    _PRICE_TOKEN_RE = re.compile(
        r':\s*(?:rp\.?\s*)?([\d.,]+)\s*(rb|ribu|rbu|k)?',
        re.IGNORECASE,
    )
    _COMMERCIAL_CODES = {
        150: 'PAKET_150',
        200: 'PAKET_200',
        250: 'PAKET_250',
        300: 'PAKET_300',
        350: 'PAKET_350',
        500: 'PAKET_500',
        600: 'PAKET_600',
    }
    _COMMERCIAL_AMOUNTS = {
        150000: 'PAKET_150',
        200000: 'PAKET_200',
        250000: 'PAKET_250',
        300000: 'PAKET_300',
        350000: 'PAKET_350',
        500000: 'PAKET_500',
        600000: 'PAKET_600',
    }
    _PPPOE_SERVICES = frozenset({'pppoe', 'any', ''})

    @api.model
    def parse_secret_comment(self, comment):
        """Ambil nama dan area dari comment secret. Harga diabaikan.

        Format baru: ``Nama - Area``.
        Format lama: ``nama - desa: harga``.
        """
        raw = (comment or '').strip()
        if not raw:
            return '', ''
        raw = re.sub(r'\s*#.*$', '', raw).strip()
        without_price = self._PRICE_SUFFIX_RE.sub('', raw).strip()
        name = without_price
        area_text = ''
        for sep in (' - ', ' – ', ' — '):
            if sep in without_price:
                name, area_text = without_price.rsplit(sep, 1)
                name = name.strip()
                area_text = area_text.strip()
                break
        if area_text and re.fullmatch(r'[\d.,\s]+', area_text):
            area_text = ''
        return name, area_text

    @api.model
    def _normalize_comment_price_token(self, num_s, suffix=None):
        """Ubah token comment menjadi kode paket komersial jika dikenal.

        Comment lapangan memakai kode paket (150/200/250/300/350/500/600),
        kadang nominal rupiah. Token lain tidak diada-adakan.
        """
        digits = re.sub(r'[^\d]', '', num_s or '')
        if not digits:
            return {'token': None, 'amount': None, 'code': None}
        try:
            number = int(digits)
        except ValueError:
            return {'token': None, 'amount': None, 'code': None}
        if suffix and suffix.lower() in ('rb', 'ribu', 'rbu', 'k'):
            number *= 1000
        if number in self._COMMERCIAL_CODES:
            return {
                'token': str(number),
                'amount': number * 1000,
                'code': self._COMMERCIAL_CODES[number],
            }
        if number in self._COMMERCIAL_AMOUNTS:
            return {
                'token': str(number),
                'amount': number,
                'code': self._COMMERCIAL_AMOUNTS[number],
            }
        amount = number * 1000 if number < 1000 else number
        return {'token': str(number), 'amount': amount, 'code': None}

    @api.model
    def parse_secret_comment_price(self, comment):
        """Ambil token harga/paket dari comment secret MikroTik.

        Return dict: token, amount (rupiah jika dikenal/dinormalisasi), code.
        """
        raw = (comment or '').strip()
        if not raw:
            return {'token': None, 'amount': None, 'code': None}
        raw = re.sub(r'\s*#.*$', '', raw).strip()
        paket = self._PAKET_NAME_RE.search(raw)
        if paket:
            return self._normalize_comment_price_token(paket.group(1))
        matches = list(self._PRICE_TOKEN_RE.finditer(raw))
        if matches:
            last = matches[-1]
            return self._normalize_comment_price_token(last.group(1), last.group(2))
        return {'token': None, 'amount': None, 'code': None}

    def _package_from_secret_comment(self, comment, area=None):
        """Paket komersial dari comment, atau empty recordset jika tidak dikenal."""
        parsed = self.parse_secret_comment_price(comment)
        return self.env['isp.package'].match_commercial_package(
            code=parsed.get('code'),
            amount=parsed.get('amount'),
            area=area,
        ), parsed

    def _host_is_loopback(self):
        self.ensure_one()
        for value in (self.host, self.access_host):
            hostname = (value or '').split(':')[0].strip()
            if hostname.startswith('127.'):
                return True
        return False

    def _import_skip_reason(self):
        """Alasan router tidak diimpor. False jika boleh dicoba."""
        self.ensure_one()
        name = (self.name or '').strip()
        if name == 'Liang Pangi':
            return 'Tidak dipakai: area ini tidak memakai PPPoE. Dilewati import secret.'
        if name in self._IMPORT_SKIP_ROUTER_NAMES:
            return 'Router dilewati.'
        if self._host_is_loopback():
            return 'Host loopback 127.* dilewati.'
        if not (self.password or '').strip():
            return 'Password API kosong.'
        if not self.active:
            return 'Router nonaktif.'
        return False

    def _is_liang_pangi(self):
        self.ensure_one()
        return (self.name or '').strip() == 'Liang Pangi'

    def _health_skip_reason(self):
        """Alasan tidak di-ping. False jika boleh dicek. Tidak memaksa Liang Pangi."""
        self.ensure_one()
        if not self.active:
            return 'Router nonaktif.'
        if self._is_liang_pangi() or (self.name or '').strip() in self._IMPORT_SKIP_ROUTER_NAMES:
            return 'Tidak dipakai: area ini tidak memakai PPPoE. Tidak dipaksa ping.'
        if self._host_is_loopback():
            return 'Host loopback 127.* dilewati.'
        return False

    def _short_health_error(self, message):
        text = (message or '').strip()
        return text[:250] if text else False

    def _tcp_ping(self, timeout=4):
        """TCP connect ke host:port API. Return (ok, ms, error)."""
        self.ensure_one()
        try:
            host, port = self._parse_host_port()
        except Exception as exc:
            return False, 0, self._short_health_error(str(exc))
        start = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                ms = max(1, int((time.monotonic() - start) * 1000))
                return True, ms, False
        except socket.timeout:
            return False, int(timeout * 1000), self._short_health_error(
                'Timeout TCP %ss ke %s:%s' % (timeout, host, port)
            )
        except OSError as exc:
            ms = int((time.monotonic() - start) * 1000)
            return False, ms, self._short_health_error(
                'TCP %s:%s — %s' % (host, port, exc)
            )

    def _icmp_ping(self, timeout=3):
        """ICMP opsional. Gagal izin/binary tidak mengubah health TCP."""
        self.ensure_one()
        hostname = (self.host or '').split(':')[0].strip()
        if not hostname:
            return False, 0, 'Host kosong'
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', str(int(timeout)), hostname],
                capture_output=True,
                text=True,
                timeout=timeout + 1,
            )
        except FileNotFoundError:
            return False, 0, 'Perintah ping tidak tersedia'
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, 0, self._short_health_error('ICMP: %s' % exc)
        output = '%s %s' % (result.stdout or '', result.stderr or '')
        match = re.search(r'time[=<]([\d.]+)\s*ms', output, re.IGNORECASE)
        ms = int(float(match.group(1))) if match else 0
        if result.returncode == 0:
            return True, ms or 1, False
        return False, ms, self._short_health_error(
            'ICMP gagal ke %s' % hostname
        )

    def _update_health(self, timeout=4):
        """Update cache health. Tidak login API, tidak sentuh CPE."""
        self.ensure_one()
        now = fields.Datetime.now()
        skip = self._health_skip_reason()
        if skip:
            state = 'inactive' if (not self.active or self._is_liang_pangi()) else 'skipped'
            self.write({
                'health_state': state,
                'last_ping_ok': False,
                'last_ping_ms': 0,
                'last_icmp_ok': False,
                'last_icmp_ms': 0,
                'last_check': now,
                'last_error': skip,
            })
            return {
                'ok': False,
                'skipped': True,
                'state': state,
                'error': skip,
            }
        ok, ms, err = self._tcp_ping(timeout=timeout)
        icmp_ok, icmp_ms = False, 0
        if ok:
            icmp_ok, icmp_ms, _icmp_err = self._icmp_ping(timeout=2)
        self.write({
            'health_state': 'reachable' if ok else 'timeout',
            'last_ping_ok': ok,
            'last_ping_ms': ms,
            'last_icmp_ok': bool(icmp_ok),
            'last_icmp_ms': icmp_ms or 0,
            'last_check': now,
            'last_error': False if ok else err,
        })
        return {
            'ok': ok,
            'skipped': False,
            'state': 'reachable' if ok else 'timeout',
            'ms': ms,
            'error': False if ok else err,
        }

    def action_check_health(self):
        """Tombol form/list: ping TCP/ICMP router ini saja."""
        results = []
        for rec in self:
            results.append((rec.display_name, rec._update_health()))
        if self.env.context.get('dashboard_silent'):
            return True
        ok_count = sum(1 for _name, res in results if res.get('ok'))
        skip_count = sum(1 for _name, res in results if res.get('skipped'))
        fail_count = len(results) - ok_count - skip_count
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Health router',
                'message': (
                    'Terjangkau: %s. Timeout: %s. Dilewati/nonaktif: %s.'
                    % (ok_count, fail_count, skip_count)
                ),
                'type': 'success' if fail_count == 0 else 'warning',
                'sticky': False,
            },
        }

    @api.model
    def action_check_all_health(self):
        return self.with_context(active_test=False).search([]).action_check_health()

    @api.model
    def _cron_check_router_health(self):
        """Cron terpisah dari PPPoE: ping ~9–10 router area, bukan 477 CPE."""
        routers = self.with_context(active_test=False).search([])
        ok = skip = fail = 0
        for router in routers:
            result = router._update_health(timeout=4)
            if result.get('skipped'):
                skip += 1
            elif result.get('ok'):
                ok += 1
            else:
                fail += 1
        _logger.info(
            'Cron health router: terjangkau=%s timeout=%s dilewati=%s dari %s',
            ok, fail, skip, len(routers),
        )
        return True

    def _secret_is_disabled(self, secret):
        val = str((secret or {}).get('disabled', 'false')).strip().lower()
        return val in ('yes', 'true', '1')

    def _resolve_import_area(self, area_text):
        """Area dari comment jika cocok; jika kosong/mismatch pakai area router."""
        self.ensure_one()
        router_area = self.area_id
        text = (area_text or '').strip()
        if not text:
            return router_area, 'missing'
        Area = self.env['isp.area']
        cleaned = re.sub(r'^(desa|kelurahan|dusun)\s+', '', text, flags=re.IGNORECASE).strip()
        area = Area.search(['|', ('name', '=ilike', text), ('name', '=ilike', cleaned)], limit=1)
        if not area:
            code = cleaned.upper().replace(' ', '_')
            area = Area.search([('code', '=ilike', code)], limit=1)
        if not area:
            return router_area, 'unmatched'
        if router_area and area != router_area:
            return router_area, 'mismatch'
        return area, 'matched'

    def _list_ppp_secrets(self):
        self.ensure_one()
        api = self.get_connection()
        if not api:
            return False, 'Gagal terhubung ke Mikrotik.'
        try:
            secrets = api.get_resource('/ppp/secret').get()
            return list(secrets or []), False
        except Exception as exc:
            _logger.exception('Gagal membaca /ppp/secret di %s', self.name)
            return False, str(exc)
        finally:
            if api and hasattr(api, 'connection_pool'):
                try:
                    api.connection_pool.disconnect()
                except Exception:
                    pass

    def _empty_router_stats(self, skip_reason=None, connect_error=None):
        return {
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'secret_count': 0,
            'skip_reason': skip_reason,
            'connect_error': connect_error,
            'parse_issues': [],
            'lines': [],
        }

    def _import_ppp_secret(self, secret, package):
        """Buat/update partner+CPE+subscription dari satu secret. Tidak menulis ke MikroTik.

        Returns: (status, message, username, parse_issue)
        """
        self.ensure_one()
        username = (secret.get('name') or '').strip()
        if not username:
            return 'skipped', 'Username secret kosong.', None, None
        service = (secret.get('service') or 'pppoe').strip().lower()
        if service not in self._PPPOE_SERVICES:
            return 'skipped', 'Bukan layanan PPPoE (%s).' % service, username, None
        password = secret.get('password') or ''
        if not str(password).strip():
            return 'skipped', 'Password secret kosong.', username, None

        comment = secret.get('comment') or ''
        name, area_text = self.parse_secret_comment(comment)
        parse_issue = None
        if not name:
            name = username
            if not (comment or '').strip():
                parse_issue = 'Comment kosong/tanpa nama, nama pelanggan = username.'
        area, area_src = self._resolve_import_area(area_text)
        if area_src == 'unmatched':
            parse_issue = 'Area comment "%s" tidak cocok master, dipakai area router.' % area_text
        elif area_src == 'mismatch':
            parse_issue = 'Area comment "%s" beda dengan router, dipakai area router.' % area_text

        disabled = self._secret_is_disabled(secret)
        partner_state = 'isolated' if disabled else 'active'
        cpe_state = 'isolated' if disabled else 'open'
        sub_state = 'isolated' if disabled else 'open'

        Cpe = self.env['isp.cpe']
        cpe = Cpe.search([
            ('pppoe_username', '=', username),
            ('mikrotik_config_id', '=', self.id),
            ('connection_type', '=', 'pppoe'),
        ], limit=1)
        other_cpe = Cpe.browse()
        if not cpe:
            other_cpe = Cpe.search([
                ('pppoe_username', '=', username),
                ('connection_type', '=', 'pppoe'),
            ], limit=1)

        commercial, parsed_price = self._package_from_secret_comment(comment, area=area)
        billing_package = commercial or package
        review_needed = not bool(commercial)

        if cpe:
            cpe_vals = {
                'pppoe_password': password,
                'mikrotik_config_id': self.id,
                'state': cpe_state,
            }
            if comment:
                cpe_vals['notes'] = 'Comment MikroTik: %s' % comment
            cpe.write(cpe_vals)
            partner = cpe.partner_id
            partner_vals = {'is_adopted_secret': True}
            if area and not partner.area_id:
                partner_vals['area_id'] = area.id
            if partner.state in ('draft', 'active', 'isolated', False):
                partner_vals['state'] = partner_state
            partner.write(partner_vals)
            sub = cpe.subscription_id
            if not sub:
                sub = cpe.subscription_ids.filtered(lambda s: s.state != 'terminated')[:1]
            if sub:
                sub_vals = {
                    'state': sub_state,
                    'partner_id': partner.id,
                }
                # Jangan timpa paket komersial dengan SAPU-JAGAD saat re-import.
                if commercial:
                    sub_vals['package_id'] = commercial.id
                    sub_vals['billing_review_needed'] = False
                    sub_vals['billing_review_note'] = False
                elif sub.package_id and not sub.package_id.is_default:
                    sub_vals['billing_review_needed'] = False
                else:
                    sub_vals['package_id'] = billing_package.id
                    sub_vals['billing_review_needed'] = review_needed
                    sub_vals['billing_review_note'] = self._billing_review_note(parsed_price)
                sub.write(sub_vals)
            else:
                self.env['isp.subscription'].create({
                    'partner_id': partner.id,
                    'cpe_id': cpe.id,
                    'package_id': billing_package.id,
                    'date_start': fields.Date.today(),
                    'due_day': 1,
                    'state': sub_state,
                    'billing_review_needed': review_needed,
                    'billing_review_note': self._billing_review_note(parsed_price),
                })
            self._apply_username_phone(partner, username)
            return 'updated', parse_issue or 'CPE diperbarui.', username, parse_issue

        if other_cpe:
            partner = other_cpe.partner_id
            partner_vals = {'is_adopted_secret': True}
            if area and not partner.area_id:
                partner_vals['area_id'] = area.id
            if not disabled:
                partner_vals['state'] = 'active'
            elif partner.state in ('draft', False):
                partner_vals['state'] = partner_state
            partner.write(partner_vals)
        else:
            country = self.env.ref('base.id', raise_if_not_found=False)
            partner = self.env['res.partner'].create({
                'name': name,
                'customer_rank': 1,
                'company_type': 'person',
                'area_id': area.id if area else False,
                'state': partner_state,
                'is_adopted_secret': True,
                'country_id': country.id if country else False,
                'notes': 'Diimpor dari PPPoE %s @ %s' % (username, self.name),
            })
        cpe_name = 'CPE-%s (%s)' % (name, self.name) if other_cpe else 'CPE-%s' % name
        cpe = Cpe.create({
            'name': cpe_name,
            'partner_id': partner.id,
            'connection_type': 'pppoe',
            'mikrotik_config_id': self.id,
            'pppoe_username': username,
            'pppoe_password': password,
            'state': cpe_state,
            'notes': ('Comment MikroTik: %s' % comment) if comment else False,
        })
        self.env['isp.subscription'].create({
            'partner_id': partner.id,
            'cpe_id': cpe.id,
            'package_id': billing_package.id,
            'date_start': fields.Date.today(),
            'due_day': 1,
            'state': sub_state,
            'billing_review_needed': review_needed,
            'billing_review_note': self._billing_review_note(parsed_price),
        })
        self._apply_username_phone(partner, username)
        return 'created', parse_issue or 'Pelanggan+CPE+langganan dibuat.', username, parse_issue

    @api.model
    def _apply_username_phone(self, partner, username):
        """Isi phone dari username secret saat import. Tidak menimpa nomor manual."""
        if not partner:
            return
        parsed = parse_phone_from_username(username)
        if parsed:
            partner._apply_phone_from_secret(parsed)

    @api.model
    def _billing_review_note(self, parsed_price):
        if not parsed_price:
            return False
        if parsed_price.get('code'):
            return False
        token = parsed_price.get('token')
        if token:
            return 'Token harga/paket "%s" tidak cocok paket komersial. Perlu review.' % token
        return 'Comment tanpa harga/paket. Perlu review.'

    def import_ppp_secrets(self):
        """Import /ppp/secret ke Odoo. Idempotent, tidak mengubah secret di MikroTik."""
        if self.env.context.get('install_mode'):
            return {
                'created': 0, 'updated': 0, 'skipped': 0, 'errors': 0,
                'parse_issues': [], 'routers': {},
            }
        package = self.env['isp.package'].get_default_package()
        if not package:
            raise ValidationError('Paket default SAPU-JAGAD tidak ditemukan.')

        totals = {
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'parse_issues': [],
            'routers': {},
        }
        import_ctx = {
            'tracking_disable': True,
            'mail_notrack': True,
            'mail_create_nolog': True,
        }
        for router in self:
            skip_reason = router._import_skip_reason()
            if skip_reason:
                rstats = router._empty_router_stats(skip_reason=skip_reason)
                rstats['skipped'] = 1
                rstats['lines'].append({
                    'username': '-',
                    'status': 'skipped',
                    'message': skip_reason,
                })
                totals['skipped'] += 1
                totals['routers'][router.name] = rstats
                _logger.info('Skip import secret %s: %s', router.name, skip_reason)
                continue

            secrets, connect_error = router._list_ppp_secrets()
            if connect_error:
                rstats = router._empty_router_stats(connect_error=connect_error)
                rstats['errors'] = 1
                rstats['lines'].append({
                    'username': '-',
                    'status': 'error',
                    'message': connect_error,
                })
                totals['errors'] += 1
                totals['routers'][router.name] = rstats
                _logger.warning('Gagal koneksi import %s: %s', router.name, connect_error)
                continue

            rstats = router._empty_router_stats()
            rstats['secret_count'] = len(secrets)
            status_key = {'error': 'errors'}
            for secret in secrets:
                username = (secret.get('name') or '').strip() or '-'
                parse_issue = None
                try:
                    with self.env.cr.savepoint():
                        status, message, uname, parse_issue = router.with_context(
                            **import_ctx
                        )._import_ppp_secret(secret, package)
                    username = uname or username
                except Exception as exc:
                    status = 'error'
                    message = str(exc)
                    _logger.exception(
                        'Gagal import secret %s di %s', username, router.name,
                    )
                key = status_key.get(status, status)
                rstats[key] = rstats.get(key, 0) + 1
                totals[key] = totals.get(key, 0) + 1
                rstats['lines'].append({
                    'username': username,
                    'status': status,
                    'message': message,
                })
                if parse_issue:
                    issue = '%s / %s: %s' % (router.name, username, parse_issue)
                    rstats['parse_issues'].append(issue)
                    totals['parse_issues'].append(issue)
            totals['routers'][router.name] = rstats
            summary = (
                'Import secret %s: dibuat %s, diperbarui %s, dilewati %s, error %s (secret=%s)'
                % (
                    router.name, rstats['created'], rstats['updated'],
                    rstats['skipped'], rstats['errors'], rstats['secret_count'],
                )
            )
            _logger.info(summary)
            router.message_post(body=summary)
        return totals

    def _import_result_action(self, stats):
        log_lines = [
            'Dibuat: %s | Diperbarui: %s | Dilewati: %s | Error: %s' % (
                stats['created'], stats['updated'], stats['skipped'], stats['errors'],
            ),
            '',
        ]
        for router_name, rstats in stats.get('routers', {}).items():
            log_lines.append('=== %s ===' % router_name)
            if rstats.get('skip_reason'):
                log_lines.append('Dilewati: %s' % rstats['skip_reason'])
            if rstats.get('connect_error'):
                log_lines.append('Gagal koneksi: %s' % rstats['connect_error'])
            log_lines.append(
                'Secret: %s | Dibuat: %s | Diperbarui: %s | Dilewati: %s | Error: %s'
                % (
                    rstats.get('secret_count', 0),
                    rstats['created'], rstats['updated'],
                    rstats['skipped'], rstats['errors'],
                )
            )
            for line in rstats.get('lines', []):
                if line['status'] in ('skipped', 'error') or (
                    line.get('message') and 'Area comment' in (line.get('message') or '')
                ):
                    log_lines.append(
                        '[%s] %s — %s' % (line['status'], line['username'], line['message'])
                    )
            log_lines.append('')
        if stats.get('parse_issues'):
            log_lines.append('--- Contoh masalah parse ---')
            for issue in stats['parse_issues'][:30]:
                log_lines.append(issue)
        wizard = self.env['isp.import.secret.wizard'].create({
            'created_count': stats['created'],
            'updated_count': stats['updated'],
            'skipped_count': stats['skipped'],
            'error_count': stats['errors'],
            'log': '\n'.join(log_lines),
        })
        notif_type = 'success' if not stats['errors'] else 'warning'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Import secret selesai',
                'message': 'Dibuat: %s, diperbarui: %s, dilewati: %s, error: %s' % (
                    stats['created'], stats['updated'], stats['skipped'], stats['errors'],
                ),
                'type': notif_type,
                'sticky': True,
                'next': {
                    'type': 'ir.actions.act_window',
                    'name': 'Hasil import secret',
                    'res_model': 'isp.import.secret.wizard',
                    'res_id': wizard.id,
                    'view_mode': 'form',
                    'target': 'new',
                },
            },
        }

    def action_import_secrets_to_odoo(self):
        """Import secret router yang dipilih ke Odoo."""
        if not self:
            raise ValidationError('Pilih router terlebih dahulu.')
        stats = self.import_ppp_secrets()
        return self._import_result_action(stats)

    @api.model
    def action_import_all_reachable_secrets(self):
        """Import secret semua router aktif yang terjangkau (skip list tetap berlaku)."""
        routers = self.env['isp.mikrotik.config'].search([('active', '=', True)])
        if not routers:
            raise ValidationError('Tidak ada router aktif.')
        stats = routers.import_ppp_secrets()
        return routers._import_result_action(stats)

    def action_push_radius_users(self):
        """Tulis user+paket ke RADIUS. Secret lokal tetap, tidak kick."""
        Radius = self.env['isp.radius']
        ok = skipped = 0
        errors = []
        for router in self:
            if not router.uses_radius_isolir():
                errors.append('%s bukan backend RADIUS.' % router.name)
                continue
            stats = Radius.push_router_users(router)
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