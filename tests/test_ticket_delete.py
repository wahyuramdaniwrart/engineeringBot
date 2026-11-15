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

