{
    'name': 'DKT ISP Billing',
    'version': '1.0',
    'category': 'Services/ISP',
    'summary': 'Modul untuk manajemen layanan ISP',
    'description': """
        Modul untuk mengelola layanan ISP termasuk:
        - Manajemen Pelanggan
        - Manajemen Layanan
        - Manajemen CPE
        - Billing dan Subscription
        - Integrasi Mikrotik
        - Notifikasi WhatsApp
    """,
    'depends': [
        'base',
        'mail',
        'account',
        'contacts',
        'sale',
        'sale_management',
        'whatsapp'
    ],
    'external_dependencies': {
        'python': [
            'routeros_api',
            'python-magic'
        ],
    },
    'data': [
        # Security
        'security/ir.model.access.csv',
        
        # Data
        'data/ir_sequence_data.xml',
        'data/cron_jobs.xml',
        'data/whatsapp_template_data.xml',
        
        # Views dan Actions
        'views/isp_customer_views.xml',
        'views/isp_package_views.xml',
        'views/isp_cpe_views.xml',
        'views/isp_device_history_views.xml',
        'views/isp_installation_type_views.xml',
        'views/isp_installation_fee_views.xml',
        'views/isp_subscription_views.xml',
        'views/isp_subscription_template_views.xml',
        'views/isp_mikrotik_views.xml',
        'views/isp_mikrotik_profile_views.xml',
        'views/isp_report_views.xml',
        
        # Menu - Harus dimuat terakhir setelah semua view dan action
        'views/menu_views.xml',
    ],
    'application': True,
    'installable': True,
    'auto_install': False,
} 