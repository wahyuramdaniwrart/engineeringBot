"""Template pesan balasan bot.

Semua teks balasan bot dibangun di modul ini, bukan di ``handlers.py``,
supaya gaya pesan bisa dimatangkan/ubah hanya di satu tempat tanpa
menyentuh alur logic.

Gaya yang dipakai:

* **Tanpa emoji** sama sekali (notifikasi arus lalu lintas/whatsapp bot).
* **Plain text polos** - tanpa markdown/bold, agar ringan dan tidak
  merusak tampilan di berbagai perangkat.
* Label dinormalisasi dengan lebar tetap (``Issue     : ...``) supaya
  mudah dibaca sekilas.
* Format seragam: ID dan issue diikuti detail per baris
  dengan label, lalu daftar perintah lanjutan bila relevan.
"""

from __future__ import annotations

from datetime import datetime

from .store import format_local_datetime, now_local

# Lebar kolom label supaya ':' selalu sejajar.
LABEL_WIDTH = 10


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def field(label: str, value: str) -> str:
    """Satu baris ``Label   : nilai``."""
    return f"{label.ljust(LABEL_WIDTH)}: {value}"


def _ticket_line(ticket) -> str:
    """Satu baris ringkasan ticket untuk daftar/ringkasan."""
    issue = ticket.detail or ticket.title
    line = f"- {ticket.ticket_id} - {issue} | {ticket.status}"
    if ticket.pic:
        line += f" | PIC: {ticket.pic}"
    return line


def _status_value(ticket) -> str:
    return ticket.status


# ---------------------------------------------------------------------------
# Balasan aksi
# ---------------------------------------------------------------------------
def ticket_created(ticket, prefix: str = "#") -> str:
    """Balasan setelah ticket baru dibuat."""
    return ticket.ticket_id


def ticket_detail(ticket) -> str:
    """Detail lengkap satu ticket."""
    lines = [ticket.ticket_id]
    issue = ticket.detail or ticket.title
    if issue:
        lines.append(field("Issue", issue))
    lines.append(field("Status", _status_value(ticket)))
    lines.append(field("Tipe", ticket.ticket_type))
    lines.append(field("Prioritas", ticket.priority))
    if ticket.pic:
        lines.append(field("PIC", ticket.pic))
    if ticket.reporter:
        lines.append(field("Pelapor", ticket.reporter))
    lines.append(
        field(
            "Dibuat",
            format_local_datetime(ticket.created_at, "%d-%m-%Y %H:%M"),
        )
    )
    if ticket.closed_at:
        lines.append(
            field("Ditutup", format_local_datetime(ticket.closed_at, "%d-%m-%Y %H:%M"))
        )
    if ticket.resolution:
        lines.append(field("Resolusi", ticket.resolution))
    if getattr(ticket, "media_filename", ""):
        lines.append(field("Foto", ticket.media_filename))
        if getattr(ticket, "media_size", 0):
            lines.append(field("Ukuran", _size_kb(ticket.media_size)))
    return "\n".join(lines)


def status_changed(ticket, old_status: str, resolution: str = "") -> str:
    """Balasan setelah status ticket diubah."""
    lines = [f"Status {ticket.ticket_id} diubah: {old_status} -> {ticket.status}"]
    if resolution:
        lines.append(f"Catatan: {resolution}")
    return "\n".join(lines)


def pic_changed(ticket) -> str:
    """Balasan setelah PIC diganti."""
    return f"PIC {ticket.ticket_id} sekarang: {ticket.pic}"


def ticket_closed(ticket, resolution: str = "") -> str:
    """Balasan setelah ticket ditutup (SELESAI)."""
    lines = [f"Ticket {ticket.ticket_id} ditandai SELESAI."]
    if resolution:
        lines.append(f"Resolusi: {resolution}")
    return "\n".join(lines)


def ticket_cancelled(ticket, reason: str = "") -> str:
    """Balasan setelah ticket dibatalkan."""
    lines = [f"Ticket {ticket.ticket_id} dibatalkan."]
    if reason:
        lines.append(f"Alasan: {reason}")
    return "\n".join(lines)


def ticket_list(tickets, limit: int | None = None) -> str:
    """Daftar ticket terbaru."""
    if not tickets:
        return "Tidak ada ticket."
    total = limit if limit is not None else len(tickets)
    lines = [f"Daftar ticket ({total} terbaru)"]
    for ticket in tickets:
        lines.append(_ticket_line(ticket))
    return "\n".join(lines)


def ticket_list_empty(status: str = "") -> str:
    scope = f" berstatus {status}" if status else ""
    return f"Tidak ada ticket{scope}."


