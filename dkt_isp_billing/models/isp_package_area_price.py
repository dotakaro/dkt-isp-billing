from odoo import api, fields, models


class ISPPackageAreaPrice(models.Model):
    _name = 'isp.package.area.price'
    _description = 'Harga paket per area'
    _order = 'area_id, package_id'

    package_id = fields.Many2one(
        'isp.package', string='Paket', required=True, ondelete='cascade', index=True,
    )
    area_id = fields.Many2one(
        'isp.area', string='Area', required=True, ondelete='cascade', index=True,
    )
    price = fields.Float('Harga', required=True)
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    note = fields.Char('Catatan')

    _package_area_uniq = models.Constraint(
        'UNIQUE(package_id, area_id)',
        'Harga untuk paket dan area ini sudah ada.',
    )

    @api.onchange('package_id')
    def _onchange_package_default_price(self):
        if self.package_id and not self.price:
            self.price = self.package_id.price
