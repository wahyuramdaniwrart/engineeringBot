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

# Deploy ulang ke container (kode di-COPY ke image, jadi WAJIB build ulang)
docker compose up -d --build app

# Log
docker compose logs -f app
docker compose logs app --since 30m | grep -i media
```

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
