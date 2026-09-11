/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const RELOAD_MS = 60000;

export class IspBillingDashboard extends Component {
    static template = "dkt_isp_billing.IspDashboard";
    static props = { ...standardActionServiceProps };
    static path = "isp-dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            checking: false,
            error: false,
            tab: "ringkasan",
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
            last_pppoe_refresh_display: false,
            pppoe_stale: false,
            network: {
                total: 0,
                connected: 0,
                disconnected: 0,
                unknown: 0,
                isolated: 0,
                multisession: 0,
            },
            customers: {
                partner_active: 0,
                subscription_open: 0,
                review_needed: 0,
                overdue: 0,
            },
            finance: {
                available: false,
                period_label: "",
                draft_count: 0,
                draft_amount_display: "-",
                outstanding_count: 0,
                outstanding_amount_display: "-",
                paid_count: 0,
                paid_amount_display: "-",
            },
            routers: [],
            activity_status: [],
            activity_multi: [],
        };
    }

    async loadData({ silent = false } = {}) {
        if (!silent) {
            this.state.loading = true;
            this.state.error = false;
        }
        try {
            const data = await this.orm.silent.call("isp.dashboard", "get_dashboard_data", []);
            this.state.data = data || this._emptyData();
            this.state.error = false;
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || "Gagal memuat dashboard.";
            if (!silent) {
                this.notification.add(this.state.error, { type: "danger" });
            }
        } finally {
            this.state.loading = false;
        }
    }

    setTab(tab) {
        this.state.tab = tab;
    }

    async openKpi(key, extra = {}) {
        try {
            const action = await this.orm.call("isp.dashboard", "action_open_kpi", [key, extra]);
            if (action) {
                await this.action.doAction(action);
            }
        } catch (error) {
            this.notification.add(
                error?.data?.message || error?.message || "Gagal membuka daftar.",
                { type: "danger" }
            );
        }
    }

    async refreshHealth() {
        this.state.checking = true;
        try {
            const data = await this.orm.call("isp.dashboard", "action_refresh_router_health", []);
            this.state.data = data || this.state.data;
            this.notification.add("Health router diperbarui (TCP/ICMP cache).", { type: "success" });
        } catch (error) {
            this.notification.add(
                error?.data?.message || error?.message || "Gagal cek health router.",
                { type: "danger" }
            );
        } finally {
            this.state.checking = false;
        }
    }

    async checkOneRouter(routerId) {
        this.state.checking = true;
        try {
            await this.orm.call("isp.mikrotik.config", "action_check_health", [[routerId]]);
            await this.loadData({ silent: true });
        } catch (error) {
            this.notification.add(
                error?.data?.message || error?.message || "Gagal cek router.",
                { type: "danger" }
            );
        } finally {
            this.state.checking = false;
        }
    }

    healthClass(state) {
        if (state === "reachable") {
            return "o_isp_badge o_isp_badge_ok";
        }
        if (state === "timeout") {
            return "o_isp_badge o_isp_badge_danger";
        }
        if (state === "inactive" || state === "skipped") {
            return "o_isp_badge o_isp_badge_muted";
        }
        return "o_isp_badge o_isp_badge_warn";
    }

    pingLabel(router) {
        if (!router.last_check_display) {
            return "Belum dicek";
        }
        if (!router.active || router.health_state === "inactive" || router.health_state === "skipped") {
            return router.last_error || router.health_label;
        }
        if (router.last_ping_ok) {
            if (router.last_icmp_ok) {
                return `TCP ${router.last_ping_ms} ms · ICMP ${router.last_icmp_ms} ms`;
            }
            return `TCP ${router.last_ping_ms} ms`;
        }
        return router.last_error || "TCP gagal";
    }
}

registry.category("actions").add("isp_billing_dashboard", IspBillingDashboard);
