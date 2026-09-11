import logging
import random
import re
import string
from calendar import monthrange
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html_escape

from .isp_ocr import extract_text_from_bytes, looks_like_payment_receipt, parse_ocr_text
from .isp_phone import mask_phone, phones_equivalent
from .isp_whatsapp_message import WA_FILE_EXT, WA_IMAGE_EXT

_logger = logging.getLogger(__name__)

SESSION_MINUTES = 30
BOT_RATE = 20
BOT_RATE_MINUTES = 15
CMD_RE = re.compile(
    r'(?im)^\s*/(daftar|pasang|eviden|status|batal|help|bantuan|menu|panduan)\b(?:\s+(\S+))?',
)
HELP_CMDS = ('help', 'bantuan', 'menu', 'panduan')
KTP_RE = re.compile(r'(?is)\bktp\b[:\s,.-]+([A-Za-z0-9._+-]+)')
RUMAH_RE = re.compile(r'(?is)\brumah\b[:\s,.-]+([A-Za-z0-9._+-]+)')
HELP_DIRECT = (
    '*PANDUAN BOT DOTAKARO* (chat pribadi)\n'
    'Balasan bot selalu diawali ~bot.\n\n'
    '*1. Daftar pasang baru*\n'
    'Ketik /daftar lalu jawab bergiliran:\n'
    '1) Nama lengkap sesuai KTP\n'
    '2) Desa (ketik nomor urutan atau nama)\n'
    '3) Paket (ketik nomor urutan atau nama)\n'
    '4) Ketik YA untuk masuk antrian\n\n'
    'Hasil /daftar: *antrian saja*. Belum ada username, '
    'belum ada koneksi, belum ada tagihan. Teknisi yang '
    'menyelesaikan lewat /pasang di grup Aktivasi-dkt, '
    'atau admin pusat yang menyetujui di billing.\n\n'
    '*2. Batalkan*\n'
    '/batal — hentikan sesi yang sedang berjalan. '
    'Boleh /daftar lagi dari awal.\n\n'
    '*3. Cek status akun Anda*\n'
    '/status — isolir, PPPoE, bayar, tempo, desa, router, peta.\n'
    'Di chat pribadi hanya akun nomor ini. '
    'Teknisi cek orang lain di grup: /status username\n\n'
    '*4. Bukti bayar tagihan*\n'
    'Nomor ini dipakai campur. Kirim *FOTO struk* dengan caption:\n'
    'BAYAR username\n'
    'atau\n'
    'BAYAR 08xxxxxxxxxx\n'
    'Foto tanpa format itu *tidak diproses*.\n\n'
    '*Tidak bisa di chat pribadi*\n'
    '/pasang dan eviden (KTP/rumah/lokasi) hanya di grup Aktivasi-dkt.\n'
    'Staf laporkan bayar di grup Billing-dkt (foto + BAYAR username).\n\n'
    'Ketik /help kapan saja untuk membaca panduan ini lagi.'
)
HELP_GROUP = (
    'PANDUAN BOT AKTIVASI-DKT\n'
    'Balasan bot selalu diawali ~bot. Omongan biasa diabaikan. '
    'Cukup /help, tidak perlu dijelaskan ulang.\n\n'
    'A. Pelanggan daftar sendiri: /daftar\n'
    'Bukan di grup ini. Chat pribadi ke nomor bot:\n'
    '1) /daftar\n'
    '2) Nama lengkap\n'
    '3) Pilih desa\n'
    '4) Pilih paket\n'
    '5) Ketik YA\n'
    'Hasil: antrian saja. Belum ada username/koneksi/tagihan.\n\n'
    'B. Anggota grup pasang: /pasang\n'
    '1) Nomor HP pelanggan\n'
    '2) Nama lengkap\n'
    '3) Desa pelanggan\n'
    '4) Router PPPoE (pilih yang [online]: Bulan Jahe, Lau Baleng, ...)\n'
    '5) Paket\n'
    '6) Ketik YA\n'
    'Username dibuat. Password hanya ke HP pelanggan. '
    'Invoice tidak otomatis. Salah? /batal lalu /pasang lagi.\n\n'
    'C. Eviden 24 jam setelah CONNECT\n'
    '/eviden username\n'
    '1) Foto KTP, caption: KTP username\n'
    '2) Foto rumah, caption: RUMAH username\n'
    '3) Pin lokasi WhatsApp (bukan ketik angka)\n'
    'Kalau tidak lengkap, secret dimatikan (bukan isolir nunggak).\n\n'
    'D. Status pelanggan: /status username\n'
    'atau /status 08xxxx. Isolir, PPPoE, bayar, tempo, desa, router, peta.\n\n'
    'E. Perintah\n'
    '/help /status /pasang /eviden username /batal\n'
    '/daftar dan BAYAR jangan di grup ini. BAYAR staf di grup Billing-dkt.\n\n'
    'F. Semua anggota grup ini boleh /pasang, /status, dan eviden. '
    'Satu sesi per orang. Jangan kirim password ke grup.'
)
HELP_BILLING = (
    'PANDUAN BOT BILLING-DKT\n'
    'Omongan biasa diabaikan.\n\n'
    'A. Laporan bayar: FOTO + caption\n'
    'BAYAR username atau BAYAR 08xxxx\n'
    'Antrian. Pusat verifikasi. Belum lunas.\n\n'
    'Struk tanpa caption: nama pengirim rekening cocok unik → bot minta YA. '
    'Rekening tujuan harus Waspada / rekening terdaftar. '
    'YA + tanggal/periode cocok = lunas. Kalau tidak cocok, '
    'kirim ulang foto + caption BAYAR username.\n\n'
    'B. /status username\n'
    'C. /pasang hanya di Aktivasi-dkt. Pelanggan BAYAR di chat 1:1.'
)
CONFIRM_YES_RE = re.compile(r'(?is)^\s*(ya|iya|yes|y|benar|betul|ok|oke)\s*[.!]*\s*$')
CONFIRM_NO_RE = re.compile(r'(?is)^\s*(tidak|tdk|no|n|salah|bukan|batal)\s*[.!]*\s*$')


