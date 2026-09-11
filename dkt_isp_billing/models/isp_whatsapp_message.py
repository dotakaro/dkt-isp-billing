import base64
import ipaddress
import json
import logging
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.modules.module import current_test
from odoo.tools import config as odoo_config

from .isp_phone import (
    mask_phone,
    pack_id_mobile_token,
    pack_username_phone_suffix,
    phones_equivalent,
    username_phone_match,
)

_logger = logging.getLogger(__name__)

FONNTE_URL = 'https://api.fonnte.com/send'
META_URL = 'https://graph.facebook.com/%s/%s/messages'
TEST_WA_PHONE = '628116343031'
INBOUND_ACK = 'Bukti diterima, menunggu verifikasi admin pusat'
INBOUND_HINT_BAYAR = (
    'Untuk bukti tagihan, kirim FOTO struk dengan caption:\n'
    'BAYAR username\n'
    'atau\n'
    'BAYAR 08xxxxxxxxxx\n'
    'Foto tanpa format itu tidak diproses (nomor ini dipakai campur).'
)
BAYAR_TOKEN_RE = re.compile(
    r'(?is)\bbayar\b[:\s,.-]+([A-Za-z0-9._+-]+)',
)
# Nomor HP setelah BAYAR: digit + pemisah (spasi/minus/titik), tanpa huruf.
BAYAR_PHONE_RE = re.compile(
    r'(?is)\bbayar\b[:\s,.\-–—]+(\+?(?:[\s./\-()–—‑‒−\u00a0]*\d){9,15})',
)
# Username + HP berpemisah: rosmina-0823-6319-7866 / dkt-uat-081 1634 3031
BAYAR_USERNAME_PHONE_RE = re.compile(
    r'(?is)\bbayar\b[:\s,.\-–—]+('
    r'[A-Za-z][A-Za-z0-9._+-]*-'
    r'(?:[\s./\-()–—‑‒−\u00a0]*\d){9,15}'
    r')',
)
WA_IMAGE_EXT = ('png', 'jpg', 'jpeg', 'webp')
WA_FILE_EXT = WA_IMAGE_EXT + ('pdf',)
WA_INBOUND_MAX_BYTES = 10 * 1024 * 1024
WA_INBOUND_RATE = 8
WA_INBOUND_MINUTES = 15

# Satu sumber teks modul (Fonnte + pratinjau). Jangan duplikat mail.template.
# Meta production: 5 variabel pertama = Approved. Fonnte boleh pakai late_date / rekening.
TEMPLATE_VAR_ORDER = ('name', 'package', 'period', 'amount', 'due_date')
TEMPLATE_KIND_SELECTION = [
    ('invoice', 'Tagihan'),
    ('due', 'Jatuh tempo'),
    ('due_soon', 'H-1 telat'),
    ('late', 'Telat'),
    ('isolir_warn', 'Peringatan isolir'),
    ('isolated', 'Sudah isolir'),
    ('thanks', 'Terima kasih bayar'),
    ('restored', 'Buka isolir'),
    ('test', 'Tes'),
    ('custom', 'Kustom'),
]
WA_TEMPLATES = {
    'invoice': (
        'Halo {name},\n\n'
        'Tagihan internet {package} periode {period} sebesar Rp {amount}.\n'
        'Jatuh tempo: {due_date}. Dianggap telat mulai {late_date}.\n\n'
        'Transfer ke {dest_bank} {dest_account} a.n. {dest_name}.\n'
        'Kirim foto struk ke WhatsApp ini dengan caption: BAYAR {username}\n'
        '(wajib, nomor ini dipakai campur; foto tanpa format tidak diproses).\n\n'
        'Abaikan pesan ini jika sudah membayar.\n\n'
        'DOTAKARO'
    ),
    'due': (
        'Halo {name},\n\n'
        'Pengingat jatuh tempo tagihan {package} periode {period} '
        'sebesar Rp {amount}.\n'
        'Jatuh tempo {due_date}. Mohon dibayar sebelum {late_date} '
        'agar tidak dianggap telat.\n\n'
        'Abaikan pesan ini jika sudah membayar.\n\n'
        'DOTAKARO'
    ),
    'due_soon': (
        'Halo {name},\n\n'
        'Besok ({late_date}) tagihan {package} periode {period} '
        'sebesar Rp {amount} akan dianggap telat.\n'
        'Mohon segera transfer ke {dest_bank} {dest_account} a.n. {dest_name} '
        'lalu kirim foto struk dengan caption: BAYAR {username}.\n\n'
        'Abaikan pesan ini jika sudah membayar.\n\n'
        'DOTAKARO'
    ),
    'late': (
        'Halo {name},\n\n'
        'Tagihan {package} periode {period} sebesar Rp {amount} sudah TELAT '
        '(batas {late_date}).\n'
        'Segera lakukan pembayaran untuk menghindari isolir layanan.\n\n'
        'Abaikan pesan ini jika sudah membayar.\n\n'
        'DOTAKARO'
    ),
    'isolir_warn': (
        'Halo {name},\n\n'
        'Peringatan: tagihan {package} periode {period} sebesar Rp {amount} '
        'masih belum lunas dan sudah telat.\n'
        'Jika belum dibayar, layanan dapat diisolir. '
        'Transfer ke {dest_bank} {dest_account} a.n. {dest_name}.\n\n'
        'Abaikan pesan ini jika sudah membayar.\n\n'
        'DOTAKARO'
    ),
    'isolated': (
        'Halo {name},\n\n'
        'Layanan internet {package} Anda sudah diisolir karena tagihan '
        'periode {period} sebesar Rp {amount} belum lunas.\n'
        'Bayar ke {dest_bank} {dest_account} a.n. {dest_name}, '
        'lalu kirim foto struk dengan caption: BAYAR {username}. '
        'Informasi: {isolir_url}\n'
        'Layanan dibuka setelah admin pusat verifikasi.\n\n'
        'Abaikan pesan ini jika sudah membayar.\n\n'
        'DOTAKARO'
    ),
    'thanks': (
        'Halo {name},\n\n'
        'Pembayaran tagihan {package} periode {period} sebesar Rp {amount} '
        'sudah kami terima dan diverifikasi. Terima kasih.\n\n'
        'DOTAKARO'
    ),
    'restored': (
        'Halo {name},\n\n'
        'Pembayaran Anda sudah diverifikasi. Layanan internet {package} '
        'yang sempat terisolir SUDAH DIAKTIFKAN KEMBALI.\n\n'
        'Mohon cek koneksi Anda sekarang. Cabut-colok adaptor router, '
        'tunggu 1–2 menit, lalu coba browsing. Jika masih putus, hubungi admin desa.\n\n'
        'DOTAKARO'
    ),
    'test': (
        'Halo {name},\n\n'
        'Ini pesan tes WhatsApp DKT (Fonnte). Jika Anda menerima ini, pengiriman berhasil.\n\n'
        'DOTAKARO'
    ),
}


