from odoo import models, fields, api

class ISPInstallationType(models.Model):
    _name = 'isp.installation.type'
    _description = 'Installation Type'
    
    name = fields.Char('Nama Jenis Instalasi', required=True)
    code = fields.Char('Kode', required=True)
    price = fields.Float('Biaya', required=True)
    description = fields.Text('Deskripsi')
    active = fields.Boolean('Active', default=True)
    
    _unique_code = models.Constraint(
        'UNIQUE(code)',
        'Kode jenis instalasi harus unik!',
    ) 