"""Konfigurasi aplikasi.

Dua lapis konfigurasi:

1. **Environment** (`.env`) - Rahasia dan hal yang tidak berubah sering:
   URL WAHA, API key, token webhook, lokasi database, lokasi service account.

2. **Database / Dashboard** (`app.store.SettingsStore`) - Yang bisa diubah
   dari dashboard tanpa restart: prefix perintah, daftar perintah, daftar status
   dan transisinya, target spreadsheet, jam ringkasan harian, dll.

Nilai dari dashboard **menang** atas nilai default env (kecuali rahasia yang
tidak pernah diekspos lewat API dashboard).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Nilai statis dari environment."""

    model_config = SettingsConfigDict(
        env_prefix="BOT_",
        env_file=(BASE_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Aplikasi
    data_dir: str = "./data"
    timezone: str = "Asia/Makassar"
    host: str = "0.0.0.0"
    port: int = 8080
    dashboard_password: str = ""
    secret_key: str = "ubah-secret-key-ini"

    # WAHA
    waha_base_url: str = "http://waha:3000"
    waha_api_key: str = ""
    waha_session: str = "engineering"
    webhook_token: str = ""
    # URL aplikasi yang bisa dijangkau container WAHA (untuk webhook).
    public_url: str = ""

    # Google Sheets
    google_sa_file: str = ""
    spreadsheet_id: str = ""

    # Scheduler
    daily_summary_hour: int = 17
    daily_summary_minute: int = 0

    # Diagnostik (sementara)
    # log_detail: catat setiap pesan yang masuk beserta keputusan pipeline-nya
    debug_webhook: bool = False
    # false = webhook diterima tanpa validasi token/HMAC (untuk isolates masalah)
    webhook_require_token: bool = True

    @property
    def db_path(self) -> Path:
        d = Path(self.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d / "bot.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# Nilai default untuk konfigurasi yang bisa diubah lewat dashboard.
# ---------------------------------------------------------------------------

DEFAULT_COMMAND_PREFIXES = ["#", "/"]

DEFAULT_STATUSES: list[dict[str, Any]] = [
    {
        "key": "BARU",
        "label": "Belum Dikerjakan",
        "color": "#e2e8f0",
        "text_color": "#1e293b",
        "open": True,
        "transitions": ["DIKERJAKAN", "MENUNGGU", "SELESAI", "DIBATALKAN"],
    },
    {
        "key": "DIKERJAKAN",
        "label": "Dikerjakan / Progress",
        "color": "#bfdbfe",
        "text_color": "#1e3a8a",
        "open": True,
        "transitions": ["MENUNGGU", "SELESAI", "DIBATALKAN"],
    },
    {
        "key": "MENUNGGU",
        "label": "Menunggu",
        "color": "#fde68a",
        "text_color": "#78350f",
        "open": True,
        "transitions": ["DIKERJAKAN", "SELESAI", "DIBATALKAN"],
    },
    {
        "key": "SELESAI",
        "label": "Selesai",
        "color": "#bbf7d0",
        "text_color": "#14532d",
        "open": False,
        "transitions": [],
    },
    {
        "key": "DIBATALKAN",
        "label": "Dibatalkan",
        "color": "#fecaca",
        "text_color": "#7f1d1d",
        "open": False,
        "transitions": ["BARU"],
    },
]

DEFAULT_COMMANDS: list[dict[str, Any]] = [
    {
        "name": "ticket",
        "aliases": ["buat", "tiket", "task"],
        "description": "Buat ticket baru / tugas engineering",
        "usage": "#ticket <issue> [@PIC]",
        "action": "create",
        "enabled": True,
        "group_only": True,
    },
    {
        "name": "status",
        "aliases": ["st"],
        "description": "Lihat atau ubah status ticket",
        "usage": "#status <ID> [STATUS] [catatan]",
        "action": "status",
        "enabled": True,
        "group_only": False,
    },
    {
        "name": "detail",
        "aliases": [],
        "description": "Lihat detail ticket dan foto lampiran",
        "usage": "#detail <ID>",
        "action": "detail",
        "enabled": True,
        "group_only": False,
    },
    {
        "name": "pic",
        "aliases": [],
        "description": "Tentukan PIC (penanggung jawab) ticket",
        "usage": "#pic <ID> <nama>",
        "action": "pic",
        "enabled": True,
        "group_only": False,
    },
    {
        "name": "tutup",
        "aliases": ["selesai", "done"],
        "description": "Tutup ticket (status SELESAI)",
        "usage": "#tutup <ID> [catatan]",
        "action": "close",
        "enabled": True,
        "group_only": False,
    },
    {
        "name": "batal",
        "aliases": ["cancel"],
        "description": "Batalkan ticket",
        "usage": "#batal <ID> [alasan]",
        "action": "cancel",
        "enabled": True,
        "group_only": False,
    },
    {
        "name": "ingatkan",
        "aliases": ["reminder", "remind"],
        "description": "Atur reminder harian untuk ticket",
        "usage": "#ingatkan <ID> besok HH:MM | batal",
        "action": "reminder",
        "enabled": True,
        "group_only": False,
    },
    {
        "name": "foto",
        "aliases": ["photo", "gambar"],
        "description": "Lampirkan foto ke ticket yang ada",
        "usage": "#foto <ID>",
        "action": "media",
        "enabled": True,
        "group_only": False,
    },
    {
        "name": "list",
        "aliases": ["daftar"],
        "description": "Daftar ticket belum selesai",
        "usage": "#list [STATUS|SEMUA] [jumlah]",
        "action": "list",
        "enabled": True,
        "group_only": False,
    },
    {
        "name": "info",
        "aliases": ["ringkasan", "rekap", "resume", "laporan"],
        "description": "Ringkasan ticket hari ini (belum selesai & selesai) untuk penanya",
        "usage": "#info",
        "action": "info",
        "enabled": True,
        "group_only": False,
    },
    {
        "name": "bantuan",
        "aliases": ["help", "menu"],
        "description": "Tampilkan daftar perintah",
        "usage": "#bantuan",
        "action": "help",
        "enabled": True,
        "group_only": False,
    },
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "bot.prefixes": DEFAULT_COMMAND_PREFIXES,
    "bot.timezone": "Asia/Makassar",
    "bot.mention_sender": False,
    "bot.reply_in_group": True,
    "bot.log_all_messages": True,
    "bot.daily_summary_enabled": True,
    "bot.daily_summary_hour": 17,
    "bot.daily_summary_minute": 0,
    "bot.ticket_prefix": "tkt",
    "bot.notify_dashboard_changes": True,
    "bot.ignore_own_messages": False,
    # Alamat server untuk tautan foto (diisi dari dashboard, mis. http://192.168.1.10:8080)
    "bot.public_url": "",
    "waha.base_url": "",
    "waha.api_key": "",
    "waha.session": "",
    "waha.webhook_token": "",
    "waha.hmac_key": "",
    "waha.public_url": "",
    "waha.webhook_url": "",
    "waha.auto_sync": True,
    "waha.sync_interval": 30,
    "sheet.spreadsheet_id": "",
    "sheet.credentials_file": "",
    "sheet.credentials_json": "",
    "sheet.enabled": True,
    "sheet.auto_sync": True,
    "commands": DEFAULT_COMMANDS,
    "statuses": DEFAULT_STATUSES,
}
