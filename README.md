# Bot WhatsApp Engineering (WAHA + Ticketing + Dashboard)

Bot WhatsApp untuk **ticketing tugas engineering** di grup WhatsApp.

```text
WhatsApp (grup engineering)
   │  pesan diawali  #  atau  /
   ▼
WAHA (self-hosted, Docker)  ──webhook──►  Aplikasi FastAPI
                                              ├─ parser perintah
                                              ├─ mesin ticket (status & transisi)
                                              ├─ balasan otomatis (#info, #status, ...)
                                              ├─ dashboard web (konfigurasi)
                                              └─ sinkron 2 arah → Google Sheets
```

---

## 1. Fitur

| Fitur | Keterangan |
|---|---|
| Deteksi perintah | Prefix `#` atau `/` (bisa diubah per grup & global) |
| Audit pesan | Semua pesan di grup tersimpan (bisa dimatikan) |
| Ticket otomatis | `#ticket <issue>` membuat ticket bernomor (mis. `tkt1120261`) |
| Handling ticket | Status: Belum Dikerjakan → Dikerjakan → Selesai / Dibatalkan, dengan aturan transisi |
| `#info` | Ringkasan ticket hari ini untuk penanya: belum selesai & sudah selesai |
| Ringkasan harian | Otomatis dikirim ke grup pada jam tertentu |
| Dashboard | Konfigurasi perintah, status, grup, WAHA, Google Sheets |
| Google Sheets | Service account, sinkron otomatis ke satu tab `TICKETS` |
| Multi-grup | Grup bisa diaktifkan/dimatikan, prefix & jadwal per grup |

Foto ticket dapat dikirim sebagai album: sertakan `#ticket <issue>` pada foto
pertama. Foto berikutnya dari pengirim dan chat yang sama akan ikut dilampirkan
selama diterima dalam 30 detik. Foto-foto tersebut tampil bersama di detail
ticket dan thumbnail daftar ticket.

Balasan pembuatan ticket hanya berisi ID (mis. `tkt1120261`). Pesan tanpa
perintah/prefix terdaftar diabaikan dan tidak dicatat. Balasan tidak menyebut
ID pengirim; jeda minimum tetap 2 detik dipakai antarbalasan agar tidak terjadi
pengiriman beruntun.

Teknisi juga dapat membalas (reply/quote) pesan bot yang berisi ID ticket
dengan `progress`, `done`, atau status yang dikenal (mis. `MENUNGGU`). Bot
mengubah status ticket yang dikutip; catatan setelah status, mis.
`done AC sudah diperbaiki`, disimpan sebagai resolusi. Reply harus berasal
dari chat ticket yang sama dan tetap mengikuti aturan transisi status.

---

## 2. Menjalankan

### 2.1 Docker Compose (disarankan)

```bash
cd bot_whatsapp
cp .env.example .env          # isi secret & token (WAHA API key WAJIB diisi)
mkdir -p secrets data

# service account Google (optional, untuk Google Sheets)
#   secrets/service-account.json  <- unduh dari Google Cloud Console
#   lalu bagikan spreadsheet ke email service account (Editor)

docker compose up -d --build
docker compose logs -f app
```

- Dashboard bot: <http://localhost:8080>
- WAHA: <http://localhost:3000> (login: `BOT_WAHA_ADMIN_USER` / `BOT_WAHA_ADMIN_PASSWORD`)
  → menu **Sessions** → create/start session `engineering` → scan QR WhatsApp

> **WAHA terbaru mewajibkan API key.** `BOT_WAHA_API_KEY` dipakai dua service
> (WAHA sebagai `WHATSAPP_API_KEY`, aplikasi sebagai header `X-Api-Key`).
> Isi dengan nilai acak sebelum deploy.

### 2.2 Lokal (tanpa Docker)

```bash
py -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8080
```
WAHA tetap lewat Docker:
```bash
docker run -d --name waha -p 3000:3000 -v waha_data:/waha/media devlikeapro/waha:latest
```

---

## 3. Menghubungkan WAHA (otomatis)

Aplikasi mengelola koneksi WAHA sendiri — **tidak perlu** membuka dashboard WAHA
untuk membuat session atau mendaftarkan webhook.

### Cara tercepat

1. Pastikan `BOT_WAHA_API_KEY` di `.env` sama dengan `WHATSAPP_API_KEY` WAHA.
2. Jalankan `docker compose up -d --build`.
3. Buka dashboard <http://localhost:8080> → kartu **WhatsApp (WAHA)** → klik
   **🔗 Hubungkan Otomatis**.

Selesai. Aplikasi otomatis:

