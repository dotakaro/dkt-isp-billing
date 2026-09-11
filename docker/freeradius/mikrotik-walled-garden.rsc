# Tempel HANYA di router UAT. Jangan jalankan di semua desa.
# Isolir = profile "isolir" + address-list isolir. Secret tetap enabled.

/ppp profile
:if ([:len [/ppp profile find name="isolir"]] = 0) do={
    add name=isolir rate-limit=64k/64k only-one=yes address-list=isolir comment="Odoo isolir landing"
} else={
    set [find name="isolir"] rate-limit=64k/64k only-one=yes address-list=isolir
}

# Ganti 145.79.15.192 jika IP billing berubah.
/ip firewall address-list
:if ([:len [find list=dkt-billing address=145.79.15.192]] = 0) do={
    add list=dkt-billing address=145.79.15.192 comment="billing.dotakaro.com"
}

/ip firewall filter
:if ([:len [find comment="dkt-isolir-dns"]] = 0) do={
    add chain=forward action=accept protocol=udp dst-port=53 src-address-list=isolir comment="dkt-isolir-dns"
}
:if ([:len [find comment="dkt-isolir-billing"]] = 0) do={
    add chain=forward action=accept dst-address-list=dkt-billing src-address-list=isolir comment="dkt-isolir-billing"
}
:if ([:len [find comment="dkt-isolir-drop"]] = 0) do={
    add chain=forward action=drop src-address-list=isolir comment="dkt-isolir-drop"
}

/ip firewall nat
:if ([:len [find comment="dkt-isolir-http"]] = 0) do={
    add chain=dstnat action=dst-nat to-addresses=145.79.15.192 to-ports=8188 protocol=tcp src-address-list=isolir dst-port=80 comment="dkt-isolir-http"
} else={
    set [find comment="dkt-isolir-http"] action=dst-nat to-addresses=145.79.15.192 to-ports=8188 protocol=tcp src-address-list=isolir dst-port=80
}

# RADIUS cadangan (secret ganti di Odoo, jangan commit):
# /radius add address=145.79.15.192 secret=GANTI service=ppp
# /ppp aaa set use-radius=yes