class IspWhatsappMessage(models.Model):
    _name = 'isp.whatsapp.message'
    _description = 'Log pesan WhatsApp ISP'
    _order = 'id desc'
    _inherit = ['mail.thread']

    name = fields.Char('Referensi', default='New', copy=False, readonly=True)
    partner_id = fields.Many2one('res.partner', string='Pelanggan', required=True, index=True, tracking=True)
    phone = fields.Char('Nomor WA', required=True, index=True)
    body = fields.Text('Isi pesan', required=True)
    template_kind = fields.Selection(
        TEMPLATE_KIND_SELECTION,
        string='Jenis',
        default='invoice',
        required=True,
    )
    provider = fields.Selection(
        [('fonnte', 'Fonnte.id'), ('meta', 'Meta Cloud API')],
        string='Provider',
        default='fonnte',
        required=True,
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('dry_run', 'Dry-run'),
            ('sent', 'Terkirim'),
            ('failed', 'Gagal'),
        ],
        string='Status',
        default='draft',
        required=True,
        tracking=True,
        index=True,
    )
    error = fields.Char('Error')
    dry_run = fields.Boolean('Dry-run', default=True)
    invoice_id = fields.Many2one('account.move', string='Invoice', index=True)
    subscription_id = fields.Many2one('isp.subscription', string='Langganan', index=True)
    external_id = fields.Char('ID eksternal')
    sent_date = fields.Datetime('Waktu kirim')
    response_raw = fields.Text('Respons API', help='Tidak menyimpan token.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('isp.whatsapp.message') or 'WA'
        return super().create(vals_list)

    @api.model
    def get_wa_config(self):
        ICP = self.env['ir.config_parameter'].sudo()
        delay = ICP.get_param('dkt_isp_billing.wa_rate_delay', '0.5')
        try:
            delay = float(delay or 0.5)
        except (TypeError, ValueError):
            delay = 0.5
        return {
            'provider': ICP.get_param('dkt_isp_billing.wa_provider', 'fonnte') or 'fonnte',
            'dry_run': ICP.get_param('dkt_isp_billing.wa_dry_run', 'True') in ('True', 'true', '1'),
            'meta_token': ICP.get_param('dkt_isp_billing.wa_meta_token', '') or '',
            'meta_phone_id': ICP.get_param('dkt_isp_billing.wa_meta_phone_number_id', '') or '',
            'meta_version': ICP.get_param('dkt_isp_billing.wa_meta_api_version', 'v21.0') or 'v21.0',
            'fonnte_token': ICP.get_param('dkt_isp_billing.wa_fonnte_token', '') or '',
            'meta_template_invoice': ICP.get_param('dkt_isp_billing.wa_meta_template_invoice', '') or '',
            'meta_template_due': ICP.get_param('dkt_isp_billing.wa_meta_template_due', '') or '',
            'meta_template_late': ICP.get_param('dkt_isp_billing.wa_meta_template_late', '') or '',
            'meta_lang': ICP.get_param('dkt_isp_billing.wa_meta_template_lang', 'id') or 'id',
            'delay': max(0.0, min(delay, 5.0)),
            'inbound_enabled': ICP.get_param(
                'dkt_isp_billing.wa_inbound_enabled', 'True',
            ) in ('True', 'true', '1'),
            'webhook_secret': ICP.get_param(
                'dkt_isp_billing.wa_fonnte_webhook_secret', '',
            ) or '',
        }

    @api.model
    def has_wa_credentials(self, cfg=None):
        cfg = cfg or self.get_wa_config()
        if cfg['provider'] == 'meta':
            return bool(cfg['meta_token'] and cfg['meta_phone_id'])
        return bool(cfg['fonnte_token'])

    @api.model
    def normalize_phone(self, phone):
        if not phone:
            return False
        digits = re.sub(r'\D', '', str(phone))
        if not digits:
            return False
        if digits.startswith('0'):
            digits = '62' + digits[1:]
        elif digits.startswith('8'):
            digits = '62' + digits
        elif digits.startswith('620'):
            digits = '62' + digits[3:]
        return digits

    @api.model
    def partner_phone(self, partner):
        return self.normalize_phone(
            getattr(partner, 'phone_wa', False) or partner.phone
        )

    @api.model
    def get_template_context(self, kind, partner, invoice=None, subscription=None):
        """Variabel template: nama, paket, periode, nominal, jatuh tempo."""
        invoice = invoice if invoice else self.env['account.move']
        if not subscription and partner:
            subscription = partner.subscription_ids[:1]
        subscription = subscription if subscription else self.env['isp.subscription']
        amount = 0.0
        due = '-'
        period = '-'
        late = 'tanggal 21'
        ICP = self.env['ir.config_parameter'].sudo()
        late_day = int(ICP.get_param('dkt_isp_billing.late_day', '21') or 21)
        if invoice:
            amount = invoice.amount_residual or invoice.amount_total
            due = invoice.invoice_date_due and invoice.invoice_date_due.strftime('%d/%m/%Y') or '-'
            if invoice.invoice_date:
                period = self.env['isp.subscription']._period_label(invoice.invoice_date)
                try:
                    late_dt = invoice.invoice_date.replace(day=late_day)
                except ValueError:
                    late_dt = invoice.invoice_date.replace(day=min(late_day, 28))
                late = late_dt.strftime('%d/%m/%Y')
        elif partner and partner.total_outstanding:
            amount = partner.total_outstanding
            late = 'tanggal %s' % late_day
        else:
            late = 'tanggal %s' % late_day
        package = subscription.package_id.display_name if subscription else '-'
        username = '-'
        if partner and getattr(partner, 'cpe_ids', False) and partner.cpe_ids:
            username = partner.cpe_ids[0].pppoe_username or '-'
        return {
            'name': (partner.name if partner else '') or 'Pelanggan',
            'package': package or '-',
            'period': period or '-',
            'amount': self._format_idr(amount),
            'due_date': due,
            'late_date': late,
            'username': username,
            'dest_bank': ICP.get_param('dkt_isp_billing.dest_bank_name', 'BANK BRI') or 'BANK BRI',
            'dest_account': ICP.get_param('dkt_isp_billing.dest_account_number', '') or '-',
            'dest_name': ICP.get_param('dkt_isp_billing.dest_account_name', '') or '-',
            'area': partner.area_id.name if partner and partner.area_id else '-',
            'kind': kind or 'invoice',
            'isolir_url': self.env['isp.radius'].landing_url(),
        }

    @api.model
    def render_template(self, kind, partner, invoice=None, subscription=None, body=None):
        Template = self.env['isp.whatsapp.template']
        if hasattr(Template, 'ensure_defaults'):
            Template.ensure_defaults()
        text = (body or '').strip() or Template.get_body(kind)
        ctx = self.get_template_context(kind, partner, invoice, subscription)

        class _Safe(dict):
            def __missing__(self, key):
                return '{%s}' % key

        return text.format_map(_Safe(ctx))

    @api.model
    def _format_idr(self, amount):
        try:
            return f'{int(round(float(amount or 0))):,}'.replace(',', '.')
        except (TypeError, ValueError):
            return '0'

    @api.model
    def queue_and_send(
        self, partners, template_kind='invoice', force_dry_run=None, invoice=None,
        body_override=None, phone_override=None,
    ):
        """Buat log lalu kirim satu per satu. Gagal tidak menghentikan batch."""
        cfg = self.get_wa_config()
        if force_dry_run is True:
            dry = True
        elif force_dry_run is False:
            dry = not self.has_wa_credentials(cfg)
        else:
            dry = cfg['dry_run'] or not self.has_wa_credentials(cfg)
        created = self.browse()
        sent = 0
        failed = 0
        dry_count = 0
        for index, partner in enumerate(partners):
            phone = self.normalize_phone(phone_override) or self.partner_phone(partner)
            inv = invoice if invoice else self._partner_open_invoice(partner)
            subscription = (
                inv.subscription_id if inv and inv.subscription_id
                else partner.subscription_ids[:1]
            )
            if not phone:
                rec = self.create({
                    'partner_id': partner.id,
                    'phone': '-',
                    'body': self.render_template(
                        template_kind, partner, inv, subscription, body=body_override,
                    ),
                    'template_kind': template_kind,
                    'provider': cfg['provider'],
                    'state': 'failed',
                    'error': 'Nomor telepon kosong',
                    'dry_run': dry,
                    'invoice_id': inv.id if inv else False,
                    'subscription_id': subscription.id if subscription else False,
                })
                created |= rec
                failed += 1
                continue
            body = self.render_template(
                template_kind, partner, inv, subscription, body=body_override,
            )
            rec = self.create({
                'partner_id': partner.id,
                'phone': phone,
                'body': body,
                'template_kind': template_kind,
                'provider': cfg['provider'],
                'state': 'draft',
                'dry_run': dry,
                'invoice_id': inv.id if inv else False,
                'subscription_id': subscription.id if subscription else False,
            })
            created |= rec
            try:
                rec._send_now(cfg=cfg, dry_run=dry)
            except Exception as exc:
                _logger.exception('WA gagal untuk partner %s', partner.id)
                rec.write({
                    'state': 'failed',
                    'error': str(exc)[:512],
                })
            if rec.state == 'sent':
                sent += 1
            elif rec.state == 'dry_run':
                dry_count += 1
            else:
                failed += 1
            if index < len(partners) - 1:
                self._rate_delay(cfg['delay'])
        return {
            'created': created,
            'sent': sent,
            'failed': failed,
            'dry_run': dry_count,
            'mode': 'dry_run' if dry else 'live',
        }

    @api.model
    def _partner_open_invoice(self, partner):
        return self.env['account.move'].search([
            ('partner_id', '=', partner.id),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial', 'in_payment')),
            ('isp_invoice_kind', '!=', 'installation'),
        ], order='invoice_date desc', limit=1)

    def _send_now(self, cfg=None, dry_run=None):
        self.ensure_one()
        cfg = cfg or self.get_wa_config()
        if dry_run is None:
            dry_run = cfg['dry_run'] or not self.has_wa_credentials(cfg)
        if dry_run:
            self.write({
                'state': 'dry_run',
                'dry_run': True,
                'error': 'Dry-run: tidak dikirim ke pelanggan.',
                'sent_date': fields.Datetime.now(),
                'provider': cfg['provider'],
            })
            _logger.info(
                'WA dry-run partner=%s phone=%s provider=%s (tidak dikirim)',
                self.partner_id.id, self.phone, cfg['provider'],
            )
            return True
        if not self.has_wa_credentials(cfg):
            self.write({
                'state': 'dry_run',
                'dry_run': True,
                'error': 'Token kosong: tetap dry-run, tidak dikirim.',
                'sent_date': fields.Datetime.now(),
            })
            return True
        try:
            if cfg['provider'] == 'meta':
                status, body, ext_id = self._send_meta(cfg)
            else:
                status, body, ext_id = self._send_fonnte(cfg)
            ok = 200 <= int(status or 0) < 300
            self.write({
                'state': 'sent' if ok else 'failed',
                'dry_run': False,
                'external_id': ext_id or False,
                'response_raw': (body or '')[:4000],
                'error': False if ok else (body or 'Gagal kirim')[:512],
                'sent_date': fields.Datetime.now(),
                'provider': cfg['provider'],
            })
            return ok
        except Exception as exc:
            self.write({
                'state': 'failed',
                'error': str(exc)[:512],
                'sent_date': fields.Datetime.now(),
            })
            _logger.warning('WA kirim gagal %s: %s', self.name, exc)
            return False

    def _fonnte_payload(self):
        self.ensure_one()
        return {
            'target': self.phone,
            'message': self.body,
            'countryCode': '62',
        }

    def _meta_template_name(self, cfg):
        self.ensure_one()
        mapping = {
            'invoice': cfg.get('meta_template_invoice') or '',
            'due': cfg.get('meta_template_due') or '',
            'due_soon': cfg.get('meta_template_due_soon') or '',
            'late': cfg.get('meta_template_late') or '',
            'isolir_warn': cfg.get('meta_template_isolir_warn') or '',
            'isolated': cfg.get('meta_template_isolated') or '',
            'thanks': cfg.get('meta_template_thanks') or '',
            'restored': cfg.get('meta_template_restored') or '',
        }
        return (mapping.get(self.template_kind) or '').strip()

    def _meta_payload(self, cfg):
        """Fonnte: teks bebas. Meta production: template Approved, selain itu teks 24 jam."""
        self.ensure_one()
        template_name = self._meta_template_name(cfg)
        if template_name:
            ctx = self.get_template_context(
                self.template_kind, self.partner_id, self.invoice_id, self.subscription_id,
            )
            return {
                'messaging_product': 'whatsapp',
                'recipient_type': 'individual',
                'to': self.phone,
                'type': 'template',
                'template': {
                    'name': template_name,
                    'language': {'code': cfg.get('meta_lang') or 'id'},
                    'components': [{
                        'type': 'body',
                        'parameters': [
                            {'type': 'text', 'text': str(ctx[key])}
                            for key in TEMPLATE_VAR_ORDER
                        ],
                    }],
                },
            }
        return {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': self.phone,
            'type': 'text',
            'text': {'preview_url': False, 'body': self.body},
        }

    def _send_fonnte(self, cfg):
        self.ensure_one()
        payload = urllib.parse.urlencode(self._fonnte_payload()).encode()
        headers = {
            'Authorization': cfg['fonnte_token'],
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        status, body = self._http_post(FONNTE_URL, headers, payload)
        ext_id = ''
        try:
            data = json.loads(body or '{}')
            ext_id = str(data.get('id') or data.get('requestid') or '')
            if data.get('status') in (False, 'false', 'False') and status == 200:
                status = 400
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        return status, body, ext_id

    def _send_meta(self, cfg):
        self.ensure_one()
        url = META_URL % (cfg['meta_version'], cfg['meta_phone_id'])
        payload = json.dumps(self._meta_payload(cfg)).encode()
        headers = {
            'Authorization': 'Bearer %s' % cfg['meta_token'],
            'Content-Type': 'application/json',
        }
        status, body = self._http_post(url, headers, payload)
        ext_id = ''
        try:
            data = json.loads(body or '{}')
            messages = data.get('messages') or []
            if messages:
                ext_id = str(messages[0].get('id') or '')
            if data.get('error') and status == 200:
                status = 400
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        return status, body, ext_id

    def _http_post(self, url, headers, payload, timeout=30):
        request = urllib.request.Request(url, data=payload, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode('utf-8', errors='replace')
        except urllib.error.URLError as exc:
            raise UserError('Gagal menghubungi API WhatsApp: %s' % exc.reason)

    def _rate_delay(self, seconds):
        if not seconds or seconds <= 0:
            return
        if self._skip_external_http():
            return
        time.sleep(float(seconds))

    @api.model
    def get_webhook_info(self, public_base=None):
        """URL webhook Fonnte. Token API tidak ikut."""
        ICP = self.env['ir.config_parameter'].sudo()
        public = (public_base if public_base is not None else '') or (
            ICP.get_param('dkt_isp_billing.wa_public_base_url', '') or ''
        )
        public = public.strip()
        base = public or (ICP.get_param('web.base.url', '') or '').strip()
        base = base.rstrip('/')
        host = (urllib.parse.urlparse(base).hostname or '').lower()
        is_public = bool(host) and host not in (
            'localhost', '127.0.0.1', '0.0.0.0', '::1',
        ) and not host.endswith('.local')
        return {
            'url': '%s/isp/whatsapp/fonnte' % base if base else '/isp/whatsapp/fonnte',
            'path': '/isp/whatsapp/fonnte',
            'base': base,
            'is_public': is_public,
        }

    @api.model
    def parse_bayar_caption(self, caption):
        """Ambil token dari caption ``BAYAR username`` atau ``BAYAR 08…``.

        Token HP murni (digit + minus/spasi/titik) di-pack jadi digit.
        Username + HP ber-minus (``user-0812-1238-4948``) di-pack
        suffix-nya saja. Username tanpa pemisah di nomor tetap utuh.
        """
        text = (caption or '').strip()
        if not text:
            return False
        phone_match = BAYAR_PHONE_RE.search(text)
        if phone_match:
            packed = pack_id_mobile_token(phone_match.group(1))
            if packed:
                return packed
        user_phone = BAYAR_USERNAME_PHONE_RE.search(text)
        if user_phone:
            packed_user = pack_username_phone_suffix(user_phone.group(1))
            if packed_user:
                return packed_user
        match = BAYAR_TOKEN_RE.search(text)
        token = (match.group(1) or '').strip() if match else ''
        if not token:
            return False
        return (
            pack_id_mobile_token(token)
            or pack_username_phone_suffix(token)
            or token
        )

    @api.model
    def find_partner_by_username(self, username):
        """Cocokkan satu pelanggan dari username PPPoE. Ambigu → kosong."""
        token = (username or '').strip()
        if not token or len(token) < 2:
            return self.env['res.partner']
        cpes = self.env['isp.cpe'].sudo().search([
            ('pppoe_username', '=ilike', token),
        ], limit=20)
        if not cpes:
            cpes = self._cpes_by_packed_username(token)
        partners = cpes.mapped('partner_id').filtered(lambda p: p)
        if not partners:
            return self.env['res.partner']
        customers = partners.filtered(lambda p: p.customer_rank > 0) or partners
        if len(customers) != 1:
            return self.env['res.partner']
        return customers

    @api.model
    def _cpes_by_packed_username(self, token):
        """Cari CPE jika caption pakai minus di nomor, secret sudah ter-pack."""
        packed = pack_username_phone_suffix(token)
        if not packed:
            return self.env['isp.cpe']
        prefix = packed.rsplit('-', 1)[0]
        if not prefix or len(prefix) < 2:
            return self.env['isp.cpe']
        candidates = self.env['isp.cpe'].sudo().search([
            ('pppoe_username', '=ilike', prefix + '-%'),
        ], limit=80)
        return candidates.filtered(
            lambda c: username_phone_match(token, c.pppoe_username)
            or username_phone_match(packed, c.pppoe_username)
        )

    @api.model
    def find_partner_by_bayar_token(self, token):
        """Token format BAYAR: nomor HP dulu jika terlihat nomor, lalu username."""
        token = (token or '').strip()
        if not token:
            return self.env['res.partner'], False
        from .isp_phone import looks_like_id_mobile

        packed = pack_id_mobile_token(token)
        if packed:
            partner = self.find_partner_by_phone(packed)
            return partner, 'format' if partner else False
        partner = self.find_partner_by_username(token)
        if partner:
            return partner, 'format'
        packed_user = pack_username_phone_suffix(token)
        if packed_user and packed_user != token:
            partner = self.find_partner_by_username(packed_user)
            if partner:
                return partner, 'format'
        if looks_like_id_mobile(token):
            partner = self.find_partner_by_phone(token)
            return partner, 'format' if partner else False
        return self.env['res.partner'], False

    @api.model
    def find_partner_by_phone(self, phone):
        """Cocokkan partner.phone / phone_wa setelah normalisasi 62…."""
        wa = self.normalize_phone(phone)
        if not wa:
            return self.env['res.partner']
        suffix = wa[-8:]
        partners = self.env['res.partner'].sudo().search([
            '|',
            ('phone_wa', 'ilike', suffix),
            ('phone', 'ilike', suffix),
        ], limit=80)
        matches = partners.filtered(
            lambda p: phones_equivalent(p.phone_wa or p.phone, wa)
        )
        if not matches:
            return self.env['res.partner']
        company = self.env.company.partner_id
        customers = matches.filtered(
            lambda p: p.customer_rank > 0
            and not p.is_company
            and p.id != company.id
        )
        return customers[:1]

    @api.model
    def _ensure_test_partner(self):
        phone = TEST_WA_PHONE
        partner = self.find_partner_by_phone(phone)
        if partner:
            if not (partner.phone_wa or '').strip():
                partner.sudo().write({'phone_wa': phone})
            return partner
        return self.env['res.partner'].sudo().create({
            'name': 'dkt-uat',
            'phone': '08116343031',
            'phone_wa': phone,
            'customer_rank': 1,
            'notes': 'User UAT unggah bukti WhatsApp. Nomor tes 08116343031.',
        })

    @api.model
    def test_send(self, phone=None, template_kind='test'):
        """Kirim 1 pesan nyata ke nomor tes saja. Tidak blast."""
        allowed = self.normalize_phone(TEST_WA_PHONE)
        target = self.normalize_phone(phone or allowed)
        if target != allowed:
            raise UserError('Tes WhatsApp hanya boleh ke nomor tes yang diizinkan.')
        partner = self._ensure_test_partner()
        kind = template_kind if template_kind in WA_TEMPLATES else 'test'
        result = self.queue_and_send(partner, template_kind=kind, force_dry_run=False)
        rec = result['created'][:1]
        _logger.info(
            'WA tes kirim phone=%s state=%s ext=%s (token tidak dilog)',
            mask_phone(target), rec.state if rec else '-', rec.external_id if rec else '-',
        )
        return {
            'ok': bool(rec) and rec.state == 'sent',
            'state': rec.state if rec else 'failed',
            'external_id': rec.external_id if rec else False,
            'error': rec.error if rec else 'Tidak ada log',
            'phone': target,
            'body': rec.body if rec else '',
            'provider': rec.provider if rec else '',
            'name': rec.name if rec else '',
        }

    @api.model
    def _inbound_accepts_billing(self, sender, caption):
        """Nomor inbox dipakai campur: jangan telan semua gambar.

        Hanya caption BAYAR username/HP yang masuk antrian bukti.
        Pengirim terdaftar tanpa format juga diabaikan.
        """
        return bool(self.parse_bayar_caption(caption))

    @api.model
    def is_group_target(self, target):
        """ID grup WhatsApp Fonnte, bukan nomor HP."""
        raw = (target or '').strip()
        if not raw:
            return False
        if '@g.us' in raw.lower():
            return True
        digits = re.sub(r'\D', '', raw)
        return digits.startswith('120363') and len(digits) >= 15

    @api.model
    def resolve_ack_target(self, phone):
        """Grup: biarkan xxx@g.us. Chat 1:1: normalisasi 62…"""
        raw = (phone or '').strip()
        if self.is_group_target(raw):
            return raw
        return self.normalize_phone(raw)

    @api.model
    def _skip_external_http(self):
        """Odoo 19 produksi tidak punya Registry.in_test_mode."""
        if current_test or odoo_config.get('test_enable'):
            return True
        checker = getattr(self.env.registry, 'in_test_mode', None)
        if callable(checker):
            return bool(checker())
        return False

    @api.model
    def _inbound_ack_payload(self, target, message, inbox_id=None):
        """Grup: tanpa inboxid. Reply-ke-pesan di grup sering antre sukses tapi tidak muncul."""
        payload = {
            'target': target,
            'message': message or INBOUND_ACK,
        }
        if self.is_group_target(target):
            payload['countryCode'] = '0'
            return payload
        payload['countryCode'] = '62'
        if inbox_id:
            payload['inboxid'] = str(inbox_id)
        return payload

    def send_inbound_ack(self, phone, inbox_id=None, message=None):
        """Balas ke pengirim 1:1 atau grup. Tanpa data invoice. Abaikan dry-run outbound."""
        target = self.resolve_ack_target(phone)
        if not target:
            return False
        if self._skip_external_http():
            return True
        cfg = self.get_wa_config()
        if not cfg.get('fonnte_token'):
            _logger.warning('WA inbound ack dilewati: token kosong.')
            return False
        payload = self._inbound_ack_payload(target, message, inbox_id)
        headers = {
            'Authorization': cfg['fonnte_token'],
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        try:
            status, body = self._http_post(
                FONNTE_URL, headers, urllib.parse.urlencode(payload).encode(),
            )
        except Exception as exc:
            _logger.warning('WA inbound ack gagal %s: %s', mask_phone(target), exc)
            return False
        ok = 200 <= int(status or 0) < 300
        try:
            data = json.loads(body or '{}')
            if data.get('status') in (False, 'false', 'False'):
                ok = False
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        _logger.info(
            'WA inbound ack target=%s http=%s ok=%s body=%s',
            target if self.is_group_target(target) else mask_phone(target),
            status, ok, (body or '')[:180],
        )
        if not ok:
            _logger.warning('WA inbound ack gagal body=%s', (body or '')[:180])
        return ok

    @api.model
    def _log_inbound_summary(self, payload, result):
        """Log ringkas webhook. Jangan simpan token/url."""
        payload = payload or {}
        result = result or {}
        sender = str(payload.get('sender') or '')[:80]
        member = mask_phone(payload.get('member')) if payload.get('member') else '-'
        msg = str(payload.get('message') or payload.get('text') or '')[:60]
        keys = ','.join(sorted(str(k) for k in payload.keys()))
        _logger.info(
            'WA inbound ringkas keys=%s sender=%s member=%s msg=%s ignored=%s replied=%s help=%s',
            keys, sender, member, msg,
            result.get('ignored'), result.get('replied'), result.get('help'),
        )
        try:
            self.env['ir.config_parameter'].sudo().set_param(
                'dkt_isp_billing.wa_last_inbound',
                json.dumps({
                    'keys': sorted(str(k) for k in payload.keys()),
                    'sender': sender,
                    'member': member,
                    'name': str(payload.get('name') or '')[:40],
                    'message': msg,
                    'ignored': result.get('ignored'),
                    'replied': result.get('replied'),
                    'help': result.get('help'),
                }, ensure_ascii=False),
            )
        except Exception:
            pass

    @api.model
    def process_fonnte_inbound(self, payload):
        """Terima webhook Fonnte: gambar jadi antrian bukti, bukan lunas."""
        payload = payload or {}
        cfg = self.get_wa_config()
        if not cfg.get('inbound_enabled', True):
            return {'ok': True, 'ignored': 'inbound_disabled'}
        expected = (cfg.get('webhook_secret') or '').strip()
        if expected:
            got = str(
                payload.get('secret')
                or payload.get('secretKey')
                or payload.get('secret_key')
                or ''
            )
            if got != expected:
                _logger.warning('WA inbound ditolak: secret webhook tidak cocok.')
                return {'ok': False, 'error': 'secret'}
        if payload.get('member') or self.is_group_target(payload.get('sender')):
            result = self.env['isp.wa.registration'].sudo().process_group_inbound(payload)
            self._log_inbound_summary(payload, result)
            return result
        register = self.env['isp.wa.registration'].sudo().process_direct_inbound(payload)
        if register.get('handled'):
            self._log_inbound_summary(payload, register)
            return register
        sender = self.normalize_phone(payload.get('sender'))
        if not sender:
            return {'ok': True, 'ignored': 'no_sender'}
        caption = (payload.get('message') or '')[:200]
        url = (payload.get('url') or '').strip()
        ext = str(payload.get('extension') or '').lower().lstrip('.')
        filename = (payload.get('filename') or '').strip() or ('bukti-wa.%s' % (ext or 'jpg'))
        if not url or ext not in WA_FILE_EXT:
            if self.parse_bayar_caption(caption):
                self.send_inbound_ack(sender, message=INBOUND_HINT_BAYAR)
                return {'ok': True, 'ignored': 'bayar_no_image', 'replied': True}
            return {'ok': True, 'ignored': 'not_image'}
        if not self._inbound_accepts_billing(sender, caption):
            _logger.info(
                'WA inbound diabaikan (bukan bukti): sender=%s',
                mask_phone(sender),
            )
            return {'ok': True, 'ignored': 'not_billing', 'replied': False}
        try:
            self._check_wa_inbound_rate(sender)
        except UserError:
            _logger.warning('WA inbound rate-limit %s', mask_phone(sender))
            return {'ok': True, 'ignored': 'rate_limit'}
        raw = self._download_https_file(url)
        inbox_id = payload.get('inboxid') or payload.get('inbox_id')
        sender_name = (payload.get('name') or '')[:80]
        Proof = self.env['isp.payment.proof'].sudo()
        if not raw:
            proof = Proof.create_from_whatsapp({
                'public_phone': sender,
                'public_name': sender_name,
                'caption': caption,
                'inbound_note': 'Gagal unduh lampiran Fonnte. Pengirim %s inbox=%s' % (
                    sender, inbox_id or '-',
                ),
            })
            if not proof:
                return {'ok': True, 'ignored': 'not_billing', 'download': False}
            return {
                'ok': True,
                'proof_name': proof.name,
                'matched': bool(proof.partner_id),
                'replied': False,
                'download': False,
            }
        image_b64 = base64.b64encode(raw)
        note_parts = ['WA %s' % sender]
        if inbox_id:
            note_parts.append('inbox=%s' % inbox_id)
        if caption:
            note_parts.append(caption)
        proof = Proof.create_from_whatsapp({
            'public_phone': sender,
            'public_name': sender_name,
            'image': image_b64,
            'image_filename': filename,
            'inbound_note': ' '.join(note_parts)[:500],
            'caption': caption,
        })
        if not proof:
            return {'ok': True, 'ignored': 'not_billing', 'download': True}
        replied = self.send_inbound_ack(sender, inbox_id=inbox_id)
        _logger.info(
            'WA inbound proof=%s matched=%s replied=%s sender=%s',
            proof.name, bool(proof.partner_id), replied, mask_phone(sender),
        )
        return {
            'ok': True,
            'proof_name': proof.name,
            'matched': bool(proof.partner_id),
            'replied': replied,
            'download': True,
        }

    @api.model
    def _check_wa_inbound_rate(self, phone):
        since = fields.Datetime.now() - timedelta(minutes=WA_INBOUND_MINUTES)
        count = self.env['isp.payment.proof'].sudo().search_count([
            ('source', '=', 'whatsapp'),
            ('public_phone', '=', phone),
            ('create_date', '>=', since),
        ])
        if count >= WA_INBOUND_RATE:
            raise UserError('Terlalu banyak unggahan WhatsApp.')
        return True

    @api.model
    def _is_safe_https_url(self, url):
        parsed = urllib.parse.urlparse(url or '')
        if parsed.scheme != 'https' or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        if host in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
            return False
        try:
            infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return False
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except (ValueError, TypeError):
                return False
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True

    @api.model
    def _download_https_file(self, url):
        if not self._is_safe_https_url(url):
            _logger.warning('WA inbound URL lampiran ditolak.')
            return b''
        request = urllib.request.Request(url, method='GET')
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                chunks = []
                total = 0
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > WA_INBOUND_MAX_BYTES:
                        _logger.warning('WA inbound lampiran melebihi 10MB.')
                        return b''
                    chunks.append(chunk)
                return b''.join(chunks)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            _logger.warning('WA inbound unduh lampiran gagal: %s', exc)
            return b''
