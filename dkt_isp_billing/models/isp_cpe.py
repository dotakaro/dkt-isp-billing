from odoo import models, fields, api
from odoo.exceptions import ValidationError
import random
import string
import logging
import requests

_logger = logging.getLogger(__name__)

class ISPCPE(models.Model):
    _name = 'isp.cpe'
    _description = 'Customer Premise Equipment'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Nama Perangkat', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Pelanggan', required=True, tracking=True,
                                domain=[('customer_rank', '>', 0)])
    mac_address = fields.Char('MAC Address', tracking=True)
    ip_address = fields.Char('IP Address', tracking=True)
    outdoor_unit = fields.Char('Outdoor Unit')
    router = fields.Char('Router')
    connection_type = fields.Selection([
        ('pppoe', 'PPPoE'),
        ('static', 'Static IP'),
        ('dhcp', 'DHCP')
    ], string='Tipe Koneksi', default='pppoe', required=True, tracking=True)
    mikrotik_config_id = fields.Many2one(
        'isp.mikrotik.config',
        string='Router Mikrotik',
        tracking=True,
        help='Router area tempat secret PPPoE dikelola.',
    )
    area_id = fields.Many2one(
        'isp.area',
        string='Area',
        related='mikrotik_config_id.area_id',
        store=True,
        index=True,
    )
    pppoe_username = fields.Char('PPPoE Username', tracking=True)
    pppoe_password = fields.Char('PPPoE Password', tracking=True)
    ownership = fields.Selection([
        ('customer', 'Milik Pelanggan'),
        ('company', 'Milik Perusahaan')
    ], string='Kepemilikan', default='company')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('isolated', 'Terisolir'),
        ('terminated', 'Terminasi')
    ], default='draft', string='Status', tracking=True)
    active = fields.Boolean('Active', default=True, tracking=True)
    history_ids = fields.One2many('isp.device.history', 'cpe_id', string='Riwayat')
    pppoe_history_ids = fields.One2many(
        'isp.cpe.pppoe.history', 'cpe_id', string='Riwayat PPPoE',
    )
    pppoe_history_count = fields.Integer(
        compute='_compute_pppoe_history_count', string='Jumlah Riwayat PPPoE',
    )
    notes = fields.Text('Catatan', tracking=True)
    
    subscription_ids = fields.One2many('isp.subscription', 'cpe_id', string='Subscription')
    subscription_id = fields.Many2one('isp.subscription', string='Active Subscription',
                                    compute='_compute_subscription', store=True)
    subscription_state = fields.Selection(related='subscription_id.state', string='Status Subscription')
    
    # Fields untuk monitoring PPPoE
    pppoe_status = fields.Selection([
        ('connected', 'Connected'),
        ('disconnected', 'Disconnected'),
        ('unknown', 'Unknown')
    ], string='Status PPPoE', default='unknown', tracking=True)
    pppoe_uptime = fields.Char('PPPoE Uptime', tracking=True)
    pppoe_last_seen = fields.Datetime('Last Seen', tracking=True)
    pppoe_caller_id = fields.Char('Caller ID', tracking=True, help='MAC Address perangkat yang terkoneksi')
    pppoe_address = fields.Char('PPPoE IP', tracking=True, help='IP Address yang diberikan ke client')
    pppoe_session_id = fields.Char('Session ID', tracking=True)
    pppoe_session_count = fields.Integer(
        'Jumlah Sesi',
        default=0,
        help='Jumlah sesi /ppp/active dengan username ini di router yang sama.',
    )
    is_multisession = fields.Boolean(
        'Multi-sesi',
        default=False,
        index=True,
        tracking=True,
        help='Username yang sama punya lebih dari satu sesi aktif di router yang sama. '
             'Hanya deteksi; sesi extra tidak diputus.',
    )
    pppoe_caller_ids = fields.Text(
        'Caller ID sesi aktif',
        help='Semua MAC/caller-id sesi aktif di router ini, dipisah koma.',
    )
    upload_usage = fields.Float('Upload (MB)', tracking=True)
    download_usage = fields.Float('Download (MB)', tracking=True)
    upload_rate = fields.Char('Upload Rate', tracking=True)
    download_rate = fields.Char('Download Rate', tracking=True)
    signal_strength = fields.Char('Signal Strength', tracking=True)
    
    # Field untuk monitoring realtime
    is_monitoring = fields.Boolean('Is Monitoring', default=False)
    current_upload_rate = fields.Char('Current Upload Rate', readonly=True)
    current_download_rate = fields.Char('Current Download Rate', readonly=True)
    last_update = fields.Datetime('Last Update', readonly=True)
    
    # Tambahkan field untuk menandai proses terminasi sedang berlangsung
    is_terminating = fields.Boolean('Is Terminating', default=False, copy=False)
    technician_user_id = fields.Many2one(
        'res.users', string='Teknisi pasang', tracking=True, copy=False,
    )
    isp_onboarding = fields.Boolean(
        'Pasang baru (wajib eviden)',
        default=False,
        copy=False,
        index=True,
        help='Hanya user baru via bot/approve. Pelanggan lama tidak kena deadline 24 jam.',
    )
    isp_first_connected_at = fields.Datetime('Connect pertama', copy=False)
    isp_evidence_deadline = fields.Datetime('Batas eviden', copy=False)
    isp_evidence_notified = fields.Boolean('Notif eviden terkirim', default=False, copy=False)
    isp_evidence_reminder_sent = fields.Boolean('Pengingat eviden terkirim', default=False, copy=False)
    isp_activation_revoked = fields.Boolean(
        'Aktivasi dicabut (eviden)',
        default=False,
        copy=False,
        tracking=True,
        help='Secret dimatikan karena eviden 24 jam belum lengkap. Bukan isolir nunggak.',
    )
    isp_evidence_waived = fields.Boolean(
        'Eviden dibebaskan admin', default=False, copy=False, tracking=True,
    )
    
    _pppoe_username_router_uniq = models.Constraint(
        'UNIQUE(pppoe_username, mikrotik_config_id, connection_type)',
        'PPPoE Username harus unik per router!',
    )
    _PPPOE_STATUS_FIELDS = frozenset({
        'pppoe_status',
        'pppoe_uptime',
        'pppoe_address',
        'pppoe_caller_id',
        'pppoe_session_id',
        'pppoe_last_seen',
        'pppoe_session_count',
        'is_multisession',
        'pppoe_caller_ids',
        'current_upload_rate',
        'current_download_rate',
        'upload_usage',
        'download_usage',
        'last_update',
    })

    def init(self):
        super().init()
        cr = self.env.cr
        cr.execute(
            "UPDATE isp_cpe SET mac_address = NULL "
            "WHERE mac_address IS NOT NULL AND btrim(mac_address) = ''"
        )
        # MAC tidak unik di lapangan (multi-router / perangkat pindah).
        cr.execute('SAVEPOINT drop_mac_address_uniq')
        try:
            cr.execute(
                'ALTER TABLE isp_cpe DROP CONSTRAINT IF EXISTS isp_cpe_mac_address_uniq'
            )
        except Exception:
            cr.execute('ROLLBACK TO SAVEPOINT drop_mac_address_uniq')
            _logger.warning(
                'Lewati drop constraint isp_cpe_mac_address_uniq (lock/timeout).'
            )
        cr.execute('SAVEPOINT drop_pppoe_username_uniq')
        try:
            cr.execute(
                'ALTER TABLE isp_cpe DROP CONSTRAINT IF EXISTS isp_cpe_pppoe_username_uniq'
            )
        except Exception:
            cr.execute('ROLLBACK TO SAVEPOINT drop_pppoe_username_uniq')
            _logger.warning(
                'Lewati drop constraint isp_cpe_pppoe_username_uniq (lock/timeout).'
            )

    @api.model
    def _normalize_mac_address(self, mac):
        """Kosong / whitespace jadi False agar tidak bentrok UNIQUE('')."""
        if not mac:
            return False
        mac = str(mac).strip()
        return mac or False

    def _mac_in_use_by_other(self, mac, extra_claimed=None):
        """True jika MAC sudah dipakai CPE lain atau sudah diklaim di batch ini."""
        mac = self._normalize_mac_address(mac)
        if not mac:
            return False
        mac_l = mac.lower()
        if extra_claimed and any(str(claimed).lower() == mac_l for claimed in extra_claimed):
            return True
        domain = [('mac_address', '=ilike', mac)]
        if self.ids:
            domain.append(('id', 'not in', self.ids))
        return bool(self.sudo().search(domain, limit=1))

    def _write_pppoe_status(self, vals, source='cron', claimed_macs=None):
        """Tulis HANYA field status PPPoE. MAC/IP unique tidak ikut di-write."""
        self.ensure_one()
        vals = {
            key: value for key, value in (vals or {}).items()
            if key in self._PPPOE_STATUS_FIELDS
        }
        if not vals:
            return False
        try:
            with self.env.cr.savepoint():
                self.with_context(pppoe_status_source=source).write(vals)
                self.env.flush_all()
        except Exception as exc:
            _logger.warning(
                'Skip write status CPE %s (%s): %s',
                self.display_name, self.pppoe_username or '', exc,
            )
            return False
        return True

    @api.depends('pppoe_history_ids')
    def _compute_pppoe_history_count(self):
        counts = {}
        if self.ids:
            grouped = self.env['isp.cpe.pppoe.history']._read_group(
                [('cpe_id', 'in', self.ids)],
                ['cpe_id'],
                ['__count'],
            )
            counts = {cpe.id: count for cpe, count in grouped}
        for record in self:
            record.pppoe_history_count = counts.get(record.id, 0)

    def write(self, vals):
        if 'mac_address' in vals:
            vals = dict(vals)
            vals['mac_address'] = self._normalize_mac_address(vals.get('mac_address'))
        snapshots = {}
        skip_history = self.env.context.get('skip_pppoe_history')
        track_status = 'pppoe_status' in vals and not skip_history
        track_multi = 'is_multisession' in vals and not skip_history
        if track_status or track_multi:
            snapshots = {
                rec.id: {
                    'old_status': rec.pppoe_status,
                    'old_multisession': rec.is_multisession,
                    'old_session_count': rec.pppoe_session_count,
                    'uptime': rec.pppoe_uptime or '',
                    'caller_id': rec.pppoe_caller_id or '',
                    'caller_ids': rec.pppoe_caller_ids or '',
                    'ip': rec.pppoe_address or rec.ip_address or '',
                    'router_id': rec.mikrotik_config_id.id,
                }
                for rec in self
            }
        res = super().write(vals)
        if snapshots and track_status:
            self._record_pppoe_status_history(snapshots, vals.get('pppoe_status'))
        if snapshots and track_multi:
            self._record_multisession_history(snapshots, vals)
        if {'pppoe_username', 'pppoe_password', 'mikrotik_config_id', 'state'} & set(vals):
            self._sync_radius_safe()
        return res

    def _sync_radius_safe(self):
        Radius = self.env['isp.radius']
        for rec in self:
            router = rec.mikrotik_config_id
            if not router or not router.uses_radius_isolir():
                continue
            try:
                Radius.sync_cpe(rec)
            except Exception as exc:
                _logger.warning('Sinkron RADIUS CPE %s dilewati: %s', rec.pppoe_username, exc)

    def _record_pppoe_status_history(self, snapshots, new_status):
        """Catat history hanya jika status PPPoE benar-benar berubah."""
        if not new_status:
            return
        source = self.env.context.get('pppoe_status_source', 'cron')
        if source not in ('cron', 'manual', 'monitor'):
            source = 'cron'
        vals_list = []
        now = fields.Datetime.now()
        for rec in self:
            snap = snapshots.get(rec.id) or {}
            previous = snap.get('old_status')
            if previous == new_status:
                continue
            if new_status == 'connected':
                uptime = rec.pppoe_uptime or snap.get('uptime') or ''
                caller_id = rec.pppoe_caller_id or snap.get('caller_id') or ''
                ip_address = rec.pppoe_address or snap.get('ip') or ''
            else:
                uptime = snap.get('uptime') or rec.pppoe_uptime or ''
                caller_id = snap.get('caller_id') or rec.pppoe_caller_id or ''
                ip_address = snap.get('ip') or rec.pppoe_address or rec.ip_address or ''
            vals_list.append({
                'cpe_id': rec.id,
                'timestamp': now,
                'old_status': previous or 'unknown',
                'new_status': new_status,
                'event_type': 'status_change',
                'session_count': rec.pppoe_session_count,
                'uptime': uptime,
                'caller_id': rec.pppoe_caller_ids or caller_id,
                'ip_address': ip_address,
                'router_id': snap.get('router_id') or rec.mikrotik_config_id.id,
                'source': source,
            })
        if vals_list:
            self.env['isp.cpe.pppoe.history'].sudo().create(vals_list)
        if new_status == 'connected':
            for rec in self:
                snap = snapshots.get(rec.id) or {}
                if snap.get('old_status') == 'connected':
                    continue
                try:
                    self.env['isp.wa.registration'].sudo().notify_first_connect(rec)
                except Exception as exc:
                    _logger.warning(
                        'Notif eviden CPE %s dilewati: %s', rec.pppoe_username, exc,
                    )

    def _record_multisession_history(self, snapshots, vals):
        """Catat masuk/keluar multi-sesi hanya saat flag berubah."""
        if 'is_multisession' not in vals:
            return
        new_flag = bool(vals.get('is_multisession'))
        source = self.env.context.get('pppoe_status_source', 'cron')
        if source not in ('cron', 'manual', 'monitor'):
            source = 'cron'
        vals_list = []
        now = fields.Datetime.now()
        for rec in self:
            snap = snapshots.get(rec.id) or {}
            was_multi = bool(snap.get('old_multisession'))
            if was_multi == new_flag:
                continue
            event_type = 'multisession_enter' if new_flag else 'multisession_leave'
            vals_list.append({
                'cpe_id': rec.id,
                'timestamp': now,
                'old_status': snap.get('old_status') or rec.pppoe_status or 'unknown',
                'new_status': rec.pppoe_status or 'unknown',
                'event_type': event_type,
                'session_count': rec.pppoe_session_count,
                'uptime': rec.pppoe_uptime or snap.get('uptime') or '',
                'caller_id': rec.pppoe_caller_ids or rec.pppoe_caller_id or snap.get('caller_ids') or '',
                'ip_address': rec.pppoe_address or snap.get('ip') or '',
                'router_id': snap.get('router_id') or rec.mikrotik_config_id.id,
                'source': source,
            })
        if vals_list:
            self.env['isp.cpe.pppoe.history'].sudo().create(vals_list)

    def action_view_pppoe_history(self):
        self.ensure_one()
        return {
            'name': 'Riwayat PPPoE',
            'type': 'ir.actions.act_window',
            'res_model': 'isp.cpe.pppoe.history',
            'view_mode': 'list,form',
            'domain': [('cpe_id', '=', self.id)],
            'context': {'default_cpe_id': self.id},
        }

    def action_open_isolate_bulk(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Isolir massal',
            'res_model': 'isp.isolate.bulk.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'isp.cpe',
                'active_ids': self.ids,
            },
        }

    def action_open_parse_phone(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Parse nomor dari secret',
            'res_model': 'isp.parse.phone.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'isp.cpe',
                'active_ids': self.ids,
            },
        }

    @api.depends('subscription_ids', 'subscription_ids.state')
    def _compute_subscription(self):
        for record in self:
            # Ambil subscription yang tidak terminated
            valid_subs = record.subscription_ids.filtered(lambda s: s.state != 'terminated')
            record.subscription_id = valid_subs[0] if valid_subs else False
    
    @api.constrains('subscription_ids')
    def _check_single_subscription(self):
        for record in self:
            active_subs = record.subscription_ids.filtered(lambda s: s.state != 'terminated')
            if len(active_subs) > 1:
                raise ValidationError(f'CPE {record.name} hanya boleh memiliki satu subscription aktif!')

    @api.model
    def _generate_random_password(self, length=8):
        """Generate random password with specified length."""
        characters = string.ascii_letters + string.digits
        return ''.join(random.choice(characters) for i in range(length))

    @api.onchange('mac_address')
    def _onchange_mac_address_duplicate(self):
        """Peringatan form saja; tidak memblokir simpan / refresh."""
        mac = self._normalize_mac_address(self.mac_address)
        if mac and self._mac_in_use_by_other(mac):
            return {
                'warning': {
                    'title': 'MAC sudah dipakai',
                    'message': (
                        'MAC Address ini sudah tercatat di CPE lain. '
                        'Boleh disimpan jika perangkat pindah atau dipakai di router lain.'
                    ),
                }
            }

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id and not self.pppoe_username:
            # Generate username dari nama pelanggan
            base_username = self.partner_id.name.lower().replace(' ', '-')
            counter = 1
            username = base_username
            while self.search_count([('pppoe_username', '=', username)]) > 0:
                username = f"{base_username}-{counter}"
                counter += 1
            self.pppoe_username = username
            self.pppoe_password = self._generate_random_password()

    def generate_pppoe_credentials(self):
        """Generate PPPoE username dan password"""
        self.ensure_one()
        if self.pppoe_username and self.pppoe_password:
            raise ValidationError('PPPoE credentials sudah ada!')
            
        # Generate username dari nama pelanggan
        if not self.pppoe_username:
            base_username = self.partner_id.name.lower().replace(' ', '-')
            counter = 1
            username = base_username
            while self.search_count([('pppoe_username', '=', username)]) > 0:
                username = f"{base_username}-{counter}"
                counter += 1
            self.pppoe_username = username
            
        # Generate random password
        if not self.pppoe_password:
            chars = string.ascii_letters + string.digits
            self.pppoe_password = ''.join(random.choice(chars) for _ in range(8))
    
    @api.onchange('mikrotik_config_id')
    def _onchange_mikrotik_config_id(self):
        if self.mikrotik_config_id and self.partner_id and not self.partner_id.area_id:
            self.partner_id.area_id = self.mikrotik_config_id.area_id

    @api.model_create_multi
    def create(self, vals_list):
        normalized = []
        for vals in vals_list:
            if 'mac_address' in vals:
                vals = dict(vals)
                vals['mac_address'] = self._normalize_mac_address(vals.get('mac_address'))
            normalized.append(vals)
        records = super().create(normalized)
        for rec in records:
            if rec.mikrotik_config_id.area_id and rec.partner_id and not rec.partner_id.area_id:
                rec.partner_id.area_id = rec.mikrotik_config_id.area_id.id
        return records

    def _require_router(self):
        self.ensure_one()
        return self.env['isp.mikrotik.config'].require_cpe_router(self)

    def _check_mikrotik_secret(self):
        """
        Cek apakah secret sudah ada di Mikrotik dan statusnya di Odoo
        Returns: (exists, user_id, error, secret_data)
        """
        self.ensure_one()
        if self.connection_type != 'pppoe':
            return False, False, None, None
            
        # Jika state adalah terminated, lewati validasi username
        if self.state == 'terminated':
            return False, False, None, None
            
        if not self.pppoe_username:
            return False, False, 'PPPoE Username tidak boleh kosong!', None
            
        mikrotik = self._require_router()
            
        api = mikrotik.get_connection()
        if not api:
            return False, False, 'Gagal terhubung ke Mikrotik!', None
            
        try:
            secret_api = api.get_resource('/ppp/secret')
            secrets = secret_api.get(name=self.pppoe_username)
            if secrets:
                return True, secrets[0].get('.id'), None, secrets[0]
            return False, False, None, None
        except Exception as e:
            return False, False, str(e), None
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect()

    def adopt_mikrotik_secret(self):
        """
        Mengadopsi secret yang sudah ada di Mikrotik
        Returns: (success, message)
        """
        self.ensure_one()
        if self.connection_type != 'pppoe':
            return True, 'Bukan koneksi PPPoE'
            
        if not self.pppoe_username or not self.pppoe_password:
            return False, 'PPPoE Username dan Password harus diisi!'
            
        # Cek apakah secret sudah ada di Mikrotik
        exists, user_id, error, secret_data = self._check_mikrotik_secret()
        if error:
            return False, error
            
        if not exists:
            return False, f'Secret {self.pppoe_username} tidak ditemukan di Mikrotik'
            
        # Cek apakah secret sudah dipakai oleh CPE lain
        existing_cpe = self.env['isp.cpe'].search([
            ('id', '!=', self.id),
            ('pppoe_username', '=', self.pppoe_username),
            ('state', 'not in', ['terminated'])
        ], limit=1)
        
        if existing_cpe:
            return False, f'Secret {self.pppoe_username} sudah dipakai oleh CPE {existing_cpe.name} (Customer: {existing_cpe.partner_id.name}). Silakan gunakan username PPPoE yang lain.'
            
        # Adopsi secret yang ada
        try:
            self.write({
                'pppoe_password': secret_data.get('password', self.pppoe_password),
                'state': 'open'
            })
            return True, f'Secret {self.pppoe_username} berhasil diadopsi dari Mikrotik'
        except Exception as e:
            _logger.error(f'Error adopting secret: {str(e)}')
            return False, f'Gagal mengadopsi secret {self.pppoe_username}: {str(e)}'

    def action_activate(self):
        """Aktivasi CPE dan buat PPPoE secret"""
        self.ensure_one()
        if not self.subscription_id:
            raise ValidationError('CPE harus memiliki subscription terlebih dahulu!')
            
        if self.connection_type == 'pppoe':
            # Cek apakah sudah ada secret di Mikrotik
            exists, user_id, error, secret_data = self._check_mikrotik_secret()
            if error:
                raise ValidationError(error)
                
            if exists:
                # Coba adopsi secret yang ada
                success, message = self.adopt_mikrotik_secret()
                if not success:
                    raise ValidationError(message)
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Sukses',
                        'message': message,
                        'type': 'success',
                    }
                }
                
            # Jika secret belum ada, buat baru di Mikrotik
            mikrotik = self._require_router()
            api = mikrotik.get_connection()
            if not api:
                raise ValidationError('Gagal terhubung ke Mikrotik!')
                
            try:
                secret_api = api.get_resource('/ppp/secret')
                secret_data = {
                    'name': self.pppoe_username,
                    'password': self.pppoe_password,
                    'service': 'pppoe',
                    'profile': self.subscription_id.package_id.get_pppoe_profile_name(),
                    'comment': mikrotik.format_secret_comment(self.partner_id),
                    'disabled': 'yes'  # Default disabled, akan di-enable oleh subscription
                }
                
                secret_api.add(**secret_data)
                self.write({'state': 'open'})
                self._sync_radius_safe()
                
                # Aktifkan subscription jika masih draft
                if self.subscription_id.state == 'draft':
                    self.subscription_id.action_open()
                    
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Sukses',
                        'message': 'CPE berhasil diaktifkan dengan membuat secret baru',
                        'type': 'success',
                    }
                }
            except Exception as e:
                raise ValidationError(f'Gagal mengaktifkan CPE di Mikrotik: {str(e)}')
            finally:
                if api and hasattr(api, 'connection_pool'):
                    api.connection_pool.disconnect()
        else:
            # Untuk koneksi non-PPPoE, langsung aktifkan
            self.write({'state': 'open'})
            if self.subscription_id.state == 'draft':
                self.subscription_id.action_open()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sukses',
                    'message': 'CPE berhasil diaktifkan',
                    'type': 'success',
                }
            }
    
    def action_isolate(self):
        """Isolir CPE"""
        self.ensure_one()
        if self.state != 'open':
            raise ValidationError('Hanya CPE open yang dapat diisolir!')
            
        if not self.subscription_id:
            raise ValidationError('CPE tidak memiliki subscription!')
            
        # Isolir subscription yang akan otomatis disable PPPoE secret
        self.subscription_id.action_isolate()
        
        self.write({'state': 'isolated'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'CPE berhasil diisolir',
                'type': 'success',
            }
        }
    
    def action_enable(self):
        """Buka isolir CPE"""
        self.ensure_one()
        if self.state != 'isolated':
            raise ValidationError('Hanya CPE terisolir yang dapat dibuka isolirnya!')
            
        if not self.subscription_id:
            raise ValidationError('CPE tidak memiliki subscription!')
            
        # Buka isolir subscription yang akan otomatis enable PPPoE secret
        self.subscription_id.action_enable()
        
        self.write({'state': 'open'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'CPE berhasil dibuka isolirnya',
                'type': 'success',
            }
        }
    
    def action_terminate(self):
        """Terminasi CPE"""
        self.ensure_one()
        if self.state not in ['open', 'isolated']:
            raise ValidationError('Hanya CPE open atau terisolir yang dapat diterminasi!')
        
        # 1. Set flag terminasi untuk bypass validasi
        self.is_terminating = True
        
        # 2. Jika ada subscription, terminate dulu
        if self.subscription_id:
            self.subscription_id.action_terminate()
        else:
            # 3. Jika tidak ada subscription, langsung proses CPE
            
            # Simpan username jika perlu menghapus secret Mikrotik
            has_pppoe = self.connection_type == 'pppoe' and self.pppoe_username
            pppoe_username = self.pppoe_username if has_pppoe else False
            
            # Set state tanpa melewati validasi (SUPER untuk bypass ORM constraint)
            # Langsung ubah ke draft, bukan terminated agar history tetap ada
            self.env.cr.execute(
                """UPDATE isp_cpe SET state = 'draft' WHERE id = %s""", 
                (self.id,)
            )
            
            # Hapus secret di Mikrotik jika perlu
            if has_pppoe:
                # Hapus secret di Mikrotik
                exists, user_id, error, secret_data = self._check_mikrotik_secret()
                if error:
                    _logger.warning(f'Error checking secret: {error}')
                
                if exists:
                    mikrotik = self.mikrotik_config_id
                    if not mikrotik:
                        _logger.warning('Konfigurasi Mikrotik tidak ditemukan!')
                    else:
                        api = mikrotik.get_connection()
                        if not api:
                            _logger.warning('Gagal terhubung ke Mikrotik!')
                        else:
                            try:
                                secret_api = api.get_resource('/ppp/secret')
                                secret_api.remove(id=user_id)
                            except Exception as e:
                                _logger.error(f'Gagal menghapus secret di Mikrotik: {str(e)}')
                            finally:
                                if hasattr(api, 'disconnect'):
                                    api.disconnect()
        
        # Refresh model data dari database
        self.env['isp.cpe'].invalidate_model()
        
        # Reset flag terminasi
        self.write({'is_terminating': False})
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'CPE berhasil diterminasi dan diubah ke status draft',
                'type': 'success',
            }
        }
    
    def action_view_subscriptions(self):
        """Tampilkan subscription CPE"""
        self.ensure_one()
        return {
            'name': 'Subscriptions',
            'type': 'ir.actions.act_window',
            'res_model': 'isp.subscription',
            'view_mode': 'list,form',
            'domain': [('cpe_id', '=', self.id)],
            'context': {
                'default_partner_id': self.partner_id.id,
                'default_cpe_id': self.id
            }
        }

    def _get_pppoe_active(self, api):
        """
        Mendapatkan informasi PPPoE active dari Mikrotik
        Returns: (active_data, error)
        """
        self.ensure_one()
        try:
            active_api = api.get_resource('/ppp/active')
            # Cari berdasarkan username yang sesuai
            active = active_api.get(name=self.pppoe_username)
            if active and isinstance(active, list) and len(active) > 0:
                return active[0], None
                
            # Jika tidak ditemukan dengan username exact match, 
            # coba cari dengan contains untuk menangani kasus username dengan format berbeda
            all_active = active_api.get()
            for conn in all_active:
                if isinstance(conn, dict) and self.pppoe_username.lower() in conn.get('name', '').lower():
                    return conn, None
                    
            return None, None
        except Exception as e:
            _logger.error(f'Error getting PPPoE active status: {str(e)}')
            return None, str(e)
            
    def _get_pppoe_secret(self, api):
        """
        Mendapatkan informasi PPPoE secret dari Mikrotik
        Returns: (secret_data, error)
        """
        self.ensure_one()
        try:
            secret_api = api.get_resource('/ppp/secret')
            secret = secret_api.get(name=self.pppoe_username)
            if secret:
                return secret[0], None
            return None, None
        except Exception as e:
            return None, str(e)
            
    def _bytes_to_mb(self, bytes_val):
        """Convert bytes to MB"""
        try:
            return float(bytes_val) / (1024 * 1024)
        except:
            return 0.0
            
    @api.model
    def _group_pppoe_sessions_by_name(self, active_list):
        """Kelompokkan /ppp/active per username (exact, case-insensitive) di router yang sama."""
        grouped = {}
        for item in active_list or []:
            if not isinstance(item, dict):
                continue
            name = (item.get('name') or '').strip()
            if not name:
                continue
            grouped.setdefault(name.lower(), []).append(item)
        return grouped

    @api.model
    def _format_pppoe_caller_ids(self, sessions):
        """Gabungkan caller-id unik dari daftar sesi, urutan kemunculan dipertahankan."""
        seen = set()
        caller_ids = []
        for sess in sessions or []:
            if not isinstance(sess, dict):
                continue
            caller = (sess.get('caller-id') or '').strip()
            if not caller:
                continue
            key = caller.lower()
            if key in seen:
                continue
            seen.add(key)
            caller_ids.append(caller)
        return ', '.join(caller_ids)

    def _prepare_pppoe_status_vals(self, active, iface_by_name, now, sessions=None):
        """Susun nilai status dari sesi /ppp/active dan dump /interface."""
        self.ensure_one()
        if sessions is None:
            sessions = [active] if active else []
        session_count = len(sessions)
        is_multisession = session_count > 1
        caller_ids = self._format_pppoe_caller_ids(sessions)
        if active and isinstance(active, dict):
            stats = iface_by_name.get(active.get('interface') or '') or {}
            caller_id = (active.get('caller-id') or '').strip()
            pppoe_address = active.get('address', '')
            return {
                'pppoe_status': 'connected',
                'pppoe_uptime': active.get('uptime', ''),
                'pppoe_address': pppoe_address or False,
                'pppoe_caller_id': caller_id or False,
                'pppoe_session_id': active.get('session-id', ''),
                'pppoe_last_seen': now,
                'pppoe_session_count': session_count,
                'is_multisession': is_multisession,
                'pppoe_caller_ids': caller_ids or False,
                'current_upload_rate': self._format_rate(stats.get('tx-byte', 0)) + ' kbps',
                'current_download_rate': self._format_rate(stats.get('rx-byte', 0)) + ' kbps',
                'upload_usage': self._bytes_to_mb(stats.get('tx-byte', 0)),
                'download_usage': self._bytes_to_mb(stats.get('rx-byte', 0)),
                'last_update': now,
            }
        return {
            'pppoe_status': 'disconnected',
            'pppoe_uptime': '',
            'pppoe_address': '',
            'pppoe_caller_id': '',
            'pppoe_session_id': '',
            'pppoe_session_count': 0,
            'is_multisession': False,
            'pppoe_caller_ids': False,
            'current_upload_rate': '0 kbps',
            'current_download_rate': '0 kbps',
            'last_update': now,
        }

    def _apply_pppoe_status_from_router(self, router, source='cron'):
        """Satu panggilan /ppp/active dan /interface per router. Deteksi multi-sesi per username."""
        api = router.get_connection()
        if not api:
            _logger.warning('Tidak bisa konek router %s untuk update PPPoE', router.name)
            return 0
        try:
            active_list = api.get_resource('/ppp/active').get() or []
            sessions_by_name = self._group_pppoe_sessions_by_name(active_list)

            iface_by_name = {}
            try:
                for iface in (api.get_resource('/interface').get() or []):
                    if isinstance(iface, dict) and iface.get('name'):
                        iface_by_name[iface['name']] = iface
            except Exception as exc:
                _logger.warning('Gagal ambil /interface di %s: %s', router.name, exc)

            now = fields.Datetime.now()
            count = 0
            for cpe in self:
                username = (cpe.pppoe_username or '').strip().lower()
                sessions = sessions_by_name.get(username) or []
                active = sessions[0] if sessions else None
                vals = cpe._prepare_pppoe_status_vals(
                    active, iface_by_name, now, sessions=sessions,
                )
                if cpe._write_pppoe_status(vals, source=source):
                    count += 1
            return count
        except Exception as exc:
            _logger.error('Gagal update PPPoE batch router %s: %s', router.name, exc)
            return 0
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect()

    def _update_pppoe_status_batch(self, source='cron'):
        """Update status PPPoE batch per router, bukan per CPE."""
        records = self.filtered(
            lambda c: c.connection_type == 'pppoe' and c.pppoe_username and c.mikrotik_config_id
        )
        if not records:
            return 0
        updated = 0
        for router in records.mapped('mikrotik_config_id'):
            router_cpes = records.filtered(lambda c, r=router: c.mikrotik_config_id == r)
            try:
                updated += router_cpes._apply_pppoe_status_from_router(router, source=source)
            except Exception as exc:
                _logger.error('Gagal update PPPoE batch router %s: %s', router.name, exc)
        return updated

    def update_pppoe_status(self):
        """Update PPPoE status from Mikrotik (satu CPE, tetap lewat batch router)."""
        self.ensure_one()
        source = self.env.context.get('pppoe_status_source', 'manual')
        return bool(self._update_pppoe_status_batch(source=source))

    def _format_rate(self, bytes_val):
        """Format byte rate ke kbps"""
        try:
            return str(int(float(bytes_val) * 8 / 1024))
        except:
            return '0'

    def action_check_pppoe(self):
        """Action untuk mengecek status PPPoE"""
        self.ensure_one()
        
        self.with_context(pppoe_status_source='manual').update_pppoe_status()
        message = f'Status PPPoE: {self.pppoe_status}.'
        if self.is_multisession:
            message += (
                f' Multi-sesi: {self.pppoe_session_count} sesi aktif'
                f' ({self.pppoe_caller_ids or "-"}). Sesi tidak diputus.'
            )
            
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Info',
                'message': message,
                'type': 'info',
            }
        }

    @api.model
    def _cron_update_pppoe_status(self):
        """Cron: satu /ppp/active per router, interval 2 menit."""
        cpes = self.search([
            ('state', 'in', ['open', 'isolated']),
            ('connection_type', '=', 'pppoe'),
        ])
        cpes.with_context(pppoe_status_source='cron')._update_pppoe_status_batch(source='cron')

    def action_refresh_list_status(self):
        """Tombol list: cek ulang status batch per router."""
        records = self.filtered(
            lambda c: c.state in ('open', 'isolated') and c.connection_type == 'pppoe'
        )
        if not records:
            records = self.search([
                ('state', 'in', ['open', 'isolated']),
                ('connection_type', '=', 'pppoe'),
            ])
        records.with_context(pppoe_status_source='manual')._update_pppoe_status_batch(
            source='manual',
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Status PPPoE',
                'message': 'Pengecekan status selesai (batch per router).',
                'type': 'success',
            },
        } 

    def _compute_rates(self):
        """Compute upload dan download rate"""
        for record in self:
            record.upload_rate = record.current_upload_rate or '0 kbps'
            record.download_rate = record.current_download_rate or '0 kbps'
            
    def action_start_monitoring(self):
        """Start monitoring PPPoE connection"""
        self.ensure_one()
        if self.connection_type != 'pppoe':
            raise ValidationError('Hanya koneksi PPPoE yang dapat dimonitor!')
        
        if not self.pppoe_username:
            raise ValidationError('PPPoE Username harus diisi!')
        
        # Update status monitoring
        self.write({
            'is_monitoring': True,
            'last_update': fields.Datetime.now()
        })
        
        # Ambil data awal
        self.update_pppoe_status()
        
        return True

    def action_stop_monitoring(self):
        """Stop monitoring PPPoE connection"""
        self.ensure_one()
        
        # Update status monitoring
        self.write({
            'is_monitoring': False,
            'current_upload_rate': '0 kbps',
            'current_download_rate': '0 kbps'
        })
        
        return True

    @api.model
    def _update_pppoe_stats(self, stats):
        """Update statistik PPPoE dari monitoring"""
        self.ensure_one()
        self.write({
            'current_upload_rate': stats['rate_out'] + ' kbps',
            'current_download_rate': stats['rate_in'] + ' kbps',
            'upload_usage': stats['bytes_out'],
            'download_usage': stats['bytes_in'],
            'pppoe_status': stats['status'],
            'pppoe_uptime': stats['uptime'],
            'pppoe_address': stats['address']
        })

    def _revoke_onboarding_access(self):
        """Matikan secret. Bukan isolir nunggak, bukan hapus user."""
        Radius = self.env['isp.radius']
        for rec in self:
            if rec.isp_evidence_waived or rec.isp_activation_revoked:
                continue
            if rec.subscription_id and rec.subscription_id.is_special_treatment:
                continue
            router = rec.mikrotik_config_id
            username = rec.pppoe_username
            if router and username:
                try:
                    router.set_secret_disabled(username, True)
                except Exception as exc:
                    _logger.warning('Cabut secret %s: %s', username, exc)
                if router.uses_radius_isolir():
                    try:
                        Radius._execute(Radius._delete_check(username, 'Cleartext-Password'))
                    except Exception as exc:
                        _logger.warning('Cabut RADIUS %s: %s', username, exc)
            rec.write({'isp_activation_revoked': True})
            rec.message_post(body='Aktivasi dicabut: eviden 24 jam belum lengkap.')

    def _restore_onboarding_access(self):
        """Nyalakan lagi setelah eviden lengkap atau admin bebaskan."""
        Radius = self.env['isp.radius']
        for rec in self:
            if not rec.isp_activation_revoked:
                rec.write({
                    'isp_evidence_deadline': False,
                    'isp_evidence_reminder_sent': True,
                })
                continue
            router = rec.mikrotik_config_id
            username = rec.pppoe_username
            if router and username:
                try:
                    router.set_secret_disabled(username, False)
                except Exception as exc:
                    _logger.warning('Pulih secret %s: %s', username, exc)
                if router.uses_radius_isolir():
                    try:
                        Radius.sync_cpe(rec, isolated=False)
                    except Exception as exc:
                        _logger.warning('Pulih RADIUS %s: %s', username, exc)
            rec.write({
                'isp_activation_revoked': False,
                'isp_evidence_deadline': False,
                'isp_evidence_reminder_sent': True,
            })
            rec.message_post(body='Aktivasi dinyalakan lagi setelah eviden/admin.')

    @api.constrains('connection_type', 'pppoe_username', 'pppoe_password', 'state')
    def _check_pppoe_fields(self):
        for record in self:
            # Jika CPE sudah dalam status terminated, lewati validasi
            if record.state == 'terminated' or record.is_terminating:
                continue
            if record.connection_type == 'pppoe':
                if not record.pppoe_username:
                    raise ValidationError('PPPoE Username harus diisi untuk koneksi PPPoE!')
                if not record.pppoe_password:
                    raise ValidationError('PPPoE Password harus diisi untuk koneksi PPPoE!') 