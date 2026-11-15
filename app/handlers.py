"""Pipeline penanganan pesan WhatsApp.

Alur:
    event WAHA -> parse -> cek grup -> log -> parse perintah -> aksi ->
    balasan ke grup -> sinkron Sheets -> log hasil
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import media
from . import replies
from .commands import (
    ParsedCommand,
    extract_mentions,
    extract_priority,
    format_help,
    parse_command,
    split_ticket_and_rest,
)
from .config import get_settings
from .models import Ticket, TicketMedia, utcnow
from .services import get_sheets, get_waha, sheets_enabled
from .store import (
    add_log,
    get_group,
    get_ticket,
    group_prefixes,
    app_timezone,
    now_local,
    next_ticket_id,
    normalize_status_key,
    settings_store,
    upsert_group,
)

log = logging.getLogger("handlers")

# ---------------------------------------------------------------------------
# Pelacakan pesan yang dikirim bot sendiri
# ---------------------------------------------------------------------------
# WAHA memberi tahu pesan yang dikirim dari perangkat tertaut (HP yang
# terhubung) dengan fromMe=true - padahal itu orang yang memakai HP, bukan
# bot. Kalau semua pesan fromMe diabaikan, perintah dari HP sendiri tidak
# pernah diproses. Solusinya: hanya abaikan pesan yang id-nya memang
# dikirim oleh bot (dicatat di bawah), sisanya diproses.
_SENT_IDS: deque[str] = deque(maxlen=500)
_SENT_ID_SET: set[str] = set()
_SENT_BODIES: deque[str] = deque(maxlen=200)
_SENT_BODY_SET: set[str] = set()
_SENT_TICKET_MESSAGES: dict[str, str] = {}
_SENT_TICKET_MESSAGE_IDS: deque[str] = deque(maxlen=500)
MEDIA_FOLLOWUP_WINDOW_SECONDS = 30
MIN_REPLY_INTERVAL_SECONDS = 2
_REPLY_SEND_LOCK = threading.Lock()
_LAST_REPLY_SENT_AT = 0.0


def extract_message_id(response: Any) -> str:
    """Ambil id pesan dari balasan WAHA (bentuknya beberapa variasi)."""
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return ""
    candidates: list[Any] = [response]
    data = response.get("_data")
    if isinstance(data, dict):
        candidates.append(data)
        inner = data.get("id")
        if isinstance(inner, dict):
            candidates.append(inner)
    for item in candidates:
        for key in ("$1", "id"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def normalize_body(body: str) -> str:
    """Normalisasi isi pesan untuk perbandingan (buang mention & spasi ganda)."""
    text = (body or "").strip()
    text = re.sub(r"^@\S+\s*", "", text)
    return re.sub(r"\s+", " ", text)


def remember_sent_message(message_id: str) -> None:
    """Catat id pesan yang dikirim bot agar tidak diproses ulang."""
    if not message_id:
        return
    if message_id in _SENT_ID_SET:
        return
    _SENT_IDS.append(message_id)
    _SENT_ID_SET.add(message_id)
    if len(_SENT_IDS) == _SENT_IDS.maxlen:
        _SENT_ID_SET.discard(_SENT_IDS[0])


def remember_sent_body(body: str) -> None:
    """Catat isi pesan yang dikirim bot (jaring pengaman bila id tak cocok).

    Balasan bot sekarang polos tanpa emoji, jadi penandaan lewat awalan
    karakter tidak lagi andal. Isi pesan dipakai sebagai cadangan bila id
    pesan yang dilaporkan WAHA tidak cocok.
    """
    norm = normalize_body(body)
    if not norm or norm in _SENT_BODY_SET:
        return
    _SENT_BODIES.append(norm)
    _SENT_BODY_SET.add(norm)
    if len(_SENT_BODIES) == _SENT_BODIES.maxlen:
        _SENT_BODY_SET.discard(_SENT_BODIES[0])


def is_own_bot_message(message_id: str, body: str = "") -> bool:
    """True bila pesan ini adalah balasan bot sendiri."""
    if message_id and message_id in _SENT_ID_SET:
        return True
    norm = normalize_body(body)
    return bool(norm) and norm in _SENT_BODY_SET



@dataclass
class InboundMessage:
    chat_id: str
    chat_name: str = ""
    is_group: bool = False
    sender_phone: str = ""
    sender_name: str = ""
    body: str = ""
    message_id: str = ""
    timestamp: str = ""
    from_me: bool = False
    raw: dict[str, Any] | None = None
    # Media (foto/dokumen) yang menyertai pesan
    msg_type: str = "text"
    has_media: bool = False
    media_url: str = ""
    media_mimetype: str = ""
    media_filename: str = ""
    media_items: list[dict[str, str]] = field(default_factory=list)
    quoted_message_id: str = ""
    quoted_body: str = ""

    @property
    def sender(self) -> str:
        return self.sender_name or self.sender_phone


# Tipe pesan yang bisa berisi media (foto, video, dokumen, audio, stiker).
MEDIA_MSG_TYPES: tuple[str, ...] = (
    "image",
    "video",
    "document",
    "audio",
    "ptt",
    "sticker",
)


def _media_info(source: dict[str, Any], data: dict[str, Any]) -> dict[str, str]:
    url = ""
    for key in ("url", "mediaUrl", "link", "href"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            url = value.strip()
            break
    if not url:
        for key in ("mediaUrl", "media_url", "url"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                url = value.strip()
                break

    mimetype = ""
    for candidate in (source.get("mimetype"), data.get("mimetype"), data.get("mimeType")):
        if isinstance(candidate, str) and candidate.strip():
            mimetype = candidate.strip()
            break

    filename = ""
    for candidate in (source.get("filename"), data.get("filename"), data.get("fileName")):
        if isinstance(candidate, str) and candidate.strip():
            filename = candidate.strip()
            break

    return {"url": url, "mimetype": mimetype, "filename": filename}


def extract_media_items(data: dict[str, Any]) -> list[dict[str, str]]:
    """Ambil seluruh lampiran dari payload WAHA.

    Bentuk payload berbeda antar versi WAHA, jadi beberapa kunci diperiksa
    sekaligus:

    * ``media: {url, mimetype, filename}``  - bentuk object (WAHA 2026)
    * ``media: [{url, mimetype, filename}, ...]`` - beberapa media
    * ``mediaUrl`` / ``url``                - bentuk string
    * ``mimetype`` / ``filename``           - metadata terpisah

    Mengembalikan daftar dict dengan kunci ``url``, ``mimetype``, ``filename``.
    """
    raw_media = data.get("media")
    if isinstance(raw_media, list):
        sources = [
            item if isinstance(item, dict) else {"url": item}
            for item in raw_media
            if isinstance(item, (dict, str))
        ]
    elif isinstance(raw_media, dict):
        sources = [raw_media]
    elif isinstance(raw_media, str):
        sources = [{"url": raw_media}]
    else:
        info = _media_info(data, data)
        return [info] if any(info.values()) else []
    return [_media_info(source, data) for source in sources]


def extract_media(data: dict[str, Any]) -> dict[str, str]:
    """Kompatibilitas helper lama: kembalikan lampiran media pertama."""
    items = extract_media_items(data)
    return items[0] if items else {"url": "", "mimetype": "", "filename": ""}



def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalisasi payload event WAHA.

    Dua perbedaan penting antar versi WAHA:

    * WAHA lama: field pesan ada langsung di level atas, termasuk ``chatId``.
    * WAHA 2026.x: field ``chatId`` **dihapus**. according to dokumentasi WAHA:
      - ``from`` = "ID for the Chat that this message was sent to" (untuk pesan
        masuk, ini id grup/chat-nya)
      - ``to``   = "ID for who this message is for" (untuk pesan yang dikirim
        bot, ini id chat tujuan)
      - ``_data``= data mentah whatsapp-web.js

    Tanpa penyesuaian ini, ``chatId`` selalu kosong sehingga semua pesan
    diam-diam diabaikan.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "_data":
            continue
        out[key] = value

    # Isi field yang tidak ada di level atas dari _data (mis. caption media).
    inner = payload.get("_data")
    if isinstance(inner, dict):
        for key, value in inner.items():
            out.setdefault(key, value)

    # id bisa berupa objek ({"id": ..., "_serialized": ...})
    raw_id = payload.get("id")
    if isinstance(raw_id, dict):
        if "fromMe" not in out and "fromMe" in raw_id:
            out["fromMe"] = bool(raw_id.get("fromMe"))
        out["id"] = str(raw_id.get("_serialized") or raw_id.get("id") or "")

    # Turunkan chat id (WAHA 2026 tidak punya field chatId).
    if not out.get("chatId"):
        from_me = bool(out.get("fromMe", False))
        candidate = out.get("to") if from_me else out.get("from")
        if candidate:
            out["chatId"] = str(candidate)

    return out


def _quoted_message_details(payload: dict[str, Any]) -> tuple[str, str]:
    """Ambil teks dan ID pesan yang dikutip dari variasi payload WAHA."""
    queue = [payload]
    while queue:
        item = queue.pop(0)
        if not isinstance(item, dict):
            continue

        for key in ("quotedMsg", "quotedMessage", "quoted_message", "replyTo", "replyMessage"):
            quoted = item.get(key)
            if isinstance(quoted, dict):
                quoted_id = quoted.get("id") or quoted.get("stanzaId")
                if isinstance(quoted_id, dict):
                    quoted_id = (
                        quoted_id.get("_serialized")
                        or quoted_id.get("id")
                        or quoted_id.get("stanzaId")
                    )
                quoted_body = (
                    quoted.get("body")
                    or quoted.get("text")
                    or quoted.get("caption")
                    or ""
                )
                return str(quoted_id or ""), str(quoted_body).strip()

        for key in ("quotedMsgId", "quotedMessageId", "replyToMessageId"):
            quoted_id = item.get(key)
            if quoted_id:
                return str(quoted_id), ""

        context = item.get("contextInfo")
        if isinstance(context, dict):
            quoted_id = context.get("stanzaId") or context.get("quotedMsgId") or ""
            quoted = context.get("quotedMessage")
            if isinstance(quoted, dict):
                quoted_body = (
                    quoted.get("body")
                    or quoted.get("text")
                    or quoted.get("caption")
                    or ""
                )
                return str(quoted_id), str(quoted_body).strip()
            if quoted_id:
                return str(quoted_id), ""

        queue.extend(
            value
            for key, value in item.items()
            if key in ("_data", "message", "extendedTextMessage", "data")
            and isinstance(value, dict)
        )
    return "", ""


def parse_waha_payload(payload: dict[str, Any]) -> InboundMessage | None:
    """Ubah payload event WAHA menjadi InboundMessage."""
    if not isinstance(payload, dict):
        return None
    data = normalize_payload(payload)
    body = str(data.get("body") or data.get("text") or "")
    msg_type = str(data.get("type") or "text").lower()
    chat_id = str(data.get("chatId") or "")
    if not chat_id:
        return None

    # Deteksi media (foto, dokumen, video, ...). Caption pesan pada
    # fotocee berada di field "body".
    has_media_flag = bool(data.get("hasMedia"))
    media_items = extract_media_items(data)
    has_media = has_media_flag or msg_type in MEDIA_MSG_TYPES or any(
        item["url"] for item in media_items
    )
    if has_media and not media_items:
        media_items = [extract_media(data)]
    if has_media and msg_type in ("", "text"):
        msg_type = "image"

    if msg_type not in ("text", "chat", "") and not has_media:
        # hanya pesan teks yang diproses
        if not body:
            return None
    from_me = bool(data.get("fromMe", False))
    sender_phone = str(data.get("participant") or data.get("from") or "")
    if chat_id.endswith("@g.us"):
        sender_phone = str(data.get("participant") or data.get("from") or "")
    else:
        sender_phone = str(data.get("from") or "")
    ts = data.get("timestamp")
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(
            ts / 1000 if ts > 10**11 else ts, app_timezone()
        ).isoformat()
    quoted_message_id, quoted_body = _quoted_message_details(payload)
    return InboundMessage(
        chat_id=chat_id,
        chat_name=str(data.get("chatName") or data.get("notifyName") or ""),
        is_group=chat_id.endswith("@g.us"),
        sender_phone=sender_phone,
        sender_name=str(data.get("pushName") or data.get("notifyName") or ""),
        body=body,
        message_id=str(data.get("id") or ""),
        timestamp=str(ts or ""),
        from_me=from_me,
        raw=payload,
        msg_type=msg_type,
        has_media=has_media,
        media_url=media_items[0]["url"] if media_items else "",
        media_mimetype=media_items[0]["mimetype"] if media_items else "",
        media_filename=media_items[0]["filename"] if media_items else "",
        media_items=media_items,
        quoted_message_id=quoted_message_id,
        quoted_body=quoted_body,
    )


def _quoted_ticket(session: Session, msg: InboundMessage) -> Ticket | None:
    ticket_id_match = re.fullmatch(
        r"[A-Za-z]+-\d{4}-\d+|[A-Za-z]+\d{7,}", msg.quoted_body.strip()
    )
    ticket_id = ticket_id_match.group(0) if ticket_id_match else ""
    if not ticket_id and msg.quoted_message_id:
        ticket_id = _SENT_TICKET_MESSAGES.get(msg.quoted_message_id, "")
    if not ticket_id:
        return None
    ticket = get_ticket(session, ticket_id)
    if ticket is None or ticket.group_id != msg.chat_id:
        return None
    return ticket


def _reply_status(body: str) -> tuple[str, str] | None:
    aliases = {
        "done": "SELESAI",
        "complete": "SELESAI",
        "completed": "SELESAI",
        "selesai": "SELESAI",
        "closed": "SELESAI",
        "progress": "DIKERJAKAN",
        "in progress": "DIKERJAKAN",
        "ongoing": "DIKERJAKAN",
        "working": "DIKERJAKAN",
        "dikerjakan": "DIKERJAKAN",
        "diproses": "DIKERJAKAN",
        "waiting": "MENUNGGU",
        "pending": "MENUNGGU",
        "hold": "MENUNGGU",
        "menunggu": "MENUNGGU",
        "cancelled": "DIBATALKAN",
        "canceled": "DIBATALKAN",
        "cancel": "DIBATALKAN",
        "dibatalkan": "DIBATALKAN",
        "new": "BARU",
        "open": "BARU",
        "baru": "BARU",
    }
    valid_statuses = settings_store.status_map()
    phrases = dict(aliases)
    for key, definition in valid_statuses.items():
        phrases[key.lower()] = key
        label = str(definition.get("label") or "").strip().lower()
        if label:
            phrases[label] = key

    text = (body or "").strip()
    for phrase, target in sorted(phrases.items(), key=lambda entry: len(entry[0]), reverse=True):
        match = re.match(
            rf"^{re.escape(phrase)}(?=$|[\s,;:.!?-])",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            status = normalize_status_key(target)
            if status in valid_statuses:
                note = text[match.end() :].lstrip(" \t,;:.!-")
                return status, note
    return None


def _handle_quoted_status(session: Session, msg: InboundMessage) -> str | None:
    ticket = _quoted_ticket(session, msg)
    parsed_status = _reply_status(msg.body) if ticket is not None else None
    if ticket is None or parsed_status is None:
        return None

    if msg.is_group:
        group = get_group(session, msg.chat_id)
        if group is not None and not group.enabled:
            return ""

    new_status, note = parsed_status
    old_status = ticket.status
    allowed = settings_store.allowed_transitions(old_status)
    if new_status != old_status and new_status not in allowed:
        reply = replies.status_transition_denied(old_status, new_status, allowed)
    else:
        ticket.status = new_status
        if settings_store.is_open_status(new_status):
            ticket.closed_at = None
        else:
            ticket.closed_at = utcnow()
        if note:
            ticket.resolution = note
        reply = replies.status_changed(ticket, old_status, ticket.resolution or "")
        sync_ticket_to_sheets(session, ticket.ticket_id)

    if reply and settings_store.get("bot.reply_in_group", True):
        await_send(session, msg, reply)

    add_log(
        session,
        event="ticket_status_reply",
        chat_id=msg.chat_id,
        chat_name=msg.chat_name,
        is_group=msg.is_group,
        sender=msg.sender,
        sender_phone=msg.sender_phone,
        body=msg.body,
        command="reply_status",
        result=reply[:200],
        ticket_ref=ticket.ticket_id,
        raw=(
            str(msg.raw)
            if settings_store.get("bot.log_all_messages", True) and msg.raw
            else ""
        ),
    )
    return reply


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def handle_inbound(session: Session, msg: InboundMessage) -> str:
    """Proses satu pesan. Mengembalikan teks balasan (bisa kosong)."""
    if not msg.body.strip() and not msg.has_media:
        return ""

    debug = get_settings().debug_webhook

    if msg.from_me:
        # Pesan dari perangkat tertaut (HP yang terhubung). Balasan bot sendiri
        # harus diabaikan, tapi perintah yang dikirim orang dari HP harus
        # diproses - kalau tidak, "#ticket" dari HP tidak pernah masuk.
        if is_own_bot_message(msg.message_id, msg.body):
            if debug:
                log.info("DIAG: pesan diabaikan (balasan bot sendiri)")
            return ""
        if settings_store.get("bot.ignore_own_messages", False):
            if debug:
                log.info("DIAG: pesan diabaikan (fromMe + abaikan pesan sendiri)")
            return ""
    # Hanya pesan yang cocok dengan prefix dan perintah terdaftar diproses.
    prefixes = group_prefixes(session, msg.chat_id) or settings_store.prefixes
    cmd = parse_command(msg.body, prefixes, settings_store.commands)
    if cmd is None:
        reply = _handle_quoted_status(session, msg)
        if reply is not None:
            return reply
        if msg.has_media:
            if msg.is_group:
                grp = get_group(session, msg.chat_id)
                if grp is not None and not grp.enabled:
                    return ""
            ticket = _recent_ticket_for_media(session, msg)
            if ticket is not None:
                reply = attach_media_to_ticket(session, ticket.ticket_id, msg)
                if "Foto tersimpan" in reply:
                    sync_ticket_to_sheets(session, ticket.ticket_id)
            return ""
        return ""

    if msg.from_me:
        log.info(
            "Perintah dari perangkat tertaut diproses: %s",
            msg.body.strip()[:60],
        )

    # 1. Grup: hanya grup terdaftar/aktif yang diproses.
    if msg.is_group:
        grp = get_group(session, msg.chat_id)
        if grp is not None and not grp.enabled:
            if debug:
                log.warning("DIAG: grup %s NONAKTIF -> pesan dibuang", msg.chat_id)
            return ""
        if grp is None:
            grp = upsert_group(session, msg.chat_id, msg.chat_name)
            if not grp.chat_name:
                try:
                    name = get_waha().chat_name(msg.chat_id)
                except Exception:  # pragma: no cover
                    name = ""
                if name:
                    grp.chat_name = name
            log.info(
                "Grup baru terdaftar: %s (%s)", grp.chat_name or "-", msg.chat_id
            )
        elif not grp.chat_name and msg.chat_name:
            grp.chat_name = msg.chat_name

    if debug:
        log.info(
            "DIAG: perintah dikenali -> %s (chat=%s fromMe=%s media=%s)",
            cmd.name,
            msg.chat_id,
            msg.from_me,
            msg.has_media,
        )

    # 4. Jalankan aksi.
    try:
        reply, ticket_ref = dispatch(session, msg, cmd)
    except Exception as exc:  # pragma: no cover - jaga bot tetap hidup
        log.exception("Gagal menjalankan perintah %s", cmd.name)
        reply = replies.command_error(cmd.prefix, cmd.name, str(exc))
        ticket_ref = ""

    # 4b. Lampirkan foto (bila ada) ke ticket tujuan.
    if msg.has_media and ticket_ref:
        media_reply = attach_media_to_ticket(session, ticket_ref, msg)
        if cmd.action == "create":
            failures = [
                line
                for line in media_reply.splitlines()
                if "gagal" in line.lower() or "tidak bisa diproses" in line.lower()
            ]
            if failures:
                reply = "\n".join([ticket_ref, *failures])
        elif media_reply:
            reply = f"{reply}\n{media_reply}" if reply else media_reply

    # 5. Balas ke grup.
    if reply and settings_store.get("bot.reply_in_group", True):
        await_send(session, msg, reply)

    # 6. Sinkron Sheets.
    if ticket_ref:
        sync_ticket_to_sheets(session, ticket_ref)

    add_log(
        session,
        chat_id=msg.chat_id,
        chat_name=msg.chat_name,
        is_group=msg.is_group,
        sender=msg.sender,
        sender_phone=msg.sender_phone,
        body=msg.body,
        command=cmd.name,
        is_command=True,
        result=reply[:200],
        ticket_ref=ticket_ref,
        raw=(
            str(msg.raw)
            if settings_store.get("bot.log_all_messages", True) and msg.raw
            else ""
        ),
    )

    return reply


def await_send(session: Session, msg: InboundMessage, reply: str) -> None:
    """Kirim tanpa mention dan cegah balasan terkirim beruntun."""
    global _LAST_REPLY_SENT_AT
    try:
        with _REPLY_SEND_LOCK:
            delay = MIN_REPLY_INTERVAL_SECONDS - (
                time.monotonic() - _LAST_REPLY_SENT_AT
            )
            if delay > 0:
                time.sleep(delay)
            try:
                response = get_waha().send_text(msg.chat_id, reply)
            finally:
                _LAST_REPLY_SENT_AT = time.monotonic()
            # Catat id pesan ini supaya event webhook-nya tidak diproses ulang.
            sent_message_id = extract_message_id(response)
            remember_sent_message(sent_message_id)
            remember_sent_body(reply)
            if sent_message_id and re.fullmatch(
                r"[A-Za-z]+-\d{4}-\d+|[A-Za-z]+\d{7,}", reply.strip()
            ):
                if sent_message_id not in _SENT_TICKET_MESSAGES:
                    if len(_SENT_TICKET_MESSAGE_IDS) == _SENT_TICKET_MESSAGE_IDS.maxlen:
                        expired_id = _SENT_TICKET_MESSAGE_IDS.popleft()
                        _SENT_TICKET_MESSAGES.pop(expired_id, None)
                    _SENT_TICKET_MESSAGE_IDS.append(sent_message_id)
                _SENT_TICKET_MESSAGES[sent_message_id] = reply.strip()
    except Exception as exc:
        log.warning("Gagal mengirim balasan: %s", exc)


# ---------------------------------------------------------------------------
# Dispatcher aksi
# ---------------------------------------------------------------------------
def dispatch(
    session: Session, msg: InboundMessage, cmd: ParsedCommand
) -> tuple[str, str]:
    action = cmd.action
    if action == "create":
        return handle_create(session, msg, cmd)
    if action == "status":
        return handle_status(session, msg, cmd)
    if action == "pic":
        return handle_pic(session, msg, cmd)
    if action == "close":
        return handle_close(session, msg, cmd)
    if action == "cancel":
        return handle_cancel(session, msg, cmd)
    if action == "list":
        return handle_list(session, msg, cmd)
    if action == "media":
        return handle_media(session, msg, cmd)
    if action == "info":
        return handle_info(session, msg, cmd)
    if action == "help":
        return format_help(settings_store.commands, settings_store.prefixes), ""
    # tidak dikenal
    return (
        replies.unknown_command(
            cmd.prefix,
            cmd.name,
            format_help(settings_store.commands, settings_store.prefixes),
        ),
        "",
    )


# ---------------------------------------------------------------------------
# Aksi: buat ticket
# ---------------------------------------------------------------------------
def handle_create(
    session: Session, msg: InboundMessage, cmd: ParsedCommand
) -> tuple[str, str]:
    raw_args = cmd.args.strip()
    if not raw_args:
        return (
            replies.format_error(
                f"{cmd.prefix}ticket <issue>",
                f"{cmd.prefix}ticket AC kamar 203 tidak dingin",
            ),
            "",
        )

    issue, priority = extract_priority(raw_args)
    issue, mentions = extract_mentions(issue.strip())
    issue = issue.strip()
    if not issue:
        return (
            replies.format_error(
                f"{cmd.prefix}ticket <issue>",
                f"{cmd.prefix}ticket AC kamar 203 tidak dingin",
            ),
            "",
        )

    pic = mentions[0] if mentions else ""

    ticket_id = next_ticket_id(session, settings_store.ticket_prefix())
    ticket = _new_ticket(
        ticket_id=ticket_id,
        detail=issue,
        priority=priority,
        pic=pic,
        msg=msg,
        session=session,
    )
    session.add(ticket)
    session.flush()

    return ticket.ticket_id, ticket.ticket_id


def _new_ticket(
    *,
    ticket_id: str,
    detail: str,
    priority: str,
    pic: str,
    msg: InboundMessage,
    session: Session,
):
    from .models import Ticket  # lokal import untuk menghindari circular

    return Ticket(
        ticket_id=ticket_id,
        title="",
        detail=detail,
        ticket_type="TASK",
        priority=priority,
        status=settings_store.status_keys()[0] if settings_store.status_keys() else "BARU",
        reporter=msg.sender,
        reporter_phone=msg.sender_phone,
        group_id=msg.chat_id,
        group_name=msg.chat_name,
        pic=pic,
        source="whatsapp",
    )


# ---------------------------------------------------------------------------
# Aksi: status / pic / tutup / batal
# ---------------------------------------------------------------------------
def handle_status(
    session: Session, msg: InboundMessage, cmd: ParsedCommand
) -> tuple[str, str]:
    ticket_id, rest = split_ticket_and_rest(cmd.args)
    if not ticket_id:
        return (
            replies.format_error(
                f"{cmd.prefix}status <TICKET-ID> [STATUS]",
                f"{cmd.prefix}status tkt1120261 DIKERJAKAN",
            ),
            "",
        )
    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        return replies.ticket_not_found(ticket_id), ""

    rest = rest.strip()
    if not rest:
        return format_ticket_detail(ticket), ticket.ticket_id

    new_status = normalize_status_key(rest.split()[0])
    valid = settings_store.status_keys()
    if new_status not in valid:
        return replies.status_unknown(new_status, valid), ticket.ticket_id
    allowed = settings_store.allowed_transitions(ticket.status)
    if new_status != ticket.status and new_status not in allowed:
        return (
            replies.status_transition_denied(ticket.status, new_status, allowed),
            ticket.ticket_id,
        )

    old_status = ticket.status
    ticket.status = new_status
    if new_status == "SELESAI":
        ticket.closed_at = utcnow()
        if rest:
            ticket.resolution = rest[len(rest.split()[0]) :].strip()
    else:
        ticket.closed_at = None

    return (
        replies.status_changed(ticket, old_status, ticket.resolution or ""),
        ticket.ticket_id,
    )


def handle_pic(
    session: Session, msg: InboundMessage, cmd: ParsedCommand
) -> tuple[str, str]:
    ticket_id, rest = split_ticket_and_rest(cmd.args)
    if not ticket_id or not rest:
        return (
            replies.format_error(
                f"{cmd.prefix}pic <TICKET-ID> <nama PIC>",
                f"{cmd.prefix}pic tkt1120261 Budi Santoso",
            ),
            "",
        )
    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        return replies.ticket_not_found(ticket_id), ""
    ticket.pic = rest.strip()
    return replies.pic_changed(ticket), ticket.ticket_id


def handle_close(
    session: Session, msg: InboundMessage, cmd: ParsedCommand
) -> tuple[str, str]:
    ticket_id, rest = split_ticket_and_rest(cmd.args)
    if not ticket_id:
        return (
            replies.format_error(f"{cmd.prefix}tutup <TICKET-ID> [catatan]"),
            "",
        )
    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        return replies.ticket_not_found(ticket_id), ""
    if not settings_store.allowed_transitions(ticket.status):
        return replies.ticket_final(ticket), ticket.ticket_id
    ticket.status = "SELESAI"
    ticket.closed_at = utcnow()
    if rest:
        ticket.resolution = rest
    return (
        replies.ticket_closed(ticket, ticket.resolution or ""),
        ticket.ticket_id,
    )


def handle_cancel(
    session: Session, msg: InboundMessage, cmd: ParsedCommand
) -> tuple[str, str]:
    ticket_id, rest = split_ticket_and_rest(cmd.args)
    if not ticket_id:
        return (
            replies.format_error(f"{cmd.prefix}batal <TICKET-ID> [alasan]"),
            "",
        )
    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        return replies.ticket_not_found(ticket_id), ""
    ticket.status = "DIBATALKAN"
    ticket.closed_at = utcnow()
    if rest:
        ticket.resolution = rest
    return (
        replies.ticket_cancelled(ticket, ticket.resolution or ""),
        ticket.ticket_id,
    )


# ---------------------------------------------------------------------------
# Aksi: lampirkan foto ke ticket
# ---------------------------------------------------------------------------
def _recent_ticket_for_media(session: Session, msg: InboundMessage) -> Ticket | None:
    """Cari ticket baru dari pengirim/chat yang sama untuk foto susulan album."""
    if (
        not msg.has_media
        or msg.msg_type != "image"
        or msg.body.strip()
        or not msg.chat_id
        or not msg.sender_phone
    ):
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=MEDIA_FOLLOWUP_WINDOW_SECONDS
    )
    return session.execute(
        select(Ticket)
        .where(
            Ticket.group_id == msg.chat_id,
            Ticket.reporter_phone == msg.sender_phone,
            Ticket.created_at >= cutoff,
        )
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def attach_media_to_ticket(session: Session, ticket_id: str, msg: InboundMessage) -> str:
    """Unduh seluruh media pesan WAHA lalu lampirkan ke ticket.

    Mengembalikan baris balasan (atau string kosong bila tidak ada media).
    """
    if not msg.has_media:
        return ""

    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        return ""

    media_items = msg.media_items or [
        {
            "url": msg.media_url,
            "mimetype": msg.media_mimetype,
            "filename": msg.media_filename,
        }
    ]
    results: list[str] = []
    for item in media_items:
        url = item.get("url", "")
        if not url:
            log.warning("Pesan punya media tapi tanpa URL media")
            results.append(replies.media_unavailable())
            continue
        try:
            data, content_type = get_waha().download_media(
                url, media.MAX_MEDIA_BYTES
            )
        except Exception as exc:
            # Cadangan: WAHA & aplikasi berbagi volume ./data, jadi berkasnya
            # bisa dibaca langsung tanpa HTTP.
            shared = media.shared_media_path(url)
            if shared is None:
                log.warning("Gagal mengunduh media: %s", exc)
                results.append(replies.media_failed(str(exc)))
                continue
            log.info("Unduh media via volume bersama: %s", shared.name)
            try:
                data = shared.read_bytes()
            except OSError as read_exc:
                log.warning("Gagal membaca media dari volume: %s", read_exc)
                results.append(replies.media_failed(str(read_exc)))
                continue
            content_type = item.get("mimetype", "")

        mimetype = item.get("mimetype") or content_type or ""
        try:
            info = media.save_media(
                ticket.ticket_id, data, item.get("filename", ""), mimetype
            )
        except ValueError as exc:
            log.warning("Gagal menyimpan media: %s", exc)
            results.append(replies.media_failed(str(exc)))
            continue

        info["caption"] = msg.body
        media.attach_to_ticket(ticket, info)
        ticket.media_items.append(
            TicketMedia(
                media_filename=info["filename"],
                media_path=info["path"],
                media_mimetype=info["mimetype"],
                media_size=info["size"],
                media_url=media.build_url(info["path"]),
                media_caption=info["caption"],
            )
        )
        results.append(replies.media_attached(info["filename"], info["size"]))
        log.info(
            "Foto dilampirkan ke %s: %s (%d bytes)",
            ticket.ticket_id,
            info["path"],
            info["size"],
        )

    session.flush()
    return "\n".join(results)


def handle_media(
    session: Session, msg: InboundMessage, cmd: ParsedCommand
) -> tuple[str, str]:
    """Perintah #foto <ID> - lampirkan foto ke ticket yang ada."""
    ticket_id, _rest = split_ticket_and_rest(cmd.args)
    if not ticket_id:
        return (
            replies.format_error(
                f"{cmd.prefix}foto <TICKET-ID>",
                f"{cmd.prefix}foto tkt1120261",
            ),
            "",
        )
    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        return replies.ticket_not_found(ticket_id), ""
    if not msg.has_media:
        return (
            "Tidak ada foto pada pesan ini.\n"
            "Kirim foto beserta perintah, contoh: "
            f"`{cmd.prefix}foto {ticket.ticket_id}`",
            ticket.ticket_id,
        )
    # Unduhan & penyimpanan dilakukan oleh attach_media_to_ticket.
    return "", ticket.ticket_id


