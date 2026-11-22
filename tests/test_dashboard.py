from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db, session_scope  # noqa: E402
from app.models import Ticket  # noqa: E402
from app.store import local_datetime, now_local, settings_store  # noqa: E402


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOT_DASHBOARD_PASSWORD", "")
    monkeypatch.setenv("BOT_TIMEZONE", "Asia/Makassar")
    import app.config as config
    import app.db as db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    init_db()
    settings_store.load()
    yield


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers.web import router

    application = FastAPI()
    application.include_router(router)
    with TestClient(application) as test_client:
        yield test_client


def _add_ticket(**values):
    defaults = {
        "ticket_id": "tkt1120261",
        "title": "",
        "detail": "AC kamar 203 rusak",
        "status": "BARU",
        "group_id": "12036@g.us",
    }
    defaults.update(values)
    with session_scope() as session:
        session.add(Ticket(**defaults))


def test_settings_command_and_status_checkboxes_stay_aligned():
    from starlette.datastructures import FormData

    from app.routers.web import _collect_commands, _collect_statuses

    command_form = FormData(
        [
            ("cmd_name", "disabled"),
            ("cmd_name", "enabled"),
            ("cmd_aliases", ""),
            ("cmd_aliases", ""),
            ("cmd_action", "list"),
            ("cmd_action", "info"),
            ("cmd_enabled_1", "on"),
        ]
    )
    commands = _collect_commands(command_form)

    status_form = FormData(
        [
            ("st_key", "SELESAI"),
            ("st_key", "BARU"),
            ("st_label", "Selesai"),
            ("st_label", "Baru"),
            ("st_transitions", ""),
            ("st_transitions", "SELESAI"),
            ("st_open_1", "on"),
            ("st_color", "#ddd"),
            ("st_color", "#eee"),
        ]
    )
    statuses = _collect_statuses(status_form)

    assert [command["enabled"] for command in commands] == [False, True]
    assert [status["open"] for status in statuses] == [False, True]


def test_settings_page_renders_all_configuration_tabs(client, monkeypatch):
    import app.routers.web as web

    monkeypatch.setenv("BOT_GOOGLE_SA_FILE", r"C:\config\service-account.json")
    monkeypatch.setenv("BOT_WAHA_API_KEY", "test-api-key")
    monkeypatch.setenv("BOT_WEBHOOK_TOKEN", "test-webhook-token")
    import app.config as config

    config.get_settings.cache_clear()

    class FakeWaha:
        def status_text(self):
            return "STOPPED"

    class FakeSheets:
        ready = False
        last_error = ""

    monkeypatch.setattr(web, "get_waha", lambda: FakeWaha())
    monkeypatch.setattr(
        web,
        "_waha_state",
        lambda: {
            "status": "STOPPED",
            "webhook_ok": False,
            "auto_sync": True,
            "checked_at": "-",
        },
    )
    monkeypatch.setattr(web, "get_sheets", lambda: FakeSheets())

    response = client.get("/settings")

    assert response.status_code == 200
    for tab in (
        "tab-whatsapp",
        "tab-sheets",
        "tab-jadwal",
        "tab-daftar-perintah",
        "tab-status",
    ):
        assert f'data-tab="{tab}"' in response.text
        assert f'id="{tab}"' in response.text
    assert r'value="C:\config\service-account.json"' in response.text
    assert "Sudah diatur dari environment" in response.text
    assert "test-api-key" not in response.text
    assert 'name="waha_webhook_token" id="waha_webhook_token" value="test-webhook-token"' not in response.text


def test_tickets_page_shows_daily_reminder_controls(client):
    _add_ticket()

    response = client.get("/tickets")

    assert response.status_code == 200
    assert "Jadwal / Reminder" in response.text
    assert 'action="/tickets/tkt1120261/reminder"' in response.text
    assert 'type="datetime-local"' in response.text
    assert "Asia/Makassar" in response.text


def test_dashboard_can_schedule_and_cancel_daily_ticket_reminder(client):
    _add_ticket()
    due_local = (now_local() + timedelta(days=2)).replace(second=0, microsecond=0)

    response = client.post(
        "/tickets/tkt1120261/reminder",
        data={
            "scheduled_at": due_local.strftime("%Y-%m-%dT%H:%M"),
            "return_status": "BARU",
            "return_q": "AC",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "status=BARU" in response.headers["location"]
    assert "q=AC" in response.headers["location"]
    assert "reminder_updated=tkt1120261" in response.headers["location"]
    with session_scope() as session:
        ticket = session.query(Ticket).one()
        assert local_datetime(ticket.reminder_at) == due_local
        assert ticket.reminder_retry_at is None

    response = client.post(
        "/tickets/tkt1120261/reminder",
        data={"cancel": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "reminder_cancelled=tkt1120261" in response.headers["location"]
    with session_scope() as session:
        ticket = session.query(Ticket).one()
        assert ticket.reminder_at is None
        assert ticket.reminder_retry_at is None


def test_dashboard_rejects_reminder_for_closed_ticket(client):
    _add_ticket(status="SELESAI")

    response = client.post(
        "/tickets/tkt1120261/reminder",
        data={"scheduled_at": (now_local() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "reminder_error=closed" in response.headers["location"]
    with session_scope() as session:
        assert session.query(Ticket).one().reminder_at is None


def test_daily_summary_can_be_scheduled_at_midnight(client):
    response = client.post(
        "/settings",
        data={"bot_daily_summary_hour": "0", "bot_daily_summary_minute": "0"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert settings_store.get("bot.daily_summary_hour") == 0
