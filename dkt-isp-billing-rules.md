# Rules Pengembangan Modul ISP Billing

## Struktur File dan Penamaan

1. File Views:
   - Harus berada di folder `views/`
   - Nama file harus menggunakan format `nama_model_views.xml` (dengan 's' di akhir)
   - Contoh: `isp_customer_views.xml`, `isp_package_views.xml`
   - Urutan definisi dalam file view:
     1. Tree View
     2. Form View
     3. Search View
     4. Action
     5. Menu

2. File Wizards:
   - Model wizard harus berada di folder `wizards/`
   - View wizard harus berada di folder `wizards/` dengan nama `nama_wizard_view.xml`
   - Harus didaftarkan di `wizards/__init__.py`
   - View wizard harus didaftarkan di manifest sebelum view utama

3. File Security:
   - Access rights harus unik (tidak boleh duplikat)
   - Format nama: `access_[model_name]_[role]`
   - Contoh: `access_isp_customer_user`

4. Manifest (`__manifest__.py`):
   - Daftar file di `data` harus sesuai dengan struktur folder
   - Security harus didaftarkan pertama
   - Wizard views harus didaftarkan sebelum model views
   - Urutan pendaftaran:
     1. Security
     2. Wizard views
     3. Model views
     4. Data
     5. Menu (terakhir)

## Konvensi Penamaan

1. Model:
   - Nama teknis: `isp.nama_model` (lowercase dengan dot)
   - Contoh: `isp.customer`, `isp.package`

2. Fields:
   - Gunakan snake_case
   - Nama harus deskriptif
   - Contoh: `pppoe_username`, `installation_date`

3. Methods:
   - Gunakan snake_case
   - Awali dengan `_` untuk private method
   - Contoh: `_create_mikrotik_user`, `action_activate`

4. XML IDs:
   - Format: `view_[model]_[type]`
   - Contoh: `view_isp_customer_form`, `view_isp_customer_tree`
   - Action: `action_[model]`
   - Menu: `menu_[module]_[name]`
   - Wizard: `wizard_[name]`

## Penanganan Error

1. Selalu gunakan try-except untuk operasi Mikrotik
2. Log error dengan level yang sesuai
3. Berikan pesan error yang informatif ke user
4. Pastikan resource (koneksi, file) selalu di-close di blok finally

## View Definition Rules

1. Urutan Definisi:
   - Tree View harus didefinisikan pertama
   - Form View setelah Tree View
   - Search View sebelum Action yang mereferensikannya
   - Action setelah semua view yang direferensikan
   - Menu setelah action yang direferensikan

2. Referensi View:
   - Jangan mereferensikan view yang belum didefinisikan
   - Gunakan `ref` attribute hanya setelah target didefinisikan
   - Contoh: `search_view_id` harus mereferensikan search view yang sudah ada

3. View Inheritance:
   - Definisikan view dasar sebelum view turunan
   - Gunakan `inherit_id` untuk mewarisi view yang sudah ada
   - Pastikan view yang diwarisi sudah didefinisikan

## Testing

1. Sebelum commit:
   - Pastikan semua file terdaftar di manifest
   - Pastikan semua model terdaftar di security
   - Pastikan semua dependensi terdaftar
   - Test upgrade modul untuk memastikan tidak ada error

## Dokumentasi

1. Setiap model harus memiliki `_description`
2. Setiap method harus memiliki docstring
3. Field yang kompleks harus memiliki help text
4. Tambahkan komentar untuk logika yang kompleks 