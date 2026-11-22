from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db, session_scope  # noqa: E402
from app.models import GroupConfig, MessageLog, Ticket, TicketMedia  # noqa: E402
from app.store import settings_store  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.config as config
    import app.db as db
    from app.routers.web import router

    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOT_DASHBOARD_PASSWORD", "")
    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    init_db()
    settings_store.load()

    application = FastAPI()
    application.include_router(router)
    with TestClient(application) as test_client:
        yield test_client


def test_delete_all_requires_confirmation(client):
    with session_scope() as session:
        session.add(Ticket(ticket_id="tkt1120261", title="Ticket uji"))

    response = client.post(
        "/tickets/delete-all",
        data={"confirmation": "HAPUS TICKET"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "delete_error=confirmation" in response.headers["location"]
    with session_scope() as session:
        assert session.query(Ticket).count() == 1


def test_delete_all_removes_local_ticket_media_and_related_logs(client, tmp_path):
    ticket_id = "tkt1120261"
    rel_path = f"media/{ticket_id}/foto.jpg"
    image = tmp_path / "media" / ticket_id / "foto.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"test image")

    with session_scope() as session:
        ticket = Ticket(
            ticket_id=ticket_id,
            title="Ticket uji",
            media_path=rel_path,
            media_filename="foto.jpg",
        )
        session.add(ticket)
        session.flush()
        session.add(
            TicketMedia(
                ticket_id=ticket.id,
                media_path=rel_path,
                media_filename="foto.jpg",
            )
        )
        session.add(
            MessageLog(ticket_ref=ticket_id, body="#ticket Ticket uji")
        )
        session.add(MessageLog(body="log tidak terkait"))

    response = client.post(
        "/tickets/delete-all",
        data={"confirmation": "HAPUS SEMUA TICKET"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "deleted=1" in response.headers["location"]
    assert "logs_deleted=1" in response.headers["location"]
    assert image.exists() is False
    assert image.parent.exists() is False
    with session_scope() as session:
        assert session.query(Ticket).count() == 0
        assert session.query(TicketMedia).count() == 0
        logs = session.query(MessageLog).all()
        assert len(logs) == 1
        assert logs[0].body == "log tidak terkait"


def test_delete_one_ticket_removes_only_selected_ticket_media_and_logs(client, tmp_path):
    ticket_id = "tkt1120261"
    other_ticket_id = "tkt1120262"
    rel_path = f"media/{ticket_id}/foto.jpg"
    image = tmp_path / "media" / ticket_id / "foto.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"test image")

    with session_scope() as session:
        ticket = Ticket(
            ticket_id=ticket_id,
            title="Ticket salah",
            media_path=rel_path,
            media_filename="foto.jpg",
        )
        session.add(ticket)
        session.flush()
        session.add(
            TicketMedia(
                ticket_id=ticket.id,
                media_path=rel_path,
                media_filename="foto.jpg",
            )
        )
        session.add(Ticket(ticket_id=other_ticket_id, title="Ticket benar"))
        session.add(MessageLog(ticket_ref=ticket_id, body="#ticket salah"))
        session.add(MessageLog(ticket_ref=other_ticket_id, body="#ticket benar"))

    response = client.post(
        f"/tickets/{ticket_id}/delete",
        data={"confirmation": ticket_id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert f"ticket_deleted={ticket_id}" in response.headers["location"]
    assert image.exists() is False
    assert image.parent.exists() is False
    with session_scope() as session:
        tickets = session.query(Ticket).all()
        assert [ticket.ticket_id for ticket in tickets] == [other_ticket_id]
        assert session.query(TicketMedia).count() == 0
        logs = session.query(MessageLog).all()
        assert len(logs) == 1
        assert logs[0].ticket_ref == other_ticket_id


def test_delete_one_ticket_requires_confirmation(client):
    ticket_id = "tkt1120261"
    with session_scope() as session:
        session.add(Ticket(ticket_id=ticket_id, title="Ticket uji"))

    response = client.post(
        f"/tickets/{ticket_id}/delete",
        data={"confirmation": "tkt1120262"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "ticket_delete_error=confirmation" in response.headers["location"]
    with session_scope() as session:
        assert session.query(Ticket).count() == 1


def test_ticket_list_shows_individual_delete_action(client):
    with session_scope() as session:
        session.add(Ticket(ticket_id="tkt1120261", title="Ticket uji"))

    response = client.get("/tickets")

    assert response.status_code == 200
    assert 'action="/tickets/tkt1120261/delete"' in response.text
    assert "Hapus ticket tkt1120261" in response.text


def test_delete_group_preserves_tickets_and_logs(client):
    chat_id = "120363429142189206@g.us"
    with session_scope() as session:
        session.add(GroupConfig(chat_id=chat_id, chat_name="Testing"))
        session.add(Ticket(ticket_id="tkt1120261", title="Ticket uji", group_id=chat_id))
        session.add(MessageLog(chat_id=chat_id, body="#ticket Ticket uji"))

    response = client.post(f"/groups/{chat_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    with session_scope() as session:
        assert session.query(GroupConfig).count() == 0
        assert session.query(Ticket).count() == 1
        assert session.query(MessageLog).count() == 1


def test_group_update_can_disable_group_when_checkbox_is_unchecked(client):
    chat_id = "120363429142189206@g.us"
    with session_scope() as session:
        session.add(GroupConfig(chat_id=chat_id, chat_name="Testing", enabled=True))

    response = client.post(
        f"/groups/{chat_id}/update",
        data={"prefixes": "", "daily_summary": "on", "log_messages": "on"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with session_scope() as session:
        group = session.query(GroupConfig).filter_by(chat_id=chat_id).one()
        assert group.enabled is False
        assert group.daily_summary is True
        assert group.log_messages is True


# ---------------------------------------------------------------------------
# Halaman detail ticket: aksi harus merespon cepat (tidak menunggu Sheets/WAHA)
# ---------------------------------------------------------------------------
def test_ticket_status_post_redirects_immediately(client, monkeypatch):
    """Simpan status harus langsung redirect (sinkron/grup jalan di background)."""
    import time

    from app import handlers as handlers_module

    def broken_sync(*_args, **_kwargs):
        raise RuntimeError("Google Sheets tidak bisa dihubungi")

    monkeypatch.setattr(handlers_module, "sync_ticket_to_sheets", broken_sync)
    monkeypatch.setattr(handlers_module, "notify_ticket_change", lambda *a, **k: None)

    with session_scope() as session:
        session.add(Ticket(ticket_id="tkt1120261", title="Ticket uji", status="BARU"))

    started = time.time()
    response = client.post(
        "/tickets/tkt1120261/status",
        data={"status": "SELESAI", "note": "sudah diperbaiki"},
        follow_redirects=False,
    )
    elapsed = time.time() - started

    assert response.status_code == 303
    assert response.headers["location"] == "/tickets/tkt1120261?saved=status"
    # Kegagalan sinkron Sheets tidak boleh menggagalkan aksi dari dashboard.
    with session_scope() as session:
        ticket = session.query(Ticket).filter_by(ticket_id="tkt1120261").one()
        assert ticket.status == "SELESAI"
        assert ticket.resolution == "sudah diperbaiki"
        assert ticket.closed_at is not None


def test_ticket_pic_post_redirects_with_feedback(client):
    with session_scope() as session:
        session.add(Ticket(ticket_id="tkt1120261", title="Ticket uji", status="BARU"))

    response = client.post(
        "/tickets/tkt1120261/pic",
        data={"pic": "Budi Santoso"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/tickets/tkt1120261?saved=pic"
    with session_scope() as session:
        ticket = session.query(Ticket).filter_by(ticket_id="tkt1120261").one()
        assert ticket.pic == "Budi Santoso"


def test_ticket_detail_page_has_action_forms(client):
    with session_scope() as session:
        session.add(Ticket(ticket_id="tkt1120261", title="Ticket uji", status="BARU"))

    response = client.get("/tickets/tkt1120261")

    assert response.status_code == 200
    assert "js-submit" in response.text
    assert "/tickets/tkt1120261/status" in response.text
    assert "/tickets/tkt1120261/pic" in response.text
