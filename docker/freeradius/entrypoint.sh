#!/bin/sh
set -eu
SRC=/etc/freeradius/mods-available/sql
DST=/etc/freeradius/mods-enabled/sql
rm -f "$DST"
cp "$SRC" "$DST"
sed -i 's/dialect = "sqlite"/dialect = "postgresql"/' "$DST"
sed -i 's/^	driver = "rlm_sql_null"/#	driver = "rlm_sql_null"/' "$DST"
sed -i 's/^#	driver = "rlm_sql_\${dialect}"/	driver = "rlm_sql_${dialect}"/' "$DST"
# Jangan taruh password di radius_db — FreeRADIUS men-log connstring.
sed -i "s|^#	server = \"localhost\"|	server = \"${RADIUS_DB_HOST}\"|" "$DST"
sed -i "s|^#	port = 3306|	port = ${RADIUS_DB_PORT}|" "$DST"
sed -i "s|^#	login = \"radius\"|	login = \"${RADIUS_DB_USER}\"|" "$DST"
sed -i "s|^#	password = \"radpass\"|	password = \"${RADIUS_DB_PASSWORD}\"|" "$DST"
sed -i 's/^#	read_clients = yes/	read_clients = yes/' "$DST"
# read_clients hanya jalan jika sql di-instantiate saat startup, bukan hanya di authorize.
if ! grep -q '^[[:space:]]*sql[[:space:]]*$' /etc/freeradius/radiusd.conf; then
    sed -i '/^instantiate {/a\
	sql
' /etc/freeradius/radiusd.conf
fi
POLICY_SRC=/opt/dkt-radius/dkt-local-fallback
if [ -f "$POLICY_SRC" ]; then
    cp "$POLICY_SRC" /etc/freeradius/policy.d/dkt-local-fallback
    SITE=/etc/freeradius/sites-enabled/default
    # Hanya di authorize. Jangan sisip di accounting/post-auth.
    sed -i '/^[[:space:]]*dkt_unknown_use_local[[:space:]]*$/d' "$SITE"
    awk '
        /See "Authorization Queries" in mods-available\/sql/ { print; getline; print; print "\tdkt_unknown_use_local"; next }
        { print }
    ' "$SITE" > "$SITE.tmp" && mv "$SITE.tmp" "$SITE"
fi
exec freeradius -f -l stdout "$@"
