"""Sinkronisasi ticket ke satu tab Google Sheets memakai service account.

Database lokal (SQLite) adalah sumber kebenaran. Ticket baru ditambahkan
di bagian bawah tab TICKETS; perubahan ticket memperbarui baris yang sama.

Semua error jaringan/permission dicatat dan tidak menggagalkan alur bot.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from .media import build_url, image_formula
from .store import format_local_datetime

log = logging.getLogger("sheets")

TICKET_HEADERS = [
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

_HEADER_ALIASES = {
    "Ticket ID": "ID",
    "ID Tiket": "ID",
    "Dibuat": "Tanggal",
    "Tanggal Dibuat": "Tanggal",
    "Detail": "Issue",
    "No HP": "Nomor HP",
    "Nomor Telepon": "Nomor HP",
    "Ditutup": "Selesai",
    "Resolusi": "Keterangan",
}


def col_letter(count: int) -> str:
    """Nomor kolom (1 = A, 2 = B, ... 26 = Z, 27 = AA)."""
    letters = ""
    while count > 0:
        count, remainder = divmod(count - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _ticket_row(ticket: Any) -> list[str]:
    """Baris ticket sesuai TICKET_HEADERS.

    Judul dan metadata tambahan sengaja tidak disinkronkan.
    """
    media_path = getattr(ticket, "media_path", "") or ""
    return [
        ticket.ticket_id,
        _fmt_dt(ticket.created_at),
        ticket.detail or "",
        ticket.status,
        ticket.pic or "",
        ticket.reporter or "",
        ticket.reporter_phone or "",
        ticket.group_name or ticket.group_id or "",
        _fmt_dt(ticket.closed_at) if ticket.closed_at else "",
        ticket.resolution or "",
        image_formula(ticket),
        build_url(media_path) if media_path else "",
    ]


class SheetsError(RuntimeError):
    pass


# Lokasi service account yang dicoba otomatis (urutan prioritas).
CREDENTIAL_CANDIDATES: tuple[str, ...] = (
    "/run/secrets/service-account.json",
    "/secrets/service-account.json",
    "secrets/service-account.json",
    "./secrets/service-account.json",
    "service-account.json",
)


class SheetsClient:
    """Wrapper tipis di atas gspread."""

    def __init__(
        self,
        spreadsheet_id: str = "",
        credentials_file: str = "",
        credentials_json: str = "",
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.credentials_file = credentials_file
        self.credentials_json = credentials_json
        self._client = None
        self._sheet = None
        self._ready = False
        self.last_error: str = ""
        self._prepared: set[str] = set()
        self._column_indexes: dict[str, dict[str, int]] = {}
        self._header_rows: dict[str, int] = {}

    # -- koneksi -----------------------------------------------------------
    def _resolve_credentials_file(self, configured: str) -> str:
        """Cari file service account: yang dikonfigurasi, lalu lokasi umum."""
        import os

        candidates: list[str] = []
        for path in (configured, *CREDENTIAL_CANDIDATES):
            if path and path not in candidates:
                candidates.append(path)
        for path in candidates:
            if os.path.isfile(path):
                return path
        tried = ", ".join(candidates)
        raise SheetsError(
            "File service account tidak ditemukan. Letakkan "
            "service-account.json di folder secrets/ (atau isi JSON-nya di "
            f"Dashboard > Google Sheets). Lokasi yang dicoba: {tried}"
        )

    def _build_client(self):
        if self._client is not None:
            return self._client
        if not self.spreadsheet_id:
            raise SheetsError("Spreadsheet ID belum dikonfigurasi")
        try:
            import gspread  # import lokal supaya app tetap jalan tanpa google libs
        except ImportError as exc:  # pragma: no cover
            raise SheetsError("Library gspread belum terpasang") from exc

        try:
            if self.credentials_json:
                info = json.loads(self.credentials_json)
                self._client = gspread.service_account(info=info)
            elif self.credentials_file:
                path = self._resolve_credentials_file(self.credentials_file)
                self._client = gspread.service_account(filename=path)
            else:
                raise SheetsError(
                    "Service account Google belum dikonfigurasi "
                    "(isi di Dashboard > Google Sheets)"
                )
        except SheetsError:
            raise
        except Exception as exc:
            raise SheetsError(f"Gagal membuat service account: {exc}") from exc
        return self._client

    def connect(self) -> bool:
        """Hubungkan dan siapkan worksheet. Return True bila berhasil."""
        try:
            client = self._build_client()
            self._sheet = client.open_by_key(self.spreadsheet_id)
            self._ready = True
            self.last_error = ""
            self._prepared.clear()
            self._column_indexes.clear()
            self._header_rows.clear()
            return True
        except Exception as exc:
            self._ready = False
            message = str(exc)
            if "403" in message or "Permission" in message or "not found" in message.lower():
                message += (
                    " | Spreadsheet harus dibagikan ke email service account "
                    "(role Editor) dan Spreadsheet ID harus benar."
                )
            self.last_error = message
            log.warning("Gagal terhubung ke Google Sheets: %s", message)
            return False

    @property
    def ready(self) -> bool:
        return self._ready and self._sheet is not None

    # -- worksheet ---------------------------------------------------------
    def ensure_worksheet(self, title: str, headers: list[str]) -> Any:
        if not self.ready and not self.connect():
            raise SheetsError(self.last_error or "Tidak bisa terhubung ke Sheets")
        assert self._sheet is not None
        try:
            ws = self._sheet.worksheet(title)
        except Exception:
            ws = self._sheet.add_worksheet(title=title, rows=100, cols=len(headers))
        columns, header_row = self._prepare(ws, headers)
        self._column_indexes[title] = columns
        self._header_rows[title] = header_row
        self._prepared.add(title)
        return ws

    def _prepare(self, ws: Any, headers: list[str]) -> tuple[dict[str, int], int]:
        """Pakai kolom yang cocok dan tambahkan header wajib yang belum ada."""
        aliases = {key.casefold(): value for key, value in _HEADER_ALIASES.items()}
        canonical = {header.casefold(): header for header in headers}
        scan_columns = max(1, min(ws.col_count, 52))
        scan_rows = min(max(ws.row_count, 1), 3)
        scanned = ws.get_values(f"A1:{col_letter(scan_columns)}{scan_rows}")
        header_row = 1
        existing: list[str] = []
        best_matches = 0
        for row_number, values in enumerate(scanned, 1):
            matches = {
                aliases.get(
                    re.sub(r"\s+", " ", (value or "").strip()).casefold()
                )
                or canonical.get(
                    re.sub(r"\s+", " ", (value or "").strip()).casefold()
                )
                for value in values
            }
            matches.discard(None)
            if len(matches) > best_matches:
                best_matches = len(matches)
                header_row = row_number
                existing = values
        if best_matches < 3:
            header_row = 1
            existing = scanned[0] if scanned else []

        columns: dict[str, int] = {}
        for index, value in enumerate(existing, 1):
            normalized = re.sub(r"\s+", " ", (value or "").strip()).casefold()
            header = aliases.get(normalized) or canonical.get(normalized)
            if header in headers and header not in columns:
                columns[header] = index

        next_column = max(len(existing), max(columns.values(), default=0)) + 1
        for header in headers:
            if header in columns:
                continue
            columns[header] = next_column
            next_column += 1

        required_columns = max(columns.values(), default=0)
        if ws.col_count < required_columns:
            ws.resize(rows=max(ws.row_count, header_row + 100), cols=required_columns)

        existing_columns = {
            index: (value or "").strip() for index, value in enumerate(existing, 1)
        }
        for header, index in columns.items():
            if existing_columns.get(index) == header:
                continue
            ws.update(
                range_name=f"{col_letter(index)}{header_row}",
                values=[[header]],
            )
        return columns, header_row

    def _worksheet(self, title: str, headers: list[str]) -> Any:
        if not self.ready and not self.connect():
            raise SheetsError(self.last_error or "Tidak bisa terhubung ke Sheets")
        assert self._sheet is not None
        try:
            ws = self._sheet.worksheet(title)
        except Exception:
            return self.ensure_worksheet(title, headers)
        # Persiapan (header + lebar kolom) cukup sekali per tab.
        if title not in self._prepared:
            columns, header_row = self._prepare(ws, headers)
            self._column_indexes[title] = columns
            self._header_rows[title] = header_row
            self._prepared.add(title)
        return ws

    # -- ticket ------------------------------------------------------------
    def append_ticket(self, ticket: Any, tab: str) -> int | None:
        """Append ticket, return nomor baris (1-based)."""
        ws = self._worksheet(tab, TICKET_HEADERS)
        values = _ticket_row(ticket)
        columns = self._column_indexes[tab]
        row_values = [""] * max(columns.values())
        for header, value in zip(TICKET_HEADERS, values):
            row_values[columns[header] - 1] = value
        image_column = columns["Foto"]
        image_value = row_values[image_column - 1]
        row_values[image_column - 1] = ""
        try:
            res = ws.append_row(
                row_values,
                table_range=f"A{self._header_rows[tab]}",
            )
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("Gagal append ticket ke Sheets: %s", exc)
            return None
        row = _row_from_result(res)
        if row and image_value:
            self._write_image_formula(ws, image_column, row, image_value)
        return row

    def update_ticket(self, ticket: Any, tab: str) -> bool:
        ws = self._worksheet(tab, TICKET_HEADERS)
        row = self.find_row(ws, ticket.ticket_id, tab)
        if not row:
            row = self.append_ticket(ticket, tab)
            if row:
                ticket.sheet_row = row
            return bool(row)
        values = _ticket_row(ticket)
        columns = self._column_indexes[tab]
        image_column = columns["Foto"]
        image_value = values[TICKET_HEADERS.index("Foto")]
        cells = sorted(
            (columns[header], value)
            for header, value in zip(TICKET_HEADERS, values)
            if header != "Foto"
        )
        ranges: list[list[Any]] = []
        for column, value in cells:
            if ranges and column == ranges[-1][1] + 1:
                ranges[-1][1] = column
                ranges[-1][2].append(value)
            else:
                ranges.append([column, column, [value]])
        try:
            for first, last, range_values in ranges:
                ws.update(
                    range_name=f"{col_letter(first)}{row}:{col_letter(last)}{row}",
                    values=[range_values],
                )
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("Gagal update ticket ke Sheets: %s", exc)
            return False
        if not self._write_image_formula(ws, image_column, row, image_value):
            return False
        ticket.sheet_row = row
        return True

    def _write_image_formula(
        self, ws: Any, column: int, row: int, formula: str
    ) -> bool:
        """Tulis formula IMAGE sebagai formula, bukan sebagai teks biasa."""
        try:
            ws.update(
                range_name=f"{col_letter(column)}{row}",
                values=[[formula]],
                raw=False,
            )
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("Gagal menulis foto ticket ke Sheets: %s", exc)
            return False
        return True

    def find_row(self, ws: Any, ticket_id: str, tab: str) -> int | None:
        col = ws.col_values(self._column_indexes[tab]["ID"])
        for idx, val in enumerate(col):
            if str(val).strip().upper() == ticket_id.upper():
                return idx + 1
        return None

    def sync_all_tickets(self, tickets: list[Any], tab: str) -> int:
        """Push semua ticket (dipakai sinkron ulang / catch-up)."""
        count = 0
        ws = self._worksheet(tab, TICKET_HEADERS)
        ordered_tickets = sorted(
            tickets,
            key=lambda ticket: (
                ticket.created_at.replace(tzinfo=timezone.utc)
                if ticket.created_at.tzinfo is None
                else ticket.created_at.astimezone(timezone.utc)
            ),
        )
        for t in ordered_tickets:
            if self.find_row(ws, t.ticket_id, tab):
                if self.update_ticket(t, tab):
                    count += 1
            else:
                row = self.append_ticket(t, tab)
                if row:
                    t.sheet_row = row
                    count += 1
        return count

def _row_from_result(res: Any) -> int | None:
    """Parse {'updatedRange': 'Sheet!A5:N5'} -> 5."""
    if not isinstance(res, dict):
        return None
    rng = res.get("updatedRange") or ""
    if "!" not in rng:
        return None
    tail = rng.split("!")[-1]
    digits = "".join(ch for ch in tail.split(":")[0] if ch.isdigit())
    return int(digits) if digits else None


def _fmt_dt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return format_local_datetime(value, "%Y-%m-%d %H:%M:%S")
    return str(value)