# ---------------------------------------------------------------------------
# Aksi: list
# ---------------------------------------------------------------------------
def handle_list(
    session: Session, msg: InboundMessage, cmd: ParsedCommand
) -> tuple[str, str]:
    from .store import list_tickets

    status = ""
    limit = 10
    for token in cmd.arg_list:
        if token.upper() in settings_store.status_keys() or token.upper() == "SEMUA":
            status = token.upper()
        elif token.isdigit():
            limit = min(int(token), 50)
    tickets = list_tickets(session, status=status or None, limit=limit)
    if not tickets:
        return replies.ticket_list_empty(status), ""

    return replies.ticket_list(tickets, len(tickets)), ""


# ---------------------------------------------------------------------------
# Aksi: info (ringkasan untuk penanya)
# ---------------------------------------------------------------------------
def handle_info(
    session: Session, msg: InboundMessage, cmd: ParsedCommand
) -> tuple[str, str]:
    scope = (cmd.args or "").strip().lower()
    day = now_local()
    if scope in ("semua", "all"):
        from .store import list_tickets

        tickets = list_tickets(session, limit=200)
    else:
        from .store import tickets_on

        tickets = tickets_on(session, day)

    open_tickets = [t for t in tickets if settings_store.is_open_status(t.status)]
    done_tickets = [t for t in tickets if not settings_store.is_open_status(t.status)]

    if scope in ("semua", "all"):
        header = "Ringkasan ticket (semua)"
    else:
        header = f"Ringkasan ticket hari ini ({day.strftime('%d-%m-%Y')})"

    return replies.summary_block(open_tickets, done_tickets, header), ""


