def migrate(cr, version):
    """Samura memakai cap 10M/10M. Router hemat lain tetap 7M."""
    cr.execute(
        """
        ALTER TABLE isp_mikrotik_config
            ADD COLUMN IF NOT EXISTS bandwidth_cap_rate varchar
        """
    )
    cr.execute(
        """
        UPDATE isp_mikrotik_config
           SET bandwidth_cap_rate = '10M/10M'
         WHERE name = 'Samura'
        """
    )
