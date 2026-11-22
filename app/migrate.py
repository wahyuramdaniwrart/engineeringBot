"""Migrasi ringan untuk skema & data.

SQLite + ``Base.metadata.create_all`` **tidak** menambahkan kolom baru ke
tabel yang sudah ada, dan perubahan nama status tidak ikut mengubah data
yang sudah tersimpan. Semua itu ditangani di sini.

Migrasi bersifat idempotent: aman dijalankan berulang kali, dan otomatis
dipanggil dari :func:`app.db.init_db` setiap aplikasi start.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from .config import DEFAULT_COMMANDS
from .store import LEGACY_STATUS_ALIASES

log = logging.getLogger("migrate")


# ---------------------------------------------------------------------------
# Kolom baru pada tabel tickets
# ---------------------------------------------------------------------------
TICKET_COLUMNS: list[tuple[str, str]] = [
    ("media_filename", "VARCHAR(255) DEFAULT ''"),
    ("media_path", "VARCHAR(512) DEFAULT ''"),
    ("media_mimetype", "VARCHAR(128) DEFAULT ''"),
    ("media_size", "INTEGER DEFAULT 0"),
    ("media_url", "VARCHAR(512) DEFAULT ''"),
    ("media_caption", "TEXT DEFAULT ''"),
    ("reminder_at", "DATETIME NULL"),
    ("reminder_retry_at", "DATETIME NULL"),
    ("reminder_last_sent_at", "DATETIME NULL"),
]


def add_missing_ticket_columns(engine: Engine) -> list[str]:
    """Tambah kolom media ke tabel tickets bila belum ada."""
    inspector = inspect(engine)
    if "tickets" not in inspector.get_table_names():
        return []
    existing = {c["name"] for c in inspector.get_columns("tickets")}
    added: list[str] = []
    with engine.begin() as conn:
        for name, ddl in TICKET_COLUMNS:
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE tickets ADD COLUMN {name} {ddl}"))
            added.append(name)
    if added:
        log.info("Migrasi: kolom ticket ditambahkan -> %s", ", ".join(added))
    return added


# ---------------------------------------------------------------------------
# Pindahkan issue lama dari kolom judul
# ---------------------------------------------------------------------------
def move_legacy_ticket_titles_to_issue(engine: Engine) -> int:
    """Isi issue kosong dari judul pada ticket lama."""
    inspector = inspect(engine)
    if "tickets" not in inspector.get_table_names():
        return 0
    columns = {column["name"] for column in inspector.get_columns("tickets")}
    if not {"title", "detail"}.issubset(columns):
        return 0

    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE tickets SET detail = title "
                "WHERE (detail IS NULL OR trim(detail) = '') "
                "AND title IS NOT NULL AND trim(title) <> ''"
            )
        )
    moved = result.rowcount or 0
    if moved:
        log.info("Migrasi: issue ticket dipindahkan dari judul -> %s", moved)
    return moved


# ---------------------------------------------------------------------------
# Perbaikan ejaan status lama ("DIBERJAKAN" -> "DIKERJAKAN")
# ---------------------------------------------------------------------------
def fix_legacy_statuses(engine: Engine) -> dict[str, int]:
    """Perbarui data tersimpan yang masih memakai ejaan lama."""
    report = {"tickets": 0, "settings": 0}
    inspector = inspect(engine)
    if "tickets" not in inspector.get_table_names():
        return report

    with engine.begin() as conn:
        for legacy, correct in LEGACY_STATUS_ALIASES.items():
            result = conn.execute(
                text("UPDATE tickets SET status = :new WHERE status = :old"),
                {"new": correct, "old": legacy},
            )
            report["tickets"] += result.rowcount or 0

    # Setting "statuses" (dikelola dari dashboard) juga menyimpan kunci status.
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT value FROM settings WHERE key = 'statuses'")
        ).fetchone()
        if row is None:
            return report
        try:
            raw = json.loads(row[0])
            statuses = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return report
        if not isinstance(statuses, list):
            return report

        changed = False
        for item in statuses:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).upper()
            if key in LEGACY_STATUS_ALIASES:
                item["key"] = LEGACY_STATUS_ALIASES[key]
                changed = True
            transitions = item.get("transitions") or []
            new_transitions = [
                LEGACY_STATUS_ALIASES.get(str(t).upper(), str(t).upper())
                for t in transitions
            ]
            if new_transitions != transitions:
                item["transitions"] = new_transitions
                changed = True

        if changed:
            conn.execute(
                text("UPDATE settings SET value = :v WHERE key = 'statuses'"),
                {"v": json.dumps(statuses, ensure_ascii=False)},
            )
            report["settings"] = 1
            log.info("Migrasi: kunci status lama diperbaiki di pengaturan")
    return report


# ---------------------------------------------------------------------------
# Perintah baru
# ---------------------------------------------------------------------------
def ensure_commands(engine: Engine) -> list[str]:
    """Pastikan perintah bawaan terbaru ikut tersimpan di pengaturan.

    Pengaturan di dashboard **meng-ALIH** daftar perintah bawaan, jadi
    perintah baru harus ikut ditulis ke sana, kalau tidak tidak akan pernah
    aktif di grup.
    """
    inspector = inspect(engine)
    if "settings" not in inspector.get_table_names():
        return []
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT value FROM settings WHERE key = 'commands'")
        ).fetchone()
        if row is None:
            return []
        try:
            raw = json.loads(row[0])
            commands = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(commands, list):
            return []

        existing_commands = {
            str(c.get("name", "")).strip().lower(): c
            for c in commands
            if isinstance(c, dict)
        }
        updated_usages: list[str] = []
        for default in DEFAULT_COMMANDS:
            if not isinstance(default, dict):
                continue
            name = str(default.get("name", "")).strip().lower()
            stored = existing_commands.get(name)
            legacy_usage = {
                "ticket": "#ticket <judul> | #ticket <judul> | <detail> | @PIC",
                "status": "#status <ID> [STATUS]",
                "list": "#list [STATUS] [jumlah]",
            }.get(name)
            changed = False
            legacy_list_usages = {
                "#list [STATUS] [jumlah]",
                "#list [STATUS AKTIF] [jumlah]",
            }
            if stored and (
                (legacy_usage and stored.get("usage") == legacy_usage)
                or (
                    name == "list"
                    and stored.get("usage") in legacy_list_usages
                )
            ):
                stored["usage"] = default.get("usage", stored["usage"])
                changed = True
            if (
                name == "list"
                and stored
                and stored.get("description")
                in (
                    "Daftar ticket terbaru",
                    "Daftar ticket yang belum selesai",
                    "Daftar ticket semua status",
                )
            ):
                stored["description"] = default.get(
                    "description", stored["description"]
                )
                changed = True
            if changed:
                updated_usages.append(name)
        added = [
            c
            for c in DEFAULT_COMMANDS
            if isinstance(c, dict)
            and str(c.get("name", "")).lower() not in existing_commands
        ]
        updated_aliases: list[str] = []
        for default in DEFAULT_COMMANDS:
            if not isinstance(default, dict):
                continue
            name = str(default.get("name", "")).strip().lower()
            stored = existing_commands.get(name)
            if stored is None:
                continue
            aliases = stored.get("aliases")
            if not isinstance(aliases, list):
                aliases = []
                stored["aliases"] = aliases
            existing_aliases = {
                str(alias).strip().lower() for alias in aliases
            }
            missing = [
                alias
                for alias in default.get("aliases", [])
                if str(alias).strip().lower() not in existing_aliases
            ]
            if missing:
                aliases.extend(missing)
                updated_aliases.append(name)
        if not added and not updated_aliases and not updated_usages:
            return []
        commands.extend(added)
        conn.execute(
            text("UPDATE settings SET value = :v WHERE key = 'commands'"),
            {"v": json.dumps(commands, ensure_ascii=False)},
        )
        changed = [str(c.get("name")) for c in added] + updated_aliases + updated_usages
        log.info("Migrasi: perintah diperbarui -> %s", ", ".join(changed))
    return changed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_migrations(engine: Engine) -> dict[str, Any]:
    """Jalankan semua migrasi (aman diulang)."""
    report: dict[str, Any] = {}
    try:
        report["columns"] = add_missing_ticket_columns(engine)
        report["issues"] = move_legacy_ticket_titles_to_issue(engine)
        report["statuses"] = fix_legacy_statuses(engine)
        report["commands"] = ensure_commands(engine)
    except Exception as exc:  # pragma: no cover - migrasi tidak boleh mematikan app
        log.warning("Migrasi gagal (diabaikan): %s", exc)
    return report
