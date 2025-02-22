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
    
    def _generate_customer_report(self):
        data = {
            'ids': self.ids,
            'model': self._name,
            'form': {
                'date_from': self.date_from,
                'date_to': self.date_to,
            }
        }
        return self.env.ref('dkt_isp_billing.action_report_customer').report_action(self, data=data)
    
    def _generate_cpe_report(self):
        data = {
            'ids': self.ids,
            'model': self._name,
            'form': {
                'date_from': self.date_from,
                'date_to': self.date_to,
            }
        }
        return self.env.ref('dkt_isp_billing.action_report_cpe').report_action(self, data=data)
    
    def _generate_package_report(self):
        data = {
            'ids': self.ids,
            'model': self._name,
            'form': {
                'date_from': self.date_from,
                'date_to': self.date_to,
            }
        }
        return self.env.ref('dkt_isp_billing.action_report_package').report_action(self, data=data)
    
    def _generate_financial_report(self):
        data = {
            'ids': self.ids,
            'model': self._name,
            'form': {
                'date_from': self.date_from,
                'date_to': self.date_to,
            }
        }
        return self.env.ref('dkt_isp_billing.action_report_financial').report_action(self, data=data)
    
    def _generate_profit_loss_report(self):
        data = {
            'ids': self.ids,
            'model': self._name,
            'form': {
                'date_from': self.date_from,
                'date_to': self.date_to,
            }
        }
        return self.env.ref('dkt_isp_billing.action_report_profit_loss').report_action(self, data=data)

    def _get_report_values(self, docids, data=None):
        docs = self.env['isp.report'].browse(docids)
        date_from = data['form']['date_from']
        date_to = data['form']['date_to']
        
        if docs.report_type == 'customer':
            customers = self.env['isp.customer'].search([
                ('create_date', '>=', date_from),
                ('create_date', '<=', date_to)
            ])
            return {
                'doc_ids': docids,
                'doc_model': 'isp.report',
                'docs': docs,
                'customers': customers,
            }
        elif docs.report_type == 'cpe':
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
        elif docs.report_type == 'package':
            packages = self.env['isp.package'].search([])
            return {
                'doc_ids': docids,
                'doc_model': 'isp.report',
                'docs': docs,
                'packages': packages,
            }
        elif docs.report_type in ['financial', 'profit_loss']:
            subscriptions = self.env['isp.subscription'].search([
                ('recurring_next_date', '>=', date_from),
                ('recurring_next_date', '<=', date_to)
            ])
            installations = self.env['isp.installation.fee'].search([
                ('date', '>=', date_from),
                ('date', '<=', date_to)
            ])
            
            total_subscription = sum(subscriptions.mapped('recurring_total'))
            total_installation = sum(installations.mapped('amount'))
            
            device_costs = self.env['isp.device.history'].search([
                ('date', '>=', date_from),
                ('date', '<=', date_to)
            ])
            total_device_cost = sum(device_costs.mapped('cost'))
            
            # Contoh biaya maintenance (bisa disesuaikan)
            total_maintenance = 1000000
            
            return {
                'doc_ids': docids,
                'doc_model': 'isp.report',
                'docs': docs,
                'subscriptions': subscriptions,
                'installations': installations,
                'total_subscription': total_subscription,
                'total_installation': total_installation,
                'total_revenue': total_subscription + total_installation,
                'total_device_cost': total_device_cost,
                'total_maintenance': total_maintenance,
                'total_cost': total_device_cost + total_maintenance,
            } 