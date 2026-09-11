from odoo.tests import TransactionCase, tagged

from odoo.addons.dkt_isp_billing.models.isp_pppoe_profile_template import (
    pick_new_pppoe_pool_ranges,
    profile_address_vals,
)


@tagged('post_install', '-at_install', 'dkt_isp_pppoe_pool')
class TestIspPppoeProfilePool(TransactionCase):

    def test_profile_address_vals_copies_sapu_pool(self):
        self.assertEqual(
            profile_address_vals({
                'local-address': '10.10.1.1',
                'remote-address': 'pppoe-pool',
            }),
            {
                'local-address': '10.10.1.1',
                'remote-address': 'pppoe-pool',
            },
        )

    def test_profile_address_vals_empty_when_no_pool(self):
        self.assertEqual(profile_address_vals({'name': '200-10M'}), {})
        self.assertEqual(profile_address_vals(None), {})

    def test_pick_new_pool_skips_reserved_and_used_ranges(self):
        ranges = pick_new_pppoe_pool_ranges([
            '10.10.8.2-10.10.8.254',
            '172.16.16.2-172.16.16.254',
        ])
        self.assertEqual(ranges, '10.10.9.2-10.10.9.254')
