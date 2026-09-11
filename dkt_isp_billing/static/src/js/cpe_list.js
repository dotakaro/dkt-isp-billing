/** @odoo-module **/

import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";
import { onMounted, onWillUnmount } from "@odoo/owl";

const CPE_LIST_REFRESH_MS = 45000;

export class CpeListController extends ListController {
    setup() {
        super.setup();
        this._cpeRefreshTimer = null;
        onMounted(() => {
            this._cpeRefreshTimer = setInterval(() => {
                this._autoReloadCpeList();
            }, CPE_LIST_REFRESH_MS);
        });
        onWillUnmount(() => {
            if (this._cpeRefreshTimer) {
                clearInterval(this._cpeRefreshTimer);
                this._cpeRefreshTimer = null;
            }
        });
    }

    async _autoReloadCpeList() {
        if (this.editedRecord) {
            return;
        }
        try {
            if (this.model.load) {
                await this.model.load();
            } else if (this.model.root) {
                await this.model.root.load();
            }
        } catch (error) {
            console.warn("Gagal auto-refresh list CPE:", error);
        }
    }
}

registry.category("views").add("isp_cpe_list", {
    ...listView,
    Controller: CpeListController,
});
