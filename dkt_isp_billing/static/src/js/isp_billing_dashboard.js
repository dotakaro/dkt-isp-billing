/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const RELOAD_MS = 60000;

export class IspInvoiceDashboard extends Component {
    static template = "dkt_isp_billing.IspInvoiceDashboard";
    static props = { ...standardActionServiceProps };
    static path = "isp-tagihan";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            error: false,
            data: this._emptyData(),
        });
        this._timer = null;
        onWillStart(() => this.loadData());
        this._timer = setInterval(() => this.loadData({ silent: true }), RELOAD_MS);
        onWillUnmount(() => {
            if (this._timer) {
                clearInterval(this._timer);
                this._timer = null;
            }
        });
    }

    _emptyData() {
        return {
            note: "",
            generated_at_display: false,
            period_label: "",
            kpis: {
                draft_count: 0,
                draft_amount_display: "-",
                outstanding_count: 0,
                outstanding_amount_display: "-",
                partial_count: 0,
                partial_amount_display: "-",
                late_count: 0,
                isolated_count: 0,
                special_count: 0,
                unisolate_failed_count: 0,
                proof_queue_count: 0,
                proof_review_count: 0,
                proof_under_count: 0,
                wa_queue_count: 0,
            },
            areas: [],
        };
    }

    async loadData({ silent = false } = {}) {
        if (!silent) {
            this.state.loading = true;
            this.state.error = false;
        }
        try {
            const data = await this.orm.silent.call(
                "isp.dashboard",
                "get_billing_dashboard_data",
                [],
            );
            this.state.data = data || this._emptyData();
            this.state.error = false;
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || "Gagal memuat dashboard tagihan.";
            if (!silent) {
                this.notification.add(this.state.error, { type: "danger" });
            }
        } finally {
            this.state.loading = false;
        }
    }

    async openKpi(key, extra = {}) {
        try {
            const action = await this.orm.call(
                "isp.dashboard",
                "action_open_billing_kpi",
                [key, extra],
            );
            if (action) {
                await this.action.doAction(action);
            }
        } catch (error) {
            this.notification.add(
                error?.data?.message || error?.message || "Gagal membuka daftar.",
                { type: "danger" },
            );
        }
    }

    areaExtra(area) {
        const extra = { area_name: area.name };
        if (area.area_id) {
            extra.area_id = area.area_id;
        } else {
            extra.no_area = true;
        }
        return extra;
    }
}

registry.category("actions").add("isp_invoice_dashboard", IspInvoiceDashboard);