class IspWaRegistration(models.Model):
    """Antrian pendaftaran WA + sesi bot. Bukan lunas, bukan isolir nunggak."""

    _name = 'isp.wa.registration'
    _description = 'Antrian pendaftaran WhatsApp'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char('Nomor', required=True, copy=False, default='New', tracking=True)
    source = fields.Selection(
        [('daftar', 'Pelanggan /daftar'), ('pasang', 'Teknisi /pasang')],
        string='Sumber', required=True, default='daftar', tracking=True,
    )
    channel = fields.Selection(
        [('direct', 'Chat 1:1'), ('group', 'Grup aktivasi')],
        string='Kanal', required=True, default='direct', tracking=True,
    )
    state = fields.Selection(
        [
            ('chatting', 'Sesi bot'),
            ('queued', 'Menunggu admin'),
            ('created', 'User dibuat'),
            ('cancelled', 'Dibatalkan'),
            ('rejected', 'Ditolak'),
        ],
        string='Status', default='chatting', required=True, tracking=True, index=True,
    )
    step = fields.Char('Langkah sesi', default='start')
    actor_phone = fields.Char('HP pengirim', index=True, tracking=True)
    group_id = fields.Char('ID grup Fonnte')
    technician_id = fields.Many2one('res.users', string='Teknisi', tracking=True)
    customer_name = fields.Char('Nama pelanggan', tracking=True)
    customer_phone = fields.Char('HP pelanggan', tracking=True)
    area_id = fields.Many2one('isp.area', string='Area / desa', tracking=True)
    mikrotik_config_id = fields.Many2one(
        'isp.mikrotik.config', string='Router PPPoE', tracking=True,
        domain="[('active', '=', True)]",
        help='POP tempat secret dibuat. Satu router bisa untuk banyak desa.',
    )
    package_id = fields.Many2one('isp.package', string='Paket', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Pelanggan', tracking=True)
    cpe_id = fields.Many2one('isp.cpe', string='CPE', tracking=True)
    subscription_id = fields.Many2one('isp.subscription', string='Langganan', tracking=True)
    pppoe_username = fields.Char('Username PPPoE', tracking=True)
    last_message = fields.Char('Pesan terakhir')
    last_activity = fields.Datetime('Aktivitas terakhir', default=fields.Datetime.now, index=True)
    actor_name = fields.Char('Nama WA pengirim', tracking=True)
    registered_by = fields.Char(
        'Didaftarkan oleh',
        compute='_compute_registered_by',
        store=True,
        index=True,
    )
    audit_log = fields.Text('Jejak audit', copy=False)
    note = fields.Text('Catatan')
    activate_error = fields.Char('Error aktivasi router')

    @api.depends('actor_phone', 'actor_name', 'technician_id', 'source')
    def _compute_registered_by(self):
        for rec in self:
            rec.registered_by = rec._actor_label()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'isp.wa.registration',
                ) or 'REG'
        return super().create(vals_list)

    @api.model
    def get_register_config(self):
        ICP = self.env['ir.config_parameter'].sudo()
        hours = ICP.get_param('dkt_isp_billing.wa_evidence_hours', '24')
        try:
            hours = int(hours or 24)
        except (TypeError, ValueError):
            hours = 24
        return {
            'enabled': ICP.get_param(
                'dkt_isp_billing.wa_register_enabled', 'False',
            ) in ('True', 'true', '1'),
            'mode': ICP.get_param(
                'dkt_isp_billing.wa_register_mode', 'queue',
            ) or 'queue',
            'group_id': (
                ICP.get_param('dkt_isp_billing.wa_register_group_id', '') or ''
            ).strip(),
            'evidence_required': ICP.get_param(
                'dkt_isp_billing.wa_evidence_required', 'False',
            ) in ('True', 'true', '1'),
            'evidence_hours': max(1, min(hours, 168)),
            'billing_group_id': (
                ICP.get_param('dkt_isp_billing.wa_billing_group_id', '') or ''
            ).strip(),
        }

    @api.model
    def _norm_group(self, value):
        """Samakan 120363…@g.us dengan 120363… saja. ID tes tetap utuh."""
        raw = (value or '').strip().lower()
        if not raw:
            return ''
        if '@g.us' in raw:
            return re.sub(r'\D', '', raw.split('@', 1)[0])
        digits = re.sub(r'\D', '', raw)
        if digits.startswith('120363') and len(digits) >= 15:
            return digits
        return raw

    @api.model
    def _payload_group_id(self, payload):
        payload = payload or {}
        for key in ('sender', 'group', 'groupid', 'groupId', 'group_id', 'gid'):
            val = str(payload.get(key) or '').strip()
            if val and (
                '@g.us' in val.lower()
                or self.env['isp.whatsapp.message'].is_group_target(val)
            ):
                return val
        if payload.get('member') and payload.get('sender'):
            return str(payload.get('sender')).strip()
        return str(payload.get('sender') or '').strip()

    @api.model
    def _inbound_text(self, payload):
        payload = payload or {}
        text = (payload.get('message') or payload.get('text') or '').strip()
        text = text.replace('／', '/')
        text = re.sub(r'^@\S+\s+', '', text)
        return text

    @api.model
    def _group_reply_target(self, inbound_id):
        cfg = self.get_register_config()
        if cfg.get('group_id') and self._is_activation_group(inbound_id):
            return cfg['group_id']
        if cfg.get('billing_group_id') and self._is_billing_group(inbound_id):
            return cfg['billing_group_id']
        return inbound_id

    @api.model
    def _is_activation_group(self, group_id):
        cfg = self.get_register_config()
        wanted = self._norm_group(cfg.get('group_id'))
        got = self._norm_group(group_id)
        return bool(wanted and got and wanted == got)

    @api.model
    def _is_billing_group(self, group_id):
        cfg = self.get_register_config()
        wanted = self._norm_group(cfg.get('billing_group_id'))
        got = self._norm_group(group_id)
        return bool(wanted and got and wanted == got)

    @api.model
    def find_staff_user(self, phone):
        """Teknisi / admin pusat yang nomornya cocok. Bukan pelanggan biasa."""
        WA = self.env['isp.whatsapp.message']
        wa = WA.normalize_phone(phone)
        if not wa:
            return self.env['res.users']
        group_xmlids = (
            'dkt_isp_billing.group_isp_technician',
            'dkt_isp_billing.group_isp_manager',
            'dkt_isp_billing.group_isp_user',
            'base.group_system',
        )
        group_ids = []
        for xmlid in group_xmlids:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                group_ids.append(group.id)
        if not group_ids:
            return self.env['res.users']
        users = self.env['res.users'].sudo().search([
            ('share', '=', False),
            ('active', '=', True),
            ('group_ids', 'in', group_ids),
        ])
        for user in users:
            partner = user.partner_id
            if phones_equivalent(partner.phone_wa or partner.phone, wa):
                return user
        return self.env['res.users']

    @api.model
    def _protected_partner(self, partner):
        if not partner:
            return True
        company = self.env.company.partner_id
        if partner.id == 1 or partner == company:
            return True
        name = (partner.name or '').strip().upper()
        return name in ('DOTAKARO', 'PT. DIGITAL KREASI TEKNOLOGI', 'YOURCOMPANY')

    def _touch(self, message=None):
        vals = {'last_activity': fields.Datetime.now()}
        if message:
            vals['last_message'] = (message or '')[:200]
        self.write(vals)

    @api.model
    def _active_session(self, phone, channel):
        WA = self.env['isp.whatsapp.message']
        actor = WA.normalize_phone(phone)
        if not actor:
            return self.browse()
        limit = fields.Datetime.now() - timedelta(minutes=SESSION_MINUTES)
        stale = self.search([
            ('actor_phone', '=', actor),
            ('channel', '=', channel),
            ('state', '=', 'chatting'),
            ('last_activity', '<', limit),
        ])
        if stale:
            stale.write({'state': 'cancelled', 'note': 'Sesi kadaluarsa (30 menit).'})
        return self.search([
            ('actor_phone', '=', actor),
            ('channel', '=', channel),
            ('state', '=', 'chatting'),
        ], limit=1, order='id desc')

    @api.model
    def _check_bot_rate(self, phone):
        WA = self.env['isp.whatsapp.message']
        actor = WA.normalize_phone(phone)
        if not actor:
            return True
        since = fields.Datetime.now() - timedelta(minutes=BOT_RATE_MINUTES)
        count = self.search_count([
            ('actor_phone', '=', actor),
            ('last_activity', '>=', since),
        ])
        if count >= BOT_RATE:
            raise UserError('Terlalu banyak pesan bot.')
        return True

    @api.model
    def _parse_cmd(self, text):
        match = CMD_RE.match((text or '').strip())
        if not match:
            return False, False
        return match.group(1).lower(), (match.group(2) or '').strip() or False

    @api.model
    def _is_help_cmd(self, cmd):
        return bool(cmd) and cmd in HELP_CMDS

    @api.model
    def _help_text(self, channel):
        if channel == 'direct':
            return HELP_DIRECT
        if channel == 'billing':
            return HELP_BILLING
        return HELP_GROUP

    @api.model
    def _payload_actor_name(self, payload):
        payload = payload or {}
        for key in ('memberName', 'member_name', 'senderName', 'sender_name', 'name'):
            val = (payload.get(key) or '').strip()
            if val:
                return val[:80]
        return False

    def _actor_label(self):
        self.ensure_one()
        parts = []
        if self.technician_id:
            parts.append(self.technician_id.name)
        if self.actor_name and self.actor_name not in parts:
            parts.append(self.actor_name)
        if not parts:
            parts.append('pelanggan' if self.source == 'daftar' else 'anggota grup')
        return '%s (%s)' % (' / '.join(parts), self.actor_phone or '-')

    def _append_audit(self, event):
        """Catatan audit + chatter. Jangan tulis password."""
        self.ensure_one()
        text = (event or '').strip()
        if not text:
            return
        stamp = fields.Datetime.context_timestamp(
            self, fields.Datetime.now(),
        ).strftime('%Y-%m-%d %H:%M')
        line = '[%s] %s' % (stamp, text)
        current = (self.audit_log or '').strip()
        self.write({'audit_log': '%s\n%s' % (current, line) if current else line})
        self.message_post(body=html_escape(text))

    def _log_audit(self, event, partner=None, cpe=None):
        if self:
            self._append_audit(event)
        if partner and not self._protected_partner(partner):
            partner.sudo().message_post(body=html_escape(event))
        if cpe:
            cpe.sudo().message_post(body=html_escape(event))

    @api.model
    def _audit_evidence(self, session, actor, actor_name, staff, cpe, kind):
        if session:
            who = session._actor_label()
        else:
            who = '%s (%s)' % (actor_name or (staff.name if staff else 'anggota grup'), actor or '-')
        event = 'Eviden %s %s diunggah oleh %s.' % (
            kind, cpe.pppoe_username or '-', who,
        )
        (session or self)._log_audit(event, partner=cpe.partner_id, cpe=cpe)

    def _apply_partner_audit(self, partner):
        self.ensure_one()
        if not partner or self._protected_partner(partner):
            return
        partner.sudo().write({
            'isp_registered_by_name': self.actor_name or self.registered_by,
            'isp_registered_by_phone': self.actor_phone,
            'isp_registered_by_user_id': self.technician_id.id if self.technician_id else False,
            'isp_wa_registration_id': self.id,
        })

    @api.model
    def _parse_location(self, raw):
        if not raw:
            return False, False
        nums = re.findall(r'-?\d+(?:\.\d+)?', str(raw))
        if len(nums) < 2:
            return False, False
        try:
            lat = float(nums[0])
            lng = float(nums[1])
        except (TypeError, ValueError):
            return False, False
        if abs(lat) < 0.0001 and abs(lng) < 0.0001:
            return False, False
        if abs(lat) > 90 or abs(lng) > 180:
            return False, False
        return lat, lng

    @api.model
    def _area_choices(self):
        return self.env['isp.area'].sudo().search([], order='name')

    @api.model
    def _package_choices(self):
        return self.env['isp.package'].sudo().search(
            [('active', '=', True)], order='name',
        )

    @api.model
    def _format_area_list(self):
        lines = ['Pilih desa (ketik nomor atau nama):']
        for idx, area in enumerate(self._area_choices(), start=1):
            lines.append('%s. %s' % (idx, area.name))
        return '\n'.join(lines)

    @api.model
    def _format_package_list(self):
        lines = ['Pilih paket (ketik nomor atau nama):']
        for idx, pkg in enumerate(self._package_choices(), start=1):
            lines.append('%s. %s' % (idx, pkg.name))
        return '\n'.join(lines)

    @api.model
    def _router_choices(self):
        """Semua router aktif. Satu POP bisa melayani banyak desa."""
        return self.env['isp.mikrotik.config'].sudo().search(
            [('active', '=', True)], order='name',
        )

    def _suggested_router(self, area=None):
        area = area or self.area_id
        routers = self._router_choices()
        if not area:
            return routers.browse()
        same_name = routers.filtered(
            lambda r: (r.name or '').strip().lower() == (area.name or '').strip().lower(),
        )
        if len(same_name) == 1:
            return same_name
        linked = routers.filtered(lambda r: r.area_id == area)
        if len(linked) == 1:
            return linked
        return routers.browse()

    @api.model
    def _router_health_kind(self, router):
        """Status tersimpan cron/tes. Jangan panggil API MikroTik live.

        online = health bagus / last_test_ok
        offline = timeout / health jelek (jelas gangguan)
        unknown = belum dicek / dilewati — boleh dipilih dengan catatan
        """
        if not router:
            return 'unknown'
        state = router.health_state
        if state == 'reachable':
            return 'online'
        if state in ('timeout', 'inactive'):
            return 'offline'
        if state == 'skipped':
            return 'unknown'
        if router.last_test_ok:
            return 'online'
        return 'unknown'

    @api.model
    def _router_health_label(self, router):
        return {
            'online': 'online',
            'offline': 'offline',
            'unknown': 'belum dicek',
        }[self._router_health_kind(router)]

    @api.model
    def _router_is_offline(self, router):
        return self._router_health_kind(router) == 'offline'

    @api.model
    def _router_offline_message(self, router):
        return (
            'POP %s [offline] / gangguan. Jangan pasang dulu. '
            'Tunggu sampai [online], lalu /pasang lagi. Atau /batal.'
        ) % (router.name or 'POP')

    def _format_router_list(self, area=None):
        routers = self._router_choices()
        lines = [
            'Pilih router PPPoE (ketik nomor atau nama).',
            'Secret dibuat di router ini. Desa tidak menentukan otomatis.',
            'Pilih yang [online]. [offline] = gangguan.',
        ]
        for idx, router in enumerate(routers, start=1):
            lines.append('%s. %s [%s]' % (
                idx, router.name, self._router_health_label(router),
            ))
        suggested = self._suggested_router(area)
        if suggested:
            lines.append('Saran desa %s: %s' % (area.name, suggested.name))
        return '\n'.join(lines)

    def _after_area_selected(self, area, ask_router=False):
        if ask_router:
            if not self._router_choices():
                return (
                    'Belum ada router aktif di billing. Hubungi admin pusat.'
                )
            self.write({
                'area_id': area.id,
                'mikrotik_config_id': False,
                'step': 'router',
            })
            return 'Desa: %s\n%s' % (area.name, self._format_router_list(area))
        self.write({
            'area_id': area.id,
            'mikrotik_config_id': False,
            'step': 'package',
        })
        return self._format_package_list()

    def _continue_router(self, text):
        router = self._match_choice(text, self._router_choices())
        if not router:
            return 'Router tidak ketemu.\n' + self._format_router_list()
        if self._router_is_offline(router):
            return '%s\n%s' % (
                self._router_offline_message(router),
                self._format_router_list(),
            )
        self.write({'mikrotik_config_id': router.id, 'step': 'package'})
        note = ''
        if self._router_health_kind(router) == 'unknown':
            note = ' [belum dicek] — lanjut jika POP hidup'
        return 'Router: %s%s\n%s' % (router.name, note, self._format_package_list())

    @api.model
    def _maps_url(self, lat, lng):
        return 'https://maps.google.com/?q=%.6f,%.6f' % (float(lat), float(lng))

    @api.model
    def _fmt_rp(self, amount):
        return 'Rp {:,.0f}'.format(amount or 0).replace(',', '.')

    @api.model
    def _cpes_for_phone(self, phone):
        partner = self.env['isp.whatsapp.message'].find_partner_by_phone(phone)
        if not partner or self._protected_partner(partner):
            return self.env['isp.cpe']
        return self.env['isp.cpe'].sudo().search([
            ('partner_id', '=', partner.id),
            ('connection_type', '=', 'pppoe'),
        ], limit=8, order='id desc')

    @api.model
    def _find_status_cpes(self, query):
        Cpe = self.env['isp.cpe'].sudo()
        token = (query or '').strip()
        if not token:
            return Cpe
        exact = Cpe.search([
            ('pppoe_username', '=ilike', token),
            ('connection_type', '=', 'pppoe'),
        ], limit=5)
        if exact:
            return exact.filtered(lambda c: not self._protected_partner(c.partner_id))
        phone = self.env['isp.whatsapp.message'].normalize_phone(token)
        if phone:
            by_phone = self._cpes_for_phone(phone)
            if by_phone:
                return by_phone
        loose = Cpe.search([
            ('pppoe_username', 'ilike', token),
            ('connection_type', '=', 'pppoe'),
        ], limit=8)
        return loose.filtered(lambda c: not self._protected_partner(c.partner_id))

    @api.model
    def _status_payment_line(self, sub):
        if not sub:
            return 'tidak ada langganan'
        invoice = sub._current_period_invoice()
        if not invoice:
            return 'belum ada invoice bulan ini'
        residual = invoice.amount_residual
        if invoice.payment_state in ('paid', 'in_payment') or invoice.currency_id.is_zero(residual):
            return 'lunas'
        label = 'kurang' if invoice.payment_state == 'partial' else 'nunggak'
        return '%s %s' % (label, self._fmt_rp(residual))

    @api.model
    def _status_due_line(self, sub):
        if not sub:
            return '-'
        invoice = sub._current_period_invoice()
        if invoice and invoice.invoice_date_due:
            return invoice.invoice_date_due.strftime('%d/%m/%Y')
        today = fields.Date.context_today(self)
        last = monthrange(today.year, today.month)[1]
        day = min(sub.due_day or 1, last)
        return today.replace(day=day).strftime('%d/%m/%Y')

    @api.model
    def _format_cpe_status(self, cpe):
        cpe.ensure_one()
        partner = cpe.partner_id
        sub = cpe.subscription_id
        router = cpe.mikrotik_config_id
        area = cpe.area_id or partner.area_id
        phone = (
            self.env['isp.whatsapp.message'].partner_phone(partner)
            or partner.phone_wa or partner.phone
        )
        if cpe.pppoe_status == 'connected':
            pppoe = '🟢 PPPoE: *online*'
            if cpe.pppoe_uptime:
                pppoe += ' · %s' % cpe.pppoe_uptime
        elif cpe.pppoe_status == 'disconnected':
            pppoe = '🔴 PPPoE: *offline*'
        else:
            pppoe = '⚪ PPPoE: belum dicek'
        isolated = cpe.state == 'isolated' or (sub and sub.state == 'isolated')
        isolir = '🔒 Isolir: *YA (nunggak)*' if isolated else '🔓 Isolir: tidak'
        lines = [
            '*STATUS %s*' % (cpe.pppoe_username or '-'),
            '👤 %s · %s' % (partner.name, mask_phone(phone)),
            '🏘️ Desa: %s' % (area.name if area else '-'),
            '📡 Router: %s' % (router.name if router else '-'),
            '📦 Paket: %s' % (sub.package_id.name if sub and sub.package_id else '-'),
            pppoe,
        ]
        if cpe.pppoe_status == 'connected' and cpe.pppoe_address:
            lines.append('🌐 IP: %s' % cpe.pppoe_address)
        if cpe.state == 'terminated' or (sub and sub.state == 'terminated'):
            lines.append('⛔ Langganan: putus')
        elif cpe.state == 'draft':
            lines.append('⏳ Langganan: belum aktif')
        lines.append(isolir)
        if cpe.isp_activation_revoked:
            lines.append('⚠️ Secret: mati (eviden 24 jam, bukan isolir nunggak)')
        if sub and sub.is_special_treatment:
            lines.append('⭐ Khusus: jangan ditagih/isolir')
        lines.append('💳 Bayar: %s' % self._status_payment_line(sub))
        lines.append('📅 Tempo: %s' % self._status_due_line(sub))
        lat, lng = partner.isp_latitude, partner.isp_longitude
        if lat and lng:
            lines.append('📍 Lokasi: %s' % self._maps_url(lat, lng))
        else:
            lines.append(
                '📍 Lokasi: belum ada pin. Kirim lewat /eviden %s' % (
                    cpe.pppoe_username or 'username',
                ),
            )
        if (
            cpe.isp_onboarding
            and partner
            and not partner.isp_evidence_complete
            and not cpe.isp_evidence_waived
        ):
            lines.append('📝 Eviden: belum lengkap')
        if cpe.pppoe_status == 'disconnected' and cpe.pppoe_last_seen:
            seen = fields.Datetime.context_timestamp(self, cpe.pppoe_last_seen)
            lines.append('🕐 Last seen: %s' % seen.strftime('%d/%m %H:%M'))
        return '\n'.join(lines)

    @api.model
    def _status_reply(self, query, actor_phone=False, is_group=True):
        query = (query or '').strip()
        if is_group:
            if not query:
                return (
                    'Pakai /status username atau /status 08xxxxxxxxxx\n'
                    'Contoh: /status budi-0812xxxx'
                )
            cpes = self._find_status_cpes(query)
        else:
            own = self._cpes_for_phone(actor_phone)
            if query:
                wanted = self._find_status_cpes(query)
                if wanted and own and wanted.partner_id == own.partner_id:
                    cpes = wanted
                else:
                    return (
                        'Di chat pribadi hanya status akun Anda. '
                        'Teknisi cek di grup Aktivasi-dkt: /status username'
                    )
            else:
                cpes = own
            if not cpes and not query:
                return 'Nomor ini belum terdaftar sebagai pelanggan Dotakaro.'
        if not cpes:
            return 'User %s tidak ketemu.' % query
        if len(cpes) > 1:
            lines = ['Beberapa user. Ketik /status username:']
            for cpe in cpes:
                lines.append('- %s (%s)' % (cpe.pppoe_username or '-', cpe.partner_id.name))
            return '\n'.join(lines)
        return self._format_cpe_status(cpes)

    @api.model
    def _match_choice(self, text, records):
        raw = (text or '').strip()
        if not raw or not records:
            return records.browse()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(records):
                return records[idx - 1]
        token = raw.lower()
        exact = records.filtered(lambda r: (r.name or '').lower() == token)
        if len(exact) == 1:
            return exact
        code_field = 'code' if 'code' in records._fields else False
        if code_field:
            by_code = records.filtered(
                lambda r: (r.code or '').lower() == token,
            )
            if len(by_code) == 1:
                return by_code
        loose = records.filtered(lambda r: token in (r.name or '').lower())
        if len(loose) == 1:
            return loose
        return records.browse()

    @api.model
    def _make_username(self, name, phone):
        slug = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-') or 'user'
        digits = re.sub(r'\D', '', phone or '')
        if digits.startswith('62') and len(digits) > 4:
            local = '0' + digits[2:]
        else:
            local = digits
        base = '%s-%s' % (slug, local) if local else slug
        Cpe = self.env['isp.cpe'].sudo()
        username = base
        counter = 1
        while Cpe.search_count([('pppoe_username', '=', username)]):
            username = '%s-%s' % (base, counter)
            counter += 1
        return username

    @api.model
    def _random_password(self, length=10):
        chars = string.ascii_letters + string.digits
        return ''.join(random.choice(chars) for _ in range(length))

    @api.model
    def _mark_bot(self, message):
        """Tanda ~bot di baris pertama supaya anggota grup tidak salah paham."""
        text = (message or '').strip()
        if text.lower().startswith('~bot'):
            return text
        if not text:
            return '~bot'
        return '~bot\n%s' % text

    @api.model
    def _chunk_wa_message(self, text, limit=400):
        """Pecah pesan grup. Fonnte antre teks panjang ke grup, tapi sering tidak muncul."""
        text = (text or '').strip()
        if not text:
            return []
        if len(text) <= limit:
            return [text]
        chunks = []
        buf = []
        size = 0
        for para in text.split('\n\n'):
            extra = len(para) + (2 if buf else 0)
            if buf and size + extra > limit:
                chunks.append('\n\n'.join(buf))
                buf = [para]
                size = len(para)
            else:
                buf.append(para)
                size += extra
        if buf:
            chunks.append('\n\n'.join(buf))
        out = []
        for idx, chunk in enumerate(chunks):
            if idx and not chunk.lower().startswith('~bot'):
                chunk = '~bot\n%s' % chunk
            out.append(chunk)
        return out

    def _reply(self, target, message, inbox_id=None):
        """Balas ke chat 1:1 atau ID grup. Jangan log isi password panjang."""
        WA = self.env['isp.whatsapp.message']
        if self._is_activation_group(target) or WA.is_group_target(target):
            target = self._group_reply_target(target)
            inbox_id = None
        parts = self._chunk_wa_message(self._mark_bot(message))
        ok = True
        for part in parts:
            if not WA.send_inbound_ack(target, inbox_id=inbox_id, message=part):
                ok = False
        return ok

    @api.model
    def process_direct_inbound(self, payload):
        """Chat 1:1: /daftar saja. BAYAR dibiarkan ke alur bukti."""
        payload = payload or {}
        cfg = self.get_register_config()
        if not cfg['enabled']:
            return {'handled': False}
        WA = self.env['isp.whatsapp.message']
        if self._is_activation_group(payload.get('sender')) or WA.is_group_target(
            payload.get('sender'),
        ):
            return {
                **self.process_group_inbound(payload),
                'handled': True,
            }
        if WA.parse_bayar_caption(payload.get('message')):
            return {'handled': False}
        sender = WA.normalize_phone(payload.get('sender'))
        if not sender:
            return {'handled': False}
        text = self._inbound_text(payload)
        cmd, arg = self._parse_cmd(text)
        session = self._active_session(sender, 'direct')
        if not cmd and not session:
            return {'handled': False}
        inbox_id = payload.get('inboxid') or payload.get('inbox_id')
        try:
            self._check_bot_rate(sender)
        except UserError:
            return {'handled': True, 'ok': True, 'ignored': 'rate_limit'}
        if self._is_help_cmd(cmd):
            self._reply(sender, self._help_text('direct'), inbox_id)
            return {'handled': True, 'ok': True, 'replied': True, 'help': True}
        if cmd == 'status':
            self._reply(
                sender,
                self._status_reply(arg, actor_phone=sender, is_group=False),
                inbox_id,
            )
            return {'handled': True, 'ok': True, 'replied': True, 'status': True}
        if cmd == 'pasang':
            self._reply(sender, 'Perintah /pasang hanya di grup aktivasi.', inbox_id)
            return {'handled': True, 'ok': True, 'replied': True}
        if cmd == 'eviden':
            self._reply(sender, 'Eviden teknisi hanya di grup aktivasi.', inbox_id)
            return {'handled': True, 'ok': True, 'replied': True}
        if cmd == 'batal':
            if session:
                session.write({'state': 'cancelled', 'note': 'Dibatalkan pelanggan.'})
                session._append_audit('Sesi /daftar dibatalkan oleh %s.' % session._actor_label())
            self._reply(sender, 'Sesi dibatalkan.', inbox_id)
            return {'handled': True, 'ok': True, 'replied': True}
        if cmd == 'daftar':
            if session:
                session.write({'state': 'cancelled', 'note': 'Diganti /daftar baru.'})
                session._append_audit('Sesi diganti /daftar baru oleh %s.' % session._actor_label())
            session = self.create({
                'source': 'daftar',
                'channel': 'direct',
                'state': 'chatting',
                'step': 'name',
                'actor_phone': sender,
                'actor_name': self._payload_actor_name(payload),
                'customer_phone': sender,
            })
            session._append_audit(
                'Sesi /daftar dimulai. Pelanggan mendaftar sendiri: %s.' % session._actor_label(),
            )
            self._reply(sender, 'Nama lengkap pelanggan?', inbox_id)
            return {
                'handled': True, 'ok': True, 'replied': True,
                'registration': session.name,
            }
        if session:
            reply = session._continue_daftar(text)
            if reply:
                self._reply(sender, reply, inbox_id)
            return {
                'handled': True, 'ok': True, 'replied': bool(reply),
                'registration': session.name,
            }
        return {'handled': False}

    @api.model
    def _image_looks_like_receipt(self, raw, filename=''):
        if not raw:
            return False
        text, _engine = extract_text_from_bytes(raw, filename=filename)
        return looks_like_payment_receipt(text)

    @api.model
    def _is_confirm_yes(self, text):
        return bool(CONFIRM_YES_RE.match((text or '').strip()))

    @api.model
    def _is_confirm_no(self, text):
        return bool(CONFIRM_NO_RE.match((text or '').strip()))

    @api.model
    def _receipt_missing_caption_warning(self, actor, actor_name=False):
        tag = '@%s' % (actor or '')
        who = (' %s' % actor_name) if actor_name else ''
        return (
            '%s%s foto ini mirip struk transfer, tapi caption BAYAR belum ada.\n\n'
            'Kirim ULANG fotonya dengan caption:\n'
            'BAYAR username\n'
            'atau\n'
            'BAYAR 08xxxxxxxxxx\n\n'
            'Tanpa caption, bukti tidak masuk antrian.'
        ) % (tag, who)

    @api.model
    def _receipt_dest_mismatch_warning(self, actor, actor_name, proof):
        tag = '@%s' % (actor or '')
        who = (' %s' % actor_name) if actor_name else ''
        dest = (proof.dest_name_ocr or proof.dest_account_ocr or '-') if proof else '-'
        name = proof.name if proof else '-'
        return (
            '%s%s rekening tujuan struk (%s) BUKAN rekening terdaftar '
            '(Waspada Sinulingga / Pengaturan ISP).\n'
            'Jangan dianggap bayar. Bukti %s masuk review.'
        ) % (tag, who, dest, name)

    @api.model
    def _receipt_confirm_prompt(self, actor, actor_name, proof):
        tag = '@%s' % (actor or '')
        who = (' %s' % actor_name) if actor_name else ''
        partner = proof.partner_id
        username = (
            proof.cpe_id.pppoe_username
            if proof.cpe_id and proof.cpe_id.pppoe_username
            else '-'
        )
        amount = proof.amount_manual or proof.amount_ocr or 0.0
        date_txt = proof.date_ocr.strftime('%d/%m/%Y') if proof.date_ocr else '-'
        return (
            '%s%s nama pengirim rekening: %s\n'
            'Ini pembayaran untuk %s (user %s)?\n'
            'Nominal Rp %s | transfer %s\n'
            'Rekening tujuan: cocok.\n'
            'Balas YA untuk lunas, atau TIDAK.'
        ) % (
            tag, who,
            proof.sender_name_ocr or '-',
            partner.name if partner else '-',
            username,
            '{:,.0f}'.format(amount).replace(',', '.'),
            date_txt,
        )

    @api.model
    def _finish_billing_confirm(self, proof, actor, actor_name, group_id, inbox_id, yes=True):
        tag = '@%s' % (actor or '')
        who = (' %s' % actor_name) if actor_name else ''
        if not yes:
            proof.write({
                'state': 'rejected',
                'reject_reason': 'Dibatalkan pelapor di grup Billing-dkt.',
                'needs_review': False,
                'review_reason': 'Dibatalkan pelapor (TIDAK).',
            })
            proof._clear_wa_confirm()
            proof.message_post(body='Dibatalkan TIDAK oleh %s di grup Billing-dkt.' % actor)
            self._reply(
                group_id,
                '%s%s dibatalkan. Kirim ulang foto + caption BAYAR username '
                'jika tagihan orang lain.' % (tag, who),
                inbox_id,
            )
            return {
                'ok': True, 'replied': True, 'billing': True,
                'confirm': 'no', 'proof_name': proof.name,
            }
        paid, why = proof._try_post_from_group_confirm(actor)
        if paid:
            username = (
                proof.cpe_id.pppoe_username
                if proof.cpe_id and proof.cpe_id.pppoe_username
                else '-'
            )
            self._reply(
                group_id,
                '%s%s lunas.\nBukti %s\nPelanggan: %s\nUser: %s' % (
                    tag, who, proof.name,
                    proof.partner_id.name if proof.partner_id else '-',
                    username,
                ),
                inbox_id,
            )
            return {
                'ok': True, 'replied': True, 'billing': True,
                'confirm': 'yes', 'paid': True, 'proof_name': proof.name,
            }
        self._reply(
            group_id,
            '%s%s tidak bisa lunas otomatis: %s\nBukti %s masuk review pusat.' % (
                tag, who, why or 'perlu review', proof.name,
            ),
            inbox_id,
        )
        return {
            'ok': True, 'replied': True, 'billing': True,
            'confirm': 'yes', 'paid': False, 'proof_name': proof.name,
        }

    @api.model
    def _handle_billing_receipt_no_caption(
        self, payload, actor, actor_name, raw, filename, group_id, inbox_id,
    ):
        """Struk tanpa BAYAR: dest wajib benar, nama unik → minta YA, lalu lunas."""
        import base64
        Proof = self.env['isp.payment.proof'].sudo()
        text, _engine = extract_text_from_bytes(raw, filename=filename)
        parsed = parse_ocr_text(text)
        dest_match, _dest_note = Proof._eval_dest_match(parsed)
        partner = Proof._suggest_partner_from_sender_name(parsed.get('sender_name') or '')
        if partner and self._protected_partner(partner):
            partner = self.env['res.partner']
        if dest_match == 'mismatch':
            image_b64 = base64.b64encode(raw) if raw else False
            proof = Proof.create_from_group_receipt({
                'public_phone': actor,
                'public_name': actor_name,
                'image': image_b64,
                'image_filename': filename,
                'partner_id': partner.id if partner else False,
                'inbound_note': 'Grup Billing-dkt | rekening tujuan salah | pelapor %s (%s)' % (
                    actor_name or 'anggota', actor,
                ),
            })
            self._reply(
                group_id,
                self._receipt_dest_mismatch_warning(actor, actor_name, proof),
                inbox_id,
            )
            return {
                'ok': True, 'replied': True, 'billing': True,
                'dest_mismatch': True, 'proof_name': proof.name,
            }
        if dest_match != 'ok' or not partner:
            self._reply(
                group_id,
                self._receipt_missing_caption_warning(actor, actor_name),
                inbox_id,
            )
            return {
                'ok': True, 'replied': True, 'billing': True,
                'receipt_hint': True,
            }
        existing = Proof._recent_open_proof(partner)
        if existing:
            self._reply(
                group_id,
                'Sudah ada antrian %s untuk %s. Jangan kirim dobel. '
                'Pusat yang verifikasi. Belum lunas.' % (
                    existing.name, partner.name,
                ),
                inbox_id,
            )
            return {
                'ok': True, 'replied': True, 'billing': True,
                'duplicate': True, 'proof_name': existing.name,
            }
        staff = self.find_staff_user(actor)
        image_b64 = base64.b64encode(raw) if raw else False
        vals = {
            'public_phone': actor,
            'public_name': actor_name,
            'image': image_b64,
            'image_filename': filename,
            'partner_id': partner.id,
            'inbound_note': 'Grup Billing-dkt | nama pengirim rekening | pelapor %s (%s)' % (
                actor_name or 'anggota', actor,
            ),
            'reporter_user_id': staff.id if staff else False,
        }
        if partner.area_id:
            vals['area_id'] = partner.area_id.id
        if partner.subscription_ids:
            vals['subscription_id'] = partner.subscription_ids[0].id
        cpe = partner.cpe_ids[:1] if hasattr(partner, 'cpe_ids') else False
        if cpe:
            vals['cpe_id'] = cpe.id
        proof = Proof.create_from_group_receipt(vals)
        dest_ok, dest_why = proof._dest_safe_for_autopay()
        if not dest_ok:
            self._reply(
                group_id,
                self._receipt_dest_mismatch_warning(actor, actor_name, proof)
                if proof.dest_match == 'mismatch'
                else (
                    '@%s%s %s Bukti %s masuk review. '
                    'Atau kirim ulang + caption BAYAR username.'
                    % (actor, (' %s' % actor_name) if actor_name else '', dest_why, proof.name)
                ),
                inbox_id,
            )
            return {
                'ok': True, 'replied': True, 'billing': True,
                'dest_unsafe': True, 'proof_name': proof.name,
            }
        blockers = proof._group_autopay_blockers()
        if blockers:
            self._reply(
                group_id,
                '@%s%s nama cocok %s, tapi belum bisa lunas otomatis: %s\n'
                'Bukti %s masuk review. Atau kirim ulang + caption BAYAR username.'
                % (
                    actor,
                    (' %s' % actor_name) if actor_name else '',
                    partner.name,
                    blockers[0],
                    proof.name,
                ),
                inbox_id,
            )
            return {
                'ok': True, 'replied': True, 'billing': True,
                'review': True, 'proof_name': proof.name,
            }
        proof._start_wa_confirm(actor)
        self._reply(
            group_id,
            self._receipt_confirm_prompt(actor, actor_name, proof),
            inbox_id,
        )
        return {
            'ok': True, 'replied': True, 'billing': True,
            'confirm_pending': True, 'proof_name': proof.name,
        }

    @api.model
    def process_billing_group_inbound(self, payload):
        """Grup Billing-dkt: BAYAR antrian, atau struk tanpa caption + konfirmasi YA."""
        payload = payload or {}
        WA = self.env['isp.whatsapp.message']
        group_id = self._payload_group_id(payload)
        text = self._inbound_text(payload)
        inbox_id = payload.get('inboxid') or payload.get('inbox_id')
        cmd, arg = self._parse_cmd(text)
        if self._is_help_cmd(cmd):
            sent = self._reply(group_id, self._help_text('billing'), inbox_id)
            return {
                'ok': True, 'replied': bool(sent), 'help': True, 'billing': True,
            }
        if cmd == 'status':
            self._reply(
                group_id,
                self._status_reply(arg, actor_phone=False, is_group=True),
                inbox_id,
            )
            return {'ok': True, 'replied': True, 'status': True, 'billing': True}
        if cmd in ('pasang', 'eviden', 'daftar'):
            self._reply(
                group_id,
                'Perintah itu di grup Aktivasi-dkt, atau /daftar di chat 1:1.',
                inbox_id,
            )
            return {'ok': True, 'replied': True, 'billing': True}
        actor = WA.normalize_phone(payload.get('member')) or WA.normalize_phone(
            str(payload.get('member') or '').split('@', 1)[0],
        )
        if not actor:
            return {'ok': True, 'ignored': 'no_member', 'billing': True}
        actor_name = self._payload_actor_name(payload)
        token = WA.parse_bayar_caption(text)
        url = (payload.get('url') or '').strip()
        ext = str(payload.get('extension') or '').lower().lstrip('.')
        if not token:
            pending = self.env['isp.payment.proof'].sudo()._find_pending_wa_confirm(actor)
            if pending and self._is_confirm_yes(text):
                return self._finish_billing_confirm(
                    pending, actor, actor_name, group_id, inbox_id, yes=True,
                )
            if pending and self._is_confirm_no(text):
                return self._finish_billing_confirm(
                    pending, actor, actor_name, group_id, inbox_id, yes=False,
                )
            if url and ext in WA_FILE_EXT:
                try:
                    WA._check_wa_inbound_rate(actor)
                except UserError:
                    return {'ok': True, 'ignored': 'rate_limit', 'billing': True}
                raw = WA._download_https_file(url)
                filename = (payload.get('filename') or '').strip() or (
                    'bukti-wa.%s' % (ext or 'jpg')
                )
                if self._image_looks_like_receipt(raw, filename):
                    return self._handle_billing_receipt_no_caption(
                        payload, actor, actor_name, raw, filename, group_id, inbox_id,
                    )
            return {'ok': True, 'ignored': 'billing_chatter', 'billing': True}
        if not url or ext not in WA_FILE_EXT:
            self._reply(
                group_id,
                'Kirim FOTO struk dengan caption BAYAR username atau BAYAR 08xxxx',
                inbox_id,
            )
            return {
                'ok': True, 'ignored': 'bayar_no_image',
                'replied': True, 'billing': True,
            }
        try:
            WA._check_wa_inbound_rate(actor)
        except UserError:
            return {'ok': True, 'ignored': 'rate_limit', 'billing': True}
        raw = WA._download_https_file(url)
        staff = self.find_staff_user(actor)
        partner, _how = WA.find_partner_by_bayar_token(token)
        if partner and self._protected_partner(partner):
            partner = self.env['res.partner']
        Proof = self.env['isp.payment.proof'].sudo()
        if partner:
            existing = Proof._recent_open_proof(partner)
            if existing:
                self._reply(
                    group_id,
                    'Sudah ada antrian %s untuk %s. Jangan kirim dobel. '
                    'Pusat yang verifikasi. Belum lunas.' % (
                        existing.name, partner.name,
                    ),
                    inbox_id,
                )
                return {
                    'ok': True, 'replied': True, 'billing': True,
                    'duplicate': True, 'proof_name': existing.name,
                }
        filename = (payload.get('filename') or '').strip() or (
            'bukti-wa.%s' % (ext or 'jpg')
        )
        image_b64 = False
        if raw:
            import base64
            image_b64 = base64.b64encode(raw)
        proof = Proof.create_from_whatsapp({
            'public_phone': actor,
            'public_name': actor_name,
            'image': image_b64,
            'image_filename': filename,
            'caption': text,
            'inbound_note': 'Grup Billing-dkt | pelapor %s (%s)' % (
                actor_name or 'anggota', actor,
            ),
            'reporter_user_id': staff.id if staff else False,
        }, match_sender=False)
        if not proof:
            self._reply(
                group_id,
                'User %s tidak ketemu. Cek username / nomor HP.' % token,
                inbox_id,
            )
            return {'ok': True, 'replied': True, 'billing': True, 'matched': False}
        customer = proof.partner_id
        username = (
            proof.cpe_id.pppoe_username
            if proof.cpe_id and proof.cpe_id.pppoe_username
            else token
        )
        if customer:
            body = (
                'Bukti masuk %s\n'
                'Pelanggan: %s\n'
                'User: %s\n'
                'Desa: %s\n'
                'Pelapor: %s\n'
                'Antrian. Pusat verifikasi. Belum lunas.'
            ) % (
                proof.name, customer.name, username,
                customer.area_id.name if customer.area_id else '-',
                '%s (%s)' % (actor_name or 'anggota', mask_phone(actor)),
            )
        else:
            body = (
                'Bukti masuk %s. User %s tidak ketemu. '
                'Gambar disimpan, pusat pasangkan pelanggan. Belum lunas.'
            ) % (proof.name, token)
        self._reply(group_id, body, inbox_id)
        return {
            'ok': True, 'replied': True, 'billing': True,
            'proof_name': proof.name,
            'matched': bool(customer),
        }

    @api.model
    def process_group_inbound(self, payload):
        """Grup aktivasi, grup billing, atau diabaikan."""
        payload = payload or {}
        group_id = self._payload_group_id(payload)
        if self._is_billing_group(group_id):
            return self.process_billing_group_inbound(payload)
        cfg = self.get_register_config()
        member = payload.get('member')
        if not cfg['enabled']:
            return {'ok': True, 'ignored': 'group'}
        if not self._is_activation_group(group_id):
            _logger.info(
                'WA grup diabaikan: sender=%s wanted=%s',
                group_id, cfg.get('group_id'),
            )
            return {'ok': True, 'ignored': 'other_group'}
        WA = self.env['isp.whatsapp.message']
        text = self._inbound_text(payload)
        if WA.parse_bayar_caption(text):
            return {'ok': True, 'ignored': 'bayar_in_group'}
        inbox_id = payload.get('inboxid') or payload.get('inbox_id')
        cmd, arg = self._parse_cmd(text)
        if self._is_help_cmd(cmd):
            self._reply(group_id, self._help_text('group'), inbox_id)
            return {'ok': True, 'replied': True, 'help': True}
        if cmd == 'status':
            self._reply(
                group_id,
                self._status_reply(arg, actor_phone=False, is_group=True),
                inbox_id,
            )
            return {'ok': True, 'replied': True, 'status': True}
        actor = WA.normalize_phone(member) or WA.normalize_phone(
            str(member or '').split('@', 1)[0],
        )
        if not actor:
            return {'ok': True, 'ignored': 'no_member'}
        location = payload.get('location')
        url = (payload.get('url') or '').strip()
        ext = str(payload.get('extension') or '').lower().lstrip('.')
        ktp_user = (KTP_RE.search(text).group(1) if KTP_RE.search(text) else False)
        rumah_user = (RUMAH_RE.search(text).group(1) if RUMAH_RE.search(text) else False)
        session = self._active_session(actor, 'group')
        has_work = bool(
            cmd or ktp_user or rumah_user or location or session
        )
        if not has_work:
            return {'ok': True, 'ignored': 'group_chatter'}
        # Grup aktivasi = allowlist. Semua anggota boleh /pasang dan eviden.
        staff = self.find_staff_user(actor)
        try:
            self._check_bot_rate(actor)
        except UserError:
            return {'ok': True, 'ignored': 'rate_limit'}
        if cmd == 'daftar':
            self._reply(group_id, 'Pelanggan daftar lewat chat pribadi: /daftar', inbox_id)
            return {'ok': True, 'replied': True, 'ignored': 'daftar_in_group'}
        if cmd == 'batal':
            if session:
                session.write({'state': 'cancelled', 'note': 'Dibatalkan teknisi.'})
                session._append_audit('Sesi dibatalkan oleh %s.' % session._actor_label())
            self._reply(group_id, 'Sesi dibatalkan.', inbox_id)
            return {'ok': True, 'replied': True}
        if cmd == 'pasang':
            if session:
                session.write({'state': 'cancelled', 'note': 'Diganti /pasang baru.'})
                session._append_audit('Sesi diganti /pasang baru oleh %s.' % session._actor_label())
            session = self.create({
                'source': 'pasang',
                'channel': 'group',
                'state': 'chatting',
                'step': 'phone',
                'actor_phone': actor,
                'actor_name': self._payload_actor_name(payload),
                'group_id': group_id,
                'technician_id': staff.id,
            })
            session._append_audit(
                'Sesi /pasang dimulai oleh %s di grup aktivasi.' % session._actor_label(),
            )
            self._reply(group_id, 'Nomor HP pelanggan?', inbox_id)
            return {'ok': True, 'replied': True, 'registration': session.name}
        image_b64 = False
        if url and ext in WA_FILE_EXT:
            raw = WA._download_https_file(url)
            if raw:
                import base64
                image_b64 = base64.b64encode(raw)
        if cmd == 'eviden' or ktp_user or rumah_user or (
            session and session.step in ('ktp', 'house', 'location')
        ) or location:
            reply = self._handle_eviden(
                staff, actor, group_id, inbox_id, text, arg or ktp_user or rumah_user,
                image_b64, ext, location, session,
                actor_name=self._payload_actor_name(payload),
            )
            return {'ok': True, 'replied': bool(reply), 'ignored': False}
        if session and session.source == 'pasang':
            reply = session._continue_pasang(text)
            if reply:
                self._reply(group_id, reply, inbox_id)
            return {
                'ok': True, 'replied': bool(reply),
                'registration': session.name,
            }
        return {'ok': True, 'ignored': 'group_chatter'}

    def _continue_daftar(self, text):
        self.ensure_one()
        self._touch(text)
        step = self.step or 'name'
        if step == 'name':
            name = (text or '').strip()
            if len(name) < 3:
                return 'Nama terlalu pendek. Ketik nama lengkap.'
            self.write({'customer_name': name[:80], 'step': 'area'})
            return self._format_area_list()
        if step == 'area':
            area = self._match_choice(text, self._area_choices())
            if not area:
                return 'Desa tidak ketemu.\n' + self._format_area_list()
            return self._after_area_selected(area, ask_router=False)
        if step == 'router':
            return self._continue_router(text)
        if step == 'package':
            package = self._match_choice(text, self._package_choices())
            if not package:
                return 'Paket tidak ketemu.\n' + self._format_package_list()
            self.write({'package_id': package.id, 'step': 'confirm'})
            return (
                'Konfirmasi pendaftaran:\n'
                'Nama: %s\nDesa: %s\nPaket: %s\nHP: %s\n\n'
                'Ketik YA untuk antri. Teknisi pilih router saat /pasang.'
            ) % (
                self.customer_name, self.area_id.name, package.name,
                mask_phone(self.customer_phone),
            )
        if step == 'confirm':
            if (text or '').strip().lower() not in ('ya', 'yes', 'y'):
                return 'Ketik YA untuk konfirmasi, atau /batal.'
            self.write({'state': 'queued', 'step': 'done'})
            self._append_audit(
                'Antrian /daftar: %s mendaftarkan diri (%s), desa %s, paket %s.' % (
                    self.customer_name, mask_phone(self.customer_phone),
                    self.area_id.name, self.package_id.name,
                ),
            )
            return (
                'Pendaftaran masuk antrian %s. '
                'Teknisi akan menghubungi untuk pemasangan. '
                'Belum ada username/koneksi.'
            ) % self.name
        return HELP_DIRECT

    def _continue_pasang(self, text):
        self.ensure_one()
        self._touch(text)
        WA = self.env['isp.whatsapp.message']
        step = self.step or 'phone'
        if step == 'phone':
            phone = WA.normalize_phone(text)
            if not phone:
                return 'Nomor HP tidak valid. Contoh 0812xxxxxxx'
            self.write({'customer_phone': phone, 'step': 'name'})
            return 'Nama lengkap pelanggan?'
        if step == 'name':
            name = (text or '').strip()
            if len(name) < 3:
                return 'Nama terlalu pendek. Ketik nama lengkap.'
            self.write({'customer_name': name[:80], 'step': 'area'})
            return self._format_area_list()
        if step == 'area':
            area = self._match_choice(text, self._area_choices())
            if not area:
                return 'Desa tidak ketemu.\n' + self._format_area_list()
            return self._after_area_selected(area, ask_router=True)
        if step == 'router':
            return self._continue_router(text)
        if step == 'package':
            package = self._match_choice(text, self._package_choices())
            if not package:
                return 'Paket tidak ketemu.\n' + self._format_package_list()
            if not self.mikrotik_config_id:
                self.write({'step': 'router'})
                return 'Router belum dipilih.\n' + self._format_router_list()
            self.write({'package_id': package.id, 'step': 'confirm'})
            return (
                'Konfirmasi pasang:\n'
                'Nama: %s\nHP: %s\nDesa: %s\nRouter: %s\nPaket: %s\n\nKetik YA.'
            ) % (
                self.customer_name, mask_phone(self.customer_phone),
                self.area_id.name, self.mikrotik_config_id.name or '-',
                package.name,
            )
        if step == 'confirm':
            if (text or '').strip().lower() not in ('ya', 'yes', 'y'):
                return 'Ketik YA untuk konfirmasi, atau /batal.'
            cfg = self.get_register_config()
            if cfg['mode'] == 'tech_auto':
                return self._create_customer_records(activate_router=True)
            self.write({'state': 'queued', 'step': 'done'})
            self._append_audit(
                'Antrian /pasang: %s mendaftarkan %s (%s), desa %s, paket %s.' % (
                    self._actor_label(), self.customer_name,
                    mask_phone(self.customer_phone),
                    self.area_id.name, self.package_id.name,
                ),
            )
            return 'Masuk antrian %s. Admin pusat yang membuat user.' % self.name
        return HELP_GROUP

    @api.model
    def _handle_eviden(
        self, staff, actor, group_id, inbox_id, text, username, image_b64, ext, location, session,
        actor_name=False,
    ):
        cmd, arg = self._parse_cmd(text)
        token = username or arg
        if cmd == 'eviden' and token:
            cpe = self._find_onboarding_cpe(token)
            if not cpe:
                self._reply(group_id, 'Username %s tidak ketemu / bukan pasang baru.' % token, inbox_id)
                return True
            if session:
                session.write({'state': 'cancelled', 'note': 'Diganti /eviden.'})
            session = self.create({
                'source': 'pasang',
                'channel': 'group',
                'state': 'chatting',
                'step': 'ktp',
                'actor_phone': actor,
                'actor_name': actor_name,
                'group_id': group_id,
                'technician_id': staff.id,
                'partner_id': cpe.partner_id.id,
                'cpe_id': cpe.id,
                'subscription_id': cpe.subscription_id.id if cpe.subscription_id else False,
                'pppoe_username': cpe.pppoe_username,
                'customer_name': cpe.partner_id.name,
                'customer_phone': self.env['isp.whatsapp.message'].partner_phone(cpe.partner_id),
                'area_id': cpe.area_id.id if cpe.area_id else False,
                'mikrotik_config_id': (
                    cpe.mikrotik_config_id.id if cpe.mikrotik_config_id else False
                ),
            })
            session._log_audit(
                'Eviden %s dimulai oleh %s.' % (cpe.pppoe_username, session._actor_label()),
                partner=cpe.partner_id, cpe=cpe,
            )
            self._reply(
                group_id,
                'Eviden %s. Kirim foto KTP (caption KTP %s).' % (
                    cpe.pppoe_username, cpe.pppoe_username,
                ),
                inbox_id,
            )
            return True
        ktp_user = KTP_RE.search(text or '')
        rumah_user = RUMAH_RE.search(text or '')
        if ktp_user and image_b64 and ext in WA_IMAGE_EXT:
            cpe = self._find_onboarding_cpe(ktp_user.group(1))
            if not cpe:
                self._reply(group_id, 'Username KTP tidak ketemu.', inbox_id)
                return True
            self._store_ktp(cpe.partner_id, image_b64)
            self._audit_evidence(session, actor, actor_name, staff, cpe, 'KTP')
            self._after_evidence_piece(cpe, group_id, inbox_id)
            return True
        if rumah_user and image_b64 and ext in WA_IMAGE_EXT:
            cpe = self._find_onboarding_cpe(rumah_user.group(1))
            if not cpe:
                self._reply(group_id, 'Username RUMAH tidak ketemu.', inbox_id)
                return True
            self._store_house(cpe.partner_id, image_b64)
            self._audit_evidence(session, actor, actor_name, staff, cpe, 'rumah')
            self._after_evidence_piece(cpe, group_id, inbox_id)
            return True
        if session and session.cpe_id:
            cpe = session.cpe_id
            if session.step == 'ktp' and image_b64 and ext in WA_IMAGE_EXT:
                self._store_ktp(cpe.partner_id, image_b64)
                session.write({'step': 'house'})
                session._touch('ktp')
                self._audit_evidence(session, actor, actor_name, staff, cpe, 'KTP')
                self._reply(
                    group_id,
                    'KTP tersimpan. Kirim foto rumah (caption RUMAH %s).' % cpe.pppoe_username,
                    inbox_id,
                )
                self._after_evidence_piece(cpe, group_id, inbox_id, reply=False)
                return True
            if session.step == 'house' and image_b64 and ext in WA_IMAGE_EXT:
                self._store_house(cpe.partner_id, image_b64)
                session.write({'step': 'location'})
                session._touch('rumah')
                self._audit_evidence(session, actor, actor_name, staff, cpe, 'rumah')
                self._reply(group_id, 'Foto rumah tersimpan. Kirim pin lokasi WhatsApp.', inbox_id)
                self._after_evidence_piece(cpe, group_id, inbox_id, reply=False)
                return True
            if session.step == 'location' or location:
                lat, lng = self._parse_location(location or text)
                if not lat:
                    self._reply(group_id, 'Kirim pin lokasi WhatsApp (bukan ketik angka).', inbox_id)
                    return True
                self._store_coords(cpe.partner_id, lat, lng)
                session.write({'state': 'created', 'step': 'done'})
                session._touch('lokasi')
                self._audit_evidence(session, actor, actor_name, staff, cpe, 'lokasi')
                self._after_evidence_piece(cpe, group_id, inbox_id)
                return True
        if cmd == 'eviden':
            self._reply(group_id, 'Pakai /eviden username', inbox_id)
            return True
        return False

    @api.model
    def _find_onboarding_cpe(self, username):
        token = (username or '').strip()
        if not token:
            return self.env['isp.cpe']
        return self.env['isp.cpe'].sudo().search([
            ('pppoe_username', '=ilike', token),
        ], limit=1)

    @api.model
    def _store_ktp(self, partner, image_b64):
        if self._protected_partner(partner):
            return
        partner.sudo().write({'isp_ktp_image': image_b64})

    @api.model
    def _store_house(self, partner, image_b64):
        if self._protected_partner(partner):
            return
        partner.sudo().write({'isp_house_image': image_b64})

    @api.model
    def _store_coords(self, partner, lat, lng):
        if self._protected_partner(partner):
            return
        partner.sudo().write({
            'isp_latitude': lat,
            'isp_longitude': lng,
        })

    @api.model
    def _after_evidence_piece(self, cpe, group_id, inbox_id, reply=True):
        partner = cpe.partner_id
        partner.invalidate_recordset(['isp_evidence_complete'])
        if partner.isp_evidence_complete:
            if cpe.isp_activation_revoked:
                cpe._restore_onboarding_access()
            if reply:
                self._reply(
                    group_id,
                    'Eviden %s lengkap. KTP, rumah, koordinat tersimpan di pelanggan.' % (
                        cpe.pppoe_username or '-',
                    ),
                    inbox_id,
                )
            return
        if not reply:
            return
        missing = []
        if not partner.isp_ktp_image:
            missing.append('KTP')
        if not partner.isp_house_image:
            missing.append('rumah')
        if not (partner.isp_latitude and partner.isp_longitude):
            missing.append('lokasi')
        self._reply(
            group_id,
            'Eviden %s belum lengkap: %s.' % (cpe.pppoe_username or '-', ', '.join(missing)),
            inbox_id,
        )

    def action_approve(self):
        self.ensure_one()
        if self.state != 'queued':
            raise UserError('Hanya antrian yang bisa disetujui.')
        self._append_audit('Disetujui admin pusat: %s.' % (self.env.user.name or '-'))
        message = self._create_customer_records(activate_router=True)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Pendaftaran',
                'message': message,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_reject(self):
        self.ensure_one()
        if self.state not in ('queued', 'chatting'):
            raise UserError('Status ini tidak bisa ditolak.')
        self.write({'state': 'rejected', 'note': 'Ditolak admin pusat.'})
        self._append_audit('Ditolak admin pusat: %s.' % (self.env.user.name or '-'))
        return True

    def action_waive_evidence(self):
        self.ensure_one()
        if not self.cpe_id:
            raise UserError('Belum ada CPE.')
        self.cpe_id.write({'isp_evidence_waived': True})
        if self.cpe_id.isp_activation_revoked:
            self.cpe_id._restore_onboarding_access()
        self.message_post(body='Admin membebaskan kewajiban eviden.')
        self._append_audit('Admin %s membebaskan kewajiban eviden.' % (self.env.user.name or '-'))
        return True

    def _create_customer_records(self, activate_router=True):
        self.ensure_one()
        if not self.customer_name or not self.customer_phone or not self.area_id or not self.package_id:
            raise UserError('Data pendaftaran belum lengkap.')
        router = self.mikrotik_config_id.filtered('active')
        if not router:
            raise UserError(
                'Pilih router PPPoE dulu (Bulan Jahe, Lau Baleng, Sinaman, ...). '
                'Desa tidak menentukan router otomatis.'
            )
        if self._router_is_offline(router):
            raise UserError(self._router_offline_message(router))
        WA = self.env['isp.whatsapp.message']
        partner = WA.find_partner_by_phone(self.customer_phone)
        if partner and self._protected_partner(partner):
            partner = self.env['res.partner']
        if not partner:
            phone_local = self.customer_phone
            if phone_local.startswith('62'):
                phone_local = '0' + phone_local[2:]
            partner = self.env['res.partner'].sudo().create({
                'name': self.customer_name,
                'phone': phone_local,
                'phone_wa': self.customer_phone,
                'customer_rank': 1,
                'state': 'draft',
                'area_id': self.area_id.id,
            })
        elif self.area_id and not partner.area_id:
            if not self._protected_partner(partner):
                partner.sudo().write({'area_id': self.area_id.id})
        username = self._make_username(self.customer_name, self.customer_phone)
        password = self._random_password()
        cpe = self.env['isp.cpe'].sudo().create({
            'name': 'CPE-%s' % self.customer_name,
            'partner_id': partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': username,
            'pppoe_password': password,
            'mikrotik_config_id': router.id if router else False,
            'state': 'draft',
            'isp_onboarding': True,
            'technician_user_id': self.technician_id.id if self.technician_id else False,
        })
        subscription = self.env['isp.subscription'].sudo().create({
            'partner_id': partner.id,
            'cpe_id': cpe.id,
            'package_id': self.package_id.id,
            'date_start': fields.Date.context_today(self),
            'due_day': self.env['isp.subscription']._billing_due_day(),
            'state': 'draft',
        })
        activate_error = False
        if activate_router and router:
            try:
                subscription.action_open()
            except Exception as exc:
                activate_error = str(exc)[:200]
                _logger.warning('Aktivasi router pendaftaran %s: %s', self.name, exc)
        self.write({
            'state': 'created',
            'step': 'done',
            'partner_id': partner.id,
            'cpe_id': cpe.id,
            'subscription_id': subscription.id,
            'pppoe_username': username,
            'mikrotik_config_id': router.id,
            'activate_error': activate_error or False,
        })
        self._apply_partner_audit(partner)
        event = (
            '%s mendaftarkan %s (%s) via %s. '
            'Antrian %s. Username %s. Desa %s. Router %s. Paket %s.'
        ) % (
            self._actor_label(), self.customer_name,
            mask_phone(self.customer_phone),
            '/pasang' if self.source == 'pasang' else '/daftar',
            self.name, username, self.area_id.name,
            router.name, self.package_id.name,
        )
        if activate_error:
            event += ' Secret router belum aktif.'
        self._log_audit(event, partner=partner, cpe=cpe)
        customer_msg = (
            'Pendaftaran Dotakaro siap.\n'
            'Username: %s\nPassword: %s\nDesa: %s\nRouter: %s\nPaket: %s'
        ) % (username, password, self.area_id.name, router.name, self.package_id.name)
        self._reply(self.customer_phone, customer_msg)
        group_msg = (
            'User dibuat %s\nPelanggan: %s\nHP: %s\nUsername: %s\n'
            'Router: %s. Password dikirim ke HP pelanggan. '
            'Invoice tidak dibuat otomatis.'
        ) % (
            self.name, self.customer_name, mask_phone(self.customer_phone),
            username, router.name,
        )
        if activate_error:
            group_msg += '\nSecret router belum aktif: admin cek koneksi API.'
        if self.group_id:
            self._reply(self.group_id, group_msg)
        return group_msg

    @api.model
    def notify_first_connect(self, cpe):
        """Panggil saat PPPoE connect pertama. Satu kali, ke grup aktivasi."""
        cfg = self.get_register_config()
        if not cfg['enabled'] or not cfg['evidence_required']:
            return False
        if not cpe or not cpe.isp_onboarding or cpe.isp_evidence_waived:
            return False
        if cpe.isp_first_connected_at or cpe.isp_evidence_notified:
            return False
        if cpe.partner_id.isp_evidence_complete:
            return False
        now = fields.Datetime.now()
        deadline = now + timedelta(hours=cfg['evidence_hours'])
        cpe.sudo().write({
            'isp_first_connected_at': now,
            'isp_evidence_deadline': deadline,
            'isp_evidence_notified': True,
        })
        group_id = cfg.get('group_id')
        if not group_id:
            return True
        username = cpe.pppoe_username or '-'
        self._reply(group_id, (
            'Pelanggan %s (%s) sudah CONNECT.\n'
            'Lengkapi eviden dalam %s jam: /eviden %s\n'
            'KTP, foto rumah, pin lokasi. Jika tidak, aktivasi dicabut '
            '(secret dimatikan, bukan isolir nunggak).'
        ) % (
            cpe.partner_id.name, username, cfg['evidence_hours'], username,
        ))
        return True

    @api.model
    def _cron_onboarding_evidence(self):
        """Pengingat 12 jam + cabut aktivasi setelah deadline. Bukan isolir nunggak."""
        cfg = self.get_register_config()
        if not cfg['enabled'] or not cfg['evidence_required']:
            return True
        now = fields.Datetime.now()
        Cpe = self.env['isp.cpe'].sudo()
        pending = Cpe.search([
            ('isp_onboarding', '=', True),
            ('isp_evidence_waived', '=', False),
            ('isp_activation_revoked', '=', False),
            ('isp_first_connected_at', '!=', False),
            ('isp_evidence_deadline', '!=', False),
            ('state', '!=', 'terminated'),
        ])
        group_id = cfg.get('group_id')
        half = timedelta(hours=max(1, cfg['evidence_hours'] / 2.0))
        for cpe in pending:
            if cpe.partner_id.isp_evidence_complete:
                continue
            sub = cpe.subscription_id
            if sub and sub.is_special_treatment:
                continue
            deadline = cpe.isp_evidence_deadline
            if not cpe.isp_evidence_reminder_sent and now >= cpe.isp_first_connected_at + half:
                cpe.isp_evidence_reminder_sent = True
                if group_id:
                    self._reply(group_id, (
                        'Pengingat eviden %s. Sisa waktu sampai %s. /eviden %s'
                    ) % (
                        cpe.pppoe_username,
                        fields.Datetime.context_timestamp(self, deadline).strftime('%d/%m %H:%M'),
                        cpe.pppoe_username,
                    ))
            if now >= deadline:
                cpe._revoke_onboarding_access()
                if group_id:
                    self._reply(group_id, (
                        'Aktivasi %s dicabut: eviden 24 jam belum lengkap. '
                        'Kirim KTP/rumah/lokasi untuk menyalakan lagi.'
                    ) % (cpe.pppoe_username or '-'))
        return True