| Langkah | Yang dilakukan |
|---|---|
| Deteksi | Mencari WAHA di `waha:3000`, `localhost:3000`, `127.0.0.1:3000`, `host.docker.internal:3000` (serta tries semua API key yang tersimpan) |
| Perbaikan otomatis | API key / base URL yang basi di dashboard diganti dengan yang benar-benar diterima WAHA |
| Session | Dibuat + di-start bila belum ada |
| Webhook | Didaftarkan otomatis (event `message` + `session.status`), versi basi dibersihkan |
| Status | Disimpan ke tabel `waha_sessions` (last-known state) dan dikirim ke dashboard tiap 30 detik |

Kalau WAHA butuh scan QR, statusnya jadi `SCAN_QR_CODE` → klik **Tampilkan QR**
di dashboard, pindai dari HP (WhatsApp → Perangkat tertaut).

### Sinkron otomatis (self-heal)

Job `run_waha_sync` berjalan tiap 30 detik (bisa diubah di
**Pengaturan → WAHA**):

- mengecek status session WAHA dan menyimpannya ke `waha_sessions`
- memastikan webhook tetap terdaftar (mendaftarkan ulang bila hilang)
- menyambung ulang / membuat session bila WAHA mati atau session hilang

Event `session.status` dari WAHA juga diterima, sehingga disconnect/reconnect
langsung tercatat (terlihat di dashboard).

### Konfigurasi manual

| Field | Keterangan |
|---|---|
| Base URL WAHA | Kosongkan saja → dideteksi otomatis. isi `http://waha:3000` untuk docker compose |
| API Key | **Wajib** — nilai sama dengan `WHATSAPP_API_KEY` di WAHA |
| Nama session | Default `engineering` |
| URL publik | URL aplikasi yang bisa dijangkau container WAHA (default `http://app:8080`) |
| Token webhook / HMAC | Lanjutan; otomatis dari env bila tidak diisi |

> **Jangan** memakai `localhost`/`127.0.0.1` untuk base URL webhook — dari
> container WAHA itu menunjuk ke dirinya sendiri (`ECONNREFUSED`). Untuk WAHA di
> luar compose, isi `BOT_PUBLIC_URL=http://host.docker.internal:8080`.

### Catatan WAHA versi 2026.x

| Hal | Keterangan |
|---|---|
| API key | WAHA terbaru mewajibkan `WHATSAPP_API_KEY` (header `X-Api-Key`). WAHA & aplikasi memakai nilai sama (`BOT_WAHA_API_KEY`). |
| Endpoint webhook | `POST /api/webhooks` **sudah dihapus**. Webhook kini bagian dari config session: `PUT /api/sessions/{name}` dengan `config.webhooks = [{url, events:["message"], hmac:{key}}]`. Kode kita sudah menyesuaikan. |
| HMAC | Bila `hmac.key` diisi, WAHA mengirim header `X-Webhook-Hmac` (HMAC-SHA256 hex dari body). |
| Chromium / `/dev/shm` | Container WAHA memakai `shm_size: 1gb`. Tanpa itu, halaman WhatsApp Web gagal ter-render dan injeksi whatsapp-web.js timeout (`Waiting failed: 30000ms exceeded`), session macet di `STARTING`. |
| Session | Dibuat otomatis oleh aplikasi (`POST /api/sessions`). Status normal: `STARTING` → `SCAN_QR_CODE` → `WORKING`. |
| **Kirim pesan wajib `session`** | `POST /api/sendText` (dan `sendSeen`) **wajib** menyertakan field `session` di body. Tanpa itu WAHA membalas `400 {"message":"Session name is required"}` — bot jadi tidak bisa membalas. Kode kita sudah otomatis mengirimkannya. |
| **Pesan dari perangkat tertaut** | WAHA menandai pesan yang dikirim dari HP yang terhubung (akun yang sama dengan bot) dengan `fromMe=true`. Pesan seperti itu **diproses** sebagai perintah biasa; hanya balasan bot sendiri yang diabaikan (berdasarkan id pesan + awalan emoji), jadi tidak terjadi loop. |

### Kalau perintah dari HP tidak diproses

1. Buka **Log** di dashboard → pesan dari HP ada di sana tapi tidak jadi ticket?
   - cek awalan pesan (harus `#` atau `/`), dan setelan prefix per grup
   - cek grup aktif di halaman **Grup**
2. Pesan tidak masuk Log sama sekali?
   - buka **Pengaturan → WAHA → Sinkronkan sekarang** (atau **Hubungkan Otomatis**)
   - cek `docker logs bot-waha` → cari `Error sending POST request` (biasanya 401 token / ECONNREFUSED)
3. Bot membalas tapi tidak ada ticket di dashboard → lihat `docker logs bot-engineering-app` (baris `Gagal menjalankan perintah`).

---

## 4. Perintah bawaan

