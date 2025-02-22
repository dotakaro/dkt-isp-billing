# DKT ISP Billing

Modul Odoo untuk manajemen billing ISP dengan fitur:

## Fitur

- Manajemen Pelanggan
- Manajemen CPE (Customer Premise Equipment)
- Manajemen Paket Layanan
- Integrasi dengan Mikrotik untuk manajemen PPPoE
- Notifikasi WhatsApp otomatis
- Penagihan berulang otomatis
- Laporan keuangan dan pelanggan

## Persyaratan

- Odoo 17.0 Enterprise
- Python 3.11+
- RouterOS API
- Koneksi ke Mikrotik Router

## Instalasi

1. Clone repository ini ke folder addons Odoo
2. Install dependensi Python:
   ```bash
   pip install routeros_api
   ```
3. Restart Odoo server
4. Aktifkan modul DKT ISP Billing dari daftar aplikasi

## Konfigurasi

1. Konfigurasi koneksi Mikrotik di menu Konfigurasi > Mikrotik
2. Buat paket layanan di menu Layanan > Paket
3. Buat template berlangganan di menu Layanan > Template Berlangganan

## Penggunaan

1. Buat data pelanggan baru
2. Registrasi CPE pelanggan
3. Buat subscription untuk pelanggan
4. Aktifkan subscription untuk memulai layanan

## Lisensi

Hak Cipta © 2024 DKT. Seluruh hak dilindungi. 