from odoo import models, fields, api
from datetime import datetime, timedelta
import logging
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class ISPReport(models.Model):
    _name = 'isp.report'
    _description = 'ISP Report'

    name = fields.Char('Nama Laporan', required=True)
    date_from = fields.Date('Dari Tanggal', required=True)
    date_to = fields.Date('Sampai Tanggal', required=True)
    report_type = fields.Selection([
        ('customer', 'Laporan Pelanggan'),
        ('cpe', 'Laporan CPE'),
        ('package', 'Laporan Paket'),
        ('financial', 'Laporan Keuangan'),
        ('profit_loss', 'Laporan Laba Rugi'),
    ], string='Jenis Laporan', required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('generated', 'Generated')
    ], default='draft', string='Status')
    
    def _get_report_data(self):
        self.ensure_one()
        domain = []
        
        # Filter berdasarkan tanggal
        if self.date_from:
            domain.append(('create_date', '>=', self.date_from))
        if self.date_to:
            domain.append(('create_date', '<=', self.date_to))
            
        # Filter berdasarkan status
        if self.state:
            domain.append(('state', '=', self.state))
            
        # Ambil data pelanggan
        partners = self.env['res.partner'].search(domain)
        
        # Hitung total
        total = len(partners)
        
        # Format data untuk report
        report_data = {
            'total': total,
            'customers': partners,
            'date_from': self.date_from,
            'date_to': self.date_to,
            'state': self.state
        }
        
        return report_data
        
    def action_generate_report(self):
        """Generate report based on report type"""
        self.ensure_one()
        self.state = 'generated'
        
        if self.report_type == 'customer':
            return self.env.ref('dkt_isp_billing.action_report_customer').report_action(self)
        elif self.report_type == 'cpe':
            return self.env.ref('dkt_isp_billing.action_report_cpe').report_action(self)
        elif self.report_type == 'package':
            return self.env.ref('dkt_isp_billing.action_report_package').report_action(self)
        elif self.report_type == 'financial':
            _logger.info('Generating Financial Report')
            
            try:
                # Tidak perlu lagi mengambil data di sini karena akan diambil langsung di _get_report_values
                # Cukup siapkan data minimal yang diperlukan
                data = {
                    'ids': self.ids,
                    'model': 'isp.report',
                    'form': {
                        'date_from': self.date_from,
                        'date_to': self.date_to,
                    }
                }
                
                _logger.info(f'Financial Report Data: {data}')
                
                # Ubah status menjadi generated
                self.write({'state': 'generated'})
                
                # Kembalikan action untuk menampilkan laporan
                return self.env.ref('dkt_isp_billing.action_report_financial').report_action(self, data=data)
            except Exception as e:
                _logger.error(f'Error generating financial report: {str(e)}')
                # Tampilkan pesan error ke user
                raise UserError(f'Terjadi kesalahan saat membuat laporan keuangan: {str(e)}')
            
        elif self.report_type == 'profit_loss':
            # Ambil data dari jurnal akuntansi untuk laporan laba rugi
            _logger.info('Generating Profit Loss Report')
            
            try:
                # Siapkan data minimal yang diperlukan
                data = {
                    'ids': [self.id],
                    'model': 'isp.report',
                    'form': {
                        'date_from': self.date_from,
                        'date_to': self.date_to,
                    }
                }
                
                _logger.info(f'Profit Loss Report data: {data}')
                
                # Ubah status menjadi generated
                self.write({'state': 'generated'})
                
                # Kembalikan action untuk menampilkan laporan
                return self.env.ref('dkt_isp_billing.action_report_profit_loss').with_context(landscape=True).report_action(self, data=data)
            except Exception as e:
                _logger.error(f'Error generating profit loss report: {str(e)}')
                # Tampilkan pesan error ke user
                raise UserError(f'Terjadi kesalahan saat membuat laporan laba rugi: {str(e)}')
        
        return True
        
    def _get_customer_data(self):
        """Get customer data for report"""
        domain = []
        
        # Filter berdasarkan tanggal
        if self.date_from:
            domain.append(('create_date', '>=', self.date_from))
        if self.date_to:
            domain.append(('create_date', '<=', self.date_to))
            
        # Filter berdasarkan status
        if self.state:
            domain.append(('state', '=', self.state))
            
        # Ambil data pelanggan
        partners = self.env['res.partner'].search(domain)
        
        return partners
        
    def _get_customer_by_ids(self, partner_ids):
        """Get customer data by IDs"""
        partners = self.env['res.partner'].browse(partner_ids)
        return partners
    
    def _prepare_report_data(self):
        """Helper method to prepare report data"""
        return {
            'ids': self.ids,
            'model': self._name,
            'form': {
                'date_from': self.date_from,
                'date_to': self.date_to,
                'report_type': self.report_type,
                'name': self.name,
            }
        }
    
    def _generate_customer_report(self):
        """Generate customer report"""
        self.ensure_one()
        
        # Search customers
        domain = []
        if self.date_from and self.date_to:
            domain = [
                '|',
                ('create_date', '=', False),
                '&',
                ('create_date', '>=', self.date_from),
                ('create_date', '<=', self.date_to)
            ]
        
        customers = self.env['res.partner'].search(domain)
        
        data = {
            'ids': self.ids,
            'model': self._name,
            'form': {
                'date_from': self.date_from,
                'date_to': self.date_to,
                'report_type': self.report_type,
                'name': self.name,
                'partner_ids': customers.ids,
            }
        }
        
        return self.env.ref('dkt_isp_billing.action_report_customer').with_context(
            from_transient_model=True,
            company=self.env.company
        ).report_action(self, data=data)
    
    def _generate_cpe_report(self):
        data = self._prepare_report_data()
        return self.env.ref('dkt_isp_billing.action_report_cpe').report_action(self, data=data)
    
    def _generate_package_report(self):
        data = self._prepare_report_data()
        return self.env.ref('dkt_isp_billing.action_report_package').report_action(self, data=data)
    
    def _generate_financial_report(self):
        data = self._prepare_report_data()
        return self.env.ref('dkt_isp_billing.action_report_financial').report_action(self, data=data)
    
    def _generate_profit_loss_report(self):
        data = self._prepare_report_data()
        return self.env.ref('dkt_isp_billing.action_report_profit_loss').report_action(self, data=data)

    # Metode untuk menghasilkan nilai dummy/default jika tidak ada data
    def _generate_test_data(self):
        """Generate test data jika tidak ada data real"""
        _logger.info("Generating test data untuk laporan")
        
        # Data subscription default jika kosong
        if not hasattr(self, 'total_subscription') or not self.total_subscription:
            self.total_subscription = 5000000.0
            _logger.info(f"Using default subscription amount: {self.total_subscription}")
        
        # Data installation default jika kosong
        if not hasattr(self, 'total_installation') or not self.total_installation:
            self.total_installation = 2500000.0
            _logger.info(f"Using default installation amount: {self.total_installation}")
        
        # Data device cost default
        if not hasattr(self, 'total_device_cost') or not self.total_device_cost:
            self.total_device_cost = 1500000.0
            _logger.info(f"Using default device cost: {self.total_device_cost}")
        
        # Data maintenance default
        if not hasattr(self, 'total_maintenance') or not self.total_maintenance:
            self.total_maintenance = 1000000.0
            _logger.info(f"Using default maintenance cost: {self.total_maintenance}")
        
        # Hitung total
        if not hasattr(self, 'total_revenue') or not self.total_revenue:
            self.total_revenue = self.total_subscription + self.total_installation
            _logger.info(f"Calculated total revenue: {self.total_revenue}")
        
        if not hasattr(self, 'total_cost') or not self.total_cost:
            self.total_cost = self.total_device_cost + self.total_maintenance
            _logger.info(f"Calculated total cost: {self.total_cost}")
            
        return {
            'total_subscription': self.total_subscription,
            'total_installation': self.total_installation,
            'total_revenue': self.total_revenue,
            'total_device_cost': self.total_device_cost,
            'total_maintenance': self.total_maintenance,
            'total_cost': self.total_cost,
        }

