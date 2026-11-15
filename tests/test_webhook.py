"""Test autentikasi webhook WAHA (token di URL & HMAC signature)."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.routers.webhook import router as webhook_router  # noqa: E402
from app.store import settings_store  # noqa: E402

BODY = {
    "event": "message",
    "session": "engineering",
    "payload": {
        "id": "m1",
        "chatId": "12036@g.us",
        "participant": "62811",
        "body": "#ticket uji webhook",
        "type": "text",
        "fromMe": False,
    },
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    # kosongkan token dari .env supaya test bisa mengatur sendiri
    monkeypatch.setenv("BOT_WEBHOOK_TOKEN", "")
    import app.config as config
    import app.db as db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    init_db()
    settings_store.load()

    app = FastAPI()
    app.include_router(webhook_router)
    with TestClient(app) as c:
        yield c


def test_no_token_configured_accepts(client):
    r = client.post("/webhook/waha", json=BODY)
    assert r.status_code == 200


def test_token_in_url_accepted(client):
    settings_store.set("waha.webhook_token", "secret123")
    r = client.post("/webhook/waha?token=secret123", json=BODY)
    assert r.status_code == 200
    assert r.json()["handled"] is True


def test_wrong_token_rejected(client):
    settings_store.set("waha.webhook_token", "secret123")
    r = client.post("/webhook/waha?token=salah", json=BODY)
    assert r.status_code == 401


def test_missing_token_rejected(client):
    settings_store.set("waha.webhook_token", "secret123")
    r = client.post("/webhook/waha", json=BODY)
    assert r.status_code == 401


def test_hmac_signature_accepted(client):
    key = "hmac-secret"
    settings_store.set("waha.hmac_key", key)
    raw = json.dumps(BODY).encode()
    sig = hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
    r = client.post(
        "/webhook/waha", content=raw, headers={"Content-Type": "application/json", "X-Signature": sig}
    )
    assert r.status_code == 200


def test_hmac_bad_signature_rejected(client):
    settings_store.set("waha.hmac_key", "hmac-secret")
    raw = json.dumps(BODY).encode()
    r = client.post(
        "/webhook/waha", content=raw, headers={"Content-Type": "application/json", "X-Signature": "deadbeef"}
    )
    assert r.status_code == 401
