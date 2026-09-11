from odoo import models, fields, api
from odoo.exceptions import ValidationError
import random
import string
import logging

_logger = logging.getLogger(__name__)

class ISPInstallationWizard(models.TransientModel):
    _name = 'isp.installation.wizard'
    _description = 'Wizard Pasang Baru'
    
    # Step indicator
    state = fields.Selection([
        ('customer', 'Data Pelanggan'),
        ('cpe', 'Data Perangkat'),
        ('subscription', 'Data Layanan'),
        ('fee', 'Biaya Pemasangan'),
        ('summary', 'Ringkasan'),
    ], default='customer', string='Tahapan')
    
    # Customer data
    partner_id = fields.Many2one('res.partner', string='Pelanggan Existing')
    is_new_customer = fields.Boolean('Pelanggan Baru', default=True)
    name = fields.Char('Nama Pelanggan')
    street = fields.Char('Alamat')
    street2 = fields.Char('Alamat (lanjutan)')
    city = fields.Char('Kota')
    state_id = fields.Many2one('res.country.state', string='Provinsi')
    zip = fields.Char('Kode Pos')
    country_id = fields.Many2one('res.country', string='Negara', default=lambda self: self.env.ref('base.id'))
    email = fields.Char('Email')
    phone = fields.Char('Telepon')
    mobile = fields.Char('Handphone')
    identity_number = fields.Char('Nomor Identitas')
    emergency_contact = fields.Char('Kontak Darurat')
    emergency_phone = fields.Char('Telepon Darurat')
    notes = fields.Text('Catatan')
    
    # CPE data
    cpe_name = fields.Char('Nama Perangkat')
    mac_address = fields.Char('MAC Address')
    ip_address = fields.Char('IP Address')
    outdoor_unit = fields.Char('Outdoor Unit')
    router = fields.Char('Router')
    area_id = fields.Many2one('isp.area', string='Area / Desa')
    mikrotik_config_id = fields.Many2one('isp.mikrotik.config', string='Router Mikrotik', 
                                        domain="[('active', '=', True)]",
                                        help="Router Mikrotik tempat user PPPoE dibuat")
    connection_type = fields.Selection([
        ('pppoe', 'PPPoE'),
        ('static', 'Static IP'),
        ('dhcp', 'DHCP')
    ], string='Tipe Koneksi', default='pppoe')
    pppoe_username = fields.Char('PPPoE Username')
    pppoe_password = fields.Char('PPPoE Password')
    ownership = fields.Selection([
        ('customer', 'Milik Pelanggan'),
        ('company', 'Milik Perusahaan')
    ], string='Kepemilikan', default='company')
    
    # Subscription data
    package_id = fields.Many2one('isp.package', string='Paket')
    date_start = fields.Date('Tanggal Mulai', default=fields.Date.today)
    due_day = fields.Integer('Tanggal Jatuh Tempo', default=1,
                          help="Tanggal jatuh tempo setiap bulannya (1-31)")
    recurring_interval = fields.Integer('Interval Penagihan', default=1)
    recurring_rule_type = fields.Selection([
        ('monthly', 'Bulanan'),
        ('quarterly', 'Triwulan'),
    ], string='Tipe Penagihan', default='monthly')
    discount_id = fields.Many2one('isp.discount', string='Diskon')
    
    # Installation fee data
    installation_type_id = fields.Many2one('isp.installation.type', string='Tipe Instalasi')
    installation_date = fields.Date('Tanggal Instalasi', default=fields.Date.today)
    installation_amount = fields.Float('Biaya Instalasi')
    technician_id = fields.Many2one('res.users', string='Teknisi',
                                 domain=lambda self: [('groups_id', 'in', [self.env.ref('dkt_isp_billing.group_isp_technician').id])],
                                 help="Teknisi yang akan melakukan instalasi")
    installation_notes = fields.Text('Catatan Instalasi')
    
    # Summary
    create_invoice = fields.Boolean('Buat Invoice Biaya Instalasi', default=True)
    activate_service = fields.Boolean('Aktifkan Layanan', default=True)
    
    @api.model
    def default_get(self, fields_list):
        """Override default_get untuk mengisi nilai default pada field-field wajib"""
        res = super(ISPInstallationWizard, self).default_get(fields_list)
        
        # Jika wizard dipanggil dari form pelanggan
        if self.env.context.get('active_model') == 'res.partner' and self.env.context.get('active_id'):
            partner = self.env['res.partner'].browse(self.env.context.get('active_id'))
            if partner.exists() and partner.customer_rank > 0:
                res.update({
                    'is_new_customer': False,
                    'partner_id': partner.id,
                })
        
        # Set default package_id jika ada
        if 'package_id' in fields_list and not res.get('package_id'):
            default_package = self.env['isp.package'].get_default_package()
            if default_package:
                res['package_id'] = default_package.id
        
        # Set default installation_type_id jika ada
        if 'installation_type_id' in fields_list and not res.get('installation_type_id'):
            default_installation_type = self.env['isp.installation.type'].search([], limit=1)
            if default_installation_type:
                res['installation_type_id'] = default_installation_type.id
                res['installation_amount'] = default_installation_type.price
        
        # Jangan pilih router default — teknisi harus pilih area/router.
        return res
    
    @api.onchange('is_new_customer')
    def _onchange_is_new_customer(self):
        if not self.is_new_customer:
            # Jangan hapus data jika ada partner_id yang dipilih
            if not self.partner_id:
                self.name = False
                self.street = False
                self.street2 = False
                self.city = False
                self.state_id = False
                self.zip = False
                self.email = False
                self.phone = False
                self.mobile = False
                self.identity_number = False
                self.emergency_contact = False
                self.emergency_phone = False
                self.notes = False
        else:
            # Reset partner_id dan data terkait
            self.partner_id = False
            self.name = False
            self.street = False
            self.street2 = False
            self.city = False
            self.state_id = False
            self.zip = False
            self.email = False
            self.phone = False
            self.mobile = False
            self.identity_number = False
            self.emergency_contact = False
            self.emergency_phone = False
            self.notes = False
    
    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            # Isi data pelanggan dari partner yang dipilih
            self.name = self.partner_id.name
            self.street = self.partner_id.street
            self.street2 = self.partner_id.street2
            self.city = self.partner_id.city
            self.state_id = self.partner_id.state_id
            self.zip = self.partner_id.zip
            self.country_id = self.partner_id.country_id
            self.email = self.partner_id.email
            self.phone = self.partner_id.phone
            self.mobile = self.partner_id.phone
            self.identity_number = self.partner_id.identity_number
            self.emergency_contact = self.partner_id.emergency_contact
            self.emergency_phone = self.partner_id.emergency_phone
            self.notes = self.partner_id.comment
            if self.partner_id.area_id:
                self.area_id = self.partner_id.area_id
            
            # Isi data CPE dengan nama pelanggan
            if not self.cpe_name:
                self.cpe_name = f"CPE-{self.partner_id.name}"
            
            # Generate username dari nama pelanggan
            if not self.pppoe_username:
                base_username = self.partner_id.name.lower().replace(' ', '-')
                counter = 1
                username = base_username
                while self.env['isp.cpe'].search_count([('pppoe_username', '=', username)]) > 0:
                    username = f"{base_username}-{counter}"
                    counter += 1
                self.pppoe_username = username
                self.pppoe_password = self._generate_random_password()
    
    @api.onchange('name')
    def _onchange_name(self):
        if self.is_new_customer and self.name:
            # Isi data CPE dengan nama pelanggan
            if not self.cpe_name:
                self.cpe_name = f"CPE-{self.name}"
            
            # Generate username dari nama pelanggan
            if not self.pppoe_username:
                base_username = self.name.lower().replace(' ', '-')
                counter = 1
                username = base_username
                while self.env['isp.cpe'].search_count([('pppoe_username', '=', username)]) > 0:
                    username = f"{base_username}-{counter}"
                    counter += 1
                self.pppoe_username = username
                self.pppoe_password = self._generate_random_password()
    
    @api.onchange('installation_type_id')
    def _onchange_installation_type_id(self):
        if self.installation_type_id:
            self.installation_amount = self.installation_type_id.price
        else:
            # Jika installation_type_id dihapus, cari nilai default
            default_installation_type = self.env['isp.installation.type'].search([], limit=1)
            if default_installation_type:
                self.installation_type_id = default_installation_type.id
                self.installation_amount = default_installation_type.price
    
    @api.onchange('area_id')
    def _onchange_area_id(self):
        # Satu router bisa untuk banyak desa. Jangan paksa router = area.
        suggested = self.area_id.mikrotik_config_ids.filtered('active')[:1]
        if suggested and not self.mikrotik_config_id:
            self.mikrotik_config_id = suggested
        return {
            'domain': {
                'mikrotik_config_id': [('active', '=', True)],
            }
        }

    @api.onchange('mikrotik_config_id')
    def _onchange_mikrotik_config_id(self):
        # Jangan timpa desa pelanggan dari area "rumah" router.
        return

    @api.onchange('package_id')
    def _onchange_package_id(self):
        if not self.package_id:
            default_package = self.env['isp.package'].get_default_package()
            if default_package:
                self.package_id = default_package.id
    
    @api.model
    def _generate_random_password(self, length=8):
        """Generate random password with specified length."""
        characters = string.ascii_letters + string.digits
        return ''.join(random.choice(characters) for i in range(length))
    
    @api.constrains('pppoe_username')
    def _check_pppoe_username(self):
        """Validasi username PPPoE unik"""
        for record in self:
            if record.pppoe_username:
                if self.env['isp.cpe'].search_count([('pppoe_username', '=', record.pppoe_username)]) > 0:
                    raise ValidationError(f'Username PPPoE "{record.pppoe_username}" sudah digunakan!')
    
    @api.constrains('due_day')
    def _check_due_day(self):
        """Validasi tanggal jatuh tempo"""
        for record in self:
            if record.due_day < 1 or record.due_day > 31:
                raise ValidationError('Tanggal jatuh tempo harus berada di antara 1-31!')
    
    def action_next(self):
        """Pindah ke tahap berikutnya"""
        self.ensure_one()
        
        # Validasi data sesuai tahapan
        if self.state == 'customer':
            if self.is_new_customer:
                if not self.name:
                    raise ValidationError('Nama pelanggan harus diisi!')
                if not self.mobile:
                    raise ValidationError('Nomor handphone harus diisi!')
            else:
                if not self.partner_id:
                    raise ValidationError('Pelanggan harus dipilih!')
            if not self.area_id:
                raise ValidationError('Area / desa harus dipilih!')
            self.state = 'cpe'
            
        elif self.state == 'cpe':
            if not self.cpe_name:
                raise ValidationError('Nama perangkat harus diisi!')
            if self.connection_type == 'pppoe':
                if not self.pppoe_username:
                    raise ValidationError('PPPoE Username harus diisi!')
                if not self.pppoe_password:
                    raise ValidationError('PPPoE Password harus diisi!')
                if not self.mikrotik_config_id:
                    raise ValidationError('Router Mikrotik harus dipilih untuk koneksi tipe PPPoE!')
            self.state = 'subscription'
            
        elif self.state == 'subscription':
            if not self.package_id:
                raise ValidationError('Paket layanan harus dipilih!')
            if self.due_day < 1 or self.due_day > 31:
                raise ValidationError('Tanggal jatuh tempo harus antara 1-31!')
            self.state = 'fee'
            
        elif self.state == 'fee':
            if not self.installation_type_id:
                raise ValidationError('Tipe instalasi harus dipilih!')
            self.state = 'summary'
            
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'isp.installation.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
    
    def action_previous(self):
        """Kembali ke tahap sebelumnya"""
        self.ensure_one()
        
        if self.state == 'cpe':
            self.state = 'customer'
        elif self.state == 'subscription':
            self.state = 'cpe'
        elif self.state == 'fee':
            self.state = 'subscription'
        elif self.state == 'summary':
            self.state = 'fee'
            
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'isp.installation.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
    
    def action_create(self):
        """Buat data pelanggan, CPE, subscription, dan biaya instalasi"""
        self.ensure_one()
        
        # 1. Buat atau ambil data pelanggan
        if self.is_new_customer:
            partner_vals = {
                'name': self.name,
                'street': self.street,
                'street2': self.street2,
                'city': self.city,
                'state_id': self.state_id.id if self.state_id else False,
                'zip': self.zip,
                'country_id': self.country_id.id if self.country_id else False,
                'email': self.email,
                'phone': self.mobile or self.phone,
                'identity_number': self.identity_number,
                'emergency_contact': self.emergency_contact,
                'emergency_phone': self.emergency_phone,
                'notes': self.notes,
                'customer_rank': 1,
                'state': 'draft',
                'area_id': self.area_id.id if self.area_id else False,
            }
            partner = self.env['res.partner'].create(partner_vals)
        else:
            partner = self.partner_id
            if self.area_id and not partner.area_id:
                partner.area_id = self.area_id.id
        
        # 2. Buat data CPE
        cpe_vals = {
            'name': self.cpe_name,
            'partner_id': partner.id,
            'mac_address': self.mac_address,
            'ip_address': self.ip_address,
            'outdoor_unit': self.outdoor_unit,
            'router': self.router,
            'connection_type': self.connection_type,
            'pppoe_username': self.pppoe_username,
            'pppoe_password': self.pppoe_password,
            'ownership': self.ownership,
            'state': 'draft',
        }
        
        # Tambahkan mikrotik_config_id jika connection_type == 'pppoe'
        if self.connection_type == 'pppoe' and self.mikrotik_config_id:
            cpe_vals['mikrotik_config_id'] = self.mikrotik_config_id.id
            
        cpe = self.env['isp.cpe'].create(cpe_vals)
        
        # 3. Buat data subscription
        subscription_vals = {
            'partner_id': partner.id,
            'cpe_id': cpe.id,
            'package_id': self.package_id.id,
            'date_start': self.date_start,
            'due_day': self.env['isp.subscription']._billing_due_day(),
            'recurring_interval': self.recurring_interval,
            'recurring_rule_type': self.recurring_rule_type,
            'discount_id': self.discount_id.id if self.discount_id else False,
            'state': 'draft',
        }
        subscription = self.env['isp.subscription'].create(subscription_vals)
        
        # 4. Buat data biaya instalasi
        installation_fee_vals = {
            'partner_id': partner.id,
            'installation_type_id': self.installation_type_id.id,
            'date': self.installation_date,
            'amount': self.installation_amount,
            'technician_id': self.technician_id.id if self.technician_id else False,
            'notes': self.installation_notes,
            'state': 'draft',
        }
        installation_fee = self.env['isp.installation.fee'].create(installation_fee_vals)
        
        # 5. Aktifkan layanan jika dipilih
        activation_error = False
        if self.activate_service:
            try:
                subscription.action_open()
            except Exception as e:
                _logger.error(f"Error saat mengaktifkan layanan: {str(e)}")
                activation_error = str(e)
                # Tidak perlu raise exception, biarkan proses tetap berlanjut
        
        # 6. Buat invoice biaya instalasi jika dipilih
        invoice_error = False
        if self.create_invoice:
            try:
                _logger.info(f"Mencoba membuat invoice untuk biaya instalasi: {installation_fee.name}")
                invoice = installation_fee.create_invoice()
                _logger.info(f"Invoice berhasil dibuat: {invoice.name} untuk biaya instalasi {installation_fee.name}")
            except Exception as e:
                _logger.error(f"Error saat membuat invoice: {str(e)}")
                invoice_error = str(e)
                # Tidak perlu raise exception, biarkan proses tetap berlanjut
        
        # 7. Tampilkan pesan sukses dan buka form subscription
        # Jika ada error, tampilkan pesan warning tetapi tetap lanjutkan
        if activation_error or invoice_error:
            message = ""
            if activation_error:
                message += f"Layanan berhasil dibuat tetapi gagal diaktifkan: {activation_error}\n"
            if invoice_error:
                message += f"Gagal membuat invoice: {invoice_error}\n"
            
            # Tampilkan notifikasi warning tetapi tetap lanjutkan ke form subscription
            self.env['bus.bus']._sendone(
                self.env.user.partner_id,
                'simple_notification',
                {
                    'title': 'Peringatan',
                    'message': message,
                    'type': 'warning',
                    'sticky': True,
                }
            )
        
        # Selalu kembalikan action untuk membuka form subscription
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subscription',
            'res_model': 'isp.subscription',
            'res_id': subscription.id,
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'form_view_initial_mode': 'edit',
                'force_detailed_view': 'true'
            }
        } 