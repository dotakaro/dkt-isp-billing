from odoo import fields, models


class ISPImportSecretWizard(models.TransientModel):
    _name = 'isp.import.secret.wizard'
    _description = 'Hasil import secret PPPoE ke Odoo'

    created_count = fields.Integer('Dibuat', readonly=True)
    updated_count = fields.Integer('Diperbarui', readonly=True)
    skipped_count = fields.Integer('Dilewati', readonly=True)
    error_count = fields.Integer('Error', readonly=True)
    log = fields.Text('Rincian', readonly=True)
