"""Test reminder harian untuk ticket."""

from __future__ import annotations

import sys
from datetime import timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db, session_scope  # noqa: E402
from app.handlers import InboundMessage, handle_inbound, parse_waha_payload  # noqa: E402
from app.models import Ticket, utcnow  # noqa: E402
from app.reminders import run_due_reminders  # noqa: E402
from app.store import local_datetime, now_local, settings_store  # noqa: E402


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    init_db()
    settings_store.load()
    settings_store.set("bot.reply_in_group", False)
    yield


def _message(body: str, message_id: str) -> InboundMessage:
    message = parse_waha_payload(
        {
            "id": message_id,
            "chatId": "12036@g.us",
            "participant": "628123456789",
            "from": "628123456789",
            "body": body,
            "type": "text",
            "fromMe": False,
        }
    )
    assert message is not None
    return message


def _add_ticket(ticket_id: str = "tkt1120261", **values) -> None:
    defaults = {
        "ticket_id": ticket_id,
        "title": "",
        "detail": "AC kamar 203 rusak",
        "status": "BARU",
        "group_id": "12036@g.us",
    }
    defaults.update(values)
    with session_scope() as session:
        session.add(Ticket(**defaults))


def test_reminder_command_schedules_daily_from_tomorrow():
    _add_ticket()

    with session_scope() as session:
        reply = handle_inbound(
            session, _message("#ingatkan tkt1120261 besok 09:00", "reminder-set-1")
        )

    assert "berulang setiap hari" in reply
    with session_scope() as session:
        ticket = session.query(Ticket).one()
        due_local = local_datetime(ticket.reminder_at)
        expected_day = now_local().date() + timedelta(days=1)
        assert due_local.date() == expected_day
        assert (due_local.hour, due_local.minute) == (9, 0)
        assert ticket.reminder_retry_at is None


def test_reminder_can_be_cancelled():
    _add_ticket(reminder_at=utcnow() + timedelta(days=1))

    with session_scope() as session:
        reply = handle_inbound(
            session, _message("#ingatkan tkt1120261 batal", "reminder-cancel-1")
        )

    assert "dibatalkan" in reply
    with session_scope() as session:
        ticket = session.query(Ticket).one()
        assert ticket.reminder_at is None
        assert ticket.reminder_retry_at is None


def test_closed_ticket_cancels_reminder():
    _add_ticket(reminder_at=utcnow() + timedelta(days=1))

    with session_scope() as session:
        handle_inbound(session, _message("#tutup tkt1120261", "reminder-close-1"))

    with session_scope() as session:
        ticket = session.query(Ticket).one()
        assert ticket.status == "SELESAI"
        assert ticket.reminder_at is None
        assert ticket.reminder_retry_at is None


def test_due_reminder_sends_and_schedules_next_day(monkeypatch):
    due_at = utcnow() - timedelta(minutes=1)
    _add_ticket(reminder_at=due_at)

    class FakeWaha:
        def __init__(self):
            self.messages = []

        def send_text(self, chat_id, text):
            self.messages.append((chat_id, text))

    fake = FakeWaha()
    monkeypatch.setattr("app.reminders.get_waha", lambda: fake)

    assert run_due_reminders() == 1
    assert len(fake.messages) == 1
    assert fake.messages[0][0] == "12036@g.us"
    assert "tkt1120261" in fake.messages[0][1]

    with session_scope() as session:
        ticket = session.query(Ticket).one()
        next_local = local_datetime(ticket.reminder_at)
        previous_local = local_datetime(due_at)
        assert next_local.date() > now_local().date()
        assert (next_local.hour, next_local.minute) == (
            previous_local.hour,
            previous_local.minute,
        )
        assert local_datetime(ticket.reminder_last_sent_at) is not None
        assert ticket.reminder_retry_at is None


def test_failed_reminder_send_is_delayed_before_retry(monkeypatch):
    due_at = utcnow() - timedelta(minutes=1)
    _add_ticket(reminder_at=due_at)

    class BrokenWaha:
        def __init__(self):
            self.calls = 0

        def send_text(self, _chat_id, _text):
            self.calls += 1
            raise RuntimeError("WAHA unavailable")

    fake = BrokenWaha()
    monkeypatch.setattr("app.reminders.get_waha", lambda: fake)

    assert run_due_reminders() == 0
    assert run_due_reminders() == 0
    assert fake.calls == 1
    with session_scope() as session:
        ticket = session.query(Ticket).one()
        assert local_datetime(ticket.reminder_at) == local_datetime(due_at)
        retry_at = ticket.reminder_retry_at
        assert retry_at is not None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        assert retry_at > utcnow()


def test_existing_database_gets_reminder_columns():
    from sqlalchemy import create_engine, inspect, text

    from app.migrate import add_missing_ticket_columns

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE tickets (id INTEGER PRIMARY KEY, ticket_id VARCHAR(32))")
        )

    added = add_missing_ticket_columns(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("tickets")}

    assert {
        "reminder_at",
        "reminder_retry_at",
        "reminder_last_sent_at",
    }.issubset(columns)
    assert {"reminder_at", "reminder_retry_at", "reminder_last_sent_at"}.issubset(
        set(added)
    )
    engine.dispose()
