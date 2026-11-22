# Catatan kerja untuk AI assistant (Claude Code) — repo ini

## Project
Bot WhatsApp untuk engineering (WAHA + FastAPI + SQLite + Google Sheets).
Semua balasannya ke WhatsApp; dashboard web untuk dikelola.

## 🚨 Aturan pengujian (WAHA / WhatsApp) — WAJIB DIPATUHI
- **Jangan pernah mengirim pesan uji ke nomor/grup lain.**
- Pengujian outbound **hanya boleh** ke chat yang namanya **"Admin Engineers"**
  (nomor `95658300190857@lid`, chat id `120363429142189206@g.us`).
- Kalau perlu menguji fungsi bot, kirim ke sana saja. Jangan broadcast
  ke nomor lain, jangan spam grup, dan jangan mengirim pesan ke pelanggan.
- Kalau ragu nomor tujuan → jangan kirim, tanya dulu.

## Perintah penting
```bash
# Test lokal (venv di host, JANGAN lewat Docker)
.venv/Scripts/python -m pytest tests -q

# Build ulang CSS Tailwind WAJIB setelah ubah template / styles
npm run build:css          # output: static/tailwind.css (minified)
npm run watch:css          # mode watch saat developing

# Deploy ulang ke container (kode di-COPY ke image, jadi WAJIB build ulang)
docker compose up -d --build app

# Log
docker compose logs -f app
docker compose logs app --since 30m | grep -i media
```

## Dashboard (Tailwind CSS + Material Symbols)
- Tampilan memakai **Tailwind 3** (Jinja template + utility class), bukan CSS
  tulisan tangan. Sumber gaya: `static/src/tailwind.css` (komponen `.card`,
  `.btn`, `.tbl`, `.pill`, `.alert`, `.tab-btn`, `.kv-list`, `.mi`, ...).
- **Tema LIGHT only** — tidak ada mode gelap. Jangan pakai varian `dark:` di
  template atau `@layer components`.
- **Ikon = Material Symbols Rounded**, self-hosted di `static/fonts/`
  (`MaterialSymbolsRounded.woff2` = outline, `...Fill.woff2` = filled).
  Cara pakai: `<span class="mi">settings</span>` / `<span class="mi mi-fill">smart_toy</span>`.
  Font ini **subset** — ikon yang belum ada akan tampil sebagai teks biasa.
  Tambah ikon: request subset Google Fonts dengan `&icon_names=...` lalu ganti
  file woff2 di `static/fonts/`.
- `tailwind.config.js` = warna brand (hijau WhatsApp), font, animasi.
  Konten dipindai dari `templates/**/*.html` + `static/app.js`.
- `static/tailwind.css` **di-commit** (hasil build) supaya aplikasi jalan tanpa
  Node.js. Dockerfile punya stage `css` yang build ulang; kalau gagal, CSS lama dipakai.
- Interaksi (toast, modal QR, **galeri foto**, pencarian instan tabel, sidebar
  mobile) semuanya di `static/app.js` — Vanilla JS, tanpa framework.
- **Galeri foto:** elemen dengan `data-gallery='<json>'` membuka galeri full-screen.
  JSON dibuat server-side oleh helper `gallery_items()` di `app/routers/web.py`
  (terdaftar sebagai global Jinja) berisi `[{url, name, sheet}]`.
  Viral: `data-gallery-index`, `data-gallery-title`, `data-gallery-detail`.
- **Aturan cascade:** di `@layer components` jangan pakai varian `dark:` lewat
  `@apply` (varian dipindah ke layer utilities dan menimpa komponen lain,
  mis. `.btn-primary` jadi abu-abu). Sekarang tema light, jadi aturan gelap dihapus.
- ID/element penting yang dipakai JS: `#waha-out`, `#sheets-out`, `#waha-info`,
  `#waha-status`, `#waha-webhook`, `#waha-checked`, `#waha-auto`, `#waha-pill`,
  `#waha-pill-text`, `#waha-link-icon`, `#qr-box`, `#webhook-url`, `#copy-webhook`,
  `#cmd-table`, `#st-table`, `#add-cmd`, `#add-st`, `.rm-row`, `#toast-wrap`,
  `#modal`, `#gallery`, `#gallery-image`, `#gallery-video`, `#gallery-thumbs`.
- Aturan HTML di template: `name=` input pengaturan tidak boleh diubah (backend
  membacanya), dan form `#settings-form` (peringatan "belum disimpan").
- Dashboard TIDAK memakai tab lagi (tab disembunyikan bagian WAHA/Sheets/Grup dan
  terasa tidak bisa diklik). Semua integrasi tampil sebagai kartu `.integrasi`
  yang bisa diklik langsung. Tab hanya dipakai di Pengaturan & Detail Ticket.
- **Umpan balik aksi**: tombol `data-action` menulis hasil ke `[data-status]`
  (elemen terdekat di dalam `.card`/`.integrasi`) dengan kelas
  `action-status show ok|err|busy`, plus toast. Semua `data-action` dilayani
  satu handler di `static/app.js` — jangan menambah handler terpisah.
- Halaman Pengaturan: `#settings-form`, `#save-settings`, `#discard-settings`,
  `#dirty-badge`, `#public-url-warning`. Route `settings_page` di
  `app/routers/web.py` mengirim `waha_status`, `wstate`, `sheets_ok`,
  `sheets_error` ke template.
- **Aksi di halaman detail ticket** (`/tickets/{id}/status` & `/pic`): sinkron
  Sheets + notifikasi grup WAHA jalan di **BackgroundTasks** (`_notify_ticket_update`),
  jadi response redirect harus tetap instan. Formnya pakai class `js-submit`
  (umpan balik spinner di `static/app.js`) dan redirect ke `?saved=status|pic`
  untuk menampilkan toast konfirmasi.

## Arsitektur singkat
- 2 container: `app` (bot + dashboard + Sheets) dan `waha` (WhatsApp API).
- **Kode** di-baked ke image → ubah kode = `build` ulang.
- **Data** (`./data`) di-mount ke host → tidak hilang saat rebuild.
- **Pengaturan dashboard** disimpan di tabel `settings` (SQLite) → langsung
  berlaku tanpa restart. DArah **`bot.public_url`** boleh diubah dari dashboard.
- Media/foto disimpan di `./data/media/<TICKET-ID>/`.

## Peringatan
- `secrets/service-account.json` = rahasia, jangan pernah dicetak/di-commit.
- API key WAHA hanya boleh dikirim ke host WAHA sendiri, bukan CDN luar
  (lihat `WahaClient.download_media`).
- Service account Google **tidak punya storage quota Drive** → jangan
  mencoba meng-upload media ke Drive; pakai tautan bertoken dari server.