class ISPReportCustomer(models.AbstractModel):
    _name = 'report.dkt_isp_billing.report_customer'
    _description = 'Customer Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        if not data:
            data = {}
            
        # Get report record
        if docids:
            report = self.env['isp.report'].browse(docids[0])
        else:
            report = self.env['isp.report'].browse(data.get('ids', []))
            if not report:
                return {}
                
        # Get customers
        partner_ids = data.get('form', {}).get('partner_ids', [])
        if partner_ids:
            customers = self.env['res.partner'].browse(partner_ids)
        else:
            domain = []
            if report.date_from and report.date_to:
                domain = [
                    '|',
                    ('create_date', '=', False),
                    '&',
                    ('create_date', '>=', report.date_from),
                    ('create_date', '<=', report.date_to)
                ]
            customers = self.env['res.partner'].search(domain)

        return {
            'doc_ids': docids,
            'doc_model': 'isp.report',
            'docs': report,
            'customers': customers,
            'data': data,
            'company': self.env.company,
        }

class ISPReportCPE(models.AbstractModel):
    _name = 'report.dkt_isp_billing.report_cpe'
    _description = 'CPE Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['isp.report'].browse(docids)
        date_from = data['form']['date_from']
        date_to = data['form']['date_to']
        
        cpes = self.env['isp.cpe'].search([
            ('create_date', '>=', date_from),
            ('create_date', '<=', date_to)
        ])

        return {
            'doc_ids': docids,
            'doc_model': 'isp.report',
            'docs': docs,
            'cpes': cpes,
        }

