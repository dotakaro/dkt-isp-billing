stats = env['isp.subscription']._ensure_indonesian_accounting()
print('ACCOUNTING journal', stats.display_name if stats else None)
print('chart', env.company.chart_template, 'currency', env.company.currency_id.name)
remap = env['isp.subscription'].remap_commercial_packages(fetch_mikrotik=True)
print('REMAP', remap)
result = env['isp.subscription'].with_context(generate_invoice_silent=True).cron_generate_invoices()
created = result.get('created') if isinstance(result, dict) else False
skipped = result.get('skipped') if isinstance(result, dict) else []
errors = result.get('errors') if isinstance(result, dict) else []
print('INVOICES created=%s skipped=%s errors=%s' % (
    len(created) if created else 0, len(skipped or []), len(errors or []),
))
if errors:
    for item in errors[:15]:
        sub = item.get('subscription')
        print('ERR', sub.display_name if sub else '-', item.get('error'))
env.cr.commit()
raise SystemExit(0)

