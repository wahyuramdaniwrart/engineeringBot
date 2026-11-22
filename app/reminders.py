"""Penjadwalan reminder harian untuk ticket."""

from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .models import Ticket, utcnow
from .services import get_waha
from .store import local_datetime, now_local, settings_store

log = logging.getLogger("reminders")

REMINDER_RETRY_DELAY = timedelta(minutes=5)


def parse_reminder_datetime(value: str) -> datetime:
    """Parse waktu lokal besok HH:MM atau tanggal YYYY-MM-DD HH:MM."""
    raw = (value or "").strip()
    local_now = now_local()
    match = re.fullmatch(
        r"besok(?:\s+jam)?\s+([01]?\d|2[0-3]):([0-5]\d)",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        date_value = local_now.date() + timedelta(days=1)
        hour, minute = int(match.group(1)), int(match.group(2))
    else:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError(
                "Gunakan format 'besok HH:MM' atau 'YYYY-MM-DD HH:MM'."
            ) from exc
        date_value = parsed.date()
        hour, minute = parsed.hour, parsed.minute

    due_local = datetime.combine(
        date_value,
        time(hour, minute),
        tzinfo=local_now.tzinfo,
    )
    if due_local <= local_now:
        raise ValueError("Waktu reminder harus di masa depan.")
    return due_local.astimezone(timezone.utc)


def clear_ticket_reminder(ticket: Ticket) -> None:
    """Hapus jadwal aktif dan percobaan ulangnya."""
    ticket.reminder_at = None
    ticket.reminder_retry_at = None


def _next_daily_occurrence(scheduled_at: datetime, now: datetime) -> datetime:
    scheduled_local = local_datetime(scheduled_at)
    now_local_value = local_datetime(now)
    if scheduled_local is None or now_local_value is None:
        raise ValueError("Waktu reminder tidak valid.")
    next_local = scheduled_local + timedelta(days=1)
    while next_local <= now_local_value:
        next_local += timedelta(days=1)
    return next_local.astimezone(timezone.utc)


def _claim_due_reminder(
    session: Session, ticket_id: int, now: datetime
) -> tuple[datetime, str, str, str] | None:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None or ticket.reminder_at is None:
        return None
    scheduled_at = ticket.reminder_at
    due_at = (
        scheduled_at
        if scheduled_at.tzinfo is not None
        else scheduled_at.replace(tzinfo=timezone.utc)
    )
    retry_at = ticket.reminder_retry_at
    if retry_at is not None:
        retry_at = (
            retry_at if retry_at.tzinfo else retry_at.replace(tzinfo=timezone.utc)
        )
    if due_at > now or (retry_at is not None and retry_at > now):
        return None
    if not settings_store.is_open_status(ticket.status):
        clear_ticket_reminder(ticket)
        return None
    if not ticket.group_id:
        log.error(
            "Reminder %s dibatalkan: ticket tidak memiliki chat tujuan",
            ticket.ticket_id,
        )
        clear_ticket_reminder(ticket)
        return None

    claimed_until = now + REMINDER_RETRY_DELAY
    result = session.execute(
        update(Ticket)
        .where(
            Ticket.id == ticket.id,
            Ticket.status == ticket.status,
            Ticket.reminder_at == scheduled_at,
            or_(Ticket.reminder_retry_at.is_(None), Ticket.reminder_retry_at <= now),
        )
        .values(reminder_retry_at=claimed_until)
    )
    if result.rowcount != 1:
        return None
    return scheduled_at, ticket.ticket_id, ticket.group_id, ticket.detail or ticket.title


def run_due_reminders() -> int:
    """Kirim reminder yang jatuh tempo; hasil sukses dijadwalkan untuk besok."""
    from .db import session_scope

    now = utcnow()
    with session_scope() as session:
        ticket_ids = list(
            session.execute(
                select(Ticket.id)
                .where(
                    Ticket.reminder_at.is_not(None),
                    Ticket.reminder_at <= now,
                    or_(
                        Ticket.reminder_retry_at.is_(None),
                        Ticket.reminder_retry_at <= now,
                    ),
                )
                .order_by(Ticket.reminder_at, Ticket.id)
            )
            .scalars()
        )

    sent_count = 0
    for ticket_id in ticket_ids:
        now = utcnow()
        with session_scope() as session:
            reminder = _claim_due_reminder(session, ticket_id, now)
        if reminder is None:
            continue

        scheduled_at, ticket_id_text, chat_id, issue = reminder
        message = (
            f"⏰ Reminder harian ticket *{ticket_id_text}*\n"
            f"🧩 {issue}\n"
            f"Gunakan #detail {ticket_id_text} untuk melihat detail ticket."
        )
        try:
            get_waha().send_text(chat_id, message)
        except Exception as exc:
            log.warning(
                "Gagal mengirim reminder ticket %s; dicoba lagi dalam %d menit: %s",
                ticket_id_text,
                int(REMINDER_RETRY_DELAY.total_seconds() // 60),
                exc,
            )
            continue

        sent_at = utcnow()
        with session_scope() as session:
            ticket = session.get(Ticket, ticket_id)
            if ticket is None or ticket.reminder_at != scheduled_at:
                continue
            if not settings_store.is_open_status(ticket.status):
                clear_ticket_reminder(ticket)
                continue
            ticket.reminder_at = _next_daily_occurrence(scheduled_at, sent_at)
            ticket.reminder_retry_at = None
            ticket.reminder_last_sent_at = sent_at
        sent_count += 1

    if sent_count:
        log.info("Reminder ticket terkirim: %d", sent_count)
    return sent_count
