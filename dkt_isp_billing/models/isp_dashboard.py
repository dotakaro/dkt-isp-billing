from datetime import timedelta

from odoo import api, fields, models


class IspDashboard(models.AbstractModel):
    _name = 'isp.dashboard'
    _description = 'Dashboard Operasional ISP'

    _CPE_STALE_MINUTES = 15
    _ACTIVITY_STATUS_LIMIT = 15
    _ACTIVITY_MULTI_LIMIT = 10

    @api.model
    def get_dashboard_data(self):
        """Satu RPC: KPI via read_group/SQL. Tidak ping router, tidak N+1 CPE."""
        Cpe = self.env['isp.cpe']
        Sub = self.env['isp.subscription']
        Partner = self.env['res.partner']
        History = self.env['isp.cpe.pppoe.history']
        Router = self.env['isp.mikrotik.config']

        status_map = {
            status or 'unknown': count
            for status, count in Cpe._read_group([], ['pppoe_status'], ['__count'])
        }
        state_map = {
            state or 'draft': count
            for state, count in Cpe._read_group([], ['state'], ['__count'])
        }
        network = {
            'total': sum(status_map.values()),
            'connected': status_map.get('connected', 0),
            'disconnected': status_map.get('disconnected', 0),
            'unknown': status_map.get('unknown', 0),
            'isolated': state_map.get('isolated', 0),
            'multisession': Cpe.search_count([('is_multisession', '=', True)]),
        }

        sub_state = {
            state or 'draft': count
            for state, count in Sub._read_group([], ['state'], ['__count'])
        }
        customers = {
            'partner_active': Partner.search_count([
                ('customer_rank', '>', 0),
                ('state', '=', 'active'),
            ]),
            'subscription_open': sub_state.get('open', 0),
            'review_needed': Sub.search_count([('is_special_treatment', '=', True)]),
            'overdue': Sub.search_count([
                '|',
                ('is_late', '=', True),
                ('overdue_marked', '=', True),
            ]),
        }

        last_pppoe = self._last_pppoe_refresh()
        now = fields.Datetime.now()
        pppoe_stale = bool(
            last_pppoe and (now - last_pppoe) > timedelta(minutes=self._CPE_STALE_MINUTES)
        )
        return {
            'generated_at': fields.Datetime.to_string(now),
            'generated_at_display': self._fmt_dt(now),
            'last_pppoe_refresh': fields.Datetime.to_string(last_pppoe) if last_pppoe else False,
            'last_pppoe_refresh_display': self._fmt_dt(last_pppoe),
            'pppoe_stale': pppoe_stale,
            'note': (
                'Angka CPE dari cron refresh (2 menit), bukan live per perangkat. '
                'Health router dari cache ping; buka halaman tidak mem-ping. '
                'Isolir tetap manual. Data Bulan Jahe bisa usang jika cron gagal.'
            ),
            'network': network,
            'customers': customers,
            'finance': self._get_finance_kpis(),
            'routers': self._get_router_rows(Cpe, Router, now),
            'activity_status': self._get_activity(
                History, [('event_type', '=', 'status_change')],
                self._ACTIVITY_STATUS_LIMIT,
            ),
            'activity_multi': self._get_activity(
                History,
                [('event_type', 'in', ['multisession_enter', 'multisession_leave'])],
                self._ACTIVITY_MULTI_LIMIT,
            ),
        }

    @api.model
    def action_open_kpi(self, key, extra=None):
        """Buka list/form existing dengan domain yang sama dengan kartu KPI."""
        extra = extra or {}
        opener = {
            'cpe_total': self._action_cpe,
            'cpe_connected': lambda: self._action_cpe(
                [('pppoe_status', '=', 'connected')], 'CPE Connected',
                {'search_default_connected': 1},
            ),
            'cpe_disconnected': lambda: self._action_cpe(
                [('pppoe_status', '=', 'disconnected')], 'CPE Disconnected',
                {'search_default_disconnected': 1},
            ),
            'cpe_unknown': lambda: self._action_cpe(
                [('pppoe_status', '=', 'unknown')], 'CPE status unknown',
            ),
            'cpe_isolated': lambda: self._action_cpe(
                [('state', '=', 'isolated')], 'CPE terisolir',
                {'search_default_isolated': 1},
            ),
            'cpe_multisession': lambda: self._action_cpe(
                [('is_multisession', '=', True)], 'CPE multi-sesi',
                {'search_default_multisession': 1},
            ),
            'cpe_router': lambda: self._action_cpe_router(extra),
            'partner_active': self._action_partner_active,
            'subscription_open': lambda: self._action_subscription(
                [('state', '=', 'open')], 'Langganan open',
                {'search_default_open': 1},
            ),
            'subscription_review': lambda: self._action_subscription(
                [('is_special_treatment', '=', True)], 'Perlakuan khusus',
                {'search_default_special_treatment': 1},
            ),
            'subscription_overdue': lambda: self._action_subscription(
                ['|', ('is_late', '=', True), ('overdue_marked', '=', True)],
                'Langganan telat',
                {'search_default_late': 1},
            ),
            'invoice_draft_month': self._action_invoice_draft_month,
            'invoice_outstanding': self._action_invoice_outstanding,
            'payment_month': self._action_payment_month,
            'history_status': lambda: self._action_history(
                [('event_type', '=', 'status_change')], 'Perubahan status PPPoE',
            ),
            'history_multisession': lambda: self._action_history(
                [('event_type', 'in', ['multisession_enter', 'multisession_leave'])],
                'Masuk/keluar multi-sesi',
            ),
            'router_form': lambda: self._action_router_form(extra),
            'cpe_form': lambda: self._action_cpe_form(extra),
        }
        method = opener.get(key)
        if not method:
            return False
        return method()

    @api.model
    def get_billing_dashboard_data(self):
        """KPI + ringkasan per desa untuk operasi tagihan. Tidak membuat/post invoice."""
        start, end = self._month_bounds()
        now = fields.Datetime.now()
        return {
            'generated_at': fields.Datetime.to_string(now),
            'generated_at_display': self._fmt_dt(now),
            'period_label': self.env['isp.subscription']._period_label(start),
            'note': (
                'Dashboard ini hanya menampilkan antrian yang sudah ada. '
                'Draft dibuat lewat Proses tagihan. Isolir tetap manual per desa. '
                'Bukti WA/kas diverifikasi di menu Keuangan.'
            ),
            'kpis': self._get_billing_kpis(start, end),
            'areas': self._get_billing_area_rows(),
        }

    @api.model
    def action_open_billing_kpi(self, key, extra=None):
        """Buka wizard/list operasi tagihan dari kartu dashboard."""
        extra = extra or {}
        opener = {
            'billing_run': lambda: self._action_from_xmlid(
                'dkt_isp_billing.action_isp_billing_run_wizard',
            ),
            'invoice_draft_month': self._action_invoice_draft_month,
            'invoice_outstanding': lambda: self._action_invoice_outstanding_filtered(extra),
            'invoice_partial': self._action_invoice_partial,
            'invoice_posted': lambda: self._action_from_xmlid(
                'dkt_isp_billing.action_isp_invoice_posted',
            ),
            'subscription_overdue': lambda: self._action_subscription(
                self._domain_subscription_late(),
                'Langganan telat',
                {'search_default_late': 1},
            ),
            'subscription_review': lambda: self._action_subscription(
                [('is_special_treatment', '=', True)], 'Perlakuan khusus',
                {'search_default_special_treatment': 1},
            ),
            'subscription_isolated': lambda: self._action_subscription(
                [('state', '=', 'isolated')], 'Langganan terisolir',
            ),
            'unisolate_failed': lambda: self._action_from_xmlid(
                'dkt_isp_billing.action_isp_unisolate_failed',
            ),
            'isolate_bulk': lambda: self._action_from_xmlid(
                'dkt_isp_billing.action_isp_isolate_bulk_wizard',
            ),
            'proof_queue': lambda: self._action_from_xmlid(
                'dkt_isp_billing.action_isp_payment_proof_verify',
            ),
            'proof_review': lambda: self._action_from_xmlid(
                'dkt_isp_billing.action_isp_payment_proof_review',
            ),
            'proof_underpayment': lambda: self._action_proofs(
                [('is_underpayment', '=', True)], 'Pembayaran kurang',
            ),
            'wa_prepare': lambda: self._action_from_xmlid(
                'dkt_isp_billing.action_isp_notification_prepare_wizard',
            ),
            'wa_queue': lambda: self._action_from_xmlid(
                'dkt_isp_billing.action_isp_notification_queue',
            ),
            'area_outstanding': lambda: self._action_invoice_outstanding_filtered(extra),
            'area_late': lambda: self._action_subscription_late_filtered(extra),
            'area_isolated': lambda: self._action_subscription_isolated_filtered(extra),
            'area_proofs': lambda: self._action_proofs_filtered(extra),
        }
        method = opener.get(key)
        if not method:
            return False
        return method()

    @api.model
    def action_refresh_router_health(self):
        """Ping router area (bukan CPE), lalu kembalikan data dashboard."""
        self.env['isp.mikrotik.config']._cron_check_router_health()
        return self.get_dashboard_data()

    def _month_bounds(self):
        today = fields.Date.context_today(self)
        start = today.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1, day=1) - timedelta(days=1)
        return start, end

    def _fmt_dt(self, dt):
        if not dt:
            return False
        if isinstance(dt, str):
            dt = fields.Datetime.to_datetime(dt)
        local = fields.Datetime.context_timestamp(self, dt)
        return local.strftime('%d/%m/%Y %H:%M')

    def _fmt_amount(self, amount):
        currency = self.env.company.currency_id
        return currency.format(amount or 0.0)

    def _last_pppoe_refresh(self):
        self.env.cr.execute(
            'SELECT MAX(last_update) FROM isp_cpe WHERE last_update IS NOT NULL'
        )
        row = self.env.cr.fetchone()
        return row[0] if row and row[0] else False

    def _get_finance_kpis(self):
        Move = self.env['account.move']
        empty = {
            'available': False,
            'currency_name': self.env.company.currency_id.name,
            'draft_count': 0,
            'draft_amount': 0.0,
            'draft_amount_display': self._fmt_amount(0),
            'outstanding_count': 0,
            'outstanding_amount': 0.0,
            'outstanding_amount_display': self._fmt_amount(0),
            'paid_count': 0,
            'paid_amount': 0.0,
            'paid_amount_display': self._fmt_amount(0),
            'period_label': self.env['isp.subscription']._period_label(
                fields.Date.context_today(self),
            ),
        }
        if not Move.has_access('read'):
            return empty
        start, end = self._month_bounds()
        draft = self._sum_moves(self._domain_invoice_draft_month(start, end), 'amount_total')
        outstanding = self._sum_moves(self._domain_invoice_outstanding(), 'amount_residual')
        paid_count, paid_amount = self._payment_month_kpis(start, end)
        return {
            'available': True,
            'currency_name': self.env.company.currency_id.name,
            'draft_count': draft[0],
            'draft_amount': draft[1],
            'draft_amount_display': self._fmt_amount(draft[1]),
            'outstanding_count': outstanding[0],
            'outstanding_amount': outstanding[1],
            'outstanding_amount_display': self._fmt_amount(outstanding[1]),
            'paid_count': paid_count,
            'paid_amount': paid_amount,
            'paid_amount_display': self._fmt_amount(paid_amount),
            'period_label': self.env['isp.subscription']._period_label(start),
        }

    def _sum_moves(self, domain, amount_field):
        grouped = self.env['account.move']._read_group(
            domain, [], ['__count', '%s:sum' % amount_field],
        )
        if not grouped:
            return 0, 0.0
        return grouped[0][0] or 0, grouped[0][1] or 0.0

    def _domain_invoice_draft_month(self, start=None, end=None):
        if not start or not end:
            start, end = self._month_bounds()
        return [
            ('move_type', '=', 'out_invoice'),
            ('subscription_id', '!=', False),
            ('isp_invoice_kind', '!=', 'installation'),
            ('state', '=', 'draft'),
            ('invoice_date', '>=', start),
            ('invoice_date', '<=', end),
        ]

    def _domain_invoice_outstanding(self):
        return [
            ('move_type', '=', 'out_invoice'),
            ('subscription_id', '!=', False),
            ('isp_invoice_kind', '!=', 'installation'),
            ('state', '=', 'posted'),
            ('amount_residual', '>', 0),
            ('payment_state', 'in', ['not_paid', 'partial', 'in_payment']),
        ]

    def _domain_payment_month(self, start=None, end=None):
        if not start or not end:
            start, end = self._month_bounds()
        return [
            ('partner_type', '=', 'customer'),
            ('payment_type', '=', 'inbound'),
            ('state', '=', 'paid'),
            ('date', '>=', start),
            ('date', '<=', end),
        ]

    def _payment_month_kpis(self, start, end):
        Payment = self.env['account.payment']
        if not Payment.has_access('read'):
            return 0, 0.0
        grouped = Payment._read_group(
            self._domain_payment_month(start, end), [], ['__count', 'amount:sum'],
        )
        if not grouped:
            return 0, 0.0
        return grouped[0][0] or 0, grouped[0][1] or 0.0

    def _get_router_rows(self, Cpe, Router, now):
        status_rows = Cpe._read_group(
            [], ['mikrotik_config_id', 'pppoe_status'], ['__count'],
        )
        multi_rows = Cpe._read_group(
            [('is_multisession', '=', True)], ['mikrotik_config_id'], ['__count'],
        )
        iso_rows = Cpe._read_group(
            [('state', '=', 'isolated')], ['mikrotik_config_id'], ['__count'],
        )
        by_router = {}
        for router, status, count in status_rows:
            rid = router.id if router else 0
            bucket = by_router.setdefault(rid, {
                'connected': 0, 'disconnected': 0, 'unknown': 0, 'total': 0,
            })
            key = status or 'unknown'
            if key not in bucket:
                bucket[key] = 0
            bucket[key] += count
            bucket['total'] += count
        multi_map = {
            (router.id if router else 0): count for router, count in multi_rows
        }
        iso_map = {
            (router.id if router else 0): count for router, count in iso_rows
        }
        self.env.cr.execute(
            'SELECT mikrotik_config_id, MAX(last_update) FROM isp_cpe '
            'GROUP BY mikrotik_config_id'
        )
        last_cpe = {row[0] or 0: row[1] for row in self.env.cr.fetchall()}
        stale_after = timedelta(minutes=self._CPE_STALE_MINUTES)
        rows = []
        for router in Router.with_context(active_test=False).search(
            [], order='active desc, name',
        ):
            stats = by_router.get(router.id, {
                'connected': 0, 'disconnected': 0, 'unknown': 0, 'total': 0,
            })
            total = stats['total']
            connected = stats.get('connected', 0)
            last_upd = last_cpe.get(router.id)
            rows.append({
                'id': router.id,
                'name': router.name,
                'identity_name': router.identity_name or False,
                'area': router.area_id.display_name if router.area_id else False,
                'area_id': router.area_id.id if router.area_id else False,
                'active': bool(router.active),
                'health_state': router.health_state or ('inactive' if not router.active else False),
                'health_label': self._health_label(router),
                'last_ping_ok': bool(router.last_ping_ok),
                'last_ping_ms': router.last_ping_ms or 0,
                'last_icmp_ok': bool(router.last_icmp_ok),
                'last_icmp_ms': router.last_icmp_ms or 0,
                'last_check': fields.Datetime.to_string(router.last_check) if router.last_check else False,
                'last_check_display': self._fmt_dt(router.last_check),
                'last_error': router.last_error or False,
                'host': router.host or False,
                'cpe_total': total,
                'cpe_connected': connected,
                'cpe_disconnected': stats.get('disconnected', 0),
                'cpe_unknown': stats.get('unknown', 0),
                'cpe_isolated': iso_map.get(router.id, 0),
                'cpe_multisession': multi_map.get(router.id, 0),
                'cpe_online_pct': round((connected * 100.0 / total), 1) if total else 0.0,
                'cpe_stale': bool(last_upd and (now - last_upd) > stale_after),
                'last_cpe_update_display': self._fmt_dt(last_upd),
            })
        return rows

    def _health_label(self, router):
        labels = {
            'reachable': 'Terjangkau',
            'timeout': 'Timeout',
            'inactive': 'Nonaktif',
            'skipped': 'Dilewati',
        }
        if router.health_state:
            return labels.get(router.health_state, router.health_state)
        if not router.active:
            return 'Nonaktif'
        return 'Belum dicek'

    def _get_activity(self, History, domain, limit):
        records = History.search_read(
            domain,
            [
                'timestamp', 'cpe_id', 'partner_id', 'old_status', 'new_status',
                'event_type', 'session_count', 'router_id', 'source',
            ],
            limit=limit,
            order='timestamp desc, id desc',
        )
        event_labels = {
            'status_change': 'Perubahan status',
            'multisession_enter': 'Masuk multi-sesi',
            'multisession_leave': 'Keluar multi-sesi',
        }
        rows = []
        for rec in records:
            cpe = rec.get('cpe_id')
            partner = rec.get('partner_id')
            router = rec.get('router_id')
            rows.append({
                'id': rec['id'],
                'timestamp_display': self._fmt_dt(rec.get('timestamp')),
                'cpe_id': cpe[0] if cpe else False,
                'cpe_name': cpe[1] if cpe else False,
                'partner_id': partner[0] if partner else False,
                'partner_name': partner[1] if partner else False,
                'old_status': rec.get('old_status') or '',
                'new_status': rec.get('new_status') or '',
                'event_type': rec.get('event_type'),
                'event_label': event_labels.get(rec.get('event_type'), rec.get('event_type')),
                'session_count': rec.get('session_count') or 0,
                'router_name': router[1] if router else False,
                'source': rec.get('source') or '',
            })
        return rows

    def _action_from_xmlid(self, xmlid, **overrides):
        action = self.env['ir.actions.act_window']._for_xml_id(xmlid)
        action.update(overrides)
        return action

    def _action_cpe(self, domain=None, name='CPE', context=None):
        ctx = {'search_default_open': 0}
        if context:
            ctx.update(context)
        return self._action_from_xmlid(
            'dkt_isp_billing.action_isp_cpe',
            name=name,
            domain=domain or [],
            context=ctx,
        )

    def _action_cpe_router(self, extra):
        router_id = extra.get('router_id')
        domain = [('mikrotik_config_id', '=', router_id)] if router_id else []
        name = 'CPE'
        ctx = {}
        if extra.get('pppoe_status'):
            domain.append(('pppoe_status', '=', extra['pppoe_status']))
            name = 'CPE %s' % extra['pppoe_status']
            ctx['search_default_%s' % extra['pppoe_status']] = 1
        if extra.get('is_multisession'):
            domain.append(('is_multisession', '=', True))
            name = 'CPE multi-sesi'
            ctx['search_default_multisession'] = 1
        if extra.get('state'):
            domain.append(('state', '=', extra['state']))
            name = 'CPE %s' % extra['state']
        if extra.get('router_name'):
            name = '%s — %s' % (name, extra['router_name'])
        return self._action_cpe(domain, name, ctx)

    def _action_partner_active(self):
        return self._action_from_xmlid(
            'dkt_isp_billing.action_isp_customer',
            name='Pelanggan aktif',
            domain=[('customer_rank', '>', 0), ('state', '=', 'active')],
            context={
                'default_customer_rank': 1,
                'search_default_customer': 1,
                'search_default_active': 1,
                'res_partner_search_mode': 'customer',
            },
        )

    def _action_subscription(self, domain, name, context=None):
        return self._action_from_xmlid(
            'dkt_isp_billing.action_isp_subscription',
            name=name,
            domain=domain,
            context=context or {},
        )

    def _action_invoice_draft_month(self):
        start, end = self._month_bounds()
        return self._action_from_xmlid(
            'dkt_isp_billing.action_isp_invoice_draft',
            name='Draft invoice %s' % self.env['isp.subscription']._period_label(start),
            domain=self._domain_invoice_draft_month(start, end),
        )

    def _domain_invoice_partial(self):
        return [
            ('move_type', '=', 'out_invoice'),
            ('subscription_id', '!=', False),
            ('isp_invoice_kind', '!=', 'installation'),
            ('state', '=', 'posted'),
            ('payment_state', '=', 'partial'),
        ]

    def _domain_subscription_late(self):
        return [
            '&',
            ('is_special_treatment', '=', False),
            '|',
            ('is_late', '=', True),
            ('overdue_marked', '=', True),
        ]

    def _domain_proof_queue(self):
        return [('state', 'in', ('submitted', 'review'))]

    def _get_billing_kpis(self, start, end):
        Sub = self.env['isp.subscription']
        Proof = self.env['isp.payment.proof']
        Partner = self.env['res.partner']
        draft = (0, 0.0)
        outstanding = (0, 0.0)
        partial = (0, 0.0)
        if self.env['account.move'].has_access('read'):
            draft = self._sum_moves(self._domain_invoice_draft_month(start, end), 'amount_total')
            outstanding = self._sum_moves(self._domain_invoice_outstanding(), 'amount_residual')
            partial = self._sum_moves(self._domain_invoice_partial(), 'amount_residual')
        late = Sub.search_count(self._domain_subscription_late())
        isolated = Sub.search_count([('state', '=', 'isolated')])
        special = Sub.search_count([('is_special_treatment', '=', True)])
        unisolate = Sub.search_count([('unisolate_failed', '=', True)])
        proof_queue = 0
        proof_review = 0
        proof_under = 0
        if Proof.has_access('read'):
            proof_queue = Proof.search_count(self._domain_proof_queue())
            proof_review = Proof.search_count([('state', '=', 'review')])
            proof_under = Proof.search_count([
                ('is_underpayment', '=', True),
                ('invoice_id.payment_state', 'in', ('not_paid', 'partial', 'in_payment')),
                ('invoice_id.amount_residual', '>', 0),
            ])
        wa_queue = Partner.search_count([
            ('customer_rank', '>', 0),
            ('isp_notif_prepared', '=', True),
        ])
        return {
            'draft_count': draft[0],
            'draft_amount_display': self._fmt_amount(draft[1]),
            'outstanding_count': outstanding[0],
            'outstanding_amount_display': self._fmt_amount(outstanding[1]),
            'partial_count': partial[0],
            'partial_amount_display': self._fmt_amount(partial[1]),
            'late_count': late,
            'isolated_count': isolated,
            'special_count': special,
            'unisolate_failed_count': unisolate,
            'proof_queue_count': proof_queue,
            'proof_review_count': proof_review,
            'proof_under_count': proof_under,
            'wa_queue_count': wa_queue,
        }

    def _get_billing_area_rows(self):
        """Ringkas nunggak / telat / isolir / antrian bukti per desa."""
        Area = self.env['isp.area']
        areas = {area.id: area.display_name for area in Area.search([], order='name')}
        outstanding = self._group_moves_by_area(self._domain_invoice_outstanding())
        late = self._group_subs_by_area(self._domain_subscription_late())
        isolated = self._group_subs_by_area([('state', '=', 'isolated')])
        proofs = {}
        if self.env['isp.payment.proof'].has_access('read'):
            proofs = self._group_count_by_area(
                self.env['isp.payment.proof'], self._domain_proof_queue(),
            )
        ids = set(areas) | set(outstanding) | set(late) | set(isolated) | set(proofs)
        rows = []
        for area_id in ids:
            out = outstanding.get(area_id, {'count': 0, 'amount': 0.0})
            late_count = late.get(area_id, 0)
            iso_count = isolated.get(area_id, 0)
            proof_count = proofs.get(area_id, 0)
            if not (out['count'] or late_count or iso_count or proof_count):
                continue
            rows.append({
                'area_id': area_id or 0,
                'name': areas.get(area_id) or 'Tanpa area',
                'outstanding_count': out['count'],
                'outstanding_amount_display': self._fmt_amount(out['amount']),
                'late_count': late_count,
                'isolated_count': iso_count,
                'proof_count': proof_count,
            })
        rows.sort(key=lambda row: (-row['outstanding_count'], row['name'] or ''))
        return rows

    def _group_moves_by_area(self, domain):
        if not self.env['account.move'].has_access('read'):
            return {}
        grouped = self.env['account.move']._read_group(
            domain,
            ['subscription_id'],
            ['__count', 'amount_residual:sum'],
        )
        by_sub = {}
        sub_ids = []
        for sub, count, amount in grouped:
            if not sub:
                by_sub.setdefault(0, {'count': 0, 'amount': 0.0})
                by_sub[0]['count'] += count or 0
                by_sub[0]['amount'] += amount or 0.0
                continue
            by_sub[sub.id] = {'count': count or 0, 'amount': amount or 0.0}
            sub_ids.append(sub.id)
        subs = self.env['isp.subscription'].browse(sub_ids)
        result = {}
        for sub in subs:
            area_id = sub.area_id.id or 0
            bucket = result.setdefault(area_id, {'count': 0, 'amount': 0.0})
            stats = by_sub.get(sub.id) or {'count': 0, 'amount': 0.0}
            bucket['count'] += stats['count']
            bucket['amount'] += stats['amount']
        if 0 in by_sub:
            bucket = result.setdefault(0, {'count': 0, 'amount': 0.0})
            bucket['count'] += by_sub[0]['count']
            bucket['amount'] += by_sub[0]['amount']
        return result

    def _group_subs_by_area(self, domain):
        grouped = self.env['isp.subscription']._read_group(domain, ['area_id'], ['__count'])
        return {
            (area.id if area else 0): count
            for area, count in grouped
        }

    def _group_count_by_area(self, model, domain):
        grouped = model._read_group(domain, ['area_id'], ['__count'])
        return {
            (area.id if area else 0): count
            for area, count in grouped
        }

    def _area_domain(self, extra, field):
        area_id = extra.get('area_id')
        if area_id:
            return [(field, '=', area_id)]
        if extra.get('no_area'):
            return [(field, '=', False)]
        return []

    def _action_invoice_outstanding(self):
        return self._action_from_xmlid(
            'dkt_isp_billing.action_isp_invoice_outstanding',
            name='Invoice posted belum lunas',
            domain=self._domain_invoice_outstanding(),
        )

    def _action_invoice_outstanding_filtered(self, extra):
        domain = self._domain_invoice_outstanding() + self._area_domain(
            extra, 'subscription_id.area_id',
        )
        name = 'Belum lunas'
        if extra.get('area_name'):
            name = 'Belum lunas — %s' % extra['area_name']
        return self._action_from_xmlid(
            'dkt_isp_billing.action_isp_invoice_outstanding',
            name=name,
            domain=domain,
        )

    def _action_invoice_partial(self):
        return self._action_from_xmlid(
            'dkt_isp_billing.action_isp_invoice_outstanding',
            name='Pelunasan sebagian',
            domain=self._domain_invoice_partial(),
        )

    def _action_subscription_late_filtered(self, extra):
        domain = self._domain_subscription_late() + self._area_domain(extra, 'area_id')
        name = 'Langganan telat'
        if extra.get('area_name'):
            name = 'Telat — %s' % extra['area_name']
        return self._action_subscription(domain, name, {'search_default_late': 1})

    def _action_subscription_isolated_filtered(self, extra):
        domain = [('state', '=', 'isolated')] + self._area_domain(extra, 'area_id')
        name = 'Langganan terisolir'
        if extra.get('area_name'):
            name = 'Isolir — %s' % extra['area_name']
        return self._action_subscription(domain, name)

    def _action_proofs(self, domain, name):
        return self._action_from_xmlid(
            'dkt_isp_billing.action_isp_payment_proof',
            name=name,
            domain=domain,
        )

    def _action_proofs_filtered(self, extra):
        domain = self._domain_proof_queue() + self._area_domain(extra, 'area_id')
        name = 'Antrian bukti'
        if extra.get('area_name'):
            name = 'Bukti — %s' % extra['area_name']
        return self._action_proofs(domain, name)

    def _action_payment_month(self):
        start, end = self._month_bounds()
        return self._action_from_xmlid(
            'dkt_isp_billing.action_isp_payment',
            name='Pembayaran %s' % self.env['isp.subscription']._period_label(start),
            domain=self._domain_payment_month(start, end),
        )

    def _action_history(self, domain, name):
        return self._action_from_xmlid(
            'dkt_isp_billing.action_isp_cpe_pppoe_history',
            name=name,
            domain=domain,
        )

    def _action_router_form(self, extra):
        router_id = extra.get('router_id')
        if not router_id:
            return self._action_from_xmlid('dkt_isp_billing.view_isp_mikrotik_config_action')
        return {
            'type': 'ir.actions.act_window',
            'name': extra.get('router_name') or 'Router',
            'res_model': 'isp.mikrotik.config',
            'res_id': router_id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def _action_cpe_form(self, extra):
        cpe_id = extra.get('cpe_id')
        if not cpe_id:
            return self._action_cpe()
        return {
            'type': 'ir.actions.act_window',
            'name': extra.get('cpe_name') or 'CPE',
            'res_model': 'isp.cpe',
            'res_id': cpe_id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }
