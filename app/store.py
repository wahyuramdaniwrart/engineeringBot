"""Repository: konfigurasi, ticket, grup, dan log pesan."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import DEFAULT_SETTINGS, get_settings
from .models import GroupConfig, MessageLog, Setting, Ticket, WahaSession, utcnow


# ---------------------------------------------------------------------------
# Settings store (key/value, di-cache di memori)
# ---------------------------------------------------------------------------
# Alias status lama -> status baru.
# "DIBERJAKAN" (typo lama) dipetakan ke "DIKERJAKAN" supaya data lama dan
# perintah lama yang masih memakai ejaan itu tetap terbaca.
LEGACY_STATUS_ALIASES: dict[str, str] = {
    "DIBERJAKAN": "DIKERJAKAN",
    "DONE": "SELESAI",
    "COMPLETE": "SELESAI",
    "COMPLETED": "SELESAI",
    "CLOSED": "SELESAI",
    "PROGRESS": "DIKERJAKAN",
    "IN PROGRESS": "DIKERJAKAN",
    "WORKING": "DIKERJAKAN",
    "PENDING": "MENUNGGU",
    "WAITING": "MENUNGGU",
    "CANCEL": "DIBATALKAN",
    "CANCELED": "DIBATALKAN",
    "CANCELLED": "DIBATALKAN",
    "OPEN": "BARU",
    "NEW": "BARU",
}


def normalize_status_key(key: str) -> str:
    """Normalisasi nama status (huruf besar + alias lama)."""
    normalized = (key or "").strip().upper()
    return LEGACY_STATUS_ALIASES.get(normalized, normalized)


class SettingsStore:
    """Konfigurasi runtime yang bisa diubah dari dashboard."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[str, Any] = {}
        self._loaded = False

    # -- load / save -------------------------------------------------------
    def load(self, session: Session | None = None) -> dict[str, Any]:
        with self._lock:
            if session is None:
                from .db import session_scope

                with session_scope() as s:
                    rows = s.execute(select(Setting)).scalars().all()
                    self._cache = {r.key: _decode(r.value) for r in rows}
            else:
                rows = session.execute(select(Setting)).scalars().all()
                self._cache = {r.key: _decode(r.value) for r in rows}
            self._loaded = True
            return dict(self._cache)

    def refresh(self) -> None:
        self._loaded = False
        self.load()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if not self._loaded:
                self.load()
            if key in self._cache and self._cache[key] not in (None, ""):
                return self._cache[key]
        if default is not None:
            return default
        return DEFAULT_SETTINGS.get(key)

    def set(self, key: str, value: Any, session: Session | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        if session is None:
            from .db import session_scope

            with session_scope() as s:
                self._upsert(s, key, payload)
        else:
            self._upsert(session, key, payload)
        with self._lock:
            self._cache[key] = value

    def set_many(self, values: dict[str, Any], session: Session | None = None) -> None:
        payload = {k: json.dumps(v, ensure_ascii=False) for k, v in values.items()}
        if session is None:
            from .db import session_scope

            with session_scope() as s:
                for k, v in payload.items():
                    self._upsert(s, k, v)
        else:
            for k, v in payload.items():
                self._upsert(session, k, v)
        with self._lock:
            self._cache.update(values)

    def _upsert(self, session: Session, key: str, payload: str) -> None:
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=payload))
        else:
            row.value = payload
            row.updated_at = utcnow()

    def all(self) -> dict[str, Any]:
        with self._lock:
            if not self._loaded:
                self.load()
            return dict(self._cache)

    # -- helper konfigurasi ------------------------------------------------
    @property
    def prefixes(self) -> list[str]:
        val = self.get("bot.prefixes") or DEFAULT_SETTINGS["bot.prefixes"]
        return [str(p) for p in val if str(p).strip()]

    @property
    def commands(self) -> list[dict[str, Any]]:
        return list(self.get("commands") or DEFAULT_SETTINGS["commands"])

    @property
    def statuses(self) -> list[dict[str, Any]]:
        return list(self.get("statuses") or DEFAULT_SETTINGS["statuses"])

    def status_map(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for s in self.statuses:
            key = normalize_status_key(s.get("key", ""))
            if not key:
                continue
            item = dict(s)
            item["key"] = key
            item["transitions"] = [
                normalize_status_key(t) for t in (s.get("transitions") or [])
            ]
            out[key] = item
        return out

    def status_keys(self) -> list[str]:
        return list(self.status_map().keys())

    def status_label(self, key: str) -> str:
        st = self.status_map().get(normalize_status_key(key))
        return st["label"] if st else key

    def is_open_status(self, key: str) -> bool:
        normalized = normalize_status_key(key)
        if normalized in ("SELESAI", "DIBATALKAN"):
            return False
        st = self.status_map().get(normalized)
        if st is None:
            return True
        return bool(st.get("open", True))

    def allowed_transitions(self, status: str) -> list[str]:
        st = self.status_map().get(normalize_status_key(status))
        if not st:
            return []
        return [t.upper() for t in st.get("transitions", [])]

    def timezone(self) -> str:
        env = get_settings().timezone
        return str(self.get("bot.timezone") or env)

    def daily_summary_hour(self) -> int:
        return int(self.get("bot.daily_summary_hour") or get_settings().daily_summary_hour)

    def daily_summary_minute(self) -> int:
        return int(
            self.get("bot.daily_summary_minute") or get_settings().daily_summary_minute
        )

    def ticket_prefix(self) -> str:
        return str(self.get("bot.ticket_prefix") or "tkt").strip().lower()

    def waha_base_url(self) -> str:
        return str(self.get("waha.base_url") or get_settings().waha_base_url).rstrip("/")

    def waha_api_key(self) -> str:
        return str(self.get("waha.api_key") or get_settings().waha_api_key)

    def waha_session(self) -> str:
        return str(self.get("waha.session") or get_settings().waha_session)

    def webhook_token(self) -> str:
        return str(self.get("waha.webhook_token") or get_settings().webhook_token)

    def public_url(self) -> str:
        """URL aplikasi yang bisa dijangkau container WAHA."""
        return str(
            self.get("waha.public_url") or get_settings().public_url or ""
        ).rstrip("/")

    def webhook_url(self, fallback: str = "") -> str:
        """URL webhook lengkap (dengan token bila ada)."""
        base = self.public_url() or (fallback or "").rstrip("/")
        url = f"{base}/webhook/waha" if base else "/webhook/waha"
        token = self.webhook_token()
        if token:
            url += f"?token={token}"
        return url

    @property
    def media_public_url(self) -> str:
        """Alamat server untuk tautan media (bisa diubah dari dashboard).

        Berbeda dengan ``waha.public_url`` yang dipakai WAHA untuk memanggil
        webhook, nilai ini adalah alamat yang diklik manusia - mis. Alamat IP
        LAN atau domain internal perusahaan.
        """
        return str(self.get("bot.public_url") or "").rstrip("/")

    def spreadsheet_id(self) -> str:
        return str(self.get("sheet.spreadsheet_id") or get_settings().spreadsheet_id)

    def sheet_tabs(self) -> tuple[str, str, str]:
        return "TICKETS", "", ""

    def sheet_credentials(self) -> tuple[str, str]:
        """Kembalikan (path_file, json_inline)."""
        path = str(
            self.get("sheet.credentials_file")
            or get_settings().google_sa_file
            or ""
        )
        inline = str(self.get("sheet.credentials_json") or "")
        return path, inline


def _decode(raw: str) -> Any:
    if raw in (None, ""):
        return ""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


settings_store = SettingsStore()


# ---------------------------------------------------------------------------
# Ticket repository
# ---------------------------------------------------------------------------
def next_ticket_id(session: Session, prefix: str, now: datetime | None = None) -> str:
    now = now or datetime.now(app_timezone())
    prefix = prefix.strip().lower()
    period = now.strftime("%m%Y")
    stem = f"{prefix}{period}"
    latest = session.execute(
        select(Ticket.ticket_id)
        .where(Ticket.ticket_id.like(f"{stem}%"))
        .order_by(Ticket.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    sequence = 1
    if latest and latest.lower().startswith(stem):
        suffix = latest[len(stem) :]
        if suffix.isdigit():
            sequence = int(suffix) + 1
    return f"{stem}{sequence}"


def get_ticket(session: Session, ticket_id: str) -> Ticket | None:
    if not ticket_id:
        return None
    ticket_id = ticket_id.strip()
    ticket = session.execute(
        select(Ticket).where(func.lower(Ticket.ticket_id) == ticket_id.lower())
    ).scalar_one_or_none()
    if ticket is None:
        # toleransi: user mengetik "12" untuk ticket ke-12
        if ticket_id.isdigit():
            return session.execute(
                select(Ticket)
                .where(Ticket.ticket_id.like(f"%-{ticket_id}"))
                .order_by(Ticket.id.desc())
                .limit(1)
            ).scalars().first()
    return ticket


def list_tickets(
    session: Session,
    status: str | None = None,
    group_id: str | None = None,
    query: str | None = None,
    limit: int | None = 50,
) -> list[Ticket]:
    stmt = select(Ticket)
    if status and status.upper() != "SEMUA":
        stmt = stmt.where(Ticket.status == status.upper())
    if group_id:
        stmt = stmt.where(Ticket.group_id == group_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            (Ticket.title.ilike(like))
            | (Ticket.detail.ilike(like))
            | (Ticket.ticket_id.ilike(like))
            | (Ticket.pic.ilike(like))
        )
    stmt = stmt.order_by(Ticket.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())


def count_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status)
    ).all()
    return {r[0]: r[1] for r in rows}


def app_timezone() -> tzinfo:
    """Timezone aplikasi (dari pengaturan dashboard / env)."""
    name = settings_store.timezone()
    try:
        return ZoneInfo(name)
    except Exception:  # pragma: no cover - timezone tidak valid
        return timezone.utc


def now_local() -> datetime:
    """Waktu sekarang dalam timezone yang dipilih di pengaturan bot."""
    return datetime.now(app_timezone())


def local_datetime(value: datetime | None) -> datetime | None:
    """Konversi timestamp database (UTC) ke timezone aplikasi untuk ditampilkan."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(app_timezone())


def format_local_datetime(value: datetime | None, fmt: str) -> str:
    localized = local_datetime(value)
    return localized.strftime(fmt) if localized else "-"


def day_bounds_utc(day: datetime | None = None) -> tuple[datetime, datetime]:
    """Batas hari lokal -> rentang UTC, untuk membandingkan dengan data UTC.

    Ticket disimpan dalam UTC, sedangkan "hari ini" memakai zona waktu lokal.
    Tanpa konversi ini, ticket yang dibuat setelah tengah malam lokal ikut
    tersimpan sebagai hari sebelumnya dan tidak muncul di ringkasan harian.
    """
    tz = app_timezone()
    day = day or now_local()
    if day.tzinfo is not None:
        day = day.astimezone(tz)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def tickets_on(session: Session, day: datetime) -> list[Ticket]:
    start, end = day_bounds_utc(day)
    stmt = (
        select(Ticket)
        .where(Ticket.created_at >= start, Ticket.created_at < end)
        .order_by(Ticket.created_at.asc())
    )
    return list(session.execute(stmt).scalars().all())


def list_open_tickets(session: Session, limit: int = 500) -> list[Ticket]:
    """Semua ticket yang BELUM selesai, berapa pun umur ticketnya.

    Dipakai #list dan ringkasan (#info & harian): bagian "belum selesai"
    harus menampilkan seluruh ticket yang masih berjalan, bukan hanya
    ticket yang dibuat hari ini.
    """
    tickets = list(
        session.execute(
            select(Ticket).order_by(Ticket.created_at.asc()).limit(limit)
        ).scalars().all()
    )
    return [t for t in tickets if settings_store.is_open_status(t.status)]


def list_closed_tickets(
    session: Session, day: datetime | None = None, limit: int = 500
) -> list[Ticket]:
    """Ticket yang sudah selesai (closed).

    - `day` diisi  -> hanya ticket yang ditutup pada hari itu.
    - `day` None  -> semua ticket yang sudah selesai, kapan pun.

    Ticket lama yang belum punya `closed_at` (ditutup sebelum kolom itu
    dipakai) dianggap selesai pada hari terakhir ticketnya diperbarui.
    """
    tickets = list(
        session.execute(
            select(Ticket).order_by(Ticket.created_at.asc()).limit(limit)
        ).scalars().all()
    )
    closed = [t for t in tickets if not settings_store.is_open_status(t.status)]
    if day is None:
        return closed

    start, end = day_bounds_utc(day)
    out: list[Ticket] = []
    for ticket in closed:
        marker = ticket.closed_at or ticket.updated_at
        if marker is None:
            continue
        if marker.tzinfo is None:
            marker = marker.replace(tzinfo=timezone.utc)
        marker = marker.astimezone(timezone.utc)
        if start <= marker < end:
            out.append(ticket)
    return out


# ---------------------------------------------------------------------------
# Group repository
# ---------------------------------------------------------------------------
def get_group(session: Session, chat_id: str) -> GroupConfig | None:
    return session.execute(
        select(GroupConfig).where(GroupConfig.chat_id == chat_id)
    ).scalar_one_or_none()


def upsert_group(session: Session, chat_id: str, chat_name: str = "") -> GroupConfig:
    grp = get_group(session, chat_id)
    if grp is None:
        grp = GroupConfig(chat_id=chat_id, chat_name=chat_name)
        session.add(grp)
        session.flush()
    elif chat_name and not grp.chat_name:
        grp.chat_name = chat_name
    return grp


def group_prefixes(session: Session, chat_id: str) -> list[str] | None:
    """Prefix khusus grup; None = pakai prefix global."""
    grp = get_group(session, chat_id)
    if grp is None or not grp.prefixes.strip():
        return None
    return [p.strip() for p in grp.prefixes.split(",") if p.strip()]


# ---------------------------------------------------------------------------
# Log repository
# ---------------------------------------------------------------------------
def add_log(
    session: Session,
    *,
    event: str = "message",
    chat_id: str = "",
    chat_name: str = "",
    is_group: bool = False,
    sender: str = "",
    sender_phone: str = "",
    body: str = "",
    command: str = "",
    is_command: bool = False,
    result: str = "",
    ticket_ref: str = "",
    raw: str = "",
) -> MessageLog:
    log = MessageLog(
        event=event,
        chat_id=chat_id,
        chat_name=chat_name,
        is_group=is_group,
        sender=sender,
        sender_phone=sender_phone,
        body=body[:4000],
        command=command,
        is_command=is_command,
        result=result[:255],
        ticket_ref=ticket_ref,
        raw=raw[:4000],
    )
    session.add(log)
    return log


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Status session WAHA (sinkron otomatis)
# ---------------------------------------------------------------------------
def get_waha_session(session: Session, name: str) -> WahaSession | None:
    """Status session WAHA terakhir yang diketahui."""
    if not name:
        return None
    return session.execute(
        select(WahaSession).where(WahaSession.name == name)
    ).scalar_one_or_none()


def save_waha_session(
    session: Session, name: str, status: str, error: str = ""
) -> WahaSession:
    """Simpan status session WAHA terakhir (dipakai untuk last-known state)."""
    row = get_waha_session(session, name)
    if row is None:
        row = WahaSession(name=name)
        session.add(row)
    row.status = status or ""
    row.last_error = error or ""
    row.last_check = now_utc()
    session.flush()
    return row
