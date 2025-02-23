from odoo import models, fields, api
from datetime import datetime, timedelta

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
    
    def action_generate_report(self):
        self.ensure_one()
        if self.report_type == 'customer':
            return self._generate_customer_report()
        elif self.report_type == 'cpe':
            return self._generate_cpe_report()
        elif self.report_type == 'package':
            return self._generate_package_report()
        elif self.report_type == 'financial':
            return self._generate_financial_report()
        elif self.report_type == 'profit_loss':
            return self._generate_profit_loss_report()
    
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
        
        customers = self.env['isp.customer'].search(domain)
        
        data = {
            'ids': self.ids,
            'model': self._name,
            'form': {
                'date_from': self.date_from,
                'date_to': self.date_to,
                'report_type': self.report_type,
                'name': self.name,
                'customer_ids': customers.ids,
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
        customer_ids = data.get('form', {}).get('customer_ids', [])
        if customer_ids:
            customers = self.env['isp.customer'].browse(customer_ids)
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
            customers = self.env['isp.customer'].search(domain)

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
        
        # Pendapatan Berlangganan
        subscriptions = self.env['isp.subscription'].search([
            ('state', '=', 'open')
        ])
        
        # Filter subscription berdasarkan invoice yang dibuat pada periode tersebut
        filtered_subscriptions = subscriptions.filtered(lambda s: 
            any(inv.invoice_date and 
                date_from <= inv.invoice_date <= date_to 
                for inv in s.invoice_ids)
        )
        
        # Pendapatan Instalasi
        installations = self.env['isp.installation.fee'].search([
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('state', '=', 'paid')
        ])
        
        total_subscription = sum(filtered_subscriptions.mapped('final_amount'))
        total_installation = sum(installations.mapped('amount'))

        return {
            'doc_ids': docids,
            'doc_model': 'isp.report',
            'docs': report,
            'subscriptions': filtered_subscriptions,
            'installations': installations,
            'total_subscription': total_subscription,
            'total_installation': total_installation,
            'company': self.env.company,
        }

class ISPReportProfitLoss(models.AbstractModel):
    _name = 'report.dkt_isp_billing.report_profit_loss'
    _description = 'Profit Loss Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['isp.report'].browse(docids)
        date_from = data['form']['date_from']
        date_to = data['form']['date_to']
        
        # Pendapatan
        subscriptions = self.env['isp.subscription'].search([
            ('recurring_next_date', '>=', date_from),
            ('recurring_next_date', '<=', date_to),
            ('state', '=', 'open')
        ])
        
        installations = self.env['isp.installation.fee'].search([
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            ('state', '=', 'paid')
        ])
        
        total_subscription = sum(subscriptions.mapped('final_amount'))
        total_installation = sum(installations.mapped('amount'))
        total_revenue = total_subscription + total_installation
        
        # Biaya
        device_costs = self.env['isp.device.history'].search([
            ('date', '>=', date_from),
            ('date', '<=', date_to)
        ])
        total_device_cost = sum(device_costs.mapped('cost'))
        
        # Biaya maintenance (contoh)
        total_maintenance = 1000000
        total_cost = total_device_cost + total_maintenance

        return {
            'doc_ids': docids,
            'doc_model': 'isp.report',
            'docs': docs,
            'total_subscription': total_subscription,
            'total_installation': total_installation,
            'total_revenue': total_revenue,
            'total_device_cost': total_device_cost,
            'total_maintenance': total_maintenance,
            'total_cost': total_cost,
        } 