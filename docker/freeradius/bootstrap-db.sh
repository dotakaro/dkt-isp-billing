#!/bin/sh
# Buat DB/user radius di Postgres existing. Tidak menyentuh DB mpp/sicantik/dotakaro.
set -eu
MODE="${1:-local}"
PASSWORD="${RADIUS_DB_PASSWORD:-}"
if [ -z "$PASSWORD" ]; then
    echo "Set RADIUS_DB_PASSWORD di environment atau .env.radius"
    exit 1
fi

if [ "$MODE" = "local" ]; then
    PSQL='docker exec -i dkt-isp-db psql -U odoo -d postgres'
    PSQL_R='docker exec -i dkt-isp-db psql -U odoo -d radius'
else
    PSQL='docker exec -i odoo19-dkt-db psql -U odoo -d postgres'
    PSQL_R='docker exec -i odoo19-dkt-db psql -U odoo -d radius'
fi

$PSQL <<SQL
SELECT 'ok';
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'radius') THEN
        CREATE ROLE radius LOGIN PASSWORD '${PASSWORD}';
    ELSE
        ALTER ROLE radius PASSWORD '${PASSWORD}';
    END IF;
END
\$\$;
SELECT 'create_db' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'radius');
SQL

$PSQL -c "SELECT 1 FROM pg_database WHERE datname = 'radius'" | grep -q 1 \
    || $PSQL -c "CREATE DATABASE radius OWNER radius"

$PSQL_R < "$(dirname "$0")/schema.sql"
$PSQL -c "GRANT ALL PRIVILEGES ON DATABASE radius TO radius;"
$PSQL_R -c "GRANT ALL ON ALL TABLES IN SCHEMA public TO radius;"
$PSQL_R -c "GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO radius;"
$PSQL_R -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO radius;"
echo "DB radius siap ($MODE)"
