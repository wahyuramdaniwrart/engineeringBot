"""Fabrik client runtime (WAHA + Google Sheets) yang membaca konfigurasi."""

from __future__ import annotations

import threading
from typing import Any

from .sheets import SheetsClient
from .store import settings_store
from .waha import WahaClient

_waha_lock = threading.Lock()
_waha: WahaClient | None = None
_waha_sig: tuple[str, str, str] | None = None

_sheets_lock = threading.Lock()
_sheets: SheetsClient | None = None
_sheets_sig: tuple[str, str, str] | None = None


def get_waha() -> WahaClient:
    """Client WAHA; dibangun ulang bila konfigurasi berubah."""
    global _waha, _waha_sig
    sig = (
        settings_store.waha_base_url(),
        settings_store.waha_api_key(),
        settings_store.waha_session(),
    )
    with _waha_lock:
        if _waha is None or _waha_sig != sig:
            _waha = WahaClient(base_url=sig[0], api_key=sig[1], session=sig[2])
            _waha_sig = sig
        return _waha


def get_sheets() -> SheetsClient:
    """Client Google Sheets; dibangun ulang bila konfigurasi berubah."""
    global _sheets, _sheets_sig
    path, inline = settings_store.sheet_credentials()
    sig = (settings_store.spreadsheet_id(), path, inline)
    with _sheets_lock:
        if _sheets is None or _sheets_sig != sig:
            _sheets = SheetsClient(
                spreadsheet_id=sig[0], credentials_file=path, credentials_json=inline
            )
            _sheets_sig = sig
        return _sheets


def sheets_enabled() -> bool:
    return bool(settings_store.get("sheet.enabled", True))


def reset_clients() -> None:
    global _waha, _waha_sig, _sheets, _sheets_sig
    with _waha_lock:
        _waha, _waha_sig = None, None
    with _sheets_lock:
        _sheets, _sheets_sig = None, None