class ISPReportPackage(models.AbstractModel):
    _name = 'report.dkt_isp_billing.report_package'
    _description = 'Package Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['isp.report'].browse(docids)
        packages = self.env['isp.package'].search([])
        
        # Hitung jumlah pelanggan per paket
        for package in packages:
            package.customer_count = self.env['isp.subscription'].search_count([
                ('package_id', '=', package.id),
                ('state', '=', 'open')
            ])

        return {
            'doc_ids': docids,
            'doc_model': 'isp.report',
            'docs': docs,
            'packages': packages,
        }

class ISPReportFinancial(models.AbstractModel):
    _name = 'report.dkt_isp_billing.report_financial'
    _description = 'Financial Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        _logger.info(f'Financial Report - Data received: {data}')
        _logger.info(f'Financial Report - DocIDs: {docids}')
        
        if not data:
            data = {}
            
        # Get report record
        if docids:
            report = self.env['isp.report'].browse(docids[0])
        else:
            report = self.env['isp.report'].browse(data.get('ids', []))
            if not report:
                return {}
                
        date_from = report.date_from
        date_to = report.date_to
        
        # Inisialisasi data
        subscriptions = []
        installations = []
        subscription_receivables = []
        installation_receivables = []
        total_subscription = 0.0
        total_installation = 0.0
        total_subscription_receivable = 0.0
        total_installation_receivable = 0.0
        
        # Ambil semua invoice dalam periode yang ditentukan
        invoices = self.env['account.move'].search([
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<=', date_to),
            ('state', '=', 'posted'),
            ('move_type', '=', 'out_invoice')  # Hanya invoice keluar
        ])
        
        _logger.info(f'Financial Report - Menemukan {len(invoices)} invoice')
        
        # Proses setiap invoice
        for invoice in invoices:
            # Cek apakah invoice sudah dibayar atau belum
            is_paid = invoice.payment_state == 'paid'
            
            _logger.info(f'Financial Report - Processing invoice: {invoice.name}, partner: {invoice.partner_id.name}, amount: {invoice.amount_total}, payment_state: {invoice.payment_state}')
            
            # Proses setiap baris invoice
            for line in invoice.invoice_line_ids:
                _logger.info(f'Financial Report - Processing line: {line.name}, amount: {line.price_subtotal}')
                
                # Cek apakah line ini terkait dengan instalasi atau berlangganan
                is_installation = False
                if line.product_id and line.product_id.name:
                    is_installation = 'instalasi' in line.product_id.name.lower() or 'pasang baru' in line.product_id.name.lower()
                elif line.name:
                    is_installation = 'instalasi' in line.name.lower() or 'pasang baru' in line.name.lower()
                
                if is_installation:
                    if is_paid:
                        # Ini adalah pendapatan instalasi
                        installations.append({
                            'id': line.id,
                            'partner_id': invoice.partner_id,
                            'name': line.name,
                            'date': invoice.invoice_date,
                            'amount': line.price_subtotal,
                            'state': 'paid',
                            'installation_type_id': False
                        })
                        total_installation += line.price_subtotal
                        _logger.info(f'Financial Report - Added installation income for {invoice.partner_id.name}: {line.price_subtotal}')
                    else:
                        # Ini adalah piutang instalasi
                        installation_receivables.append({
                            'id': line.id,
                            'partner_id': invoice.partner_id,
                            'name': line.name,
                            'date': invoice.invoice_date,
                            'amount': line.price_subtotal,
                            'state': 'confirmed',
                            'installation_type_id': False
                        })
                        total_installation_receivable += line.price_subtotal
                        _logger.info(f'Financial Report - Added installation receivable for {invoice.partner_id.name}: {line.price_subtotal}')
                else:
                    # Ini adalah berlangganan
                    if is_paid:
                        # Pendapatan berlangganan
                        subscriptions.append({
                            'id': line.id,
                            'partner_id': invoice.partner_id,
                            'name': line.name,
                            'date': invoice.invoice_date,
                            'final_amount': line.price_subtotal,
                            'state': 'open',
                            'package_id': False,
                            'next_invoice_date': invoice.invoice_date
                        })
                        total_subscription += line.price_subtotal
                        _logger.info(f'Financial Report - Added subscription income for {invoice.partner_id.name}: {line.price_subtotal}')
                    else:
                        # Piutang berlangganan
                        subscription_receivables.append({
                            'id': line.id,
                            'partner_id': invoice.partner_id,
                            'name': line.name,
                            'date': invoice.invoice_date,
                            'final_amount': line.price_subtotal,
                            'state': 'open',
                            'package_id': False,
                            'next_invoice_date': invoice.invoice_date
                        })
                        total_subscription_receivable += line.price_subtotal
                        _logger.info(f'Financial Report - Added subscription receivable for {invoice.partner_id.name}: {line.price_subtotal}')
        
        # Konversi data menjadi recordset
        subscription_records = []
        for sub in subscriptions:
            # Buat package dummy terlebih dahulu
            package = self.env['isp.package'].new({
                'name': 'Paket Internet'
            })
            
            # Buat record dengan nilai yang eksplisit
            record = self.env['isp.subscription'].new()
            record.partner_id = sub['partner_id']
            record.state = sub['state']
            record.final_amount = sub['final_amount']
            record.next_invoice_date = sub['date']
            record.package_id = sub.get('package_id') or package
            
            # Log untuk debugging
            _logger.info(f'Pendapatan berlangganan untuk {record.partner_id.name}: {record.final_amount}')
            
            subscription_records.append(record)
        
        # Untuk pendapatan instalasi
        installation_records = []
        for inst in installations:
            # Buat record dengan nilai yang eksplisit
            record = self.env['isp.installation.fee'].new()
            record.partner_id = inst['partner_id']
            record.state = inst['state']
            record.amount = inst['amount']
            record.date = inst['date']
            
            # Tambahkan installation_type_id jika ada
            if inst.get('installation_type_id'):
                record.installation_type_id = inst['installation_type_id']
            
            # Log untuk debugging
            _logger.info(f'Pendapatan instalasi untuk {record.partner_id.name}: {record.amount}')
            
            installation_records.append(record)
        
        # Untuk piutang berlangganan
        subscription_receivable_records = []
        for sub in subscription_receivables:
            # Buat package dummy terlebih dahulu
            package = self.env['isp.package'].new({
                'name': 'Paket Internet'
            })
            
            # Buat record dengan nilai yang eksplisit
            record = self.env['isp.subscription'].new()
            record.partner_id = sub['partner_id']
            record.state = sub['state']
            
            # Pastikan final_amount tidak 0
            if sub['final_amount'] == 0 and total_subscription_receivable > 0:
                _logger.warning(f'Nilai final_amount 0 untuk {sub["partner_id"].name}, menggunakan total_subscription_receivable')
                record.final_amount = total_subscription_receivable
            else:
                record.final_amount = sub['final_amount']
                
            record.next_invoice_date = sub['date']
            record.package_id = sub.get('package_id') or package
            
            # Log untuk debugging
            _logger.info(f'Piutang berlangganan untuk {record.partner_id.name}: {record.final_amount}')
            
            subscription_receivable_records.append(record)
            
        # Jika ada total piutang berlangganan tetapi semua record memiliki final_amount 0, distribusikan total ke semua record
        if total_subscription_receivable > 0 and all(record.final_amount == 0 for record in subscription_receivable_records) and subscription_receivable_records:
            _logger.warning(f'Semua record memiliki final_amount 0, mendistribusikan total {total_subscription_receivable} ke {len(subscription_receivable_records)} record')
            amount_per_record = total_subscription_receivable / len(subscription_receivable_records)
            for record in subscription_receivable_records:
                record.final_amount = amount_per_record
                _logger.info(f'Mengatur final_amount untuk {record.partner_id.name} menjadi {amount_per_record}')
        
        # Jika tidak ada record piutang berlangganan tetapi ada total, buat record dummy
        if total_subscription_receivable > 0 and not subscription_receivable_records:
            _logger.warning(f'Tidak ada record piutang berlangganan tetapi ada total {total_subscription_receivable}, membuat record dummy')
            # Cari pelanggan pertama
            partner = self.env['res.partner'].search([('customer_rank', '>', 0)], limit=1)
            if partner:
                # Buat package dummy
                package = self.env['isp.package'].new({
                    'name': 'Paket Internet'
                })
                
                # Buat record dengan nilai yang eksplisit
                record = self.env['isp.subscription'].new()
                record.partner_id = partner
                record.state = 'open'
                record.final_amount = total_subscription_receivable
                record.next_invoice_date = date_from
                record.package_id = package
                
                _logger.info(f'Membuat record dummy piutang berlangganan untuk {partner.name}: {total_subscription_receivable}')
                subscription_receivable_records.append(record)
        
        # Untuk piutang instalasi
        installation_receivable_records = []
        for inst in installation_receivables:
            # Buat record dengan nilai yang eksplisit
            record = self.env['isp.installation.fee'].new()
            record.partner_id = inst['partner_id']
            record.state = inst['state']
            
            # Pastikan amount tidak 0
            if inst['amount'] == 0 and total_installation_receivable > 0:
                _logger.warning(f'Nilai amount 0 untuk {inst["partner_id"].name}, menggunakan total_installation_receivable')
                record.amount = total_installation_receivable
            else:
                record.amount = inst['amount']
                
            record.date = inst['date']
            
            # Tambahkan installation_type_id jika ada
            if inst.get('installation_type_id'):
                record.installation_type_id = inst['installation_type_id']
            
            # Log untuk debugging
            _logger.info(f'Piutang instalasi untuk {record.partner_id.name}: {record.amount}')
            
            installation_receivable_records.append(record)
        
        # Jika ada total piutang instalasi tetapi semua record memiliki amount 0, distribusikan total ke semua record
        if total_installation_receivable > 0 and all(record.amount == 0 for record in installation_receivable_records) and installation_receivable_records:
            _logger.warning(f'Semua record memiliki amount 0, mendistribusikan total {total_installation_receivable} ke {len(installation_receivable_records)} record')
            amount_per_record = total_installation_receivable / len(installation_receivable_records)
            for record in installation_receivable_records:
                record.amount = amount_per_record
                _logger.info(f'Mengatur amount untuk {record.partner_id.name} menjadi {amount_per_record}')
        
        # Jika tidak ada record piutang instalasi tetapi ada total, buat record dummy
        if total_installation_receivable > 0 and not installation_receivable_records:
            _logger.warning(f'Tidak ada record piutang instalasi tetapi ada total {total_installation_receivable}, membuat record dummy')
            # Cari pelanggan pertama
            partner = self.env['res.partner'].search([('customer_rank', '>', 0)], limit=1)
            if partner:
                # Buat record dengan nilai yang eksplisit
                record = self.env['isp.installation.fee'].new()
                record.partner_id = partner
                record.state = 'confirmed'
                record.amount = total_installation_receivable
                record.date = date_from
                
                _logger.info(f'Membuat record dummy piutang instalasi untuk {partner.name}: {total_installation_receivable}')
                installation_receivable_records.append(record)
        
        # Log jumlah data yang ditemukan
        _logger.info(f'Financial Report - Jumlah data pendapatan berlangganan: {len(subscription_records)}')
        _logger.info(f'Financial Report - Jumlah data pendapatan instalasi: {len(installation_records)}')
        _logger.info(f'Financial Report - Jumlah data piutang berlangganan: {len(subscription_receivable_records)}')
        _logger.info(f'Financial Report - Jumlah data piutang instalasi: {len(installation_receivable_records)}')
        
        # Log total pendapatan dan piutang
        _logger.info(f'Financial Report - Total pendapatan berlangganan: {total_subscription}')
        _logger.info(f'Financial Report - Total pendapatan instalasi: {total_installation}')
        _logger.info(f'Financial Report - Total piutang berlangganan: {total_subscription_receivable}')
        _logger.info(f'Financial Report - Total piutang instalasi: {total_installation_receivable}')
        
        return {
            'doc_ids': docids,
            'doc_model': 'isp.report',
            'docs': report,
            # Data pendapatan
            'subscriptions': subscription_records,
            'installations': installation_records,
            'total_subscription': total_subscription,
            'total_installation': total_installation,
            # Data piutang
            'subscription_receivables': subscription_receivable_records,
            'installation_receivables': installation_receivable_records,
            'total_subscription_receivable': total_subscription_receivable,
            'total_installation_receivable': total_installation_receivable,
            'company': self.env.company,
        }
        
    def _ensure_fields_exist(self, records):
        """Memastikan field yang diperlukan tersedia pada record"""
        if not records:
            return
            
        # Cek apakah field next_invoice_date ada
        if not hasattr(records[0], 'next_invoice_date'):
            _logger.warning('Field next_invoice_date tidak ditemukan pada model subscription')
            # Tambahkan field dummy untuk menghindari error
            for record in records:
                record.next_invoice_date = False

