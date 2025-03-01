# Ambil semua jurnal line untuk akun pendapatan
revenue_lines = self.env['account.move.line'].search([
    ('move_id', 'in', all_moves.ids),
    ('account_id.code', 'in', [revenue_accounts['subscription'], revenue_accounts['installation']])
])

_logger.info(f'Financial Report - Menemukan {len(revenue_lines)} baris jurnal pendapatan')

# Pisahkan berdasarkan label/nama produk
for line in revenue_lines:
    # Cek apakah invoice sudah dibayar atau belum
    is_paid = line.move_id.payment_state == 'paid'
    
    # Log untuk debugging
    _logger.info(f'Financial Report - Processing line: {line.name}, partner: {line.partner_id.name}, amount: {line.price_subtotal}, payment_state: {line.move_id.payment_state}')
    
    # Cek apakah line ini terkait dengan instalasi atau berlangganan
    if line.name and ('instalasi' in line.name.lower() or 'pasang baru' in line.name.lower()):
        if is_paid:
            # Ini adalah pendapatan instalasi
            installations.append({
                'id': line.id,
                'partner_id': line.partner_id,
                'name': line.name,
                'date': line.date,
                'amount': line.price_subtotal,
                'state': 'paid',
                'installation_type_id': False
            })
            total_installation += line.price_subtotal
        else:
            # Ini adalah piutang instalasi
            installation_receivables.append({
                'id': line.id,
                'partner_id': line.partner_id,
                'name': line.name,
                'date': line.date,
                'amount': line.price_subtotal,
                'state': 'confirmed',
                'installation_type_id': False
            })
            total_installation_receivable += line.price_subtotal
    else:
        # Ini adalah berlangganan
        if is_paid:
            # Pendapatan berlangganan
            subscriptions.append({
                'id': line.id,
                'partner_id': line.partner_id,
                'name': line.name,
                'date': line.date,
                'final_amount': line.price_subtotal,
                'state': 'open',
                'package_id': False,
                'next_invoice_date': line.date
            })
            total_subscription += line.price_subtotal
        else:
            # Piutang berlangganan
            subscription_receivables.append({
                'id': line.id,
                'partner_id': line.partner_id,
                'name': line.name,
                'date': line.date,
                'final_amount': line.price_subtotal,
                'state': 'open',
                'package_id': False,
                'next_invoice_date': line.date
            })
            total_subscription_receivable += line.price_subtotal
            _logger.info(f'Financial Report - Added subscription receivable for {line.partner_id.name}: {line.price_subtotal}')

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
    record.final_amount = sub['final_amount']  # Gunakan nilai asli dari invoice
    record.next_invoice_date = sub['date']
    record.package_id = sub.get('package_id') or package
    
    # Log untuk debugging
    _logger.info(f'Piutang berlangganan untuk {record.partner_id.name}: {record.final_amount}')
    
    subscription_receivable_records.append(record)

# Untuk piutang instalasi
installation_receivable_records = []
for inst in installation_receivables:
    # Buat record dengan nilai yang eksplisit
    record = self.env['isp.installation.fee'].new()
    record.partner_id = inst['partner_id']
    record.state = inst['state']
    record.amount = inst['amount']  # Gunakan nilai asli dari invoice
    record.date = inst['date']
    
    # Tambahkan installation_type_id jika ada
    if inst.get('installation_type_id'):
        record.installation_type_id = inst['installation_type_id']
    
    # Log untuk debugging
    _logger.info(f'Piutang instalasi untuk {record.partner_id.name}: {record.amount}')
    
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

# Jika ada total piutang berlangganan tetapi tidak ada data, buat data dummy
if total_subscription_receivable > 0 and not subscription_receivable_records:
    _logger.info(f'Membuat data dummy untuk piutang berlangganan dengan total {total_subscription_receivable}')
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
        
        _logger.info(f'Membuat data dummy piutang berlangganan untuk {partner.name}: {total_subscription_receivable}')
        subscription_receivable_records.append(record)

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
            record.final_amount = sub['final_amount']
            record.next_invoice_date = sub['date']
            record.package_id = sub.get('package_id') or package
            
            # Log untuk debugging
            _logger.info(f'Piutang berlangganan untuk {record.partner_id.name}: {record.final_amount}')
            
            subscription_receivable_records.append(record)
        
        # Untuk piutang instalasi
        installation_receivable_records = []
        for inst in installation_receivables:
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
            _logger.info(f'Piutang instalasi untuk {record.partner_id.name}: {record.amount}')
            
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