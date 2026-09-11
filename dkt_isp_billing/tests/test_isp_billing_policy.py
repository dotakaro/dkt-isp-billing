from datetime import date
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dkt_isp_billing_policy')
class TestIspBillingPolicy(TransactionCase):
    """Kebijakan: due 1, telat 21, pajak 0, isolir manual, buka isolir aman."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Sub = cls.env['isp.subscription']
        cls.area = cls.env['isp.area'].create({
            'name': 'Desa Uji Kebijakan',
            'code': 'UJI_KEBIJAKAN',
        })
        cls.router = cls.env['isp.mikrotik.config'].create({
            'name': 'Router Uji Kebijakan',
            'host': '192.0.2.88:18728',
            'username': 'test',
            'password': 'test',
            'active': True,
            'area_id': cls.area.id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Pelanggan Uji Kebijakan',
            'customer_rank': 1,
            'area_id': cls.area.id,
            'phone': '081234567890',
        })
        cls.package = cls.env.ref('dkt_isp_billing.isp_package_150')
        cls.default_pkg = cls.env.ref('dkt_isp_billing.isp_package_sapu_jagad')
        cls.cpe = cls.env['isp.cpe'].create({
            'name': 'CPE Uji Kebijakan',
            'partner_id': cls.partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'uji-kebijakan-1',
            'pppoe_password': 'secret123',
            'mikrotik_config_id': cls.router.id,
            'state': 'open',
        })
        cls.sub = cls.Sub.create({
            'partner_id': cls.partner.id,
            'cpe_id': cls.cpe.id,
            'package_id': cls.package.id,
            'state': 'open',
            'due_day': 15,
        })

    def test_due_day_forced_to_one(self):
        self.assertEqual(self.sub.due_day, 1)
        self.sub.due_day = 20
        self.assertEqual(self.sub.due_day, 1)
        self.assertEqual(self.Sub._billing_due_day(), 1)
        self.assertEqual(self.Sub._billing_late_day(), 21)
        self.assertFalse(self.Sub._billing_apply_tax())

    def test_late_starts_on_day_21(self):
        period = date(2026, 8, 1)
        due = date(2026, 8, 1)
        self.assertFalse(self.Sub.is_date_late_for_period(
            period, today=date(2026, 8, 14), due_date=due, late_day=21,
        ))
        self.assertTrue(self.Sub.is_date_late_for_period(
            period, today=date(2026, 8, 21), due_date=due, late_day=21,
        ))
        self.assertTrue(self.Sub.is_date_late_for_period(
            date(2026, 7, 1), today=date(2026, 8, 14), due_date=date(2026, 7, 1), late_day=21,
        ))

    def test_special_treatment_not_late_and_not_billed(self):
        cpe = self.env['isp.cpe'].create({
            'name': 'CPE Khusus',
            'partner_id': self.partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': 'uji-khusus-1',
            'pppoe_password': 'secret123',
            'mikrotik_config_id': self.router.id,
            'state': 'open',
        })
        special = self.Sub.create({
            'partner_id': self.partner.id,
            'cpe_id': cpe.id,
            'package_id': self.default_pkg.id,
            'state': 'open',
            'billing_review_needed': True,
        })
        self.assertTrue(special.is_special_treatment)
        self.assertFalse(special.is_late)
        result = special._generate_invoice_one()
        self.assertTrue(result.get('skipped'))
        ok, err = special.isolate_safe()
        self.assertFalse(ok)
        self.assertIn('Perlakuan khusus', err)

    def test_invoice_vals_tax_zero_due_day_one(self):
        vals = self.sub._prepare_invoice_values()
        self.assertEqual(vals['invoice_date'], fields.Date.today().replace(day=1))
        self.assertEqual(vals['invoice_date_due'].day, 1)
        self.assertEqual(vals['invoice_line_ids'][0][2]['tax_ids'], [(6, 0, [])])
        self.assertNotIn('action_post', vals)

    def test_cron_mark_does_not_isolate(self):
        with patch.object(type(self.Sub), 'isolate_safe') as mock_iso, \
                patch.object(type(self.Sub), 'action_isolate') as mock_act:
            self.Sub.cron_mark_overdue()
            mock_iso.assert_not_called()
            mock_act.assert_not_called()

    def test_auto_isolate_cron_is_noop_when_off(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('dkt_isp_billing.auto_isolate_enabled', 'False')
        ICP.set_param('dkt_isp_billing.auto_isolate_backend', 'mikrotik_secret')
        cfg = self.Sub._auto_isolate_config()
        self.assertFalse(cfg['enabled'])
        self.assertEqual(cfg['backend'], 'mikrotik_secret')
        with patch.object(type(self.Sub), 'isolate_safe') as mock_iso, \
                patch.object(type(self.Sub), 'action_isolate') as mock_act:
            self.assertTrue(self.Sub._cron_auto_isolate_overdue())
            ICP.set_param('dkt_isp_billing.auto_isolate_enabled', 'True')
            self.assertTrue(self.Sub._cron_auto_isolate_overdue())
            mock_iso.assert_not_called()
            mock_act.assert_not_called()
        ICP.set_param('dkt_isp_billing.auto_isolate_enabled', 'False')

    def test_isolate_safe_uses_cpe_router_mock(self):
        with patch.object(
            type(self.env['isp.mikrotik.config']),
            'set_secret_disabled',
            return_value=(True, None),
        ) as mock_set:
            ok, err = self.sub.isolate_safe()
        self.assertTrue(ok)
        self.assertFalse(err)
        self.assertEqual(self.sub.state, 'isolated')
        mock_set.assert_called_once()
        args = mock_set.call_args[0]
        self.assertEqual(args[0], 'uji-kebijakan-1')
        self.assertTrue(args[1])
        self.assertEqual(self.sub.cpe_id.mikrotik_config_id, self.router)
        self.assertFalse(self.env['isp.package']._apply_package_rate_limit())

    def test_unisolate_after_payment_idempotent_and_safe(self):
        self.sub.write({'state': 'isolated'})
        self.sub.cpe_id.write({'state': 'isolated'})
        with patch.object(
            type(self.env['isp.mikrotik.config']),
            'set_secret_disabled',
            return_value=(True, None),
        ):
            self.assertTrue(self.sub.try_unisolate_after_payment())
            self.assertEqual(self.sub.state, 'open')
            self.assertTrue(self.sub.try_unisolate_after_payment())
        self.sub.write({'state': 'isolated'})
        with patch.object(
            type(self.env['isp.mikrotik.config']),
            'set_secret_disabled',
            return_value=(False, 'timeout API'),
        ):
            self.assertFalse(self.sub.try_unisolate_after_payment())
        self.assertEqual(self.sub.state, 'isolated')
        self.assertTrue(self.sub.unisolate_failed)
        self.assertIn('timeout', self.sub.unisolate_error)

    def test_paid_hook_does_not_raise(self):
        self.sub.write({'state': 'isolated'})
        with patch.object(
            type(self.Sub), 'try_unisolate_after_payment', side_effect=Exception('boom'),
        ):
            self.env['account.move']._invoice_paid_hook()

    def test_notification_wizard_tags_only(self):
        wiz = self.env['isp.notification.prepare.wizard'].create({
            'only_late': False,
            'add_queue_tag': True,
            'add_auto_tag': True,
        })
        with patch.object(type(self.Sub), '_send_whatsapp_notification') as mock_wa:
            wiz.action_load()
            self.assertTrue(wiz.line_ids)
            wiz.action_prepare()
            mock_wa.assert_not_called()
        self.assertTrue(self.partner.isp_notif_prepared)
        names = set(self.partner.category_id.mapped('name'))
        self.assertIn('notif_siap', names)
        self.assertIn('notif_otomatis', names)

    def test_bulk_isolate_excludes_special_and_confirms(self):
        self.sub.overdue_marked = True
        wiz = self.env['isp.isolate.bulk.wizard'].create({
            'area_id': self.area.id,
        })
        wiz.action_load()
        special_ids = wiz.line_ids.filtered(lambda l: l.subscription_id.is_special_treatment)
        self.assertFalse(special_ids)
        with patch.object(type(self.Sub), 'isolate_safe', return_value=(True, None)) as mock_iso:
            if wiz.line_ids:
                wiz.action_confirm_isolate()
                mock_iso.assert_called()
            else:
                mock_iso.assert_not_called()
