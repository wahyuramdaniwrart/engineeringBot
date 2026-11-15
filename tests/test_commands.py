"""Unit test untuk parser perintah (tanpa DB / jaringan)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.commands import (  # noqa: E402
    DEFAULT_PREFIXES,
    extract_mentions,
    extract_priority,
    format_help,
    parse_command,
    split_ticket_and_rest,
)
from app.config import DEFAULT_COMMANDS  # noqa: E402


def test_parse_prefix_hash():
    cmd = parse_command("#ticket AC kamar 203 tidak dingin", DEFAULT_PREFIXES, DEFAULT_COMMANDS)
    assert cmd is not None
    assert cmd.name == "ticket"
    assert cmd.action == "create"
    assert cmd.args == "AC kamar 203 tidak dingin"


def test_parse_prefix_slash():
    cmd = parse_command("/status TKT-2026-0001", DEFAULT_PREFIXES, DEFAULT_COMMANDS)
    assert cmd is not None
    assert cmd.name == "status"
    assert cmd.action == "status"


def test_parse_alias():
    cmd = parse_command("#buat AC lobby", DEFAULT_PREFIXES, DEFAULT_COMMANDS)
    assert cmd is not None
    assert cmd.name == "ticket"
    assert cmd.action == "create"


def test_parse_not_a_command():
    assert parse_command("halo semua", DEFAULT_PREFIXES, DEFAULT_COMMANDS) is None
    assert parse_command("", DEFAULT_PREFIXES, DEFAULT_COMMANDS) is None


def test_parse_unknown_command():
    cmd = parse_command("#entah apa", DEFAULT_PREFIXES, DEFAULT_COMMANDS)
    assert cmd is not None
    assert cmd.action == "unknown"


def test_parse_disabled_command():
    commands = [dict(c, enabled=False) for c in DEFAULT_COMMANDS]
    cmd = parse_command("#ticket AC", DEFAULT_PREFIXES, commands)
    assert cmd is not None
    assert cmd.action == "unknown"


def test_split_ticket_and_rest():
    tid, rest = split_ticket_and_rest("TKT-2026-0001 ACGV dignity")
    assert tid == "TKT-2026-0001"
    assert rest == "ACGV dignity"


def test_split_new_ticket_id():
    tid, rest = split_ticket_and_rest("tkt1120261 catatan")
    assert tid == "tkt1120261"
    assert rest == "catatan"


def test_split_ticket_numeric():
    tid, rest = split_ticket_and_rest("12 repair AC")
    assert tid == "12"
    assert rest == "repair AC"


def test_split_ticket_invalid():
    tid, rest = split_ticket_and_rest("bukan id")
    assert tid == ""
    assert rest == "bukan id"


def test_extract_priority():
    text, prio = extract_priority("[tinggi] Air panas tidak keluar")
    assert prio == "TINGGI"
    assert text == "Air panas tidak keluar"


def test_extract_priority_none():
    text, prio = extract_priority("Ganti filter AC")
    assert prio == "NORMAL"
    assert text == "Ganti filter AC"


def test_extract_mentions():
    text, names = extract_mentions("Ganti filter @Budi di lobby")
    assert "Budi" in names
    assert "@" not in text


def test_format_help_contains_usage():
    txt = format_help(DEFAULT_COMMANDS, DEFAULT_PREFIXES)
    assert "#ticket" in txt
    assert "#info" in txt
