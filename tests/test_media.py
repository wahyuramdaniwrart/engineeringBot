"""Test fitur foto: lampiran media pada ticket + perbaikan ejaan status."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from itertools import count
from pathlib import Path
from time import sleep
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULT_STATUSES  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.handlers import (  # noqa: E402
    extract_media,
    extract_media_items,
    handle_inbound,
    parse_waha_payload,
)
from app.store import settings_store  # noqa: E402

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
_TEXT_MESSAGE_IDS = count(1)


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
    settings_store.set("bot.reply_in_group", True)


class FakeWaha:
    """Pengganti WahaClient: mengembalikan isi media dummy."""

    def __init__(self, data: bytes = PNG, content_type: str = "image/png") -> None:
        self.data = data
        self.content_type = content_type
        self.calls: list[str] = []

    def download_media(self, url: str, max_bytes: int = 0):
        self.calls.append(url)
        return self.data, self.content_type


def _photo_payload(body: str = "", chat_id: str = "12036@g.us") -> dict:
    return {
        "id": "msg-photo-1",
        "chatId": chat_id,
        "chatName": "Unit Test",
        "from": "628123456789",
        "participant": "628123456789",
        "pushName": "Front Office",
        "body": body,
        "type": "image",
        "hasMedia": True,
        "media": {
            "url": (
                "http://localhost:3000/api/files/engineering/"
                "false_120363@g.us_A5D12C9DB72BD2C617B8F0004E641E3B.jpeg"
            ),
            "filename": None,
            "mimetype": "image/jpeg",
        },
        "fromMe": False,
    }


def _text_payload(body: str, chat_id: str = "12036@g.us") -> dict:
    return {
        "id": f"msg-text-{next(_TEXT_MESSAGE_IDS)}",
        "chatId": chat_id,
        "chatName": "Unit Test",
        "from": "628123456789",
        "participant": "628123456789",
        "pushName": "Front Office",
        "body": body,
        "type": "text",
        "fromMe": False,
    }


# ---------------------------------------------------------------------------
# Pemeriksaan ejaan status
# ---------------------------------------------------------------------------
def test_status_key_uses_correct_spelling():
    keys = [s["key"] for s in DEFAULT_STATUSES]
    assert "DIKERJAKAN" in keys
    assert "DIBERJAKAN" not in keys


def test_legacy_status_spelling_still_accepted():
    """Perintah lama dengan ejaan 'DIBERJAKAN' tetap bisa dipakai."""
    msg = parse_waha_payload(_text_payload("#ticket Ganti filter lobby"))
    with session_scope() as s:
        handle_inbound(s, msg)
    with session_scope() as s:
        from app.store import list_tickets

        tid = list_tickets(s, limit=1)[0].ticket_id

    msg2 = parse_waha_payload(_text_payload(f"#status {tid} DIBERJAKAN"))
    with session_scope() as s:
        reply = handle_inbound(s, msg2)
    assert "DIKERJAKAN" in reply
    assert "DIBERJAKAN" not in reply


# ---------------------------------------------------------------------------
# Deteksi media di payload
# ---------------------------------------------------------------------------
def test_extract_media_from_object_form():
    info = extract_media(
        {"media": {"url": "http://x/y.jpg", "mimetype": "image/jpeg", "filename": "a.jpg"}}
    )
    assert info["url"] == "http://x/y.jpg"
    assert info["mimetype"] == "image/jpeg"
    assert info["filename"] == "a.jpg"


def test_extract_media_from_flat_form():
    info = extract_media({"mediaUrl": "http://x/y.jpg", "mimetype": "image/png"})
    assert info["url"] == "http://x/y.jpg"
    assert info["mimetype"] == "image/png"


def test_extract_media_empty():
    info = extract_media({"body": "teks biasa"})
    assert info["url"] == ""


def test_extract_media_list_from_payload():
    info = extract_media_items(
        {
            "media": [
                {"url": "http://x/1.jpg", "mimetype": "image/jpeg"},
                {"url": "http://x/2.jpg", "mimetype": "image/jpeg"},
            ]
        }
    )
    assert [item["url"] for item in info] == ["http://x/1.jpg", "http://x/2.jpg"]


# ---------------------------------------------------------------------------
# Alur lampiran foto
# ---------------------------------------------------------------------------
def test_photo_with_command_creates_ticket_with_photo(monkeypatch, tmp_path):
    fake = FakeWaha()
    monkeypatch.setattr("app.handlers.get_waha", lambda: fake)

    msg = parse_waha_payload(_photo_payload("#ticket AC kamar 203 tidak dingin"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)

    assert reply.startswith("tkt")
    assert fake.calls == [
        "http://localhost:3000/api/files/engineering/"
        "false_120363@g.us_A5D12C9DB72BD2C617B8F0004E641E3B.jpeg"
    ]

    with session_scope() as s:
        from app.store import list_tickets

        ticket = list_tickets(s, limit=1)[0]
        assert ticket.media_filename.endswith(".jpg")
        assert ticket.media_path.startswith("media/")
        assert ticket.media_size == len(PNG)
        assert ticket.media_mimetype

    # Berkas benar-benar tersimpan di folder data
    files = list((tmp_path / "media").rglob("*.jpg"))
    assert len(files) == 1
    assert files[0].read_bytes() == PNG


def test_replayed_photo_command_creates_only_one_ticket_and_download(monkeypatch):
    fake = FakeWaha()
    monkeypatch.setattr("app.handlers.get_waha", lambda: fake)
    msg = parse_waha_payload(_photo_payload("#ticket AC kamar 203 tidak dingin"))

    with session_scope() as s:
        first_reply = handle_inbound(s, msg)
    with session_scope() as s:
        second_reply = handle_inbound(s, msg)

    assert first_reply.startswith("tkt")
    assert second_reply == ""
    assert len(fake.calls) == 1
    with session_scope() as s:
        from app.store import list_tickets

        tickets = list_tickets(s)
        assert len(tickets) == 1
        assert len(tickets[0].media_attachments) == 1


def test_concurrent_replayed_photo_command_creates_only_one_ticket(monkeypatch):
    class SlowWaha(FakeWaha):
        def download_media(self, url: str, max_bytes: int = 0):
            sleep(0.1)
            return super().download_media(url, max_bytes)

    fake = SlowWaha()
    monkeypatch.setattr("app.handlers.get_waha", lambda: fake)
    msg = parse_waha_payload(_photo_payload("#ticket AC kamar 203 tidak dingin"))

    def process_message(_):
        with session_scope() as s:
            return handle_inbound(s, msg)

    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(process_message, range(2)))

    assert sum(reply.startswith("tkt") for reply in replies) == 1
    assert sum(not reply for reply in replies) == 1
    assert len(fake.calls) == 1
    with session_scope() as s:
        from app.store import list_tickets

        assert len(list_tickets(s)) == 1


def test_multiple_photos_are_saved_and_shown_in_ticket(monkeypatch):
    _no_password(monkeypatch)
    fake = FakeWaha()
    monkeypatch.setattr("app.handlers.get_waha", lambda: fake)
    payload = _photo_payload("#ticket AC kamar 203 tidak dingin")
    first = payload["media"]
    payload["media"] = [
        first,
        {
            **first,
            "url": "http://localhost:3000/api/files/engineering/foto-kedua.jpeg",
        },
    ]

    msg = parse_waha_payload(payload)
    with session_scope() as s:
        reply = handle_inbound(s, msg)

    assert reply.startswith("tkt")
    assert len(fake.calls) == 2
    with session_scope() as s:
        from app.store import list_tickets

        ticket = list_tickets(s, limit=1)[0]
        ticket_id = ticket.ticket_id
        assert len(ticket.media_attachments) == 2
        assert len({photo.media_path for photo in ticket.media_attachments}) == 2

    with _web_client() as client:
        response = client.get(f"/tickets/{ticket_id}")
    assert response.status_code == 200
    assert response.text.count(f'src="/media/{ticket_id}/') == 2


def test_followup_album_photos_attach_to_recent_ticket(monkeypatch):
    fake = FakeWaha()
    monkeypatch.setattr("app.handlers.get_waha", lambda: fake)
    ticket_message = parse_waha_payload(_text_payload("#ticket Bocor di kamar 12"))
    with session_scope() as s:
        handle_inbound(s, ticket_message)

    photo_message = parse_waha_payload(_photo_payload(""))
    with session_scope() as s:
        reply = handle_inbound(s, photo_message)

    assert reply == ""
    with session_scope() as s:
        from app.store import list_tickets

        tickets = list_tickets(s)
        assert len(tickets) == 1
        assert len(tickets[0].media_attachments) == 1


def test_followup_photo_is_not_attached_to_another_sender(monkeypatch):
    monkeypatch.setattr("app.handlers.get_waha", lambda: FakeWaha())
    ticket_message = parse_waha_payload(_text_payload("#ticket Bocor di kamar 12"))
    with session_scope() as s:
        handle_inbound(s, ticket_message)

    payload = _photo_payload("")
    payload["participant"] = "628999999999"
    photo_message = parse_waha_payload(payload)
    with session_scope() as s:
        reply = handle_inbound(s, photo_message)

    assert reply == ""
    with session_scope() as s:
        from app.store import list_tickets

        assert list_tickets(s, limit=1)[0].media_attachments == []


def test_followup_photo_outside_window_is_not_attached(monkeypatch):
    monkeypatch.setattr("app.handlers.get_waha", lambda: FakeWaha())
    ticket_message = parse_waha_payload(_text_payload("#ticket Bocor di kamar 12"))
    with session_scope() as s:
        handle_inbound(s, ticket_message)
    with session_scope() as s:
        from app.models import Ticket

        ticket = s.query(Ticket).one()
        ticket.created_at = datetime.now(timezone.utc) - timedelta(seconds=31)

    photo_message = parse_waha_payload(_photo_payload(""))
    with session_scope() as s:
        reply = handle_inbound(s, photo_message)

    assert reply == ""
    with session_scope() as s:
        from app.store import list_tickets

        assert list_tickets(s, limit=1)[0].media_attachments == []


def test_unrelated_photo_is_ignored(monkeypatch):
    monkeypatch.setattr("app.handlers.get_waha", lambda: FakeWaha())
    msg = parse_waha_payload(_photo_payload(""))
    with session_scope() as s:
        reply = handle_inbound(s, msg)
    assert reply == ""


def test_photo_attached_to_existing_ticket(monkeypatch):
    fake = FakeWaha()
    monkeypatch.setattr("app.handlers.get_waha", lambda: fake)

    msg = parse_waha_payload(_text_payload("#ticket Bocor di kamar 12"))
    with session_scope() as s:
        handle_inbound(s, msg)
    with session_scope() as s:
        from app.store import list_tickets

        tid = list_tickets(s, limit=1)[0].ticket_id

    msg2 = parse_waha_payload(_photo_payload(f"#foto {tid}"))
    with session_scope() as s:
        reply = handle_inbound(s, msg2)
    assert reply.startswith("Foto tersimpan")

    with session_scope() as s:
        from app.store import get_ticket

        ticket = get_ticket(s, tid)
        assert ticket.media_filename
        assert ticket.media_path


def test_foto_command_without_photo(monkeypatch):
    monkeypatch.setattr("app.handlers.get_waha", lambda: FakeWaha())
    msg = parse_waha_payload(_text_payload("#ticket Bocor di kamar 12"))
    with session_scope() as s:
        handle_inbound(s, msg)
    with session_scope() as s:
        from app.store import list_tickets

        tid = list_tickets(s, limit=1)[0].ticket_id

    msg2 = parse_waha_payload(_text_payload(f"#foto {tid}"))
    with session_scope() as s:
        reply = handle_inbound(s, msg2)
    assert "Tidak ada foto" in reply


def test_media_download_failure_is_reported(monkeypatch):
    class Broken:
        def download_media(self, url: str, max_bytes: int = 0):
            raise RuntimeError("koneksi media Putus")

    monkeypatch.setattr("app.handlers.get_waha", lambda: Broken())
    msg = parse_waha_payload(_photo_payload("#ticket AC rusak"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)
    assert "gagal disimpan" in reply.lower()


# ---------------------------------------------------------------------------
# Penyerahan media: host lokal harus ditulis ulang ke host WAHA
# ---------------------------------------------------------------------------
def test_localhost_media_url_is_rewritten_to_waha_host():
    """WAHA memberi URL 'localhost'; dari container app itu tidak valid."""
    from app.waha import WahaClient

    client = WahaClient(base_url="http://waha:3000", api_key="k", session="engineering")
    resolved = client._resolve_media_url(
        "http://localhost:3000/api/files/engineering/abc.jpeg"
    )
    assert resolved == "http://waha:3000/api/files/engineering/abc.jpeg"


def test_external_media_url_is_not_rewritten():
    """URL CDN luar tidak boleh ikut diganti (dan API key tidak dikirim)."""
    from app.waha import WahaClient

    client = WahaClient(base_url="http://waha:3000", api_key="k", session="engineering")
    resolved = client._resolve_media_url("https://mmg.whatsapp.net/v/t62.7118-24/x.enc")
    assert resolved.startswith("https://mmg.whatsapp.net/")


def test_send_image_uses_global_waha_endpoint_and_file_metadata(monkeypatch):
    from app.waha import WahaClient

    client = WahaClient(base_url="http://waha:3000", api_key="k", session="engineering")
    calls = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        return {"id": "image-message"}

    monkeypatch.setattr(client, "request", fake_request)
    result = client.send_image(
        "12036@g.us",
        "http://app:8080/media/foto.jpg?t=signature",
        caption="Foto ticket",
        mimetype="image/jpeg",
        filename="foto.jpg",
    )

    assert result == {"id": "image-message"}
    assert calls == [
        (
            "POST",
            "/api/sendImage",
            {
                "session": "engineering",
                "chatId": "12036@g.us",
                "file": {
                    "url": "http://app:8080/media/foto.jpg?t=signature",
                    "mimetype": "image/jpeg",
                    "filename": "foto.jpg",
                },
                "caption": "Foto ticket",
            },
        )
    ]


def test_send_file_uses_global_waha_endpoint(monkeypatch):
    from app.waha import WahaClient

    client = WahaClient(base_url="http://waha:3000", api_key="k", session="engineering")
    calls = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        return {"id": "file-message"}

    monkeypatch.setattr(client, "request", fake_request)
    client.send_file(
        "12036@g.us",
        "http://app:8080/media/manual.pdf?t=signature",
        mimetype="application/pdf",
        filename="manual.pdf",
    )

    assert calls == [
        (
            "POST",
            "/api/sendFile",
            {
                "session": "engineering",
                "chatId": "12036@g.us",
                "file": {
                    "url": "http://app:8080/media/manual.pdf?t=signature",
                    "mimetype": "application/pdf",
                    "filename": "manual.pdf",
                },
            },
        )
    ]


def test_shared_media_path_finds_file_in_volume(tmp_path):
    """Fallback: berkas WAHA di volume bersama bisa dibaca langsung."""
    from app.media import shared_media_path

    folder = tmp_path / "waha" / "engineering"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "abc.jpeg").write_bytes(PNG)

    found = shared_media_path(
        "http://localhost:3000/api/files/engineering/abc.jpeg"
    )
    assert found is not None
    assert found.read_bytes() == PNG


def test_shared_media_path_missing_file(tmp_path):
    from app.media import shared_media_path

    assert (
        shared_media_path("http://localhost:3000/api/files/engineering/zzz.jpeg")
        is None
    )


def test_shared_media_path_rejects_other_urls():
    from app.media import shared_media_path

    assert shared_media_path("https://mmg.whatsapp.net/v/x.enc") is None
    assert shared_media_path("http://localhost:3000/api/files/abc.jpeg") is None


def test_media_saved_when_http_fails_but_volume_has_file(tmp_path, monkeypatch):
    """Kalau HTTP gagal, berkas dari volume bersama tetap dipakai."""
    name = "false_120363@g.us_A5D12C9DB72BD2C617B8F0004E641E3B.jpeg"
    folder = tmp_path / "waha" / "engineering"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(PNG)

    class Broken:
        def download_media(self, url: str, max_bytes: int = 0):
            raise RuntimeError("connection refused")

    monkeypatch.setattr("app.handlers.get_waha", lambda: Broken())
    msg = parse_waha_payload(_photo_payload("#ticket AC rusak"))
    with session_scope() as s:
        reply = handle_inbound(s, msg)

    assert reply.startswith("tkt")
    with session_scope() as s:
        from app.store import list_tickets

        ticket = list_tickets(s, limit=1)[0]
        assert ticket.media_size == len(PNG)
        assert ticket.media_path.startswith("media/")


# ---------------------------------------------------------------------------
# Tautan bertoken (untuk Google Sheets)
# ---------------------------------------------------------------------------
def test_token_roundtrip():
    from app.media import sign_path, valid_token

    rel = "media/TKT-2026-0001/foto.jpg"
    token = sign_path(rel)
    assert valid_token(rel, token) is True
    assert valid_token(rel, "token-palsu") is False
    assert valid_token("media/TKT-2026-0002/foto.jpg", token) is False


def test_build_url_contains_base_and_token(monkeypatch):
    from app import media

    settings_store.set("bot.public_url", "http://192.168.1.10:8080")
    url = media.build_url("media/TKT-2026-0001/foto.jpg")
    assert url.startswith("http://192.168.1.10:8080/media/TKT-2026-0001/foto.jpg")
    assert "?t=" in url


def test_media_route_open_with_token_without_login(tmp_path, monkeypatch):
    """Tautan dari Sheets harus bisa dibuka tanpa login dashboard."""
    from app.media import sign_path

    monkeypatch.setenv("BOT_DASHBOARD_PASSWORD", "rahasia")
    import app.config as config

    config.get_settings.cache_clear()
    name = "TKT-2026-0001/foto.jpg"
    target = tmp_path / "media" / "TKT-2026-0001" / "foto.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PNG)

    rel = f"media/{name}"
    token = sign_path(rel)
    with _web_client() as client:
        resp = client.get(f"/media/{name}?t={token}", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.content == PNG


def test_media_route_without_token_needs_login(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DASHBOARD_PASSWORD", "rahasia")
    import app.config as config

    config.get_settings.cache_clear()
    target = tmp_path / "media" / "TKT-2026-0001" / "foto.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PNG)

    with _web_client() as client:
        resp = client.get("/media/TKT-2026-0001/foto.jpg", follow_redirects=False)
    assert resp.status_code in (302, 401)


def test_sheets_row_uses_only_requested_ticket_fields():
    from app.sheets import TICKET_HEADERS, _ticket_row

    settings_store.set("bot.timezone", "Asia/Makassar")
    ticket = SimpleNamespace(
        ticket_id="tkt1120261",
        title="Uji",
        detail="AC tidak dingin",
        ticket_type="TASK",
        status="BARU",
        priority="NORMAL",
        pic="",
        reporter="",
        reporter_phone="",
        group_name="Grup",
        group_id="g@g.us",
        created_at=datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc),
        updated_at=None,
        closed_at=None,
        resolution="",
    )
    row = _ticket_row(ticket)
    assert TICKET_HEADERS == [
        "ID",
        "Tanggal",
        "Issue",
        "Status",
        "PIC",
        "Pelapor",
        "Nomor HP",
        "Grup",
        "Selesai",
        "Keterangan",
        "Foto",
        "Link Foto",
    ]
    assert len(row) == len(TICKET_HEADERS)
    assert row[:4] == [
        "tkt1120261",
        "2026-11-15 20:00:00",
        "AC tidak dingin",
        "BARU",
    ]
    assert "Uji" not in row


# ---------------------------------------------------------------------------
# Foto langsung di dalam sel spreadsheet (formula IMAGE)
# ---------------------------------------------------------------------------
def _ticket_with_photo(media_path: str = "media/TKT-2026-0001/foto.jpg"):
    return SimpleNamespace(
        media_path=media_path,
        media_filename="foto.jpg",
        media_url="",
    )


def test_image_formula_uses_public_url():
    """Dengan URL yang bisa dijangkau, sel Foto berisi formula IMAGE()."""
    from app.media import image_formula

    settings_store.set("bot.public_url", "https://bot.perusahaan.co.id")
    formula = image_formula(_ticket_with_photo())
    assert formula.startswith('=IMAGE("https://bot.perusahaan.co.id/media/')
    # satu argumen saja: pemisah argumen di locale in_ID adalah ';'
    assert formula.endswith('")')
    # token harus ikut supaya foto tidak jadi publik
    assert "?t=" in formula


def test_image_formula_empty_for_lan_ip():
    """IP LAN tidak bisa dipakai: server Google yang menarik gambarnya."""
    from app.media import image_formula

    settings_store.set("bot.public_url", "http://192.168.54.90:8080")
    assert image_formula(_ticket_with_photo()) == ""

    settings_store.set("bot.public_url", "http://10.0.0.5:8080")
    assert image_formula(_ticket_with_photo()) == ""


def test_image_formula_empty_for_internal_url():
    """Nama host container tidak bisa dibuka pembaca -> jangan tulis formula."""
    from app.media import image_formula

    settings_store.set("bot.public_url", "http://app:8080")
    assert image_formula(_ticket_with_photo()) == ""

    settings_store.set("bot.public_url", "http://localhost:8080")
    assert image_formula(_ticket_with_photo()) == ""


def test_image_formula_empty_without_photo():
    from app.media import image_formula

    settings_store.set("bot.public_url", "https://bot.perusahaan.co.id")
    assert image_formula(_ticket_with_photo(media_path="")) == ""


def test_is_public_url_detects_internal_addresses():
    """Hanya alamat publik yang bisa dipakai untuk IMAGE() di Sheets."""
    from app.media import is_public_url

    assert is_public_url("https://bot.perusahaan.co.id") is True
    assert is_public_url("http://bot.perusahaan.co.id") is True
    assert is_public_url("http://app:8080") is False
    assert is_public_url("http://localhost:8080") is False
    assert is_public_url("http://waha:3000") is False
    assert is_public_url("http://host.docker.internal:8080") is False
    assert is_public_url("http://192.168.54.90:8080") is False
    assert is_public_url("http://10.0.0.5:8080") is False
    assert is_public_url("http://172.20.0.3:8080") is False
    assert is_public_url("http://172.32.0.3:8080") is True  # di luar rentang privat
    assert is_public_url("") is False
    assert is_public_url("ftp://contoh.com") is False


# ---------------------------------------------------------------------------
# Penyajian media lewat dashboard
# ---------------------------------------------------------------------------
def _web_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers.web import router as web_router

    application = FastAPI()
    application.include_router(web_router)
    return TestClient(application)


def _no_password(monkeypatch) -> None:
    """Matikan password dashboard & segarkan cache settings."""
    monkeypatch.setenv("BOT_DASHBOARD_PASSWORD", "")
    import app.config as config

    config.get_settings.cache_clear()


def test_media_route_serves_file(tmp_path, monkeypatch):
    _no_password(monkeypatch)
    target = tmp_path / "media" / "TKT-2026-0001" / "foto.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PNG)

    with _web_client() as client:
        resp = client.get("/media/TKT-2026-0001/foto.jpg")
    assert resp.status_code == 200
    assert resp.content == PNG


def test_media_route_missing_file(tmp_path, monkeypatch):
    _no_password(monkeypatch)
    with _web_client() as client:
        resp = client.get("/media/TKT-2026-0001/tidak-ada.jpg")
    assert resp.status_code == 404


def test_media_route_requires_login(tmp_path, monkeypatch):
    """Tanpa login, media tidak boleh terbuka."""
    monkeypatch.setenv("BOT_DASHBOARD_PASSWORD", "rahasia")
    import app.config as config

    config.get_settings.cache_clear()
    target = tmp_path / "media" / "TKT-2026-0001" / "foto.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PNG)

    with _web_client() as client:
        resp = client.get("/media/TKT-2026-0001/foto.jpg", follow_redirects=False)
    assert resp.status_code in (302, 401)


def test_media_route_blocks_path_traversal(tmp_path, monkeypatch):
    _no_password(monkeypatch)
    secret = tmp_path / "rahasia.txt"
    secret.write_text("jangan bocorkan")
    with _web_client() as client:
        resp = client.get("/media/../rahasia.txt", follow_redirects=False)
    assert resp.status_code in (400, 404)
    assert "jangan bocorkan" not in resp.text


# ---------------------------------------------------------------------------
# Render halaman dashboard
# ---------------------------------------------------------------------------
def test_ticket_list_page_renders_photo_thumbnail(tmp_path, monkeypatch):
    """Halaman daftar ticket harus merender thumbnail foto (tanpa error)."""
    _no_password(monkeypatch)
    settings_store.set("bot.public_url", "http://app:8080")
    from app.media import sign_path

    msg = parse_waha_payload(_text_payload("#ticket Uji thumbnail"))
    with session_scope() as s:
        handle_inbound(s, msg)

    # Pasang foto untuk ticket tadi
    with session_scope() as s:
        from app.store import list_tickets

        ticket = list_tickets(s, limit=1)[0]
        ticket.media_filename = "foto.jpg"
        ticket.media_path = f"media/{ticket.ticket_id}/foto.jpg"
        ticket.media_size = len(PNG)
        media_file = tmp_path / "media" / ticket.ticket_id / "foto.jpg"
        media_file.parent.mkdir(parents=True, exist_ok=True)
        media_file.write_bytes(PNG)
        ticket_id = ticket.ticket_id
        s.flush()

    with _web_client() as client:
        resp = client.get("/tickets")
        image_url = (
            f"/media/{ticket_id}/foto.jpg?"
            f"t={sign_path(ticket.media_path)}"
        )
        image_resp = client.get(image_url)
    assert resp.status_code == 200
    assert f'src="{image_url}"' in resp.text
    assert "http://app:8080/media/" not in resp.text
    assert image_resp.status_code == 200
    assert image_resp.content == PNG


def test_ticket_detail_page_renders_photo(tmp_path, monkeypatch):
    _no_password(monkeypatch)
    settings_store.set("bot.public_url", "http://app:8080")

    msg = parse_waha_payload(_text_payload("#ticket Uji detail"))
    with session_scope() as s:
        handle_inbound(s, msg)
    with session_scope() as s:
        from app.store import list_tickets

        ticket = list_tickets(s, limit=1)[0]
        ticket.media_filename = "foto.jpg"
        ticket.media_path = f"media/{ticket.ticket_id}/foto.jpg"
        ticket.media_size = len(PNG)
        tid = ticket.ticket_id
        s.flush()

    with _web_client() as client:
        resp = client.get(f"/tickets/{tid}")
    assert resp.status_code == 200
    assert "<img" in resp.text
    assert "download=1" in resp.text
    assert f'src="/media/{tid}/foto.jpg?t=' in resp.text
