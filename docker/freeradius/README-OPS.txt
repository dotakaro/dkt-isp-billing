DKT FreeRADIUS — stack terpisah dari Odoo
=========================================

Lokal:
  docker compose -f docker-compose.radius.yml --env-file .env.radius up -d
  ./docker/freeradius/bootstrap-db.sh local

Produksi (jangan campur dengan /opt/odoo19-dkt compose):
  /opt/dkt-radius/
  Server ini memakai docker-compose 1.29:
    cd /opt/dkt-radius && docker-compose -p dkt-radius up -d
  Jangan: docker-compose down di folder Odoo (RADIUS tetap nyala).

Port:
  UDP 1812 auth, 1813 acct
  Lokal bind 127.0.0.1
  Produksi: publish 0.0.0.0 HANYA setelah apply-firewall.sh + nas-allow.list

Jangan sentuh Odoo 18.4 / MPP / SiCantik / port 8065.
Isolir otomatis tetap OFF. Walled garden hanya di router UAT (mikrotik-walled-garden.rsc).