# ---------------------------------------------------------------------------
# Format detail ticket
# ---------------------------------------------------------------------------
def format_ticket_detail(ticket) -> str:
    return replies.ticket_detail(ticket)


# ---------------------------------------------------------------------------
# Sinkronisasi ticket -> Google Sheets
# ---------------------------------------------------------------------------
def sync_ticket_to_sheets(session: Session, ticket_id: str) -> None:
    if not sheets_enabled():
        return
    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        return
    if not settings_store.get("sheet.auto_sync", True):
        return
    sheets = get_sheets()
    tickets_tab, _, _ = settings_store.sheet_tabs()
    try:
        if not sheets.ready and not sheets.connect():
            return
        sheets.update_ticket(ticket, tickets_tab)
        ticket.sheet_synced_at = utcnow()
    except Exception as exc:  # pragma: no cover
        log.warning("Sinkron Sheets gagal: %s", exc)


# ---------------------------------------------------------------------------
# Ringkasan harian
# ---------------------------------------------------------------------------
def build_summary_rows(day: datetime | None = None) -> list[list[Any]]:
    """Baris untuk tab SUMMARY di Google Sheets."""
    from .db import session_scope
    from .store import tickets_on

    day = day or now_local()
    with session_scope() as s:
        tickets = tickets_on(s, day)

    counts: dict[str, int] = {}
    for t in tickets:
        key = normalize_status_key(t.status)
        counts[key] = counts.get(key, 0) + 1

    status_keys = settings_store.status_keys()
    detail = "; ".join(
        f"{t.ticket_id} {t.detail or t.title} [{t.status}]" for t in tickets
    )
    row = [
        day.strftime("%Y-%m-%d"),
        len(tickets),
        counts.get("BARU", 0),
        counts.get("DIKERJAKAN", 0),
        counts.get("MENUNGGU", 0),
        counts.get("SELESAI", 0),
        counts.get("DIBATALKAN", 0),
        detail,
    ]
    # sisakan baris kosong bila status custom ada
    for key in status_keys:
        if key not in ("BARU", "DIKERJAKAN", "MENUNGGU", "SELESAI", "DIBATALKAN"):
            row.insert(6, counts.get(key, 0))
    return [row]


