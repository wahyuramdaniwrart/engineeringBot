"""Parser perintah (murni, tanpa akses database / jaringan) agar mudah dites."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_PREFIXES = ["#", "/"]


@dataclass
class ParsedCommand:
    """Hasil parsing satu pesan perintah."""

    prefix: str
    name: str
    action: str
    args: str = ""
    arg_list: list[str] = field(default_factory=list)
    raw: str = ""
    definition: dict[str, Any] = field(default_factory=dict)

    @property
    def is_create_like(self) -> bool:
        return self.action == "create"


def normalize_prefixes(prefixes: list[str] | None) -> list[str]:
    out: list[str] = []
    for p in prefixes or DEFAULT_PREFIXES:
        p = (p or "").strip()
        if p and p not in out:
            out.append(p)
    return out or list(DEFAULT_PREFIXES)


def _build_index(commands: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for cmd in commands or []:
        if not cmd.get("enabled", True):
            continue
        name = str(cmd.get("name", "")).strip().lower()
        if name:
            index[name] = cmd
        for alias in cmd.get("aliases", []) or []:
            alias = str(alias).strip().lower()
            if alias:
                index.setdefault(alias, cmd)
    return index


def parse_command(
    text: str,
    prefixes: list[str] | None = None,
    commands: list[dict[str, Any]] | None = None,
) -> ParsedCommand | None:
    """Deteksi perintah di awal pesan.

    Mengembalikan ``None`` bila pesan bukan perintah (atau perintahnya
    dinonaktifkan di dashboard).
    """
    if not text:
        return None
    body = text.strip()
    if not body:
        return None

    prefixes = normalize_prefixes(prefixes)
    prefix = next((p for p in prefixes if body.startswith(p)), None)
    if prefix is None:
        return None

    rest = body[len(prefix) :].strip()
    if not rest:
        return None

    # Token pertama = nama perintah. Mendukung "#status" dan "#status <args>".
    match = re.match(r"^([A-Za-z0-9_\-]+)\s*(.*)$", rest, flags=re.S)
    if not match:
        return None
    token = match.group(1).lower()
    args = (match.group(2) or "").strip()

    index = _build_index(commands or [])
    definition = index.get(token)
    if definition is None:
        # Perintah tidak dikenal -> tetap dikembalikan agar bisa dibalas
        # dengan pesan bantuan.
        return ParsedCommand(
            prefix=prefix,
            name=token,
            action="unknown",
            args=args,
            arg_list=args.split() if args else [],
            raw=body,
            definition={},
        )

    return ParsedCommand(
        prefix=prefix,
        name=str(definition.get("name", token)),
        action=str(definition.get("action", "unknown")),
        args=args,
        arg_list=args.split() if args else [],
        raw=body,
        definition=definition,
    )


def split_ticket_and_rest(args: str) -> tuple[str, str]:
    """Pisahkan '<ID> <sisanya>' dari argumen perintah.

    Mengembalikan (ticket_id, sisa_args). Bila argumen pertama bukan pola ID
    ticket (mis. ``tkt1120261``, ``TKT-2026-0001``, atau angka), ticket_id dikembalikan kosong.
    """
    args = (args or "").strip()
    if not args:
        return "", ""
    parts = args.split(None, 1)
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if (
        re.fullmatch(r"[A-Za-z]+-\d{4}-\d+", first)
        or re.fullmatch(r"[A-Za-z]+\d{7,}", first)
        or re.fullmatch(r"\d+", first)
    ):
        return first, rest.strip()
    return "", args


def extract_priority(text: str) -> tuple[str, str]:
    """Ambil marker prioritas dari teks.

    Mendukung ``[tinggi]``, ``[TINGGI]``, ``(tinggi)`` di awal teks.
    Mengembalikan (teks_bersih, prioritas).
    """
    priority = "NORMAL"
    cleaned = (text or "").strip()
    m = re.match(r"^\s*[\[\(]\s*(tinggi|ting|sedang|normal|rendah|low|high)\s*[\]\)]\s*", cleaned, flags=re.I)
    if m:
        word = m.group(1).lower()
        mapping = {
            "tinggi": "TINGGI",
            "ting": "TINGGI",
            "sedang": "NORMAL",
            "normal": "NORMAL",
            "rendah": "RENDAH",
            "low": "RENDAH",
            "high": "TINGGI",
        }
        priority = mapping[word]
        cleaned = cleaned[m.end() :].strip()
    return cleaned, priority


def extract_mentions(text: str) -> tuple[str, list[str]]:
    """Pisahkan mention '@nama' dari teks.

    Mengembalikan (teks_bersih, daftar_nama). Cocok untuk WAHA yang menulis
    mention sebagai '@628123456789' atau '@Budi'.
    """
    names: list[str] = []
    cleaned = re.sub(r"@([\w\.\-]{2,})", lambda m: names.append(m.group(1)) or " ", text or "")
    return re.sub(r"\s{2,}", " ", cleaned).strip(), names


def format_help(commands: list[dict[str, Any]], prefixes: list[str]) -> str:
    """Buat menu bantuan ringkas untuk WhatsApp."""
    prefixes = normalize_prefixes(prefixes)
    prefix = prefixes[0]
    icons = {
        "create": "🎫",
        "status": "🔄",
        "detail": "🔍",
        "pic": "👤",
        "close": "✅",
        "cancel": "🚫",
        "reminder": "⏰",
        "media": "📷",
        "list": "📋",
        "info": "📊",
        "help": "❔",
    }
    lines = ["📚 *MENU PERINTAH BOT*", f"Gunakan prefix: {', '.join(prefixes)}", ""]
    for cmd in commands or []:
        if not cmd.get("enabled", True):
            continue
        aliases = cmd.get("aliases") or []
        usage = str(cmd.get("usage") or f"{prefix}{cmd.get('name')}").strip()
        if usage.startswith(("#", "/")):
            usage = f"{prefix}{usage[1:]}"
        desc = str(cmd.get("description") or "")
        icon = icons.get(str(cmd.get("action") or ""), "•")
        lines.append(f"{icon} *{usage}*")
        if desc:
            lines.append(f"   {desc}")
        if aliases:
            lines.append(f"   Alias: {', '.join(prefix + str(alias) for alias in aliases)}")
        lines.append("")
    lines.append("ℹ️ Ganti TICKET-ID dengan ID tiket, mis. tkt1120261.")
    return "\n".join(lines)
