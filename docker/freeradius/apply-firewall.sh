#!/bin/sh
# Allowlist UDP 1812/1813. Tidak mengaktifkan UFW (supaya SSH/Nginx tidak ikut berubah).
# Pakai rantai iptables DKT_RADIUS + DOCKER-USER.
set -eu
LIST="${1:-/opt/dkt-radius/nas-allow.list}"
if [ ! -f "$LIST" ]; then
    LIST="$(dirname "$0")/nas-allow.list"
fi

iptables -N DKT_RADIUS 2>/dev/null || true
iptables -F DKT_RADIUS
iptables -A DKT_RADIUS -s 127.0.0.1 -j ACCEPT
iptables -A DKT_RADIUS -s ::1 -j ACCEPT 2>/dev/null || true

while IFS= read -r line; do
    ip=$(echo "$line" | sed 's/#.*//' | tr -d ' ')
    [ -z "$ip" ] && continue
    [ "$ip" = "127.0.0.1" ] && continue
    iptables -A DKT_RADIUS -s "$ip" -j ACCEPT
done < "$LIST"

iptables -A DKT_RADIUS -j DROP

for port in 1812 1813; do
    iptables -C DOCKER-USER -p udp --dport "$port" -j DKT_RADIUS 2>/dev/null \
        || iptables -I DOCKER-USER -p udp --dport "$port" -j DKT_RADIUS
    iptables -C INPUT -p udp --dport "$port" -j DKT_RADIUS 2>/dev/null \
        || iptables -I INPUT -p udp --dport "$port" -j DKT_RADIUS
done

echo "DKT_RADIUS: allowlist UDP 1812/1813 diterapkan dari $LIST"
iptables -L DKT_RADIUS -n