def daily_summary_text(day: datetime | None = None) -> str:
    """Teks ringkasan harian yang dikirim ke grup."""
    from .db import session_scope
    from .store import tickets_on

    day = day or now_local()
    with session_scope() as s:
        tickets = tickets_on(s, day)
    open_tickets = [t for t in tickets if settings_store.is_open_status(t.status)]
    done_tickets = [t for t in tickets if not settings_store.is_open_status(t.status)]

    return replies.daily_summary(open_tickets, done_tickets, day)


def notify_ticket_change(ticket, changes: list[str]) -> None:
    """Kirim notifikasi ke grup bahwa ticket diubah dari dashboard.

    Ini yang membuat dashboard dan WhatsApp sinkron dua arah.
    """
    if not settings_store.get("bot.notify_dashboard_changes", True):
        return
    if not ticket or not getattr(ticket, "group_id", ""):
        return
    lines = replies.dashboard_change(ticket.ticket_id, changes)
    try:
        response = get_waha().send_text(ticket.group_id, lines)
        remember_sent_message(extract_message_id(response))
        remember_sent_body(lines)
    except Exception as exc:
        log.warning("Gagal mengirim notifikasi dashboard ke grup: %s", exc)


def send_daily_summary() -> None:
    """Kirim ringkasan harian ke semua grup yang mengaktifkan."""
    from .db import session_scope
    from .models import GroupConfig

    if not settings_store.get("bot.daily_summary_enabled", True):
        return
    text = daily_summary_text(now_local())
    with session_scope() as s:
        groups = s.execute(GroupConfig).scalars().all()
        targets = [g for g in groups if g.enabled and g.daily_summary]
    if not targets:
        return
    waha = get_waha()
    for g in targets:
        try:
            response = waha.send_text(g.chat_id, text)
            remember_sent_message(extract_message_id(response))
            remember_sent_body(text)
        except Exception as exc:
            log.warning("Gagal kirim ringkasan ke %s: %s", g.chat_id, exc)
