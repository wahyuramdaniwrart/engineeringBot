"""Test perintah yang dikirim dari perangkat tertaut (fromMe=true).

WAHA menandai pesan yang dikirim dari HP yang terhubung dengan fromMe=true.
Pesan seperti ini harus tetap diproses (itulah cara orang memakai bot),
sedangkan balasan bot sendiri harus diabaikan agar tidak ada loop.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULT_COMMANDS  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.handlers import (  # noqa: E402
    extract_message_id,
    handle_inbound,
    is_own_bot_message,
    parse_waha_payload,
    remember_sent_body,
    remember_sent_message,
)
from app.store import list_tickets, settings_store  # noqa: E402


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


def _payload(body: str, from_me: bool = True, msg_id: str = "msg-1") -> dict:
    return {
        "id": msg_id,
        "chatId": "12036@g.us",
        "chatName": "Unit Test",
        "from": "628123456789",
        "participant": "628123456789",
        "pushName": "Front Office",
        "body": body,
        "type": "text",
        "fromMe": from_me,
    }


def test_command_from_own_phone_is_processed():
    """Perintah dari HP sendiri harus membuat ticket."""
    msg = parse_waha_payload(_payload("#ticket AC kamar 203 tidak dingin"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)
    assert re.fullmatch(r"tkt\d{7,}", reply)
    with session_scope() as s:
        tickets = list_tickets(s)
    assert len(tickets) == 1
    assert tickets[0].title == ""
    assert tickets[0].detail == "AC kamar 203 tidak dingin"


def test_own_bot_reply_is_ignored():
    """Balasan bot sendiri (id-nya tercatat) tidak boleh diproses lagi."""
    remember_sent_message("msg-bot-1")
    msg = parse_waha_payload(_payload("#ticket ulang-tidak-diproses", msg_id="msg-bot-1"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)
    assert reply == ""
    with session_scope() as s:
        assert list_tickets(s) == []


def test_bot_reply_pattern_is_ignored_even_without_id():
    """Jaring pengaman: isi pesan yang sama dengan balasan bot diabaikan."""
    remember_sent_body("tkt1120261")
    msg = parse_waha_payload(_payload("tkt1120261", msg_id="msg-x"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)
    assert reply == ""
    with session_scope() as s:
        assert list_tickets(s) == []


def test_setting_can_ignore_own_messages():
    settings_store.set("bot.ignore_own_messages", True)
    msg = parse_waha_payload(_payload("#ticket harus diabaikan"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)
    assert reply == ""
    with session_scope() as s:
        assert list_tickets(s) == []


def test_is_own_bot_message_helper():
    remember_sent_message("abc")
    remember_sent_body("Daftar ticket (10 terbaru)")
    assert is_own_bot_message("abc") is True
    assert is_own_bot_message("lain") is False
    # isi pesan sama persis dengan balasan bot -> dianggap pesan bot sendiri
    assert is_own_bot_message("", "Daftar ticket (10 terbaru)") is True
    # mention di depan tidak membuat perbandingan gagal
    assert is_own_bot_message("", "@628123456789 Daftar ticket (10 terbaru)") is True
    # pesan lain tetap diproses
    assert is_own_bot_message("", "#ticket baru") is False


def test_extract_message_id_variants():
    # bentuk balasan WAHA yang diamati di versi 2026.x
    assert extract_message_id({"id": "X1"}) == "X1"
    assert extract_message_id({"_data": {"id": {"id": "X2", "$1": "true_gus_X2"}}}) in (
        "X2",
        "true_gus_X2",
    )
    assert extract_message_id({"_data": {"id": {"$1": "true_gus_X3"}}}) == "true_gus_X3"
    assert extract_message_id(None) == ""
    assert extract_message_id({}) == ""
