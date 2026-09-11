stats = env['isp.mikrotik.config'].search([('active', '=', True)]).import_ppp_secrets()
print('TOTAL created=%s updated=%s skipped=%s errors=%s' % (
    stats['created'], stats['updated'], stats['skipped'], stats['errors'],
))
print('--- PER ROUTER ---')
for name, rstats in stats.get('routers', {}).items():
    print('%s | secrets=%s created=%s updated=%s skipped=%s errors=%s skip=%s connect=%s' % (
        name,
        rstats.get('secret_count', 0),
        rstats['created'], rstats['updated'], rstats['skipped'], rstats['errors'],
        rstats.get('skip_reason') or '',
        rstats.get('connect_error') or '',
    ))
print('--- PARSE ISSUES (%s) ---' % len(stats.get('parse_issues') or []))
for issue in (stats.get('parse_issues') or [])[:40]:
    print(issue)
env.cr.commit()
raise SystemExit(0)
