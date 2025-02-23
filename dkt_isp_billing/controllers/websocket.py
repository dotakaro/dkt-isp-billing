from odoo import http
from odoo.http import request
import json
import logging
import threading
import time
from routeros_api import RouterOsApi

_logger = logging.getLogger(__name__)

class PPPoEMonitorController(http.Controller):
    
    def __init__(self):
        super().__init__()
        self.clients = {}  # {cpe_id: [websocket]}
        self.monitoring_threads = {}  # {cpe_id: thread}
        
    @http.route('/pppoe/monitor/<int:cpe_id>', type='http', auth='user')
    def monitor_pppoe(self, cpe_id):
        """Endpoint untuk websocket monitoring PPPoE"""
        # Upgrade ke websocket
        request.httprequest.environ['wsgi.websocket'] = request.httprequest.environ['wsgi.websocket_websocket']
        ws = request.httprequest.environ['wsgi.websocket']
        
        # Dapatkan CPE
        cpe = request.env['isp.cpe'].browse(cpe_id)
        if not cpe.exists():
            ws.send(json.dumps({'error': 'CPE not found'}))
            return
            
        # Tambahkan client ke list
        if cpe_id not in self.clients:
            self.clients[cpe_id] = []
        self.clients[cpe_id].append(ws)
        
        # Mulai thread monitoring jika belum ada
        if cpe_id not in self.monitoring_threads:
            thread = threading.Thread(target=self._monitor_pppoe, args=(cpe,))
            thread.daemon = True
            thread.start()
            self.monitoring_threads[cpe_id] = thread
            
        try:
            while True:
                message = ws.receive()
                if message == 'stop':
                    break
        except Exception as e:
            _logger.error(f'Websocket error: {str(e)}')
        finally:
            # Cleanup
            self.clients[cpe_id].remove(ws)
            if not self.clients[cpe_id]:
                del self.clients[cpe_id]
                if cpe_id in self.monitoring_threads:
                    del self.monitoring_threads[cpe_id]
                cpe.write({
                    'is_monitoring': False,
                    'current_upload_rate': False,
                    'current_download_rate': False
                })
                
    def _monitor_pppoe(self, cpe):
        """Thread untuk monitoring PPPoE"""
        while cpe.id in self.clients:
            try:
                # Connect ke router
                api = RouterOsApi({
                    'host': cpe.router_id.ip_address,
                    'username': cpe.router_id.username,
                    'password': cpe.router_id.password,
                    'port': cpe.router_id.port or 8728
                })
                
                # Get PPPoE active
                pppoe = api.get_resource('/ppp/active')
                active = pppoe.get(name=cpe.pppoe_username)
                
                if active:
                    data = active[0]
                    # Convert bytes to MB
                    bytes_in = float(data.get('bytes-in', 0)) / (1024 * 1024)
                    bytes_out = float(data.get('bytes-out', 0)) / (1024 * 1024)
                    
                    # Get rates in kbps
                    rate_in = float(data.get('rate-in', 0)) / 1024
                    rate_out = float(data.get('rate-out', 0)) / 1024
                    
                    # Update CPE
                    cpe.write({
                        'current_upload_rate': f'{rate_out:.1f} kbps',
                        'current_download_rate': f'{rate_in:.1f} kbps',
                        'upload_usage': bytes_out,
                        'download_usage': bytes_in
                    })
                    
                    # Send to websocket clients
                    message = {
                        'status': 'connected',
                        'uptime': data.get('uptime', ''),
                        'address': data.get('address', ''),
                        'bytes_in': bytes_in,
                        'bytes_out': bytes_out,
                        'rate_in': f'{rate_in:.1f}',
                        'rate_out': f'{rate_out:.1f}'
                    }
                else:
                    message = {'status': 'disconnected'}
                    
                # Send ke semua client
                for ws in self.clients.get(cpe.id, []):
                    try:
                        ws.send(json.dumps(message))
                    except Exception as e:
                        _logger.error(f'Error sending to websocket: {str(e)}')
                        continue
                        
            except Exception as e:
                _logger.error(f'Error monitoring PPPoE: {str(e)}')
                message = {'error': str(e)}
                for ws in self.clients.get(cpe.id, []):
                    try:
                        ws.send(json.dumps(message))
                    except:
                        pass
                        
            time.sleep(1)  # Update setiap 1 detik 