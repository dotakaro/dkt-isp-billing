/** @odoo-module */

import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { formView } from "@web/views/form/form_view";
import { FormController } from "@web/views/form/form_controller";
import { useService } from "@web/core/utils/hooks";

class PPPoEMonitorController extends FormController {
    setup() {
        super.setup(...arguments);
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.rpc = useService("rpc");
        
        this.isMonitoring = false;
        this.monitoringInterval = null;
        
        if (this.props.resModel === "isp.cpe") {
            this._setupMonitoring();
        }
    }

    _setupMonitoring() {
        this.model.addEventListener("update", () => {
            const record = this.model.root;
            if (record.data.is_monitoring && !this.isMonitoring) {
                this._startMonitoring();
            } else if (!record.data.is_monitoring && this.isMonitoring) {
                this._stopMonitoring();
            }
        });
    }

    async _pollStats() {
        try {
            const stats = await this.rpc('/pppoe/monitor/' + this.props.resId);
            
            if (stats.error) {
                console.error("Error polling stats:", stats.error);
                this.notification.add(this.env._t(stats.error), {
                    type: "warning",
                });
                await this._stopMonitoring();
                return;
            }
            
            this.model.root.data.current_upload_rate = stats.rate_out + ' kbps';
            this.model.root.data.current_download_rate = stats.rate_in + ' kbps';
            this.model.root.data.upload_usage = stats.bytes_out;
            this.model.root.data.download_usage = stats.bytes_in;
            this.model.root.data.pppoe_status = stats.status;
            this.model.root.data.pppoe_uptime = stats.uptime;
            this.model.root.data.pppoe_address = stats.address;
            
            this.model.notify();
            
        } catch (error) {
            console.error("Error polling stats:", error);
            if (this.isMonitoring) {
                this.notification.add(this.env._t("Gagal memperbarui statistik"), {
                    type: "warning",
                });
                await this._stopMonitoring();
            }
        }
    }

    async _startMonitoring() {
        if (this.isMonitoring) return;
        
        try {
            await this.rpc('/pppoe/monitor/' + this.props.resId + '/start');
            
            this.isMonitoring = true;
            
            this.monitoringInterval = browser.setInterval(async () => {
                if (this.isMonitoring) {
                    await this._pollStats();
                }
            }, 1000);
            
            await this._pollStats();
            
            this.notification.add(this.env._t("Monitoring dimulai"), {
                type: "info",
            });
        } catch (error) {
            console.error("Error starting monitoring:", error);
            this.notification.add(this.env._t("Gagal memulai monitoring"), {
                type: "warning",
            });
            this.isMonitoring = false;
        }
    }

    async _stopMonitoring() {
        if (!this.isMonitoring) return;
        
        try {
            this.isMonitoring = false;
            
            if (this.monitoringInterval) {
                browser.clearInterval(this.monitoringInterval);
                this.monitoringInterval = null;
            }
            
            await this.rpc('/pppoe/monitor/' + this.props.resId + '/stop');
            await this.model.root.load();
            
            this.notification.add(this.env._t("Monitoring dihentikan"), {
                type: "info",
            });
        } catch (error) {
            console.error("Error stopping monitoring:", error);
            this.notification.add(this.env._t("Gagal menghentikan monitoring"), {
                type: "warning",
            });
        }
    }

    beforeUnmount() {
        if (this.isMonitoring) {
            this._stopMonitoring();
        }
        super.beforeUnmount();
    }
}

registry.category("views").add("isp_cpe_form", {
    ...formView,
    Controller: PPPoEMonitorController,
}); 