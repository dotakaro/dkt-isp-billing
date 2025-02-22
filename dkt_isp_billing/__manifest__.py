{
    'name': 'DKT ISP Billing',
    'version': '1.0',
    'category': 'Services/ISP',
    'summary': 'Modul untuk manajemen billing ISP',
    'description': """
        Modul untuk manajemen billing Internet Service Provider (ISP)
        - Manajemen Pelanggan
        - Manajemen Layanan
        - Integrasi dengan Mikrotik
        - Manajemen Tagihan
        - Manajemen Subscription
        - Manajemen CPE dan Perangkat
        - Laporan dan Analisis
    """,
    'author': 'PT. Digital Kreasi Teknologi',
    'website': 'https://dkt.co.id',
    'depends': [
        'base',
        'mail',
        'account',
        'product',
    ],
    'data': [
        # Security
        'security/ir.model.access.csv',
        
        # Data
        'data/ir_sequence_data.xml',
        'data/whatsapp_template_data.xml',
        'data/cron_jobs.xml',
        
        # Views - Actions harus didefinisikan sebelum menu
        'views/isp_customer_views.xml',
        'views/isp_package_views.xml',
        'views/isp_cpe_views.xml',
        'views/isp_mikrotik_views.xml',
        'views/isp_mikrotik_profile_views.xml',
        'views/isp_subscription_views.xml',
        'views/isp_subscription_template_views.xml',
        'views/isp_installation_type_views.xml',
        'views/isp_installation_fee_views.xml',
        'views/isp_device_history_views.xml',
        'views/isp_report_views.xml',
        
        # Wizards
        'wizards/isp_adopt_secret_wizard_views.xml',
        
        # Menu (harus terakhir karena bergantung pada action)
        'views/menu_views.xml',
        
        # Reports
        'reports/isp_report_templates.xml',
    ],
    'demo': [],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
} 