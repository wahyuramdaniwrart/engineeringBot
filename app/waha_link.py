"""Koneksi & sinkron otomatis antara WAHA dan aplikasi.

Tujuan modul ini: onboarding WAHA jadi **satu klik**, dan status WAHA selalu
ikut berubah di dashboard tanpa perlu tombol manual.

Tanggung jawab:

1. **Deteksi base URL otomatis** - mencoba kandidat umum (docker compose
   `waha:3000`, `localhost:3000`, `host.docker.internal:3000`) lalu memakai
   yang pertama bisa dijangkau dengan API key yang benar.
2. **Buat & start session otomatis** - session yang hilang dibuatkan, session
   yang mati di-start.
3. **Webhook self-heal** - webhook didaftarkan otomatis dan otomatis
   diperbaiki bila hilang / URL-nya basi.
4. **Simpan status session** - status terakhir ditulis ke tabel
   ``waha_sessions`` supaya dashboard tetap informatif saat WAHA mati.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from .config import get_settings
from .services import get_waha, reset_clients
from .store import get_waha_session, now_local, save_waha_session, settings_store
from .waha import WahaClient, WahaError

log = logging.getLogger("waha_link")

# Kandidat base URL WAHA, urut dari yang paling mungkin.
CANDIDATE_URLS: tuple[str, ...] = (
    "http://waha:3000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://host.docker.internal:3000",
)

# Event WAHA yang dipakai untuk webhook.
WEBHOOK_EVENTS: tuple[str, ...] = ("message", "session.status")


# ---------------------------------------------------------------------------
# Deteksi
# ---------------------------------------------------------------------------
def _probe(url: str, api_key: str) -> tuple[bool, str]:
    """Coba hubungi WAHA di `url`. Balikan (berhasil?, pesan error)."""
    try:
        client = WahaClient(base_url=url, api_key=api_key, timeout=8.0)
        client.request("GET", "/api/sessions?all=true")
        return True, "ok"
    except WahaError as exc:
        return False, str(exc)


def candidate_keys(extra: str = "") -> list[str]:
    """Kandidat API key: dari dashboard, dari env, lalu tambahan.

    Kunci yang disimpan di dashboard bisa saja basi (mis. WAHA di-restart
    dengan key baru) - karena itu semua kunci dicoba.
    """
    keys: list[str] = []
    for raw in (extra, settings_store.waha_api_key(), get_settings().waha_api_key):
        key = (raw or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def candidate_urls(extra: str = "") -> list[str]:
    """Kandidat base URL WAHA, urut dari yang paling mungkin."""
    urls: list[str] = []
    for raw in (extra, settings_store.waha_base_url(), *CANDIDATE_URLS):
        url = (raw or "").strip().rstrip("/")
        if url and url not in urls:
            urls.append(url)
    return urls


def resolve_connection(extra_url: str = "") -> dict[str, Any]:
    """Cari kombinasi (base URL, API key) yang benar-benar bisa dipakai.

    Balikan ``{"url", "key", "error"}``.
    """
    errors: list[str] = []
    for url in candidate_urls(extra_url):
        for key in candidate_keys():
            ok, err = _probe(url, key)
            if ok:
                return {"url": url, "key": key, "error": ""}
            errors.append(f"{url} -> {err}")
    return {"url": "", "key": "", "error": "; ".join(errors) or "tidak ada kandidat"}


def detect_base_url(api_key: str = "", extra: str = "") -> tuple[str, str]:
    """Cari base URL WAHA yang bisa dijangkau.

    Balikan ``(url, error)``; url kosong bila tidak ada yang bisa dijangkau.
    """
    result = resolve_connection(extra)
    return result["url"], result["error"]


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------
def desired_webhook_url() -> str:
    """URL webhook yang harus terdaftar di WAHA."""
    base = settings_store.public_url()
    if not base:
        base = str(settings_store.get("waha.public_url") or "").rstrip("/")
    return settings_store.webhook_url(fallback=base)


def _same_path(a: str, b: str) -> bool:
    try:
        return urlparse(a).path == urlparse(b).path
    except ValueError:  # pragma: no cover - urlparse jarang gagal
        return False


def sync_webhook() -> dict[str, Any]:
    """Pastikan webhook terdaftar di WAHA (self-heal).

    - menambah webhook bila URL kita belum ada
    - menghapus versi basi (path sama, host berbeda, mis. 127.0.0.1)
    """
    waha = get_waha()
    desired = desired_webhook_url()
    if not desired.startswith("http"):
        return {
            "ok": False,
            "error": "URL webhook belum valid. Isi 'URL publik' di Pengaturan → WAHA.",
            "url": desired,
        }

    hmac_key = str(settings_store.get("waha.hmac_key") or "")
    try:
        registered = waha.list_webhooks()
    except WahaError as exc:
        return {"ok": False, "error": str(exc), "url": desired}

    urls = [str(h.get("url") or "") for h in registered if isinstance(h, dict)]
    removed: list[str] = []

    # Buang versi lama milik kita (path sama tapi host berbeda).
    for url in urls:
        if url and url != desired and _same_path(url, desired):
            try:
                waha.remove_webhook(url)
                removed.append(url)
            except WahaError as exc:
                log.warning("Gagal hapus webhook lama %s: %s", url, exc)

    added = False
    if desired not in urls:
        try:
            waha.configure_webhook(desired, events=WEBHOOK_EVENTS, hmac_key=hmac_key)
            added = True
        except WahaError as exc:
            return {
                "ok": False,
                "error": f"Gagal mendaftarkan webhook: {exc}",
                "url": desired,
                "removed": removed,
            }

    settings_store.set("waha.webhook_url", desired)
    return {
        "ok": True,
        "url": desired,
        "added": added,
        "removed": removed,
        "registered": [str(h.get("url") or "") for h in waha.list_webhooks()],
    }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def refresh_status() -> dict[str, Any]:
    """Ambil status session dari WAHA dan simpan ke database."""
    waha = get_waha()
    name = waha.session
    status = ""
    error = ""
    me = ""
    try:
        data = waha.session_status()
        status = str(data.get("status") or "")
        me_info = data.get("me")
        if isinstance(me_info, dict):
            me = str(me_info.get("id") or "")
    except WahaError as exc:
        error = str(exc)
        status = "ERROR"

    from .db import session_scope

    try:
        with session_scope() as s:
            save_waha_session(s, name, status, error)
    except Exception as exc:  # pragma: no cover - database tidak boleh menggagalkan sync
        log.warning("Gagal menyimpan status session: %s", exc)

    return {
        "session": name,
        "status": status,
        "error": error,
        "me": me,
        "checked_at": now_local().isoformat(timespec="seconds"),
    }


def session_state() -> dict[str, Any]:
    """Ringkasan status WAHA untuk dashboard (live + last known)."""
    live = refresh_status()
    last_known = ""
    last_check = ""
    last_error = ""
    try:
        from .db import session_scope

        with session_scope() as s:
            row = get_waha_session(s, live["session"])
            if row is not None:
                last_known = row.status or ""
                last_check = row.last_check.isoformat(timespec="seconds") if row.last_check else ""
                last_error = row.last_error or ""
    except Exception:  # pragma: no cover
        pass

    webhook_ok = False
    webhook_error = ""
    webhook_url = desired_webhook_url()
    try:
        waha = get_waha()
        urls = [str(h.get("url") or "") for h in waha.list_webhooks()]
        webhook_ok = webhook_url in urls
        if not webhook_ok:
            webhook_error = "Webhook belum terdaftar di WAHA"
    except WahaError as exc:
        webhook_error = str(exc)

    return {
        "ok": not live["error"],
        "base_url": settings_store.waha_base_url(),
        "session": live["session"],
        "status": live["status"] or (last_known or "tidak diketahui"),
        "live_status": live["status"],
        "error": live["error"] or last_error,
        "me": live["me"],
        "checked_at": live["checked_at"],
        "last_known": last_known,
        "last_check": last_check,
        "webhook_url": webhook_url,
        "webhook_ok": webhook_ok,
        "webhook_error": webhook_error,
        "auto_sync": bool(settings_store.get("waha.auto_sync", True)),
    }


# ---------------------------------------------------------------------------
# Auto connect
# ---------------------------------------------------------------------------
def auto_connect(*, register_webhook: bool = True, start_session: bool = True) -> dict[str, Any]:
    """Sambungkan WAHA secara otomatis: deteksi -> session -> webhook.

    Aman dipanggil berkala; semua error dikembalikan sebagai dict, tidak raise.
    """
    steps: list[str] = []

    # 1. Deteksi base URL + API key yang benar (self-heal bila basi).
    conn = resolve_connection()
    base_url = conn["url"]
    if not base_url:
        return {
            "ok": False,
            "error": f"WAHA tidak dapat dihubungi. {conn['error']}",
            "steps": steps,
        }

    url_changed = base_url != settings_store.waha_base_url()
    key_changed = bool(conn["key"]) and conn["key"] != settings_store.waha_api_key()
    if url_changed:
        settings_store.set("waha.base_url", base_url)
        steps.append(f"Base URL WAHA disesuaikan ke {base_url}")
    if key_changed:
        settings_store.set("waha.api_key", conn["key"])
        steps.append("API key diperbarui ke key yang diterima WAHA")
    if url_changed or key_changed:
        reset_clients()

    waha = get_waha()
    name = waha.session

    # 2. Pastikan session ada.
    status_data: dict[str, Any] = {}
    try:
        status_data = waha.session_status()
    except WahaError:
        status_data = {}

    if not status_data:
        try:
            waha.request("POST", "/api/sessions", {"name": name, "config": {}})
            steps.append(f"Session '{name}' dibuat")
            status_data = waha.session_status()
        except WahaError as exc:
            if "422" not in str(exc) and "409" not in str(exc):
                return {
                    "ok": False,
                    "error": f"Gagal membuat session '{name}': {exc}",
                    "steps": steps,
                }

    state = str(status_data.get("status") or "")

    # 3. Start bila session belum jalan.
    if start_session and state not in ("WORKING", "SCAN_QR_CODE", "STARTING"):
        try:
            waha.start_session()
            steps.append(f"Session '{name}' di-start")
        except WahaError as exc:
            log.warning("Gagal start session: %s", exc)
            steps.append(f"Gagal start session: {exc}")

    # 4. Webhook otomatis (self-heal).
    hook_result: dict[str, Any] = {}
    if register_webhook:
        hook_result = sync_webhook()
        if hook_result.get("ok"):
            if hook_result.get("added"):
                steps.append("Webhook didaftarkan otomatis")
            elif hook_result.get("removed"):
                steps.append("Webhook lama dibersihkan")
            else:
                steps.append("Webhook sudah sinkron")
        else:
            steps.append(f"Webhook bermasalah: {hook_result.get('error')}")

    # 5. Simpan status terbaru.
    live = refresh_status()

    return {
        "ok": not live["error"],
        "base_url": base_url,
        "session": name,
        "status": live["status"],
        "error": live["error"],
        "needs_qr": live["status"] in ("SCAN_QR_CODE", "STARTING"),
        "webhook": hook_result,
        "steps": steps,
    }


def record_session_status(session_name: str, status: str, error: str = "") -> None:
    """Catat status session yang diterima lewat event webhook."""
    name = session_name or settings_store.waha_session()
    try:
        from .db import session_scope

        with session_scope() as s:
            save_waha_session(s, name, status, error)
    except Exception as exc:  # pragma: no cover
        log.warning("Gagal menyimpan status session: %s", exc)
