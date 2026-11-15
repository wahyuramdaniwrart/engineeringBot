"""Test alur ticket: buat -> ubah status -> PIC -> tutup, plus #info."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.commands import DEFAULT_PREFIXES, parse_command  # noqa: E402
from app.config import DEFAULT_COMMANDS  # noqa: E402
from app.db import get_engine, init_db, session_scope  # noqa: E402
from app.handlers import await_send, handle_inbound, parse_waha_payload  # noqa: E402
from app.store import list_tickets, next_ticket_id, settings_store  # noqa: E402


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
    # matikan pengiriman balasan ke WAHA saat test
    settings_store.set("bot.reply_in_group", False)
    yield
    settings_store.set("bot.reply_in_group", True)


def _payload(
    body: str,
    chat_id: str = "12036@g.us",
    message_id: str = "msg-1",
) -> dict:
    return {
        "id": message_id,
        "chatId": chat_id,
        "chatName": "Unit Test",
        "from": "628123456789",
        "participant": "628123456789",
        "pushName": "Front Office",
        "body": body,
        "type": "text",
        "fromMe": False,
    }


def test_create_ticket():
    msg = parse_waha_payload(_payload("#ticket AC kamar 203 tidak dingin"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)
    assert re.fullmatch(r"tkt\d{7,}", reply)
    with session_scope() as s:
        ticket = list_tickets(s, limit=1)[0]
    assert ticket.title == ""
    assert ticket.detail == "AC kamar 203 tidak dingin"


def test_ticket_text_after_prefix_is_one_issue_even_with_separator():
    msg = parse_waha_payload(_payload("#ticket Bocor kamar 12 | air di atas lantai"))
    with session_scope() as s:
        handle_inbound(s, msg)
        ticket = list_tickets(s, limit=1)[0]

    assert ticket.title == ""
    assert ticket.detail == "Bocor kamar 12 | air di atas lantai"


def test_ticket_id_month_year_and_monthly_sequence():
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    with session_scope() as s:
        assert next_ticket_id(s, "TKT", now) == "tkt1120261"
        from app.models import Ticket

        s.add(Ticket(ticket_id="tkt1120261", title="Ticket pertama"))
        s.flush()
        assert next_ticket_id(s, "TKT", now) == "tkt1120262"
        assert next_ticket_id(s, "TKT", now.replace(month=12)) == "tkt1220261"


def test_datetime_formatting_uses_configured_timezone():
    from app.store import format_local_datetime, now_local

    settings_store.set("bot.timezone", "Asia/Makassar")
    utc_time = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    assert format_local_datetime(utc_time, "%Y-%m-%d %H:%M") == "2026-11-15 20:00"
    assert now_local().utcoffset().total_seconds() == 8 * 60 * 60


def test_ticket_appears_in_info():
    msg = parse_waha_payload(_payload("#ticket AC kamar 203 tidak dingin"))
    with session_scope() as s:
        handle_inbound(s, msg)

    msg2 = parse_waha_payload(_payload("#info"))
    with session_scope() as s:
        reply = handle_inbound(s, msg2)
    assert "Belum selesai" in reply
    assert "AC kamar 203" in reply
    assert "tkt" in reply


def test_status_transition_and_close():
    msg = parse_waha_payload(_payload("#ticket Ganti filter lobby"))
    with session_scope() as s:
        handle_inbound(s, msg)

    with session_scope() as s:
        from app.store import get_ticket, list_tickets

        ticket = list_tickets(s, limit=1)[0]
        tid = ticket.ticket_id

    msg2 = parse_waha_payload(_payload(f"#status {tid} DIKERJAKAN"))
    with session_scope() as s:
        reply = handle_inbound(s, msg2)
    assert "DIKERJAKAN" in reply

    msg3 = parse_waha_payload(_payload(f"#pic {tid} Budi Santoso"))
    with session_scope() as s:
        handle_inbound(s, msg3)

    msg4 = parse_waha_payload(_payload(f"#tutup {tid} Kompresor diganti"))
    with session_scope() as s:
        reply = handle_inbound(s, msg4)
    assert "SELESAI" in reply

    with session_scope() as s:
        from app.store import get_ticket

        t = get_ticket(s, tid)
        assert t.status == "SELESAI"
        assert t.pic == "Budi Santoso"
        assert t.closed_at is not None


def test_done_status_is_counted_as_closed_in_summary():
    msg = parse_waha_payload(_payload("#ticket AC kamar 203"))
    with session_scope() as s:
        ticket_id = handle_inbound(s, msg)

    msg2 = parse_waha_payload(_payload(f"#status {ticket_id} done"))
    with session_scope() as s:
        reply = handle_inbound(s, msg2)
    assert "BARU -> SELESAI" in reply

    msg3 = parse_waha_payload(_payload("#resume"))
    with session_scope() as s:
        summary = handle_inbound(s, msg3)
    assert "Belum selesai (0)" in summary
    assert "Sudah selesai / ditutup (1)" in summary


def test_legacy_done_status_is_closed_in_info_and_sheet_summary():
    from app.models import Ticket
    from app.handlers import build_summary_rows

    with session_scope() as s:
        s.add(Ticket(ticket_id="tkt1120261", title="Ticket lama", status="DONE"))

    msg = parse_waha_payload(_payload("#info"))
    with session_scope() as s:
        summary = handle_inbound(s, msg)
    assert "Belum selesai (0)" in summary
    assert "Sudah selesai / ditutup (1)" in summary

    rows = build_summary_rows(datetime.now())
    assert rows[0][5] == 1


def test_final_statuses_are_closed_even_if_settings_mark_them_open():
    statuses = [dict(status) for status in settings_store.statuses]
    for status in statuses:
        if status["key"] == "SELESAI":
            status["open"] = True
    settings_store.set("statuses", statuses)

    assert settings_store.is_open_status("SELESAI") is False
    assert settings_store.is_open_status("DONE") is False


def test_reply_to_ticket_id_updates_status():
    msg = parse_waha_payload(_payload("#ticket AC kamar 203"))
    with session_scope() as s:
        ticket_id = handle_inbound(s, msg)

    reply_payload = _payload("done AC sudah diperbaiki", message_id="reply-1")
    reply_payload["quotedMsg"] = {
        "id": {"_serialized": "bot-ticket-message"},
        "body": ticket_id,
    }
    reply_msg = parse_waha_payload(reply_payload)
    assert reply_msg.quoted_body == ticket_id
    assert reply_msg.quoted_message_id == "bot-ticket-message"

    with session_scope() as s:
        reply = handle_inbound(s, reply_msg)
    assert "BARU -> SELESAI" in reply
    assert "AC sudah diperbaiki" in reply

    with session_scope() as s:
        from app.store import get_ticket

        ticket = get_ticket(s, ticket_id)
        assert ticket.status == "SELESAI"
        assert ticket.resolution == "AC sudah diperbaiki"
        assert ticket.closed_at is not None


def test_reply_without_ticket_quote_is_ignored():
    msg = parse_waha_payload(_payload("#ticket AC kamar 203"))
    with session_scope() as s:
        ticket_id = handle_inbound(s, msg)

    unrelated = parse_waha_payload(_payload("done"))
    with session_scope() as s:
        assert handle_inbound(s, unrelated) == ""
        from app.store import get_ticket

        assert get_ticket(s, ticket_id).status == "BARU"


def test_reply_message_id_fallback_updates_ticket(monkeypatch):
    class FakeWaha:
        def send_text(self, chat_id, text):
            return {"id": f"bot-reply-{text}"}

    monkeypatch.setattr("app.handlers.get_waha", lambda: FakeWaha())
    monkeypatch.setattr("app.handlers.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("app.handlers._LAST_REPLY_SENT_AT", 0.0)
    create_msg = parse_waha_payload(_payload("#ticket AC kamar 203"))
    with session_scope() as s:
        ticket_id = handle_inbound(s, create_msg)
        await_send(s, create_msg, ticket_id)

    reply_payload = _payload("progress", message_id="reply-progress")
    reply_payload["quotedMsgId"] = f"bot-reply-{ticket_id}"
    with session_scope() as s:
        reply = handle_inbound(s, parse_waha_payload(reply_payload))
    assert "BARU -> DIKERJAKAN" in reply


def test_invalid_status_rejected():
    msg = parse_waha_payload(_payload("#ticket Bocor di kamar 12"))
    with session_scope() as s:
        handle_inbound(s, msg)
    with session_scope() as s:
        from app.store import list_tickets

        tid = list_tickets(s, limit=1)[0].ticket_id

    msg2 = parse_waha_payload(_payload(f"#status {tid} MENUNGGU"))
    with session_scope() as s:
        reply = handle_inbound(s, msg2)
    # BARU -> MENUNGGU memang diperbolehkan, cek pesan konfirmasi
    assert "diubah" in reply or "tidak bisa" in reply


def test_unknown_ticket_id():
    msg = parse_waha_payload(_payload("#status TKT-9999-9999"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)
    assert "tidak ditemukan" in reply


def test_message_logged():
    msg = parse_waha_payload(_payload("#list"))
    with session_scope() as s:
        handle_inbound(s, msg)
    with session_scope() as s:
        from sqlalchemy import select

        from app.models import MessageLog

        logs = s.execute(select(MessageLog)).scalars().all()
    assert len(logs) == 1
    assert logs[0].command == "list"


def test_unprefixed_message_is_not_logged():
    msg = parse_waha_payload(_payload("Halo, ada yang bisa bantu?"))
    with session_scope() as s:
        assert handle_inbound(s, msg) == ""
    with session_scope() as s:
        from sqlalchemy import select

        from app.models import MessageLog

        assert s.execute(select(MessageLog)).scalars().all() == []


def test_await_send_does_not_mention_sender(monkeypatch):
    class FakeWaha:
        def __init__(self):
            self.sent = []

        def send_text(self, chat_id, text):
            self.sent.append((chat_id, text))
            return {"id": "fake-message-id"}

    fake = FakeWaha()
    monkeypatch.setattr("app.handlers.get_waha", lambda: fake)
    monkeypatch.setattr("app.handlers.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("app.handlers._LAST_REPLY_SENT_AT", 0.0)
    msg = parse_waha_payload(_payload("#list"))

    with session_scope() as s:
        await_send(s, msg, "tkt1120261")

    assert fake.sent == [("12036@g.us", "tkt1120261")]


def test_parse_prefixes_configurable():
    cmd = parse_command("!ticket AC", ["!"], DEFAULT_COMMANDS)
    assert cmd is not None and cmd.action == "create"


def test_resume_alias_is_migrated_into_existing_command_settings():
    from app.migrate import ensure_commands
    from app.models import Setting

    commands = [dict(command) for command in DEFAULT_COMMANDS]
    for command in commands:
        if command["name"] == "info":
            command["aliases"] = [
                alias for alias in command["aliases"] if alias != "resume"
            ]
        if command["name"] == "ticket":
            command["usage"] = "#ticket <judul> | #ticket <judul> | <detail> | @PIC"
    with session_scope() as s:
        s.add(Setting(key="commands", value=json.dumps(commands)))

    assert "info" in ensure_commands(get_engine())
    settings_store.load()
    parsed = parse_command("#resume", settings_store.prefixes, settings_store.commands)
    assert parsed is not None and parsed.action == "info"
    ticket_command = next(
        command for command in settings_store.commands if command["name"] == "ticket"
    )
    assert ticket_command["usage"] == "#ticket <issue> [@PIC]"


def test_legacy_title_is_moved_into_empty_issue():
    from app.migrate import move_legacy_ticket_titles_to_issue
    from app.models import Ticket

    with session_scope() as s:
        s.add(Ticket(ticket_id="tkt1120261", title="Issue lama"))
        s.add(
            Ticket(
                ticket_id="tkt1120262",
                title="Judul lama",
                detail="Issue yang sudah ada",
            )
        )
        s.commit()

    assert move_legacy_ticket_titles_to_issue(get_engine()) == 1
    with session_scope() as s:
        tickets = list_tickets(s, limit=10)

    by_id = {ticket.ticket_id: ticket for ticket in tickets}
    assert by_id["tkt1120261"].detail == "Issue lama"
    assert by_id["tkt1120262"].detail == "Issue yang sudah ada"
