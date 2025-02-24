from odoo import http, fields
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)

class PPPoEMonitorController(http.Controller):
    
    def _check_module_installed(self):
        """Cek apakah modul sudah terinstall"""
        module = request.env['ir.module.module'].sudo().search([
            ('name', '=', 'dkt_isp_billing'),
            ('state', '=', 'installed')
        ], limit=1)
        return bool(module)
    
    @http.route(['/pppoe/monitor/<int:cpe_id>'], type='json', auth='user', csrf=False)
    def pppoe_monitor(self, cpe_id, **kwargs):
        """Endpoint untuk monitoring PPPoE via polling"""
        if not self._check_module_installed():
            return {'error': 'Module not installed'}
            
        try:
            _logger.debug(f'Polling stats for CPE {cpe_id}')
            
            # Validasi akses
            cpe = request.env['isp.cpe'].sudo().browse(cpe_id)
            if not cpe.exists():
                _logger.warning(f'CPE {cpe_id} not found')
                return {'error': 'CPE tidak ditemukan'}
                
            # Cek apakah user punya akses ke CPE ini
            if not request.env.user.has_group('dkt_isp_billing.group_isp_user'):
                _logger.warning(f'User {request.env.user.id} does not have access to CPE {cpe_id}')
                return {'error': 'Tidak memiliki akses'}
                
            # Ambil data statistik
            stats = self._get_pppoe_stats(cpe)
            if not stats:
                _logger.warning(f'Could not get stats for CPE {cpe_id}')
                return {'error': 'Tidak dapat mengambil data statistik'}
                
            # Update data di model
            try:
                cpe.write({
                    'is_monitoring': True,
                    'current_upload_rate': stats['rate_out'] + ' kbps',
                    'current_download_rate': stats['rate_in'] + ' kbps',
                    'upload_usage': stats['bytes_out'],
                    'download_usage': stats['bytes_in'],
                    'pppoe_status': stats['status'],
                    'pppoe_uptime': stats['uptime'],
                    'pppoe_address': stats['address'],
                    'last_update': fields.Datetime.now()
                })
                _logger.debug(f'Successfully updated stats for CPE {cpe_id}')
            except Exception as e:
                _logger.error(f'Error updating CPE {cpe_id} stats: {str(e)}')
                return {'error': f'Gagal memperbarui data: {str(e)}'}
                
            return stats
            
        except Exception as e:
            _logger.error(f'Error in monitor endpoint for CPE {cpe_id}: {str(e)}')
            return {'error': str(e)}

    @http.route(['/pppoe/monitor/<int:cpe_id>/start'], type='json', auth='user', csrf=False)
    def start_monitor(self, cpe_id, **kwargs):
        """Endpoint untuk memulai monitoring"""
        if not self._check_module_installed():
            return {'error': 'Module not installed'}
            
        try:
            _logger.debug(f'Starting monitor for CPE {cpe_id}')
            
            cpe = request.env['isp.cpe'].sudo().browse(cpe_id)
            if not cpe.exists():
                _logger.warning(f'CPE {cpe_id} not found')
                return {'error': 'CPE tidak ditemukan'}
                
            try:
                cpe.write({
                    'is_monitoring': True,
                    'last_update': fields.Datetime.now()
                })
                _logger.debug(f'Successfully started monitoring for CPE {cpe_id}')
            except Exception as e:
                _logger.error(f'Error starting monitor for CPE {cpe_id}: {str(e)}')
                return {'error': f'Gagal memulai monitoring: {str(e)}'}
                
            return {'status': 'success', 'message': 'Monitoring started'}
            
        except Exception as e:
            _logger.error(f'Error in start monitor endpoint for CPE {cpe_id}: {str(e)}')
            return {'error': str(e)}

    @http.route(['/pppoe/monitor/<int:cpe_id>/stop'], type='json', auth='user', csrf=False)
    def stop_monitor(self, cpe_id, **kwargs):
        """Endpoint untuk menghentikan monitoring"""
        if not self._check_module_installed():
            return {'error': 'Module not installed'}
            
        try:
            _logger.debug(f'Stopping monitor for CPE {cpe_id}')
            
            cpe = request.env['isp.cpe'].sudo().browse(cpe_id)
            if not cpe.exists():
                _logger.warning(f'CPE {cpe_id} not found')
                return {'error': 'CPE tidak ditemukan'}
                
            try:
                cpe.write({
                    'is_monitoring': False,
                    'current_upload_rate': '0 kbps',
                    'current_download_rate': '0 kbps'
                })
                _logger.debug(f'Successfully stopped monitoring for CPE {cpe_id}')
            except Exception as e:
                _logger.error(f'Error stopping monitor for CPE {cpe_id}: {str(e)}')
                return {'error': f'Gagal menghentikan monitoring: {str(e)}'}
                
            return {'status': 'success', 'message': 'Monitoring stopped'}
            
        except Exception as e:
            _logger.error(f'Error in stop monitor endpoint for CPE {cpe_id}: {str(e)}')
            return {'error': str(e)}
    
    def _get_pppoe_stats(self, cpe):
        """Ambil statistik PPPoE dari Mikrotik"""
        try:
            mikrotik = request.env['isp.mikrotik.config'].sudo().search([('active', '=', True)], limit=1)
            if not mikrotik:
                _logger.warning('No active Mikrotik configuration found')
                return None
                
            api = mikrotik.get_connection()
            if not api:
                _logger.warning('Could not connect to Mikrotik')
                return None
                
            try:
                # Ambil data dari Mikrotik
                active_api = api.get_resource('/ppp/active')
                active = active_api.get(name=cpe.pppoe_username)
                
                if active:
                    active = active[0]
                    interface = active.get('interface')
                    
                    # Ambil data traffic
                    stats = {}
                    if interface:
                        interface_api = api.get_resource('/interface')
                        interface_stats = interface_api.get(name=interface)
                        if interface_stats:
                            stats = interface_stats[0]
                            
                    return {
                        'status': 'connected',
                        'uptime': active.get('uptime', ''),
                        'address': active.get('address', ''),
                        'rate_in': self._format_rate(stats.get('rx-byte', 0)),
                        'rate_out': self._format_rate(stats.get('tx-byte', 0)),
                        'bytes_in': float(stats.get('rx-byte', 0)) / (1024 * 1024),
                        'bytes_out': float(stats.get('tx-byte', 0)) / (1024 * 1024)
                    }
                else:
                    return {
                        'status': 'disconnected',
                        'uptime': '',
                        'address': '',
                        'rate_in': '0',
                        'rate_out': '0',
                        'bytes_in': 0,
                        'bytes_out': 0
                    }
                    
            finally:
                if api and hasattr(api, 'connection_pool'):
                    api.connection_pool.disconnect()
                    
        except Exception as e:
            _logger.error(f'Error getting PPPoE stats: {str(e)}')
            return None
    
    def _format_rate(self, bytes_val):
        """Format byte rate ke kbps"""
        try:
            return str(int(float(bytes_val) * 8 / 1024))
        except:
            return '0' 