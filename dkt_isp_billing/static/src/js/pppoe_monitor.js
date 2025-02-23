/** @odoo-module */

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";

class PPPoEMonitor extends Component {
    setup() {
        this.cpe_id = this.props.action.params.cpe_id;
        this.websocket = null;
        this.state = {
            current_upload_rate: '0 kbps',
            current_download_rate: '0 kbps',
            upload_usage: 0,
            download_usage: 0,
            pppoe_status: 'unknown',
            pppoe_uptime: '',
            pppoe_address: ''
        };
        
        this._initWebSocket();
    }
    
    _initWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = protocol + '//' + window.location.host + '/pppoe/monitor/' + this.cpe_id;
        
        this.websocket = new WebSocket(wsUrl);
        
        this.websocket.onopen = () => {
            console.log('WebSocket connected');
        };
        
        this.websocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this._updateStats(data);
        };
        
        this.websocket.onclose = () => {
            console.log('WebSocket closed');
            // Reload form view
            this.env.services.action.doAction({
                type: 'ir.actions.act_window',
                res_model: 'isp.cpe',
                res_id: this.cpe_id,
                views: [[false, 'form']],
                target: 'current'
            });
        };
        
        this.websocket.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }
    
    _updateStats(data) {
        if (data.error) {
            this.env.services.notification.add(data.error, {
                type: 'warning',
                title: this.env._t('Error'),
                sticky: false
            });
            return;
        }
        
        // Update state
        this.state = {
            current_upload_rate: data.rate_out + ' kbps',
            current_download_rate: data.rate_in + ' kbps',
            upload_usage: data.bytes_out,
            download_usage: data.bytes_in,
            pppoe_status: data.status,
            pppoe_uptime: data.uptime,
            pppoe_address: data.address
        };
        
        // Update model
        this.orm.write('isp.cpe', [this.cpe_id], {
            current_upload_rate: data.rate_out + ' kbps',
            current_download_rate: data.rate_in + ' kbps',
            upload_usage: data.bytes_out,
            download_usage: data.bytes_in,
            pppoe_status: data.status,
            pppoe_uptime: data.uptime,
            pppoe_address: data.address
        });
    }
    
    willDestroy() {
        if (this.websocket) {
            this.websocket.close();
        }
    }
}

PPPoEMonitor.template = 'PPPoEMonitor';

registry.category('actions').add('pppoe_monitor', PPPoEMonitor); 