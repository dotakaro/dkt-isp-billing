from odoo import models, fields, api
from odoo.exceptions import ValidationError
import logging

from .isp_phone import (
    mask_username,
    parse_phone_from_username,
    phones_equivalent,
)

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Fields untuk ISP
    identity_number = fields.Char('Nomor Identitas')
    isp_ktp_image = fields.Binary('Foto KTP', attachment=True, copy=False)
    isp_house_image = fields.Binary('Foto rumah', attachment=True, copy=False)
    isp_latitude = fields.Float('Latitude', digits=(10, 7), copy=False)
    isp_longitude = fields.Float('Longitude', digits=(10, 7), copy=False)
    isp_evidence_complete = fields.Boolean(
        'Eviden lengkap',
        compute='_compute_isp_evidence_complete',
        store=True,
        help='KTP + foto rumah + koordinat. Wajib untuk pasang baru via bot.',
    )
    isp_registered_by_name = fields.Char(
        'Didaftarkan oleh (nama WA)', copy=False, tracking=True,
    )
    isp_registered_by_phone = fields.Char(
        'Didaftarkan oleh (HP)', copy=False, tracking=True, index=True,
    )
    isp_registered_by_user_id = fields.Many2one(
        'res.users', string='Didaftarkan oleh (user)', copy=False, tracking=True,
    )
    isp_wa_registration_id = fields.Many2one(
        'isp.wa.registration', string='Antrian pendaftaran WA', copy=False,
    )
    phone_wa = fields.Char(
        'WhatsApp (62…)',
        tracking=True,
        help='Nomor WhatsApp dinormalisasi 62…. Diisi parser username PPPoE atau manual.',
    )
    isp_phone_from_secret = fields.Boolean(
        'Nomor dari secret',
        default=False,
        copy=False,
        help='True jika phone diisi parser username PPPoE. Nomor manual berbeda tidak ditimpa.',
    )
    area_id = fields.Many2one(
        'isp.area', string='Area / Desa', tracking=True, index=True,
        help='Menentukan router PPPoE dan harga paket.',
    )
    cpe_ids = fields.One2many('isp.cpe', 'partner_id', string='CPE')
    cpe_count = fields.Integer('Jumlah CPE', compute='_compute_cpe_count')
    active_cpe_count = fields.Integer('CPE Aktif', compute='_compute_cpe_count')
    subscription_ids = fields.One2many('isp.subscription', 'partner_id', string='Subscription')
    subscription_count = fields.Integer('Jumlah Subscription', compute='_compute_subscription_count')
    active_subscription_count = fields.Integer('Subscription Aktif', compute='_compute_subscription_count')
    invoice_ids = fields.One2many('account.move', 'partner_id', string='Invoice', domain=[('move_type', '=', 'out_invoice')])
    invoice_count = fields.Integer('Jumlah Invoice', compute='_compute_invoice_count')
    invoice_draft_count = fields.Integer('Invoice Draft', compute='_compute_invoice_count')
    total_outstanding = fields.Monetary('Total Outstanding', compute='_compute_total_outstanding')
    emergency_contact = fields.Char('Kontak Darurat')
    emergency_phone = fields.Char('Telepon Darurat')
    notes = fields.Text('Catatan')
    is_adopted_secret = fields.Boolean('Is Adopted Secret', default=False, help='Menandakan bahwa secret/user ini diadopsi dari Mikrotik yang sudah ada')
    isp_notif_prepared = fields.Boolean(
        'Antrian pemberitahuan',
        default=False,
        help='Ditandai wizard siapkan pemberitahuan. Belum dikirim.',
    )
    isp_notif_prepared_date = fields.Datetime('Tanggal antrian pemberitahuan')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Aktif'),
        ('inactive', 'Non-Aktif'),
        ('isolated', 'Terisolir'),
        ('terminated', 'Terminasi')
    ], string='Status', default='draft', tracking=True)

    @api.depends('isp_ktp_image', 'isp_house_image', 'isp_latitude', 'isp_longitude')
    def _compute_isp_evidence_complete(self):
        for rec in self:
            rec.isp_evidence_complete = bool(
                rec.isp_ktp_image
                and rec.isp_house_image
                and rec.isp_latitude
                and rec.isp_longitude
            )

    @api.depends('cpe_ids', 'cpe_ids.state')
    def _compute_cpe_count(self):
        for record in self:
            record.cpe_count = len(record.cpe_ids)
            record.active_cpe_count = len(record.cpe_ids.filtered(lambda c: c.state == 'open'))

    @api.depends('subscription_ids', 'subscription_ids.state')
    def _compute_subscription_count(self):
        for record in self:
            record.subscription_count = len(record.subscription_ids)
            record.active_subscription_count = len(record.subscription_ids.filtered(lambda s: s.state == 'open'))

    @api.depends('invoice_ids', 'invoice_ids.state')
    def _compute_invoice_count(self):
        for record in self:
            record.invoice_count = len(record.invoice_ids)
            record.invoice_draft_count = len(record.invoice_ids.filtered(lambda i: i.state == 'draft'))

    @api.depends('invoice_ids')
    def _compute_total_outstanding(self):
        for record in self:
            record.total_outstanding = sum(record.invoice_ids.filtered(lambda i: i.state == 'posted' and i.payment_state != 'paid').mapped('amount_residual'))

    def write(self, vals):
        if (
            'phone' in vals
            and not vals.get('isp_phone_from_secret')
            and not self.env.context.get('isp_parsing_phone')
        ):
            vals = dict(vals)
            vals['isp_phone_from_secret'] = False
        res = super().write(vals)
        evidence_keys = {
            'isp_ktp_image', 'isp_house_image', 'isp_latitude', 'isp_longitude',
        }
        if evidence_keys & set(vals):
            for rec in self:
                if rec.id == 1 or rec == self.env.company.partner_id:
                    continue
                if rec.isp_evidence_complete:
                    revoked = rec.cpe_ids.filtered('isp_activation_revoked')
                    if revoked:
                        revoked._restore_onboarding_access()
        return res

    @api.model
    def parse_phone_from_username(self, username):
        """Parse nomor dari username PPPoE. False jika tidak ada nomor."""
        return parse_phone_from_username(username)

    def _apply_phone_from_secret(self, parsed, overwrite_manual=False):
        """Isi phone/phone_wa dari hasil parse. Tidak menimpa nomor manual berbeda."""
        self.ensure_one()
        if not parsed:
            return 'skip_no_number'
        existing = (self.phone or '').strip()
        if not existing:
            self.with_context(isp_parsing_phone=True).write({
                'phone': parsed['phone'],
                'phone_wa': parsed['phone_wa'],
                'isp_phone_from_secret': True,
            })
            return 'filled'
        if self.isp_phone_from_secret:
            wa_ok = (self.phone_wa or '') == parsed['phone_wa']
            if phones_equivalent(self.phone, parsed['phone']) and wa_ok:
                return 'unchanged'
            if phones_equivalent(self.phone, parsed['phone']) or overwrite_manual:
                self.with_context(isp_parsing_phone=True).write({
                    'phone': parsed['phone'],
                    'phone_wa': parsed['phone_wa'],
                    'isp_phone_from_secret': True,
                })
                return 'updated'
            return 'skip_other_username'
        if phones_equivalent(existing, parsed['phone']):
            vals = {}
            if not (self.phone_wa or '').strip():
                vals['phone_wa'] = parsed['phone_wa']
            if vals:
                self.with_context(isp_parsing_phone=True).write(vals)
            return 'unchanged'
        if overwrite_manual:
            self.with_context(isp_parsing_phone=True).write({
                'phone': parsed['phone'],
                'phone_wa': parsed['phone_wa'],
                'isp_phone_from_secret': True,
            })
            return 'overwritten'
        return 'skip_manual'

    @api.model
    def _backfill_phone_from_pppoe_username(self, partners=None, overwrite_manual=False):
        """Parse unik per username, isi partner.phone sekali. Tidak kirim WA."""
        Cpe = self.env['isp.cpe']
        domain = [
            ('pppoe_username', '!=', False),
            ('partner_id', '!=', False),
        ]
        if partners:
            domain.append(('partner_id', 'in', partners.ids))
        cpes = Cpe.search(domain)
        by_user = {}
        for cpe in cpes:
            key = (cpe.pppoe_username or '').strip().lower()
            if not key:
                continue
            if key not in by_user:
                by_user[key] = {
                    'username': (cpe.pppoe_username or '').strip(),
                    'parsed': parse_phone_from_username(cpe.pppoe_username),
                    'partners': self.env['res.partner'],
                }
            by_user[key]['partners'] |= cpe.partner_id

        stats = {
            'parsed': 0,
            'skipped': 0,
            'filled': 0,
            'updated': 0,
            'unchanged': 0,
            'overwritten': 0,
            'skip_manual': 0,
            'skip_no_number': 0,
            'skip_other_username': 0,
            'partners': 0,
            'skip_examples': [],
        }
        applied = set()
        for info in by_user.values():
            parsed = info['parsed']
            if not parsed:
                stats['skipped'] += 1
                stats['skip_no_number'] += 1
                if len(stats['skip_examples']) < 8:
                    stats['skip_examples'].append(mask_username(info['username']))
                continue
            stats['parsed'] += 1
            for partner in info['partners']:
                if partner.id in applied:
                    continue
                applied.add(partner.id)
                try:
                    result = partner._apply_phone_from_secret(
                        parsed, overwrite_manual=overwrite_manual,
                    )
                except Exception:
                    _logger.exception(
                        'Gagal isi phone dari username %s untuk partner %s',
                        mask_username(info['username']), partner.id,
                    )
                    continue
                stats[result] = stats.get(result, 0) + 1
                stats['partners'] += 1
        _logger.info(
            'Parse nomor dari secret: parsed=%s skipped=%s filled=%s '
            'updated=%s unchanged=%s skip_manual=%s contoh_gagal=%s',
            stats['parsed'], stats['skipped'], stats['filled'],
            stats['updated'], stats['unchanged'], stats['skip_manual'],
            stats['skip_examples'][:3],
        )
        return stats

    def action_parse_phone_from_secret(self):
        """Parse nomor dari username CPE partner yang dipilih."""
        stats = self._backfill_phone_from_pppoe_username(partners=self)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Parse nomor dari secret',
                'message': (
                    'Username ter-parse: %s. Dilewati tanpa nomor: %s. '
                    'Phone diisi: %s. Contoh gagal: %s'
                ) % (
                    stats['parsed'],
                    stats['skipped'],
                    stats['filled'],
                    ', '.join(stats['skip_examples'][:3]) or '-',
                ),
                'type': 'success' if stats['filled'] or stats['parsed'] else 'warning',
                'sticky': False,
            },
        }

    def _check_existing_mikrotik_user(self, username, user_id):
        """
        Mengecek apakah user Mikrotik sudah terkait dengan pelanggan lain
        Returns: customer yang menggunakan secret tersebut atau False
        """
        existing_customer = self.search([
            ('cpe_ids.pppoe_username', '=', username),
            ('id', '!=', self.id)
        ], limit=1)
        return existing_customer

    def action_view_subscriptions(self):
        """Tampilkan subscription pelanggan"""
        self.ensure_one()
        return {
            'name': 'Subscriptions',
            'type': 'ir.actions.act_window',
            'res_model': 'isp.subscription',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id}
        }

    def action_view_invoices(self):
        """Tampilkan invoice pelanggan"""
        self.ensure_one()
        return {
            'name': 'Invoices',
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id), ('move_type', '=', 'out_invoice')],
            'context': {'default_partner_id': self.id, 'default_move_type': 'out_invoice'}
        }

    def action_view_cpe(self):
        """Tampilkan CPE pelanggan"""
        self.ensure_one()
        return {
            'name': 'CPE',
            'type': 'ir.actions.act_window',
            'res_model': 'isp.cpe',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id}
        }

    def action_adopt_secret(self):
        """
        Mengadopsi secret/user yang sudah ada di Mikrotik
        """
        self.ensure_one()
        if not self.cpe_ids:
            raise ValidationError('CPE harus diisi terlebih dahulu!')
            
        cpe = self.cpe_ids[0]
        if not cpe.pppoe_username or not cpe.pppoe_password:
            raise ValidationError('PPPoE Username dan Password harus diisi di CPE!')
            
        if cpe.adopt_mikrotik_secret():
            self.write({
                'state': 'active',
                'is_adopted_secret': True
            })
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sukses',
                    'message': f'Secret/User PPPoE {cpe.pppoe_username} berhasil diadopsi',
                    'type': 'success',
                }
            }
        raise ValidationError(f'Gagal mengadopsi secret {cpe.pppoe_username}')

    def create_mikrotik_user(self, cpe):
        """Buat user di Mikrotik"""
        if not cpe:
            return False, 'CPE tidak ditemukan'
            
        # Cek apakah secret sudah ada
        exists, user_id, error, secret_data = cpe._check_mikrotik_secret()
        _logger.info(f'Checking secret {cpe.pppoe_username}: exists={exists}, user_id={user_id}, error={error}')
        
        if error:
            return False, error
            
        if exists:
            # Coba adopsi secret yang ada
            success, message = cpe.adopt_mikrotik_secret()
            if not success:
                _logger.error(f'Create Mikrotik user error: {message}')
                return False, message
            return True, message
            
        # Buat secret baru
        try:
            # Gunakan konfigurasi Mikrotik dari CPE jika ada
            mikrotik = self.env['isp.mikrotik.config'].require_cpe_router(cpe)
                
            api = mikrotik.get_connection()
            if not api:
                return False, 'Gagal terhubung ke Mikrotik'
                
            secret_api = api.get_resource('/ppp/secret')
            secret_data = {
                'name': cpe.pppoe_username,
                'password': cpe.pppoe_password,
                'service': 'pppoe',
                'profile': (
                    cpe.subscription_id._get_pppoe_profile_name()
                    if cpe.subscription_id else 'default'
                ),
                'comment': mikrotik.format_secret_comment(cpe.partner_id),
            }
            
            secret_api.add(**secret_data)
            return True, 'Secret berhasil dibuat'
        except Exception as e:
            _logger.error(f'Create Mikrotik user error: {str(e)}', exc_info=True)
            return False, str(e)
        finally:
            if api and hasattr(api, 'connection_pool'):
                api.connection_pool.disconnect()

    def action_activate(self):
        """Aktivasi pelanggan"""
        self.ensure_one()
        if self.state == 'draft':
            success, message = self.create_mikrotik_user(self.cpe_ids[0])
            if success:
                self.write({'state': 'active'})
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Sukses',
                        'message': 'Pelanggan berhasil diaktifkan',
                        'type': 'success',
                    }
                }
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Gagal',
                        'message': message,
                        'type': 'danger',
                    }
                }

    def action_isolate(self):
        self.ensure_one()
        if self.state != 'active':
            return True
        errors = []
        for sub in self.subscription_ids.filtered(lambda s: s.state == 'open'):
            ok, err = sub.isolate_safe()
            if not ok:
                errors.append(err or sub.display_name)
        if errors:
            raise ValidationError('Gagal isolir: %s' % '; '.join(errors[:6]))
        if self.subscription_ids.filtered(lambda s: s.state == 'isolated'):
            self.write({'state': 'isolated'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'Pelanggan berhasil diisolir',
                'type': 'success',
            },
        }

    def action_enable(self):
        self.ensure_one()
        if self.state != 'isolated':
            return True
        errors = []
        for sub in self.subscription_ids.filtered(lambda s: s.state == 'isolated'):
            ok, err = sub.enable_safe()
            if not ok:
                errors.append(err or sub.display_name)
        if errors:
            raise ValidationError('Gagal buka isolir: %s' % '; '.join(errors[:6]))
        if not self.subscription_ids.filtered(lambda s: s.state == 'isolated'):
            self.write({'state': 'active'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sukses',
                'message': 'Pelanggan berhasil dibuka isolirnya',
                'type': 'success',
            },
        }

    @api.model
    def _ensure_dkt_uat(self):
        """User + tagihan UAT dkt-uat / 08116343031 untuk uji unggah bukti."""
        phone = '08116343031'
        wa = '628116343031'
        Partner = self.sudo()
        company_partner = self.env.company.partner_id
        domain = [
            ('is_company', '=', False),
            ('id', '!=', company_partner.id),
            '|', '|',
            ('phone_wa', '=', wa),
            ('phone', 'in', [phone, wa]),
            ('name', 'in', ['dkt-uat', 'User Tes WhatsApp']),
        ]
        partner = Partner.search(domain, order='id desc', limit=1)
        if partner.is_company or partner.id == company_partner.id:
            partner = Partner.browse()
        vals = {
            'name': 'dkt-uat',
            'phone': phone,
            'phone_wa': wa,
            'customer_rank': 1,
            'state': 'active',
            'notes': 'User UAT unggah bukti WhatsApp. Nomor tes 08116343031.',
            'isp_phone_from_secret': True,
        }
        if partner:
            partner.write(vals)
        else:
            partner = Partner.create(vals)

        package = self.env['isp.package'].sudo().search([
            '|', ('code', '=', 'PAKET_200'), ('price', '=', 200000),
        ], limit=1)
        if not package:
            raise ValidationError('Paket 200 belum ada. Tidak bisa membuat UAT.')

        cpe = partner.cpe_ids[:1]
        cpe_vals = {
            'name': 'CPE-dkt-uat',
            'partner_id': partner.id,
            'state': 'open',
        }
        if not cpe or not cpe.mikrotik_config_id:
            cpe_vals['pppoe_username'] = 'dkt-uat-08116343031'
            cpe_vals['pppoe_password'] = 'uat-dkt'
        if cpe:
            cpe.sudo().write(cpe_vals)
        else:
            cpe = self.env['isp.cpe'].sudo().create(cpe_vals)

        sub = partner.subscription_ids.filtered(
            lambda s: s.state in ('open', 'draft', 'isolated')
        )[:1]
        sub_vals = {
            'partner_id': partner.id,
            'cpe_id': cpe.id,
            'package_id': package.id,
            'state': 'open',
            'due_day': 1,
            'billing_review_needed': False,
        }
        if not sub:
            sub = self.env['isp.subscription'].sudo().create(sub_vals)
        else:
            sub.sudo().write(sub_vals)
        sub.invalidate_recordset(['amount', 'final_amount', 'is_special_treatment'])

        unpaid = sub.invoice_ids.filtered(
            lambda m: m.move_type == 'out_invoice'
            and m.state in ('draft', 'posted')
            and m.payment_state != 'paid'
        )[:1]
        generated = False
        if unpaid:
            move = unpaid
        else:
            generated = sub._generate_invoice_one()
            if not isinstance(generated, dict):
                move = generated
            elif generated.get('invoice') and generated['invoice'].payment_state != 'paid':
                move = generated['invoice']
            elif generated.get('invoice') and generated['invoice'].payment_state == 'paid':
                move = self.env['account.move'].sudo().create(sub._prepare_invoice_values())
            else:
                move = False
        if not move:
            raise ValidationError('Gagal membuat tagihan UAT: %s' % (
                generated.get('reason') if isinstance(generated, dict) else 'kosong',
            ))
        if move.state == 'draft':
            if not sub._billing_apply_tax():
                move._isp_clear_taxes()
            move.action_post()
        _logger.info(
            'UAT dkt-uat siap partner=%s invoice=%s residual=%s',
            partner.id, move.name, move.amount_residual,
        )
        return {
            'partner_id': partner.id,
            'partner': partner.name,
            'phone': partner.phone,
            'phone_wa': partner.phone_wa,
            'cpe': cpe.pppoe_username,
            'package': package.display_name,
            'subscription_id': sub.id,
            'invoice': move.name,
            'invoice_id': move.id,
            'amount_residual': move.amount_residual,
            'payment_state': move.payment_state,
        } 