# ---------------------------------------------------------------------------
# Ringkasan
# ---------------------------------------------------------------------------
def summary_block(
    open_tickets,
    done_tickets,
    header: str,
    open_label: str = "Belum selesai",
    done_label: str = "Sudah selesai / ditutup",
) -> str:
    """Blok ringkasan ticket (dipakai #info dan ringkasan harian)."""
    lines = [header, ""]
    lines.append(f"{open_label} ({len(open_tickets)}):")
    if open_tickets:
        for ticket in open_tickets:
            lines.append(_ticket_line(ticket))
    else:
        lines.append("- tidak ada")

    lines.append("")
    lines.append(f"{done_label} ({len(done_tickets)}):")
    if done_tickets:
        for ticket in done_tickets:
            lines.append(_ticket_line(ticket))
    else:
        lines.append("- tidak ada")

    lines.append("")
    lines.append(
        f"Total {len(open_tickets) + len(done_tickets)} | "
        f"belum selesai {len(open_tickets)} | selesai {len(done_tickets)}"
    )
    return "\n".join(lines)


def info_summary(open_tickets, done_tickets, day: datetime | None = None) -> str:
    """Balasan perintah #info."""
    day = day or now_local()
    header = f"Ringkasan ticket hari ini ({day.strftime('%d-%m-%Y')})"
    return summary_block(open_tickets, done_tickets, header)


def daily_summary(open_tickets, done_tickets, day: datetime | None = None) -> str:
    """Ringkasan harian yang dikirim otomatis ke grup."""
    day = day or now_local()
    header = f"Ringkasan ticket harian - {day.strftime('%d-%m-%Y')}"
    return summary_block(open_tickets, done_tickets, header)


# ---------------------------------------------------------------------------
# Galat & validasi
# ---------------------------------------------------------------------------
def ticket_not_found(ticket_id: str) -> str:
    return f"Ticket {ticket_id} tidak ditemukan."


def format_error(usage: str, example: str = "") -> str:
    """Balasan saat format perintah salah."""
    lines = ["Format salah. Contoh:"]
    lines.append(f"- {usage}")
    if example:
        lines.append(f"- {example}")
    return "\n".join(lines)


def status_unknown(status: str, valid: list[str]) -> str:
    lines = [f"Status {status} tidak dikenal."]
    lines.append(f"Status yang tersedia: {', '.join(valid)}")
    return "\n".join(lines)


def status_transition_denied(current: str, target: str, allowed: list[str]) -> str:
    lines = [f"Status {current} tidak bisa langsung ke {target}."]
    lines.append(f"Yang diperbolehkan: {', '.join(allowed) if allowed else '(tidak ada)'}")
    return "\n".join(lines)


def ticket_final(ticket) -> str:
    """Ticket yang sudah final (tidak bisa diubah lagi)."""
    return (
        f"Ticket {ticket.ticket_id} sudah berstatus {ticket.status} "
        "dan tidak bisa diubah lagi."
    )


def unknown_command(prefix: str, name: str, help_text: str = "") -> str:
    head = f"Perintah {prefix}{name} tidak dikenal."
    return f"{head}\n\n{help_text}" if help_text else head


def command_error(prefix: str, name: str, error: str) -> str:
    return f"Terjadi kesalahan saat memproses perintah {prefix}{name}: {error}"


# ---------------------------------------------------------------------------
# Media (foto)
# ---------------------------------------------------------------------------
def _size_kb(size: int) -> str:
    value = float(size or 0) / 1024
    if value < 1024:
        return f"{value:.0f} KB"
    return f"{value / 1024:.1f} MB"


def media_hint() -> str:
    """Balasan saat foto dikirim tanpa perintah."""
    return (
        "Foto diterima.\n"
        "Untuk membuat ticket, tulis perintahnya pada caption foto, contoh:\n"
        "- #ticket AC kamar 203 tidak dingin\n"
        "- #tutup tkt1120261 sudah diperbaiki"
    )


def media_attached(filename: str, size: int = 0) -> str:
    """Balasan saat foto berhasil dilampirkan ke ticket."""
    return f"Foto tersimpan: {filename} ({_size_kb(size)})"


def media_failed(reason: str) -> str:
    """Balasan saat foto gagal diunduh/disimpan."""
    return f"Foto gagal disimpan: {reason}"


def media_unavailable() -> str:
    """Balasan saat pesan punya media tapi tanpa tautan unduhan."""
    return "Foto tidak bisa diproses: pesan tidak memuat tautan media."


# ---------------------------------------------------------------------------
# Notifikasi lain
# ---------------------------------------------------------------------------
def dashboard_change(ticket_id: str, changes: list[str]) -> str:
    """Notifikasi perubahan ticket yang dilakukan dari dashboard."""
    lines = [f"Ticket {ticket_id} diperbarui dari dashboard:"]
    for item in changes:
        if item:
            lines.append(f"- {item}")
    return "\n".join(lines)
