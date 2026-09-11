from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'dkt_isp_mac_refresh')
class TestPppoeRefreshMac(TransactionCase):
    """Refresh status PPPoE tidak menulis MAC/IP dan tidak raise ValidationError."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Pelanggan Uji Refresh MAC',
            'customer_rank': 1,
        })

    def _create_cpe(self, name, username, mac=False):
        return self.env['isp.cpe'].create({
            'name': name,
            'partner_id': self.partner.id,
            'connection_type': 'pppoe',
            'pppoe_username': username,
            'pppoe_password': 'secret123',
            'mac_address': mac,
            'state': 'open',
        })

    def _active_session(self, caller_id, address='10.10.10.10'):
        return {
            'caller-id': caller_id,
            'address': address,
            'uptime': '1h',
            'session-id': '1',
            'interface': '',
        }

    def test_empty_mac_normalized_allows_multiple(self):
        cpe1 = self._create_cpe('CPE kosong 1', 'zz-mac-empty-1', '')
        cpe2 = self._create_cpe('CPE kosong 2', 'zz-mac-empty-2', '   ')
        self.assertFalse(cpe1.mac_address)
        self.assertFalse(cpe2.mac_address)

    def test_prepare_status_vals_never_includes_mac_or_ip(self):
        mac = 'AA:BB:CC:DD:EE:01'
        owner = self._create_cpe('CPE pemilik MAC', 'zz-mac-owner', mac)
        other = self._create_cpe('CPE tanpa MAC', 'zz-mac-other', False)
        vals = other._prepare_pppoe_status_vals(
            self._active_session(mac, '10.20.30.40'), {}, fields.Datetime.now(),
        )
        self.assertNotIn('mac_address', vals)
        self.assertNotIn('ip_address', vals)
        self.assertEqual(vals['pppoe_caller_id'], mac)
        self.assertEqual(vals['pppoe_address'], '10.20.30.40')
        self.assertEqual(owner.mac_address, mac)

    def test_refresh_does_not_write_mac_even_if_passed(self):
        mac = 'AA:BB:CC:DD:EE:03'
        owner = self._create_cpe('CPE A', 'zz-mac-raise-a', mac)
        target = self._create_cpe('CPE B', 'zz-mac-raise-b', False)
        try:
            target._write_pppoe_status({
                'pppoe_status': 'connected',
                'pppoe_caller_id': mac,
                'pppoe_uptime': '2m',
                'mac_address': mac,
                'ip_address': '10.1.1.1',
            }, source='manual')
        except ValidationError as exc:
            self.fail(f'Refresh tidak boleh raise ValidationError: {exc}')
        self.assertEqual(target.pppoe_status, 'connected')
        self.assertEqual(target.pppoe_caller_id, mac)
        self.assertFalse(target.mac_address)
        self.assertFalse(target.ip_address)
        self.assertEqual(owner.mac_address, mac)

    def test_two_cpes_same_caller_id_both_update_status(self):
        mac = 'AA:BB:CC:DD:EE:02'
        first = self._create_cpe('CPE pertama', 'zz-mac-first', False)
        second = self._create_cpe('CPE kedua', 'zz-mac-second', False)
        now = fields.Datetime.now()
        active = self._active_session(mac)
        first._write_pppoe_status(
            first._prepare_pppoe_status_vals(active, {}, now), source='manual',
        )
        second._write_pppoe_status(
            second._prepare_pppoe_status_vals(active, {}, now), source='manual',
        )
        self.assertFalse(first.mac_address)
        self.assertFalse(second.mac_address)
        self.assertEqual(first.pppoe_status, 'connected')
        self.assertEqual(second.pppoe_status, 'connected')
        self.assertEqual(first.pppoe_caller_id, mac)
        self.assertEqual(second.pppoe_caller_id, mac)

    def test_pppoe_history_written_without_mac_write(self):
        mac = 'AA:BB:CC:DD:EE:04'
        self._create_cpe('CPE hist owner', 'zz-mac-hist-a', mac)
        target = self._create_cpe('CPE hist target', 'zz-mac-hist-b', False)
        target._write_pppoe_status({
            'pppoe_status': 'connected',
            'pppoe_caller_id': mac,
            'pppoe_uptime': '3m',
            'mac_address': mac,
        }, source='manual')
        history = target.pppoe_history_ids.filtered(
            lambda h: h.new_status == 'connected'
        )
        self.assertTrue(history)
        self.assertEqual(history[0].caller_id, mac)
        self.assertFalse(target.mac_address)
