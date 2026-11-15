"""Test koneksi & sinkron otomatis WAHA (deteksi, self-heal webhook, status)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.config as config  # noqa: E402
import app.db as db  # noqa: E402
import app.waha_link as waha_link  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.routers.webhook import router as webhook_router  # noqa: E402
from app.store import get_waha_session, settings_store  # noqa: E402


class FakeWaha:
    """Pengganti WahaClient untuk pengujian tanpa jaringan."""

    def __init__(self, sessions=None, hooks=None, session_name="engineering"):
        self.session = session_name
        self.sessions = list(sessions or [])
        self.config = {"webhooks": list(hooks or [])}
        self.created: list[str] = []
        self.started = False

    # -- session
    def session_status(self):
        for s in self.sessions:
            if s.get("name") == self.session:
                return s
        return {}

    def list_sessions(self):
        return self.sessions

    def request(self, method, path, json_body=None):
        if method == "POST" and path == "/api/sessions":
            name = (json_body or {}).get("name", self.session)
            self.created.append(name)
            self.sessions.append({"name": name, "status": "STOPPED", "config": {}})
            return {"name": name}
        if method == "PUT":
            self.config = json_body or {}
            return self.config
        raise AssertionError(f"request tak terduga: {method} {path}")

    def start_session(self):
        self.started = True
        for s in self.sessions:
            if s.get("name") == self.session:
                s["status"] = "WORKING"
        return {}

    # -- webhook
    def list_webhooks(self):
        return list(self.config.get("webhooks") or [])

    def configure_webhook(self, url, events=("message",), hmac_key=""):
        hooks = [h for h in self.list_webhooks() if h.get("url") != url]
        hook = {"url": url, "events": list(events)}
        if hmac_key:
            hook["hmac"] = {"key": hmac_key}
        hooks.append(hook)
        self.config["webhooks"] = hooks
        return self.config

    def remove_webhook(self, url=""):
        hooks = [
            h
            for h in self.list_webhooks()
            if not (url == "" or h.get("url") == url)
        ]
        self.config["webhooks"] = hooks
        return self.config


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOT_WEBHOOK_TOKEN", "")
    monkeypatch.setenv("BOT_WAHA_BASE_URL", "http://waha:3000")
    monkeypatch.setenv("BOT_PUBLIC_URL", "http://app:8080")
    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    init_db()
    settings_store.load()
    yield tmp_path


# ---------------------------------------------------------------------------
# Deteksi base URL
# ---------------------------------------------------------------------------
def test_detect_base_url_picks_reachable_candidate(env, monkeypatch):
    probed: list[str] = []

    def fake_probe(url: str, api_key: str):
        probed.append(url)
        reachable = url == "http://localhost:3000"
        return reachable, "ok" if reachable else "ditolak"

    monkeypatch.setattr(waha_link, "_probe", fake_probe)
    url, err = waha_link.detect_base_url("key")
    assert url == "http://localhost:3000"
    assert err == ""
    assert "http://waha:3000" in probed  # kandidat pertama dicoba


def test_detect_base_url_reports_all_failures(env, monkeypatch):
    monkeypatch.setattr(waha_link, "_probe", lambda url, key: (False, "tolak"))
    url, err = waha_link.detect_base_url("key")
    assert url == ""
    assert "tolak" in err


# ---------------------------------------------------------------------------
# Self-heal webhook
# ---------------------------------------------------------------------------
def test_sync_webhook_registers_when_missing(env, monkeypatch):
    fake = FakeWaha(hooks=[])
    monkeypatch.setattr(waha_link, "get_waha", lambda: fake)
    settings_store.set("waha.public_url", "http://app:8080")
    settings_store.set("waha.webhook_token", "")

    result = waha_link.sync_webhook()
    assert result["ok"] is True
    assert result["added"] is True
    assert fake.list_webhooks()[0]["url"].startswith("http://app:8080/webhook/waha")


def test_sync_webhook_removes_stale_host(env, monkeypatch):
    stale = {"url": "http://127.0.0.1:8080/webhook/waha", "events": ["message"]}
    fake = FakeWaha(hooks=[stale])
    monkeypatch.setattr(waha_link, "get_waha", lambda: fake)
    settings_store.set("waha.public_url", "http://app:8080")

    result = waha_link.sync_webhook()
    assert result["ok"] is True
    assert stale["url"] in result["removed"]
    assert all("127.0.0.1" not in h["url"] for h in fake.list_webhooks())


def test_sync_webhook_idempotent(env, monkeypatch):
    fake = FakeWaha(hooks=[])
    monkeypatch.setattr(waha_link, "get_waha", lambda: fake)
    settings_store.set("waha.public_url", "http://app:8080")

    waha_link.sync_webhook()
    result = waha_link.sync_webhook()
    assert result["added"] is False
    assert len(fake.list_webhooks()) == 1


# ---------------------------------------------------------------------------
# Auto connect
# ---------------------------------------------------------------------------
def test_auto_connect_creates_missing_session(env, monkeypatch):
    fake = FakeWaha(sessions=[])
    monkeypatch.setattr(waha_link, "get_waha", lambda: fake)
    monkeypatch.setattr(
        waha_link,
        "resolve_connection",
        lambda extra_url="": {"url": "http://waha:3000", "key": "k", "error": ""},
    )
    settings_store.set("waha.public_url", "http://app:8080")

    result = waha_link.auto_connect()
    assert result["ok"] is True
    assert "engineering" in fake.created
    assert fake.started is True
    assert fake.list_webhooks()


def test_auto_connect_repairs_stale_api_key(env, monkeypatch):
    """Key di dashboard basi -> otomatis diganti key yang diterima WAHA."""
    fake = FakeWaha(sessions=[{"name": "engineering", "status": "WORKING"}])
    monkeypatch.setattr(waha_link, "get_waha", lambda: fake)
    monkeypatch.setattr(
        waha_link,
        "resolve_connection",
        lambda extra_url="": {"url": "http://waha:3000", "key": "key-baru", "error": ""},
    )
    settings_store.set("waha.api_key", "key-lama")
    settings_store.set("waha.base_url", "http://waha:3000")

    result = waha_link.auto_connect()
    assert result["ok"] is True
    assert settings_store.waha_api_key() == "key-baru"
    assert any("API key" in s for s in result["steps"])


def test_auto_connect_reports_unreachable(env, monkeypatch):
    monkeypatch.setattr(
        waha_link,
        "resolve_connection",
        lambda extra_url="": {"url": "", "key": "", "error": "semua kandidat gagal"},
    )
    result = waha_link.auto_connect()
    assert result["ok"] is False
    assert "tidak dapat dihubungi" in result["error"]


# ---------------------------------------------------------------------------
# Status session tersimpan
# ---------------------------------------------------------------------------
def test_refresh_status_persists_last_known(env, monkeypatch):
    fake = FakeWaha(sessions=[{"name": "engineering", "status": "WORKING"}])
    monkeypatch.setattr(waha_link, "get_waha", lambda: fake)

    state = waha_link.refresh_status()
    assert state["status"] == "WORKING"
    with session_scope() as s:
        row = get_waha_session(s, "engineering")
        assert row is not None
        assert row.status == "WORKING"
        assert row.last_check is not None


def test_webhook_session_status_event_is_recorded(env, monkeypatch):
    fake = FakeWaha(sessions=[{"name": "engineering", "status": "WORKING"}])
    monkeypatch.setattr(waha_link, "get_waha", lambda: fake)

    app = FastAPI()
    app.include_router(webhook_router)
    with TestClient(app) as c:
        r = c.post(
            "/webhook/waha",
            json={
                "event": "session.status",
                "session": "engineering",
                "payload": {"status": "SCAN_QR_CODE"},
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "SCAN_QR_CODE"

    with session_scope() as s:
        row = get_waha_session(s, "engineering")
        assert row is not None
        assert row.status == "SCAN_QR_CODE"