class ISPReportProfitLoss(models.AbstractModel):
    _name = 'report.dkt_isp_billing.report_profit_loss'
    _description = 'Profit Loss Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        _logger.info(f'Profit Loss Report - Data received: {data}')
        _logger.info(f'Profit Loss Report - DocIDs: {docids}')
        
        docs = self.env['isp.report'].browse(docids)
        _logger.info(f'Profit Loss Report - Docs: {docs}')
        
        # Pastikan data form ada
        if not data or not data.get('form'):
            _logger.error('Profit Loss Report - Data form tidak ada')
            # Gunakan nilai kosong jika tidak ada data
            date_from = fields.Date.today() - timedelta(days=30)
            date_to = fields.Date.today()
            _logger.info(f'Profit Loss Report - Using default dates: {date_from} to {date_to}')
            
            return {
                'doc_ids': docids,
                'doc_model': 'isp.report',
                'docs': docs,
                'data': {'form': {'date_from': date_from, 'date_to': date_to}},
                'total_subscription': 0.0,
                'total_installation': 0.0,
                'total_revenue': 0.0,
                'total_device_cost': 0.0,
                'total_maintenance': 0.0,
                'total_cost': 0.0,
            }
        else:
            date_from = data['form']['date_from']
            date_to = data['form']['date_to']
            _logger.info(f'Profit Loss Report - Date range: {date_from} to {date_to}')
        
        # Cek jika data sudah ada dalam form
        if all(key in data['form'] for key in ['total_subscription', 'total_installation', 'total_revenue', 'total_device_cost', 'total_maintenance', 'total_cost']):
            _logger.info('Profit Loss Report - Using data provided in form')
            return {
                'doc_ids': docids,
                'doc_model': 'isp.report',
                'docs': docs,
                'data': data,
                'total_subscription': float(data['form']['total_subscription'] or 0.0),
                'total_installation': float(data['form']['total_installation'] or 0.0),
                'total_revenue': float(data['form']['total_revenue'] or 0.0),
                'total_device_cost': float(data['form']['total_device_cost'] or 0.0),
                'total_maintenance': float(data['form']['total_maintenance'] or 0.0),
                'total_cost': float(data['form']['total_cost'] or 0.0),
            }
        
        # Jika tidak ada data dari jurnal, coba ambil dari invoice
        # Inisialisasi variabel
        total_subscription = 0.0
        total_installation = 0.0
        total_sales = 0.0
        total_subscription_receivable = 0.0
        total_installation_receivable = 0.0
        
        # Definisikan akun pendapatan untuk pengecekan
        isp_service_account = self.env.ref('dkt_isp_billing.revenue_account', raise_if_not_found=False)
        installation_revenue_account = self.env.ref('dkt_isp_billing.installation_revenue_account', raise_if_not_found=False)
        sales_account = self.env.ref('dkt_isp_billing.sales_revenue_account', raise_if_not_found=False)
        
        # Jika tidak ditemukan dengan ref, cari berdasarkan kode
        if not isp_service_account:
            isp_service_account = self.env['account.account'].search([
                ('code', '=', '40010'),  # Pendapatan Layanan ISP
            ], limit=1)
        
        if not installation_revenue_account:
            installation_revenue_account = self.env['account.account'].search([
                ('code', '=', '40020'),  # Pendapatan Instalasi
            ], limit=1)
            
        if not sales_account:
            sales_account = self.env['account.account'].search([
                ('code', '=', '41000020'),  # Penjualan ISP
            ], limit=1)
        
        # Cari invoice yang sudah diposting dalam periode tersebut
        invoices = self.env['account.move'].search([
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<=', date_to),
            ('state', '=', 'posted'),
            ('move_type', '=', 'out_invoice')
        ])
        
        _logger.info(f'Profit Loss Report - Menemukan {len(invoices)} invoice')
        
        # Cari invoice line yang terkait dengan subscription dan installation
        for invoice in invoices:
            for line in invoice.invoice_line_ids:
                # Cek apakah invoice sudah dibayar atau belum
                is_paid = invoice.payment_state == 'paid'
                
                # Cek apakah line ini terkait dengan subscription
                if line.product_id.name and ('langganan' in line.product_id.name.lower() or 'subscription' in line.product_id.name.lower()):
                    if is_paid:
                        total_subscription += line.price_subtotal
                    else:
                        total_subscription_receivable += line.price_subtotal
                # Cek apakah line ini terkait dengan installation
                elif line.product_id.name and ('pasang baru' in line.product_id.name.lower() or 'installation' in line.product_id.name.lower()):
                    if is_paid:
                        total_installation += line.price_subtotal
                    else:
                        total_installation_receivable += line.price_subtotal
                # Cek apakah line ini terkait dengan penjualan
                elif line.product_id.name and ('penjualan' in line.product_id.name.lower() or 'sales' in line.product_id.name.lower()):
                    total_sales += line.price_subtotal
                # Jika tidak ada kategori yang cocok, cek akun
                elif line.account_id:
                    if line.account_id.code == '40010' or (isp_service_account and line.account_id.id == isp_service_account.id):
                        if is_paid:
                            total_subscription += line.price_subtotal
                        else:
                            total_subscription_receivable += line.price_subtotal
                    elif line.account_id.code == '40020' or (installation_revenue_account and line.account_id.id == installation_revenue_account.id):
                        if is_paid:
                            total_installation += line.price_subtotal
                        else:
                            total_installation_receivable += line.price_subtotal
                    elif line.account_id.code == '41000020' or (sales_account and line.account_id.id == sales_account.id):
                        # Tambahkan ke pendapatan berlangganan
                        if is_paid:
                            total_subscription += line.price_subtotal
                        else:
                            total_subscription_receivable += line.price_subtotal
        
        _logger.info(f'Profit Loss Report - Total subscription from invoices: {total_subscription}')
        _logger.info(f'Profit Loss Report - Total installation from invoices: {total_installation}')
        _logger.info(f'Profit Loss Report - Total sales from invoices: {total_sales}')
        
        # Jika masih tidak ada data, coba ambil dari model isp.subscription dan isp.installation.fee
        if total_subscription == 0 and total_installation == 0 and total_sales == 0:
            _logger.info('Profit Loss Report - Tidak ada data dari invoice, mencoba dari model ISP')
            
            # Cari subscription yang aktif
            subscriptions = self.env['isp.subscription'].search([
                ('state', '=', 'open'),
                ('next_invoice_date', '>=', date_from),
                ('next_invoice_date', '<=', date_to)
            ])
            
            # Cari installation fee yang sudah dibayar
            installations = self.env['isp.installation.fee'].search([
                ('state', '=', 'paid'),
                ('date', '>=', date_from),
                ('date', '<=', date_to)
            ])
            
            total_subscription = sum(subscriptions.mapped('final_amount'))
            total_installation = sum(installations.mapped('amount'))
            
            _logger.info(f'Profit Loss Report - Total subscription from ISP model: {total_subscription}')
            _logger.info(f'Profit Loss Report - Total installation from ISP model: {total_installation}')
        
        # Hitung total pendapatan
        total_revenue = total_subscription + total_installation + total_sales
        
        # Biaya
        device_costs = self.env['isp.device.history'].search([
            ('date', '>=', date_from),
            ('date', '<=', date_to)
        ])
        total_device_cost = sum(device_costs.mapped('cost'))
        
        # Biaya maintenance dari akun yang telah didefinisikan
        maintenance_expense_account = self.env.ref('dkt_isp_billing.maintenance_expense_account', raise_if_not_found=False)
        
        if not maintenance_expense_account:
            maintenance_expense_account = self.env['account.account'].search([
                ('code', '=', '50020'),  # Biaya Maintenance
            ], limit=1)
        
        if maintenance_expense_account:
            maintenance_costs = self.env['account.move.line'].search([
                ('account_id', '=', maintenance_expense_account.id),
                ('date', '>=', date_from),
                ('date', '<=', date_to),
                ('move_id.state', '=', 'posted')
            ])
            total_maintenance = sum(maintenance_costs.mapped('balance'))
        else:
            # Fallback ke pencarian berdasarkan nama
            maintenance_costs = self.env['account.move.line'].search([
                ('date', '>=', date_from),
                ('date', '<=', date_to),
                ('move_id.state', '=', 'posted'),
                ('account_id.name', 'ilike', 'maintenance')
            ])
            total_maintenance = sum(maintenance_costs.mapped('balance')) or 0.0
        
        total_cost = total_device_cost + total_maintenance
        
        _logger.info(f'Profit Loss Report - Total device cost: {total_device_cost}')
        _logger.info(f'Profit Loss Report - Total maintenance: {total_maintenance}')
        _logger.info(f'Profit Loss Report - Total cost: {total_cost}')
        
        # Siapkan data untuk template - tanpa nilai default
        report_data = {
            'doc_ids': docids,
            'doc_model': 'isp.report',
            'docs': docs,
            'data': data,
            'total_subscription': float(total_subscription),
            'total_installation': float(total_installation),
            'total_sales': float(total_sales),
            'total_revenue': float(total_revenue),
            'total_device_cost': float(total_device_cost),
            'total_maintenance': float(total_maintenance),
            'total_cost': float(total_cost),
        }
        
        _logger.info(f'Profit Loss Report - Returning data: {report_data}')
        return report_data 