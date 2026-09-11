pkg = env['isp.package'].with_context(install_mode=False).get_default_package()
print('DEFAULT', pkg.name if pkg else None)
if not pkg:
    raise SystemExit(1)
result = pkg.action_push_and_assign_shared_profile()
print(result)
env.cr.commit()
raise SystemExit(0)
