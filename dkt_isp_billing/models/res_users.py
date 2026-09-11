from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    isp_area_ids = fields.Many2many(
        'isp.area',
        'isp_area_res_users_rel',
        'user_id',
        'area_id',
        string='Area Admin Desa / Kolektor',
        help='Area yang boleh dikelola Admin Desa atau Kolektor: pelanggan, CPE, invoice, dan bukti transfer.',
    )

    def _isp_is_village_admin_only(self):
        """Kompatibel: desa atau kolektor tanpa hak pusat."""
        return self._isp_is_submitter_only()

    def _isp_is_submitter_only(self):
        """Desa/kolektor: unggah + ajukan, tidak verifikasi / post lunas."""
        self.ensure_one()
        if self.has_group('base.group_system') or self.has_group('dkt_isp_billing.group_isp_manager'):
            return False
        if self.has_group('dkt_isp_billing.group_isp_user'):
            return False
        return (
            self.has_group('dkt_isp_billing.group_isp_village_admin')
            or self.has_group('dkt_isp_billing.group_isp_collector')
        )

    def _isp_can_verify_payment(self):
        """Admin pusat: cek bukti TF lalu post account.payment."""
        self.ensure_one()
        if self._isp_is_submitter_only():
            return False
        return (
            self.has_group('dkt_isp_billing.group_isp_manager')
            or self.has_group('dkt_isp_billing.group_isp_user')
            or self.has_group('base.group_system')
        )

    def _isp_submitter_source(self):
        self.ensure_one()
        if self.has_group('dkt_isp_billing.group_isp_collector') and self._isp_is_submitter_only():
            return 'collector'
        if self.has_group('dkt_isp_billing.group_isp_village_admin') and self._isp_is_submitter_only():
            return 'village'
        return 'backend'
