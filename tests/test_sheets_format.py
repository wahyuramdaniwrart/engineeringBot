from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.sheets import TICKET_HEADERS, SheetsClient  # noqa: E402
from app.store import settings_store  # noqa: E402


@pytest.fixture(autouse=True)
def setup_local_db(tmp_path, monkeypatch):
    import app.config as config
    import app.db as db

    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    init_db()
    settings_store.load()
    settings_store.set("bot.timezone", "Asia/Makassar")


class FakeWorksheet:
    def __init__(
        self,
        title: str,
        headers: list[str] | None = None,
        rows: list[list[str]] | None = None,
        column_data: list[str] | None = None,
    ) -> None:
        self.title = title
        self.rows = rows if rows is not None else ([headers] if headers else [])
        self.column_data = column_data
        self.col_count = max(10, max((len(row) for row in self.rows), default=0))
        self.row_count = 100
        self.updates: list[tuple[str, list[list[str]]]] = []
        self.appended: list[list[str]] = []
        self.append_ranges: list[str | None] = []

    def row_values(self, row: int) -> list[str]:
        return self.rows[row - 1] if row <= len(self.rows) else []

    def update(self, values=None, range_name=None, **_kwargs):
        self.updates.append((range_name, values))
        return {}

    def get_values(self, _range_name: str) -> list[list[str]]:
        return self.rows[:3]

    def resize(self, rows: int, cols: int) -> None:
        self.row_count = rows
        self.col_count = cols

    def append_row(self, values: list[str], table_range: str | None = None):
        self.appended.append(values)
        self.append_ranges.append(table_range)
        row = max(2, len(self.rows) + 1)
        return {"updatedRange": f"{self.title}!A{row}:J{row}"}

    def col_values(self, _column: int) -> list[str]:
        return self.column_data if self.column_data is not None else ["ID", "tkt1120261"]


class FakeSpreadsheet:
    def __init__(self, worksheet: FakeWorksheet) -> None:
        self._worksheet = worksheet

    def worksheet(self, _title: str) -> FakeWorksheet:
        return self._worksheet


def _client(worksheet: FakeWorksheet) -> SheetsClient:
    client = SheetsClient()
    client._sheet = FakeSpreadsheet(worksheet)
    client._ready = True
    return client


def _ticket(sheet_row: int | None = None):
    return SimpleNamespace(
        ticket_id="tkt1120261",
        detail="AC tidak dingin",
        status="DIKERJAKAN",
        pic="Budi",
        reporter="Ayu",
        reporter_phone="628123",
        group_name="Engineering",
        group_id="120363@g.us",
        created_at=datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc),
        closed_at=None,
        resolution="",
        sheet_row=sheet_row,
    )


def test_existing_headers_are_reused_and_missing_fields_added():
    ws = FakeWorksheet(
        "TICKETS",
        [
            "Ticket ID",
            "Judul",
            "Detail",
            "Status",
            "PIC",
            "Pelapor",
            "No HP",
            "Grup",
            "Ditutup",
            "Resolusi",
        ],
    )
    client = _client(ws)

    assert client.update_ticket(_ticket(sheet_row=2), "TICKETS") is True

    header_updates = [call for call in ws.updates if call[0].endswith("1")]
    assert ("A1", [["ID"]]) in header_updates
    assert ("C1", [["Issue"]]) in header_updates
    assert ("K1", [["Tanggal"]]) in header_updates
    row_ranges = {call[0] for call in ws.updates if call[0].endswith("2")}
    assert row_ranges == {"A2:A2", "C2:K2"}


def test_new_ticket_is_appended_with_only_required_headers_and_values():
    ws = FakeWorksheet("TICKETS")
    client = _client(ws)

    assert client.append_ticket(_ticket(), "TICKETS") == 2

    assert ws.appended == [[
        "tkt1120261",
        "2026-11-15 20:00:00",
        "AC tidak dingin",
        "DIKERJAKAN",
        "Budi",
        "Ayu",
        "628123",
        "Engineering",
        "",
        "",
    ]]
    header_cells = {call[1][0][0] for call in ws.updates if call[0].endswith("1")}
    assert header_cells == set(TICKET_HEADERS)


def test_header_after_summary_row_is_preserved_and_used():
    ws = FakeWorksheet(
        "TICKETS",
        rows=[
            ["", "AREA PROJECT", "", "", "", "", "", "", "BARU", "PROGRESS", "DONE", "TOTAL"],
            ["ID", "Tanggal", "Issue", "Status", "Keterangan"],
        ],
    )
    client = _client(ws)

    assert client.append_ticket(_ticket(), "TICKETS") == 3

    assert ws.append_ranges == ["A2"]
    assert all(not range_name.endswith("1") for range_name, _ in ws.updates)
    assert {range_name for range_name, _ in ws.updates} >= {
        "F2",
        "G2",
        "H2",
        "I2",
        "J2",
    }


def test_full_sync_appends_missing_tickets_oldest_first():
    ws = FakeWorksheet("TICKETS", column_data=[])
    client = _client(ws)
    older = _ticket()
    older.ticket_id = "tkt1120260"
    older.created_at = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    newer = _ticket()
    newer.ticket_id = "tkt1120261"

    assert client.sync_all_tickets([newer, older], "TICKETS") == 2

    assert [row[0] for row in ws.appended] == ["tkt1120260", "tkt1120261"]