| Perintah | Contoh | Fungsi |
|---|---|---|
| `#ticket` / `#buat` | `#ticket AC kamar 203 tidak dingin` | Buat ticket baru |
| `#status` / `#st` | `#status tkt1120261` | Lihat detail ticket |
| `#status` + status | `#status tkt1120261 DIKERJAKAN` | Ubah status |
| `#pic` | `#pic tkt1120261 Budi` | Tentukan PIC |
| `#tutup` / `#selesai` | `#tutup tkt1120261 Kompresor diganti` | Tutup ticket (SELESAI) |
| `#batal` | `#batal tkt1120261 Tamu pindah` | Batalkan ticket |
| `#list` / `#daftar` | `#list DIKERJAKAN` | Daftar ticket terbaru |
| `#info` / `#resume` / `#ringkasan` | `#info` | Ringkasan hari ini (belum selesai & selesai) |
| `#bantuan` / `#help` | `#bantuan` | Daftar perintah |
| `#foto` / `#photo` | `#foto tkt1120261` | Lampirkan foto ke ticket |

Tambahan:
- Seluruh teks setelah `#ticket` disimpan langsung sebagai `Issue`, termasuk jika memuat `|`
- Prioritas tinggi: awali dengan `[tinggi]`
- Mention sebagai PIC: `#ticket Ganti filter @Budi` (mention dipisahkan dari Issue)

Semua perintah **bisa diedit dari dashboard** (nama, alias, aksi, format, status, dan
transisinya) — perubahan langsung dipakai bot tanpa restart.

### Status & transisi default

```text
BARU ──► DIKERJAKAN ──► SELESAI
  │          │
  └──► MENUNGGU ◄┘
  └──► DIBATALKAN
```

Status bertanda "masih dikerjakan" = kategori **belum selesai** pada ringkasan `#info`.

---

## 5. Foto / lampiran media

Bot bisa menerima satu atau beberapa foto yang menyertai perintah. Foto
**diunduh dari WAHA**, disimpan di server, dicatat di database, dan ditampilkan
di dashboard.

| Cara kirim | Hasil |
|---|---|
| `#ticket AC kamar 203 tidak dingin` + foto | Ticket dibuat, foto dilampirkan |
| `#tutup tkt1120261 sudah diperbaiki` + foto | Ticket ditutup, foto dilampirkan |
| `#foto tkt1120261` + foto | Foto dilampirkan ke ticket yang ada |
| Foto tambahan tanpa caption, maksimal 30 detik setelah ticket dibuat | Dilampirkan jika pengirim dan chat sama |
| Foto saja tanpa ticket terbaru yang cocok | Bot meminta perintah pada caption |

**Penyimpanan**

```text
data/media/<TICKET-ID>/<20261115-142233>-kamar-203.jpg
```

Folder `data/` sudah berupa volume, jadi foto **tetap ada** walau container
dibangun ulang. Batas ukuran berkas: 10 MB.

Setiap lampiran dicatat sebagai baris terpisah di tabel `ticket_media`. Kolom
media pada tabel `tickets` tetap menyimpan foto pertama untuk kompatibilitas
dengan integrasi lama.

Semua foto ticket ditampilkan di halaman detail ticket dashboard
(`/tickets/<ID>`), yang memerlukan login. Foto tidak ditulis ke Google Sheets.

> Catatan: kolom-kolom media & perbaikan ejaan status lama dijalankan otomatis
> oleh migrasi saat aplikasi start (`app/migrate.py`). Migrasi aman diulang.

---

## 6. Google Sheets

1. Buat project di Google Cloud Console → aktifkan **Google Sheets API**.
2. Buat **Service Account** → unduh JSON → simpan sebagai `secrets/service-account.json`
   (folder `secrets/` sudah di-mount ke `/secrets` **dan** `/run/secrets`, jadi
   aplikasi otomatis menemukannya tanpa perlu set path).
3. Bagikan spreadsheet ke email service account (role **Editor**).
4. Di dashboard → **Pengaturan → Google Sheets**: isi Spreadsheet ID dan path
   service account (atau paste JSON). Ticket disinkronkan hanya ke tab `TICKETS`
   dengan kolom `ID`, `Tanggal`, `Issue`, `Status`, `PIC`, `Pelapor`, `Nomor HP`,
   `Grup`, `Selesai`, dan `Keterangan`.
5. Klik **Tes koneksi**, lalu **Sinkron semua ticket**.

Tab dibuat otomatis saat pertama kali sinkron. Database lokal (SQLite) adalah sumber
kebenaran; ticket baru ditambahkan di bagian bawah. Log, ringkasan, judul, dan
foto tidak disinkronkan ke Sheets.

> **Spreadsheet ID** juga bisa lewat `.env` (`BOT_SPREADSHEET_ID`).
> Error `403`/`PermissionDenied` → spreadsheet belum dibagikan ke email service
> account. "File service account tidak ditemukan" → taruh `service-account.json`
> di folder `secrets/` atau paste JSON-nya di dashboard.

