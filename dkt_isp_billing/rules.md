### **1. Manajemen Pelanggan**

- **Model `isp.customer`** dengan field:
    - Nama
    - No KTP
    - Alamat
    - No HP
    - ID Pelanggan (Unique)

### **2. Manajemen Layanan**

- **Model `isp.package`** berisi:
    
    - Nama Paket
    - Harga
- **Relasi `isp.customer` ke `isp.package`**, sehingga setiap pelanggan bisa berlangganan satu paket.
    

### **3. Manajemen Subscriber CPE (Customer Premises Equipment)**

- **Model `isp.cpe`** berisi:
    - Outdoor Unit
    - Router
    - IP
    - Username
    - Password
    - Status (Milik Sendiri/Dipinjamkan)

### **4. Riwayat Penggantian Alat**

- **Model `isp.device_history`** dengan field:
    - Tanggal
    - Nama Alat
    - Alasan Pergantian
    - Status Alat (Dipinjamkan/Milik Sendiri)
    - Petugas
    - Biaya

### **5. Biaya Pasang Baru**

- **Model `isp.installation_fee`** berisi:
    - ID Pelanggan (Relasi ke `isp.customer`)
    - Jenis Pasang Baru (Relasi ke jenis PSB)
    - Status Pembayaran

### **6. Biaya Berlangganan dan Billing**

- **Integrasi dengan modul `subscription` Odoo** untuk membuat invoice secara berkala (bulanan dan triwulanan).
- Invoice otomatis dibuat setiap jatuh tempo.

### **7. Sistem Notifikasi WhatsApp**

- Notifikasi 3 hari sebelum jatuh tempo.
- Notifikasi 3 hari setelah keterlambatan.
- Notifikasi pemblokiran layanan (isolir).
- **Fitur isolir otomatis & manual**:
    - Isolir dilakukan jika invoice belum dibayar.
    - Buka isolir otomatis jika pembayaran sudah diterima.

### **8. Integrasi dengan Mikrotik**

- **Gunakan Python API untuk Mikrotik** agar bisa:
    - Mendaftarkan user
    - Mengatur paket, harga paket, dan kecepatan per user
    - Mengelompokkan user sesuai paket (User Group)
    - Mengaktifkan dan menonaktifkan isolir otomatis

### **9. Laporan-Laporan**

- Laporan Pelanggan
- Laporan CPE
- Laporan Paket
- Laporan Keuangan Bulanan
- Laporan Laba Rugi

Buatkan kode Odoo dengan struktur yang rapi, gunakan ORM Odoo, dan pastikan kompatibilitas dengan Odoo 17. Tambahkan menu antarmuka di backend untuk mengelola setiap model.







