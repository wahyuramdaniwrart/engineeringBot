"""Klien HTTP WAHA (self-hosted WhatsApp API).

Dokumentasi endpoint yang dipakai:
    GET  /health                      -> health check
    GET  /api/sessions                -> daftar session
    POST /api/sessions                -> buat session
    POST /api/{session}/start         -> start / attach session
    GET  /api/{session}/status        -> status session (SCREEN_CONNECTED, dll)
    POST /api/sendText                -> kirim pesan teks
    POST /api/sendSeen                -> tandai sudah dibaca
    POST /api/webhooks                -> daftarkan webhook
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger("waha")


class WahaError(RuntimeError):
    """Error dari WAHA."""


class WahaClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        session: str = "default",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.session = session or "default"
        self.timeout = timeout
        self.last_error: str = ""

    # -- internals ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def request(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> Any:
        if not self.base_url:
            raise WahaError("WAHA base URL belum dikonfigurasi")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.request(
                    method, self._url(path), json=json_body, headers=self._headers()
                )
        except httpx.HTTPError as exc:  # pragma: no cover - jaringan
            self.last_error = f"Gagal menghubungi WAHA: {exc}"
            raise WahaError(self.last_error) from exc
        if resp.status_code >= 400:
            self.last_error = (
                f"WAHA {method} {path} -> {resp.status_code}: {resp.text[:200]}"
            )
            raise WahaError(self.last_error)
        self.last_error = ""
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- session ------------------------------------------------------------
    def list_sessions(self, include_all: bool = True) -> list[dict[str, Any]]:
        path = "/api/sessions?all=true" if include_all else "/api/sessions"
        data = self.request("GET", path)
        if isinstance(data, dict):
            data = data.get("sessions", [])
        return data or []

    def session_exists(self) -> bool:
        try:
            self.request("GET", f"/api/sessions/{self.session}")
            return True
        except WahaError:
            return False

    def ensure_session(self) -> dict[str, Any]:
        """Buat session bila belum ada, lalu start."""
        if not self.session_exists():
            try:
                self.request(
                    "POST",
                    "/api/sessions",
                    {"name": self.session, "config": {}},
                )
            except WahaError as exc:
                # 422 = sudah ada (WAHA menolak create ulang)
                if "422" not in str(exc) and "409" not in str(exc):
                    raise
        return self.start_session()

    def start_session(self) -> dict[str, Any]:
        return self.request("POST", f"/api/sessions/{self.session}/start", {})

    def stop_session(self) -> dict[str, Any]:
        return self.request("POST", f"/api/sessions/{self.session}/stop", {})

    def restart_session(self) -> dict[str, Any]:
        """Stop lalu start session (dipakai saat QR perlu di-refresh)."""
        try:
            self.stop_session()
        except WahaError:
            pass
        return self.start_session()

    def get_qr(self) -> dict[str, Any]:
        """Ambil QR pairing (base64) untuk ditampilkan di dashboard.

        Endpoint WAHA: GET /api/{session}/auth/qr
        """
        data = self.request("GET", f"/api/{self.session}/auth/qr")
        return data if isinstance(data, dict) else {}

    def session_status(self) -> dict[str, Any]:
        try:
            return self.request("GET", f"/api/sessions/{self.session}")
        except WahaError:
            return {}

    def status_text(self) -> str:
        try:
            data = self.session_status()
            if self.last_error:
                return f"ERROR: {self.last_error[:120]}"
            return str(data.get("status", "") or "")
        except Exception:
            return f"ERROR: {self.last_error[:120]}" if self.last_error else ""

    # -- pengiriman --------------------------------------------------------
    # WAHA 2026.x: endpoint global (/api/sendText) menuntut field `session`
    # di body (dari SessionBaseRequest). Tanpa itu: 400 "Session name is required".
    def send_text(
        self,
        chat_id: str,
        text: str,
        mentions: list[dict[str, Any]] | None = None,
    ) -> Any:
        body: dict[str, Any] = {
            "session": self.session,
            "chatId": chat_id,
            "text": text,
        }
        if mentions:
            body["mentions"] = mentions
        try:
            return self.request("POST", "/api/sendText", body)
        except WahaError as exc:
            # Versi WAHA lama memakai path per-session.
            if "Session name is required" not in str(exc) and "404" not in str(exc):
                raise
            return self.request(
                "POST", f"/api/{self.session}/sendText", body
            )

    def send_image(
        self,
        chat_id: str,
        url: str,
        caption: str = "",
        mimetype: str = "",
        filename: str = "",
    ) -> Any:
        file: dict[str, str] = {"url": url}
        if mimetype:
            file["mimetype"] = mimetype
        if filename:
            file["filename"] = filename
        body: dict[str, Any] = {
            "session": self.session,
            "chatId": chat_id,
            "file": file,
        }
        if caption:
            body["caption"] = caption
        try:
            return self.request("POST", "/api/sendImage", body)
        except WahaError as exc:
            if "Session name is required" not in str(exc) and "404" not in str(exc):
                raise
            return self.request("POST", f"/api/{self.session}/sendImage", body)

    def send_file(
        self,
        chat_id: str,
        url: str,
        caption: str = "",
        mimetype: str = "",
        filename: str = "",
    ) -> Any:
        file: dict[str, str] = {"url": url}
        if mimetype:
            file["mimetype"] = mimetype
        if filename:
            file["filename"] = filename
        body: dict[str, Any] = {
            "session": self.session,
            "chatId": chat_id,
            "file": file,
        }
        if caption:
            body["caption"] = caption
        try:
            return self.request("POST", "/api/sendFile", body)
        except WahaError as exc:
            if "Session name is required" not in str(exc) and "404" not in str(exc):
                raise
            return self.request("POST", f"/api/{self.session}/sendFile", body)

    def send_seen(self, chat_id: str) -> Any:
        body = {"session": self.session, "chatId": chat_id}
        try:
            return self.request("POST", "/api/sendSeen", body)
        except WahaError as exc:
            if "Session name is required" not in str(exc) and "404" not in str(exc):
                raise
            return self.request("POST", f"/api/{self.session}/sendSeen", body)

    # -- chat & grup -------------------------------------------------------
    def list_chats(self) -> list[dict[str, Any]]:
        """Daftar chat (grup & kontak) yang terlihat session."""
        data = self.request("GET", f"/api/{self.session}/chats")
        if isinstance(data, dict):
            data = data.get("items") or data.get("chats") or []
        return data or []

    def chat_name(self, chat_id: str) -> str:
        """Nama chat/grup (payload pesan WAHA 2026 tidak membawa chatName)."""
        if not chat_id:
            return ""
        try:
            for chat in self.list_chats():
                cid = chat.get("id")
                if isinstance(cid, dict):
                    cid = cid.get("_serialized") or cid.get("user")
                if str(cid) == chat_id:
                    return str(chat.get("name") or "")
        except WahaError as exc:
            log.debug("Gagal ambil nama chat %s: %s", chat_id, exc)
        return ""

    # -- media -------------------------------------------------------------
    def _resolve_media_url(self, url: str) -> str:
        """Buat URL media menjadi absolut & arahkan ke host WAHA yang benar.

        WAHA menyusun URL media memakai alamat yang dia anggap sebagai
        dirinya sendiri, yaitu ``http://localhost:3000/...``. Dari dalam
        container aplikasi, ``localhost`` adalah container aplikasi - bukan
        WAHA - sehingga koneksi ditolak. Karena itu host lokal ditulis ulang
        ke host WAHA yang dikonfigurasi (``BOT_WAHA_BASE_URL``).
        """
        raw = (url or "").strip()
        if not raw:
            raise WahaError("URL media kosong")
        if raw.startswith(("http://", "https://")):
            return self._rewrite_local_host(raw)
        if raw.startswith("/"):
            return f"{self.base_url}{raw}"
        # selain itu anggap sebagai media id milik WAHA
        return f"{self.base_url}/api/{self.session}/media/{raw}"

    def _rewrite_local_host(self, url: str) -> str:
        """Ganti host lokal (localhost/127.0.0.1) dengan host WAHA."""
        parts = urlsplit(url)
        if (parts.hostname or "").lower() not in (
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "::1",
        ):
            return url
        if not self.base_url:
            return url
        base = urlsplit(self.base_url)
        return urlunsplit(
            parts._replace(scheme=base.scheme or "http", netloc=base.netloc)
        )

    def download_media(self, url: str, max_bytes: int = 10 * 1024 * 1024) -> tuple[bytes, str]:
        """Unduh media (foto) yang dirujuk payload webhook.

        Mengembalikan ``(data, content_type)``.

        Catatan keamanan: API key hanya dikirim bila targetnya adalah
        WAHA milik kita sendiri. Kalau URL-nya menunjuk CDN WhatsApp,
        key **tidak** boleh ikut terkirim.
        """
        target = self._resolve_media_url(url)
        headers: dict[str, str] = {}
        try:
            same_host = urlsplit(target).netloc == urlsplit(self.base_url).netloc
        except ValueError:  # pragma: no cover - URL aneh
            same_host = False
        if same_host and self.api_key:
            headers["X-Api-Key"] = self.api_key

        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                resp = client.get(target, headers=headers)
        except httpx.HTTPError as exc:  # pragma: no cover - jaringan
            raise WahaError(f"Gagal mengunduh media: {exc}") from exc

        if resp.status_code >= 400:
            raise WahaError(
                f"Gagal mengunduh media ({resp.status_code}): {resp.text[:120]}"
            )
        data = resp.content
        if len(data) > max_bytes:
            raise WahaError(
                f"Media terlalu besar ({len(data) // 1024} KB, "
                f"maksimum {max_bytes // 1024} KB)"
            )
        content_type = str(resp.headers.get("content-type") or "").split(";")[0].strip()
        return data, content_type

    # -- webhook ------------------------------------------------------------
    # WAHA 2026.x: webhook dikonfigurasi per-session di dalam `config`,
    # bukan lewat endpoint /api/webhooks (endpoint itu sudah dihapus).
    #   config.webhooks = [{url, events: [...], hmac: {key}}]
    def get_session_config(self) -> dict[str, Any]:
        data = self.request("GET", f"/api/sessions/{self.session}")
        config = data.get("config") if isinstance(data, dict) else None
        return dict(config) if isinstance(config, dict) else {}

    def configure_webhook(
        self,
        url: str,
        events: list[str] | tuple[str, ...] = ("message",),
        hmac_key: str = "",
    ) -> Any:
        """Daftarkan webhook untuk session ini (mengganti yang punya URL sama)."""
        config = self.get_session_config()
        hooks = [h for h in (config.get("webhooks") or []) if isinstance(h, dict)]
        hooks = [h for h in hooks if h.get("url") != url]
        hook: dict[str, Any] = {"url": url, "events": list(events)}
        if hmac_key:
            hook["hmac"] = {"key": hmac_key}
        hooks.append(hook)
        config["webhooks"] = hooks
        return self.request(
            "PUT", f"/api/sessions/{self.session}", {"config": config}
        )

    def remove_webhook(self, url: str = "") -> Any:
        """Hapus webhook (semua, atau yang URL-nya cocok)."""
        config = self.get_session_config()
        hooks = [h for h in (config.get("webhooks") or []) if isinstance(h, dict)]
        if url:
            hooks = [h for h in hooks if h.get("url") != url]
        config["webhooks"] = hooks
        return self.request(
            "PUT", f"/api/sessions/{self.session}", {"config": config}
        )

    def list_webhooks(self) -> list[dict[str, Any]]:
        return list(self.get_session_config().get("webhooks") or [])

    def health(self) -> bool:
        try:
            self.request("GET", "/health")
            return True
        except WahaError:
            return False


def build_client(
    base_url: str, api_key: str = "", session: str = "default"
) -> WahaClient:
    return WahaClient(base_url=base_url, api_key=api_key, session=session)