> **Catatan Windows**: database SQLite dibuka di dalam container (mode WAL).
> Membaca `data/bot.db` langsung dari Windows saat container berjalan bisa
> menampilkan data lama. Untuk data terkini pakai dashboard, atau
> `docker exec bot-engineering-app python -c "import sqlite3; ..."`.

---

## 7. Dashboard

| Halaman | Isi |
|---|---|
| Dashboard | Statistik, ticket per status, grup, status koneksi WAHA & Sheets (auto-refresh 10 detik) |
| Tickets | Cari/filter, detail ticket, ubah status & PIC, riwayat perintah, hapus seluruh ticket lokal dengan konfirmasi |
| Grup | Aktif/nonaktifkan grup, prefix per grup, ringkasan harian per grup |
| Log | Semua pesan masuk + hasil balasan bot |
| Pengaturan | Prefix, daftar perintah, status & transisi, WAHA, Sheets, jadwal ringkasan |

Login dashboard: set `BOT_DASHBOARD_PASSWORD` di `.env` (kosong = terbuka, untuk lokal saja).
Penghapusan seluruh ticket dari halaman Tickets menghapus ticket lokal, lampiran,
dan log terkait; baris Google Sheets tidak ikut dihapus.

---

## 8. Konfigurasi (`.env`)

| Variabel | Default | Keterangan |
|---|---|---|
| `BOT_DATA_DIR` | `./data` | Lokasi database SQLite |
| `BOT_TIMEZONE` | `Asia/Makassar` | Timezone |
| `BOT_DASHBOARD_PASSWORD` | – | Password dashboard |
| `BOT_SECRET_KEY` | – | Kunci penanda tangan cookie login |
| `BOT_WAHA_BASE_URL` | `http://waha:3000` | Alamat WAHA |
| `BOT_WAHA_API_KEY` | – | API key WAHA |
| `BOT_WAHA_SESSION` | `engineering` | Nama session WAHA |
| `BOT_WEBHOOK_TOKEN` | – | Token validasi webhook |
| `BOT_GOOGLE_SA_FILE` | `/run/secrets/service-account.json` | Lokasi service account (volume `secrets/` sudah di-mount ke `/secrets` **dan** `/run/secrets`) |
| `BOT_SPREADSHEET_ID` | – | ID spreadsheet |

Semua nilai ini bisa ditimpa dari dashboard (kecuali `BOT_SECRET_KEY` & password).
Pengaturan tambahan yang bisa diubah dari dashboard:

| Kunci | Default | Keterangan |
|---|---|---|
| `waha.auto_sync` | `true` | Sinkron otomatis WAHA (status + webhook) |
| `waha.sync_interval` | `30` | Interval sinkron (detik) |
| `waha.public_url` | – | URL aplikasi yang bisa dijangkau WAHA |
| `bot.public_url` | – | URL publik server untuk tautan & foto di Google Sheets |
| `bot.notify_dashboard_changes` | `true` | Notifikasi ke grup saat ticket diubah dari dashboard |

---

## 9. Endpoint penting

| Endpoint | Keterangan |
|---|---|
| `POST /webhook/waha?token=...` | Tujuan event WAHA (`message` + `session.status`) |
| `GET /health` | Health check |
| `GET /api/stats`, `GET /api/tickets`, `GET /api/settings` | Data JSON |
| `POST /api/waha/connect` | **Satu klik**: deteksi → session → webhook (dipakai tombol "Hubungkan Otomatis") |
| `POST /api/waha/sync` | Sinkronkan sekarang (status + self-heal webhook) |
| `GET /api/waha/status` | Status live + last-known + status sinkronisasi webhook |
| `GET /api/waha/detect` | Cari base URL WAHA yang bisa dijangkau |
| `GET /api/waha/webhooks` | Webhook yang benar-benar terdaftar di WAHA |
| `POST /api/waha/start`, `/api/waha/restart`, `/api/waha/qr` | Utilitas WAHA |
| `POST /api/sheets/test`, `/api/sheets/sync` | Utilitas Sheets |

---

## 10. Development

```bash
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Struktur:

```text
app/
├─ main.py            # FastAPI + startup
├─ config.py          # env + default perintah/status
├─ db.py / models.py  # SQLite + ORM
├─ store.py           # repository & settings store
├─ commands.py        # parser perintah (murni, mudah dites)
├─ handlers.py        # aksi perintah & ringkasan harian
├─ waha.py            # klien WAHA
├─ waha_link.py       # deteksi + auto-connect + sinkron/self-heal WAHA
├─ sheets.py          # klien Google Sheets
├─ scheduler.py       # APScheduler
└─ routers/           # dashboard (web.py) + webhook.py
```
