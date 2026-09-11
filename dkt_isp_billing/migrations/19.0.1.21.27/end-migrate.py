def migrate(cr, version):
    """Tandai Sinaman dan Bulan Jahe bebas cap 7M. Push ke router dilakukan manual."""
    cr.execute(
        """
        UPDATE isp_mikrotik_config
           SET bandwidth_cap_exempt = TRUE
         WHERE name IN ('Sinaman', 'Bulan Jahe')
        """
    )
