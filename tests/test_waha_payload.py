"""Test parsing payload event WAHA (format lama & format 2026.x).

Perbedaan penting: WAHA 2026.x menghapus field ``chatId`` dari payload pesan.
Kalau parser tidak menyesuaikan, ``chatId`` kosong dan semua pesan diam-diam
diabaikan (HTTP 200 tapi tidak ada yang diproses).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db, session_scope  # noqa: E402
from app.handlers import handle_inbound, parse_waha_payload  # noqa: E402
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


def _payload_2026(body: str, from_me: bool = False) -> dict:
    """Bentuk payload WAHA 2026.x (tanpa chatId)."""
    return {
        "id": "false_120363422939771664@g.us_ACB7012FCDFA5BA4C7639BECDABFE837",
        "timestamp": 1760000000,
        "from": "120363422939771664@g.us",
        "chatName": "Unit Test",
        "fromMe": from_me,
        "participant": "628123456789@c.us",
        "source": "APP",
        "to": "6285158499093@c.us",
        "body": body,
        "hasMedia": False,
        "ack": 3,
        "_data": {"from": "120363422939771664@g.us", "body": body},
    }


def _payload_legacy(body: str) -> dict:
    """Bentuk payload WAHA lama (ada chatId)."""
    return {
        "id": "false_12036@g.us_LEGACY",
        "chatId": "12036@g.us",
        "chatName": "Unit Test",
        "from": "628123456789",
        "participant": "628123456789",
        "pushName": "Front Office",
        "body": body,
        "type": "text",
        "fromMe": False,
    }


def test_payload_2026_group_message_chat_id_derived():
    msg = parse_waha_payload(_payload_2026("#ticket AC ruang server panas"))
    assert msg is not None
    assert msg.chat_id == "120363422939771664@g.us"
    assert msg.is_group is True
    assert msg.from_me is False
    assert msg.sender_phone == "628123456789@c.us"
    assert msg.body == "#ticket AC ruang server panas"


def test_payload_2026_own_message_uses_to_as_chat():
    """Pesan yang dikirim bot: `from` = nomor bot, `to` = chat tujuan."""
    payload = _payload_2026("#ticket dari bot sendiri", from_me=True)
    payload["from"] = "6285158499093@c.us"
    payload["to"] = "120363422939771664@g.us"
    msg = parse_waha_payload(payload)
    assert msg is not None
    assert msg.chat_id == "120363422939771664@g.us"
    assert msg.from_me is True


def test_payload_legacy_still_works():
    msg = parse_waha_payload(_payload_legacy("#ticket uji format lama"))
    assert msg is not None
    assert msg.chat_id == "12036@g.us"
    assert msg.is_group is True


def test_payload_extracts_quoted_ticket_message():
    payload = _payload_2026("progress")
    payload["_data"]["quotedMsg"] = {
        "id": {"_serialized": "bot-message-id"},
        "body": "tkt1120261",
    }
    msg = parse_waha_payload(payload)
    assert msg.quoted_message_id == "bot-message-id"
    assert msg.quoted_body == "tkt1120261"


def test_ticket_created_from_payload_2026():
    msg = parse_waha_payload(_payload_2026("#ticket AC ruang server panas"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)
    assert reply.startswith("tkt")
    with session_scope() as s:
        tickets = list_tickets(s)
    assert len(tickets) == 1
    assert tickets[0].group_id == "120363422939771664@g.us"


def test_private_chat_message():
    payload = {
        "id": "false_628111111111@c.us_PRIV",
        "timestamp": 1760000000,
        "from": "628111111111@c.us",
        "fromMe": False,
        "participant": "628111111111@c.us",
        "to": "6285158499093@c.us",
        "body": "#ticketprivat",
    }
    msg = parse_waha_payload(payload)
    assert msg is not None
    assert msg.chat_id == "628111111111@c.us"
    assert msg.is_group is False
