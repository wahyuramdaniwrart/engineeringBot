"""Router: endpoint webhook yang dipanggil WAHA.

WAHA tidak menyediakan field "token" pada form webhook-nya. Dua cara yang
dipakai sistem ini:

1. **Token di URL** (default) - URL webhook berakhiran ``?token=<TOKEN>``,
   diisi otomatis oleh tombol "Daftarkan webhook" di dashboard.
2. **HMAC key** (opsional, lebih aman) - WAHA mengirim header ``X-Signature``
   berisi HMAC-SHA256 dari body. Aktifkan dengan mengisi "HMAC key".
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Header, Request, Response

from ..config import get_settings
from ..db import session_scope
from ..handlers import handle_inbound, parse_waha_payload
from ..store import settings_store

log = logging.getLogger("webhook")

router = APIRouter(prefix="/webhook", tags=["webhook"])


def _hmac_ok(body: bytes, signature: str, key: str) -> bool:
    if not signature:
        return False
    expected = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


@router.post("/waha")
@router.post("/waha/{session}")
async def waha_webhook(
    request: Request,
    session: str = "default",
    token: str = "",
    x_webhook_token: str = Header(default="", alias="X-Webhook-Token"),
    x_webhook_hmac: str = Header(default="", alias="X-Webhook-Hmac"),
    x_signature: str = Header(default="", alias="X-Signature"),
):
    """Terima event dari WAHA.

    Body bentuknya:
        {"event": "message", "session": "engineering",
         "payload": {"id": "...", "chatId": "...@g.us", "body": "#ticket ..."}}
    """
    raw = await request.body()

    hmac_key = str(settings_store.get("waha.hmac_key") or "")
    expected_token = settings_store.webhook_token()
    require_token = get_settings().webhook_require_token
    debug = get_settings().debug_webhook

    if not require_token:
        log.warning(
            "MODE DIAGNOSTIK: validasi token webhook dimatikan "
            "(BOT_WEBHOOK_REQUIRE_TOKEN=false)"
        )
    elif hmac_key:
        # Validasi HMAC (tidak perlu token di URL).
        signature = x_webhook_hmac or x_signature
        if not _hmac_ok(raw, signature, hmac_key):
            log.warning("Webhook ditolak: signature HMAC tidak cocok")
            return Response(status_code=401, content="invalid signature")
    elif expected_token:
        given = token or x_webhook_token
        if not hmac.compare_digest(expected_token, given or ""):
            log.warning("Webhook ditolak: token tidak cocok")
            return Response(status_code=401, content="invalid token")

    try:
        import json

        data = json.loads(raw or b"{}")
    except Exception:
        return Response(status_code=400, content="invalid json")

    if not isinstance(data, dict):
        return Response(status_code=400, content="invalid payload")

    event = str(data.get("event") or "message")
    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    session_name = str(data.get("session") or "")

    if debug:
        keys = ",".join(sorted(payload.keys()))[:200]
        inner = payload.get("_data")
        inner_keys = ",".join(sorted(inner.keys()))[:200] if isinstance(inner, dict) else "-"
        log.info(
            "WEBHOOK MASUK: event=%s session=%s chatId=%s fromMe=%s type=%s body=%r | "
            "top-keys=[%s] _data-keys=[%s]",
            event,
            session_name or "-",
            payload.get("chatId"),
            payload.get("fromMe"),
            payload.get("type"),
            str(payload.get("body") or payload.get("text") or "")[:80],
            keys,
            inner_keys,
        )

    # Event status session (mis. "session.status"): simpan state supaya
    # dashboard tetap tahu kondisi WAHA walau tidak ada pesan masuk.
    if event.startswith("session.") or event == "state.change":
        status = str(payload.get("status") or payload.get("state") or "")
        if status:
            from ..waha_link import record_session_status

            record_session_status(session_name, status)
            log.info("Status session WAHA: %s (%s)", status, session_name or "-")
        return {"ok": True, "handled": True, "event": event, "status": status}

    msg = parse_waha_payload(payload, session_name=session_name or session)
    if msg is None:
        return {"ok": True, "handled": False}

    try:
        with session_scope() as s:
            reply = handle_inbound(s, msg)
    except Exception as exc:  # pragma: no cover
        log.exception("Gagal menangani webhook: %s", exc)
        return Response(status_code=500, content="internal error")

    return {"ok": True, "handled": True, "event": event, "reply": reply[:500]}
