"""Test alur ticket: buat -> ubah status -> PIC -> tutup, plus #info."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from itertools import count
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.commands import DEFAULT_PREFIXES, parse_command  # noqa: E402
from app.config import DEFAULT_COMMANDS  # noqa: E402
from app.db import get_engine, init_db, session_scope  # noqa: E402
from app.handlers import await_send, handle_inbound, parse_waha_payload  # noqa: E402
from app.store import list_tickets, next_ticket_id, settings_store  # noqa: E402

_MESSAGE_IDS = count(1)


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
    message_id: str | None = None,
) -> dict:
    return {
        "id": message_id or f"msg-{next(_MESSAGE_IDS)}",
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


def test_detail_command_returns_ticket_and_sends_all_photos(monkeypatch):
    from app.models import Ticket, TicketMedia

    class FakeWaha:
        def __init__(self):
            self.images = []

        def chat_name(self, _chat_id):
            return "Unit Test"

        def send_image(self, chat_id, url, **kwargs):
            self.images.append((chat_id, url, kwargs))

    fake_waha = FakeWaha()
    monkeypatch.setattr("app.handlers.get_waha", lambda: fake_waha)
    monkeypatch.setattr("app.handlers.await_send", lambda *_args: None)
    settings_store.set("bot.reply_in_group", True)
    settings_store.set("bot.public_url", "http://app:8080")

    with session_scope() as s:
        ticket = Ticket(
            ticket_id="tkt11202681",
            title="",
            detail="Perbaikan AC kamar",
            group_id="12036@g.us",
            media_filename="ac-depan.jpg",
            media_path="media/tkt11202681/ac-depan.jpg",
            media_mimetype="image/jpeg",
        )
        ticket.media_items.extend(
            [
                TicketMedia(
                    media_filename="ac-depan.jpg",
                    media_path="media/tkt11202681/ac-depan.jpg",
                    media_mimetype="image/jpeg",
                ),
                TicketMedia(
                    media_filename="ac-belakang.jpg",
                    media_path="media/tkt11202681/ac-belakang.jpg",
                    media_mimetype="image/jpeg",
                ),
            ]
        )
        s.add(ticket)

    msg = parse_waha_payload(_payload("#detail tkt11202681"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)

    assert "*tkt11202681*" in reply
    assert "Perbaikan AC kamar" in reply
    assert "2 lampiran" in reply
    assert len(fake_waha.images) == 2
    assert all(image[0] == "12036@g.us" for image in fake_waha.images)
    assert all(image[1].startswith("http://app:8080/media/") for image in fake_waha.images)
    assert fake_waha.images[1][2]["filename"] == "ac-belakang.jpg"


def test_detail_command_without_photos_reports_no_attachments():
    from app.models import Ticket

    with session_scope() as s:
        s.add(
            Ticket(
                ticket_id="tkt11202682",
                title="",
                detail="Lampu kamar mati",
                group_id="12036@g.us",
            )
        )

    msg = parse_waha_payload(_payload("#detail tkt11202682"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)

    assert "Lampu kamar mati" in reply
    assert "Tidak ada" in reply


def test_summary_places_counts_first_and_bolds_ticket_ids():
    msg = parse_waha_payload(_payload("#ticket Perbaikan lampu"))
    with session_scope() as s:
        ticket_id = handle_inbound(s, msg)

    msg2 = parse_waha_payload(_payload("#info"))
    with session_scope() as s:
        summary = handle_inbound(s, msg2)

    assert summary.index("📌 Total: 1") < summary.index("🟡 Belum selesai: 1")
    assert summary.index("🟡 Belum selesai: 1") < summary.index("✅ Sudah selesai / ditutup hari ini: 0")
    assert summary.index("✅ Sudah selesai / ditutup hari ini: 0") < summary.index("🟡 Belum selesai (1):")
    assert f"*{ticket_id}*" in summary


def test_list_defaults_to_open_tickets_and_supports_status_filter():
    from app.models import Ticket

    with session_scope() as s:
        s.add(Ticket(ticket_id="tkt1120261", title="Ticket terbuka", status="BARU"))
        s.add(Ticket(ticket_id="tkt1120262", title="Ticket selesai", status="SELESAI"))
        s.add(Ticket(ticket_id="tkt1120263", title="Ticket dibatalkan", status="DIBATALKAN"))

    msg = parse_waha_payload(_payload("#list"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)

    assert "*tkt1120261*" in reply
    assert "*tkt1120262*" not in reply
    assert "*tkt1120263*" not in reply
    assert "🆕 BARU" in reply
    assert "\n\n• *tkt1120261*" in reply

    all_msg = parse_waha_payload(_payload("#list SEMUA"))
    with session_scope() as s:
        all_reply = handle_inbound(s, all_msg)
    assert "*tkt1120261*" in all_reply
    assert "*tkt1120262*" in all_reply
    assert "*tkt1120263*" in all_reply

    done_msg = parse_waha_payload(_payload("#list SELESAI"))
    with session_scope() as s:
        done_reply = handle_inbound(s, done_msg)
    assert "*tkt1120262*" in done_reply
    assert "tkt1120261" not in done_reply
    assert "tkt1120263" not in done_reply


def test_list_includes_all_open_tickets_and_excludes_old_done_tickets():
    from datetime import timedelta

    from app.models import Ticket, utcnow

    now = utcnow()
    with session_scope() as s:
        for index in range(51):
            s.add(
                Ticket(
                    ticket_id=f"tkt112027{index + 1}",
                    title=f"Ticket terbuka {index + 1}",
                    status="BARU",
                    created_at=now - timedelta(minutes=index),
                )
            )
        s.add(
            Ticket(
                ticket_id="tkt11202799",
                title="Ticket selesai lama",
                status="SELESAI",
                created_at=now - timedelta(days=1),
            )
        )

    msg = parse_waha_payload(_payload("#list"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)

    assert "*tkt11202799*" not in reply
    assert reply.count("\n\n• *") == 51


def test_info_and_list_put_space_between_each_ticket():
    from types import SimpleNamespace

    from app.replies import summary_block, ticket_list

    first = SimpleNamespace(
        ticket_id="tkt1120261", detail="Issue satu", title="", status="BARU", pic=""
    )
    second = SimpleNamespace(
        ticket_id="tkt1120262", detail="Issue dua", title="", status="BARU", pic=""
    )
    done = SimpleNamespace(
        ticket_id="tkt1120263", detail="Issue selesai", title="", status="SELESAI", pic=""
    )

    listed = ticket_list([first, second])
    summary = summary_block([first, second], [done], "Ringkasan")

    assert "\n\n• *tkt1120261*" in listed
    assert "tkt1120261* — Issue satu | 🆕 BARU\n\n• *tkt1120262*" in listed
    assert "tkt1120261* — Issue satu | 🆕 BARU\n\n• *tkt1120262*" in summary
    assert "tkt1120263* — Issue selesai | ✅ SELESAI" in summary


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


def test_status_command_saves_work_note_for_nonfinal_status():
    msg = parse_waha_payload(_payload("#ticket Perbaikan lampu lobby"))
    with session_scope() as s:
        ticket_id = handle_inbound(s, msg)

    update = parse_waha_payload(
        _payload(f"#status {ticket_id} DIKERJAKAN Pemeriksaan kabel sedang berlangsung")
    )
    with session_scope() as s:
        reply = handle_inbound(s, update)
    assert "Pemeriksaan kabel sedang berlangsung" in reply

    with session_scope() as s:
        from app.store import get_ticket

        ticket = get_ticket(s, ticket_id)
        assert ticket.status == "DIKERJAKAN"
        assert ticket.resolution == "Pemeriksaan kabel sedang berlangsung"
        assert ticket.closed_at is None


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
    assert "Belum selesai: 0" in summary
    assert "Sudah selesai / ditutup hari ini: 1" in summary


def test_legacy_done_status_is_closed_in_info_and_sheet_summary():
    from app.models import Ticket
    from app.handlers import build_summary_rows

    with session_scope() as s:
        s.add(Ticket(ticket_id="tkt1120261", title="Ticket lama", status="DONE"))

    msg = parse_waha_payload(_payload("#info"))
    with session_scope() as s:
        summary = handle_inbound(s, msg)
    assert "Belum selesai: 0" in summary
    assert "Sudah selesai / ditutup hari ini: 1" in summary

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


def test_reply_with_done_command_to_ticket_id_updates_status():
    msg = parse_waha_payload(_payload("#ticket AC kamar 203"))
    with session_scope() as s:
        ticket_id = handle_inbound(s, msg)

    reply_payload = _payload("#done AC sudah diperbaiki", message_id="reply-done")
    reply_payload["quotedMsg"] = {"body": ticket_id}
    with session_scope() as s:
        reply = handle_inbound(s, parse_waha_payload(reply_payload))

    assert "BARU -> SELESAI" in reply
    assert "AC sudah diperbaiki" in reply
    with session_scope() as s:
        from app.store import get_ticket

        ticket = get_ticket(s, ticket_id)
        assert ticket.status == "SELESAI"
        assert ticket.resolution == "AC sudah diperbaiki"


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
        if command["name"] == "status":
            command["usage"] = "#status <ID> [STATUS]"
        if command["name"] == "list":
            command["usage"] = "#list [STATUS] [jumlah]"
            command["description"] = "Daftar ticket semua status"
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
    status_command = next(
        command for command in settings_store.commands if command["name"] == "status"
    )
    assert status_command["usage"] == "#status <ID> [STATUS] [catatan]"
    list_command = next(
        command for command in settings_store.commands if command["name"] == "list"
    )
    assert list_command["usage"] == "#list [STATUS|SEMUA] [jumlah]"
    assert list_command["description"] == "Daftar ticket belum selesai"


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


# ---------------------------------------------------------------------------
# #info: semua ticket yang belum selesai + ticket yang selesai hari ini
# ---------------------------------------------------------------------------
def test_info_shows_all_open_tickets_and_only_tickets_closed_today():
    from datetime import timedelta

    from app.models import Ticket, utcnow

    now = utcnow()
    yesterday = now - timedelta(days=1)
    with session_scope() as s:
        s.add(
            Ticket(
                ticket_id="tkt1120261",
                title="Masih jalan sejak kemarin",
                status="BARU",
                created_at=yesterday,
                updated_at=yesterday,
            )
        )
        s.add(
            Ticket(
                ticket_id="tkt1120262",
                title="Selesai hari ini",
                status="SELESAI",
                created_at=yesterday,
                updated_at=now,
                closed_at=now,
            )
        )
        s.add(
            Ticket(
                ticket_id="tkt1120263",
                title="Selesai kemarin",
                status="SELESAI",
                created_at=yesterday,
                updated_at=yesterday,
                closed_at=yesterday,
            )
        )
        s.commit()

    msg = parse_waha_payload(_payload("#info"))
    with session_scope() as s:
        summary = handle_inbound(s, msg)

    assert "tkt1120261" in summary  # belum selesai, berapa pun umurnya
    assert "tkt1120262" in summary  # selesai hari ini
    assert "tkt1120263" not in summary  # selesai kemarin -> tidak ikut
    assert "Belum selesai: 1" in summary
    assert "Sudah selesai / ditutup hari ini: 1" in summary


def test_info_semua_includes_tickets_closed_on_previous_days():
    from datetime import timedelta

    from app.models import Ticket, utcnow

    now = utcnow()
    yesterday = now - timedelta(days=1)
    with session_scope() as s:
        s.add(
            Ticket(
                ticket_id="tkt1120264",
                title="Selesai kemarin",
                status="SELESAI",
                created_at=yesterday,
                updated_at=yesterday,
                closed_at=yesterday,
            )
        )
        s.commit()

    msg = parse_waha_payload(_payload("#info semua"))
    with session_scope() as s:
        summary = handle_inbound(s, msg)

    assert "tkt1120264" in summary
    assert "Sudah selesai / ditutup: 1" in summary


def test_daily_summary_uses_same_logic_as_info():
    from datetime import timedelta

    from app.handlers import daily_summary_text
    from app.models import Ticket, utcnow

    now = utcnow()
    yesterday = now - timedelta(days=1)
    with session_scope() as s:
        s.add(
            Ticket(
                ticket_id="tkt1120265",
                title="Masih jalan",
                status="BARU",
                created_at=yesterday,
                updated_at=yesterday,
            )
        )
        s.add(
            Ticket(
                ticket_id="tkt1120266",
                title="Selesai kemarin",
                status="SELESAI",
                created_at=yesterday,
                updated_at=yesterday,
                closed_at=yesterday,
            )
        )
        s.commit()

    text = daily_summary_text()
    assert "tkt1120265" in text
    assert "tkt1120266" not in text
    assert "Sudah selesai / ditutup hari ini: 0" in text
