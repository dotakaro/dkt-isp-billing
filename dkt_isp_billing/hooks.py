import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Isi password API dari env, push profile SAPU-JAGAD, pindahkan semua secret."""
    mikrotik = env['isp.mikrotik.config'].with_context(install_mode=False)
    filled = mikrotik._fill_password_from_env()
    _logger.info('post_init: password API terisi untuk %s router.', filled)
    try:
        env['isp.subscription']._ensure_indonesian_accounting()
    except Exception:
        _logger.exception('post_init: gagal memuat bagan akun Indonesia.')
    pkg = env['isp.package'].with_context(install_mode=False).get_default_package()
    if not pkg or not pkg.profile_template_id:
        _logger.warning(
            'post_init: paket default SAPU-JAGAD belum ada, skip migrasi secret.'
        )
        return
    try:
        result = pkg.action_push_and_assign_shared_profile()
        _logger.info('post_init SAPU-JAGAD: %s', result)
    except Exception:
        _logger.exception(
            'post_init: gagal push/assign SAPU-JAGAD (install tetap dilanjutkan).'
        )
    try:
        phone_stats = env['res.partner']._backfill_phone_from_pppoe_username()
        _logger.info('post_init parse nomor secret: %s', phone_stats)
    except Exception:
        _logger.exception('post_init: gagal parse nomor dari username PPPoE.')
    try:
        env['isp.whatsapp.template'].ensure_defaults()
    except Exception:
        _logger.exception('post_init: gagal seed template WhatsApp.')
