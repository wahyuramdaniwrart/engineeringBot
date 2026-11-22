"""Penjadwal: ringkasan harian + sinkron Sheets berkala."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .store import settings_store

log = logging.getLogger("scheduler")

_scheduler: BackgroundScheduler | None = None
_daily_job_id = "daily-summary"
_waha_job_id = "waha-auto-sync"
_ticket_reminders_job_id = "ticket-reminders"


def _tz() -> str:
    return settings_store.timezone()


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone=_tz())
        _scheduler.start()
        log.info("Scheduler dijalankan (timezone=%s)", _tz())
    reschedule_daily()
    reschedule_waha_sync()
    reschedule_ticket_reminders()
    return _scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def reschedule_daily() -> None:
    """Atur ulang job ringkasan harian sesuai konfigurasi dashboard."""
    if _scheduler is None:
        return
    hour = settings_store.daily_summary_hour()
    minute = settings_store.daily_summary_minute()
    try:
        _scheduler.remove_job(_daily_job_id)
    except Exception:
        pass
    _scheduler.add_job(
        run_daily_summary,
        CronTrigger(hour=hour, minute=minute, timezone=_tz()),
        id=_daily_job_id,
        replace_existing=True,
    )
    log.info("Ringkasan harian dijadwalkan pada %02d:%02d (%s)", hour, minute, _tz())


def reschedule_waha_sync() -> None:
    """Atur ulang job sinkron otomatis WAHA (status + webhook self-heal)."""
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(_waha_job_id)
    except Exception:
        pass
    if not settings_store.get("waha.auto_sync", True):
        log.info("Sinkron otomatis WAHA dimatikan")
        return
    seconds = max(10, int(settings_store.get("waha.sync_interval", 30) or 30))
    _scheduler.add_job(
        run_waha_sync,
        IntervalTrigger(seconds=seconds),
        id=_waha_job_id,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    log.info("Sinkron otomatis WAHA dijadwalkan tiap %d detik", seconds)


def reschedule_ticket_reminders() -> None:
    """Periksa reminder yang tersimpan di database setiap menit."""
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(_ticket_reminders_job_id)
    except Exception:
        pass
    _scheduler.add_job(
        run_ticket_reminders,
        IntervalTrigger(seconds=60),
        id=_ticket_reminders_job_id,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    log.info("Pemeriksaan reminder ticket dijadwalkan tiap 60 detik")


def run_waha_sync() -> None:
    """Satu siklus sinkron WAHA: cek status, heal webhook, buat session bila perlu."""
    from .waha_link import auto_connect, refresh_status, sync_webhook

    try:
        state = refresh_status()
        status = str(state.get("status") or "")
        if not status or status in ("ERROR", "STOPPED", "STOPPED_BY_USER"):
            # Session hilang / WAHA mati -> coba sambungkan ulang.
            result = auto_connect()
            log.info("Auto-connect WAHA: %s", result.get("status") or result.get("error"))
            return
        hook = sync_webhook()
        if not hook.get("ok"):
            log.warning("Sinkron webhook WAHA bermasalah: %s", hook.get("error"))
    except Exception as exc:  # pragma: no cover - job tidak boleh mematikan scheduler
        log.exception("Gagal sinkron WAHA: %s", exc)


def run_daily_summary() -> None:
    from .handlers import send_daily_summary

    try:
        send_daily_summary()
    except Exception as exc:  # pragma: no cover
        log.exception("Gagal menjalankan ringkasan harian: %s", exc)


def run_ticket_reminders() -> None:
    from .reminders import run_due_reminders

    try:
        run_due_reminders()
    except Exception as exc:  # pragma: no cover - job tidak boleh mematikan scheduler
        log.exception("Gagal menjalankan reminder ticket: %s", exc)


def run_sheets_sync() -> None:
    """Sinkron ulang semua ticket ke Google Sheets."""
    from .db import session_scope
    from .services import get_sheets
    from .store import list_tickets

    try:
        sheets = get_sheets()
        if not sheets.connect():
            return
        tickets_tab, _, _ = settings_store.sheet_tabs()
        with session_scope() as s:
            items = list_tickets(s, limit=1000)
            count = sheets.sync_all_tickets(items, tickets_tab)
        log.info("Sinkron Sheets: %s ticket", count)
    except Exception as exc:  # pragma: no cover
        log.exception("Gagal sinkron Sheets: %s", exc)
