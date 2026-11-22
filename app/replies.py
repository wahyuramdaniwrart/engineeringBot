"""Template pesan balasan bot."""

from __future__ import annotations

from datetime import datetime

from .store import format_local_datetime, now_local

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def field(label: str, value: str) -> str:
    """Satu baris detail berlabel."""
    return f"{label}: {value}"


def _status_icon(status: str) -> str:
    icons = {
        "BARU": "🆕",
        "DIKERJAKAN": "🛠️",
        "MENUNGGU": "⏳",
        "SELESAI": "✅",
        "DIBATALKAN": "🚫",
    }
    return icons.get((status or "").strip().upper(), "📌")


def _ticket_line(ticket) -> str:
    """Satu baris ringkasan ticket untuk daftar/ringkasan."""
    issue = ticket.detail or ticket.title
    line = f"• *{ticket.ticket_id}* — {issue} | {_status_icon(ticket.status)} {ticket.status}"
    if ticket.pic:
        line += f" | 👤 {ticket.pic}"
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
    lines = [f"🎫 *{ticket.ticket_id}*"]
    issue = ticket.detail or ticket.title
    if issue:
        lines.append(field("🧩 Issue", issue))
    lines.append(field(f"{_status_icon(ticket.status)} Status", _status_value(ticket)))
    lines.append(field("🧰 Tipe", ticket.ticket_type))
    lines.append(field("⚡ Prioritas", ticket.priority))
    if ticket.pic:
        lines.append(field("👤 PIC", ticket.pic))
    if ticket.reporter:
        lines.append(field("📣 Pelapor", ticket.reporter))
    lines.append(
        field(
            "🕒 Dibuat",
            format_local_datetime(ticket.created_at, "%d-%m-%Y %H:%M"),
        )
    )
    if ticket.closed_at:
        lines.append(
            field("✅ Ditutup", format_local_datetime(ticket.closed_at, "%d-%m-%Y %H:%M"))
        )
    if ticket.reminder_at:
        lines.append(
            field(
                "⏰ Reminder harian",
                format_local_datetime(ticket.reminder_at, "%d-%m-%Y %H:%M"),
            )
        )
    if ticket.resolution:
        lines.append(field("📝 Catatan", ticket.resolution))
    attachments = getattr(ticket, "media_attachments", [])
    if attachments:
        names = ", ".join(
            item.media_filename for item in attachments if item.media_filename
        )
        count = f"{len(attachments)} lampiran"
        lines.append(field("📎 Lampiran", f"{count}: {names}" if names else count))
    else:
        lines.append(field("📎 Lampiran", "Tidak ada"))
    return "\n".join(lines)


def status_changed(ticket, old_status: str, resolution: str = "") -> str:
    """Balasan setelah status ticket diubah."""
    lines = [
        f"🔄 Status *{ticket.ticket_id}* diubah: "
        f"{old_status} -> {ticket.status} {_status_icon(ticket.status)}"
    ]
    if resolution:
        lines.append(f"📝 Catatan: {resolution}")
    return "\n".join(lines)


def pic_changed(ticket) -> str:
    """Balasan setelah PIC diganti."""
    return f"👤 PIC *{ticket.ticket_id}* sekarang: {ticket.pic}"


def ticket_closed(ticket, resolution: str = "") -> str:
    """Balasan setelah ticket ditutup (SELESAI)."""
    lines = [f"✅ Ticket *{ticket.ticket_id}* ditandai SELESAI."]
    if resolution:
        lines.append(f"📝 Resolusi: {resolution}")
    return "\n".join(lines)


def ticket_cancelled(ticket, reason: str = "") -> str:
    """Balasan setelah ticket dibatalkan."""
    lines = [f"🚫 Ticket *{ticket.ticket_id}* dibatalkan."]
    if reason:
        lines.append(f"📝 Alasan: {reason}")
    return "\n".join(lines)


def ticket_reminder_status(ticket) -> str:
    if ticket.reminder_at is None:
        return f"ℹ️ Reminder ticket *{ticket.ticket_id}* belum diatur."
    return (
        f"⏰ Reminder harian ticket *{ticket.ticket_id}* aktif. "
        f"Pengiriman berikutnya: "
        f"{format_local_datetime(ticket.reminder_at, '%d-%m-%Y %H:%M')}."
    )


def ticket_reminder_set(ticket) -> str:
    return (
        f"⏰ Reminder harian ticket *{ticket.ticket_id}* diatur. "
        f"Mulai {format_local_datetime(ticket.reminder_at, '%d-%m-%Y %H:%M')} "
        "dan berulang setiap hari pada jam yang sama."
    )


def ticket_reminder_cancelled(ticket) -> str:
    return f"✅ Reminder ticket *{ticket.ticket_id}* dibatalkan."


def ticket_reminder_not_set(ticket) -> str:
    return f"ℹ️ Reminder ticket *{ticket.ticket_id}* belum diatur."


def ticket_reminder_closed(ticket) -> str:
    return (
        f"⚠️ Reminder ticket *{ticket.ticket_id}* hanya dapat diatur "
        "selama ticket belum selesai."
    )


def ticket_reminder_no_chat(ticket) -> str:
    return (
        f"⚠️ Reminder ticket *{ticket.ticket_id}* tidak dapat diatur karena "
        "ticket tidak memiliki grup/chat tujuan."
    )


def ticket_reminder_invalid(reason: str) -> str:
    return f"⚠️ Waktu reminder tidak valid. {reason}"


def ticket_list(tickets, limit: int | None = None) -> str:
    """Daftar ticket dari status yang dipilih."""
    if not tickets:
        return "ℹ️ Tidak ada ticket ditemukan."
    total = limit if limit is not None else len(tickets)
    lines = [f"📋 Ticket ({total})"]
    for ticket in tickets:
        lines.append("")
        lines.append(_ticket_line(ticket))
    return "\n".join(lines)


def ticket_list_empty(status: str = "") -> str:
    scope = f" dengan status {status}" if status else ""
    return f"ℹ️ Tidak ada ticket ditemukan{scope}."


# ---------------------------------------------------------------------------
# Ringkasan
# ---------------------------------------------------------------------------
def summary_block(
    open_tickets,
    done_tickets,
    header: str,
    open_label: str = "Belum selesai",
    done_label: str = "Sudah selesai / ditutup",
    note: str = "",
) -> str:
    """Blok ringkasan ticket (dipakai #info dan ringkasan harian)."""
    total = len(open_tickets) + len(done_tickets)
    max_shown = 100  # jaga pesan tetap muat di WhatsApp
    lines = [
        f"📊 {header}",
    ]
    if note:
        lines.append(f"ℹ️ {note}")
    lines += [
        "",
        f"📌 Total: {total}",
        f"🟡 {open_label}: {len(open_tickets)}",
        f"✅ {done_label}: {len(done_tickets)}",
        "",
        f"🟡 {open_label} ({len(open_tickets)}):",
    ]
    if open_tickets:
        for ticket in open_tickets[:max_shown]:
            lines.append("")
            lines.append(_ticket_line(ticket))
        if len(open_tickets) > max_shown:
            lines.append(f"… dan {len(open_tickets) - max_shown} ticket lainnya")
    else:
        lines.append("• Tidak ada")

    lines.append("")
    lines.append(f"✅ {done_label} ({len(done_tickets)}):")
    if done_tickets:
        for ticket in done_tickets[:max_shown]:
            lines.append("")
            lines.append(_ticket_line(ticket))
        if len(done_tickets) > max_shown:
            lines.append(f"… dan {len(done_tickets) - max_shown} ticket lainnya")
    else:
        lines.append("• Tidak ada")
    return "\n".join(lines)


def info_summary(open_tickets, done_tickets, day: datetime | None = None) -> str:
    """Balasan perintah #info."""
    day = day or now_local()
    header = f"Ringkasan ticket · {day.strftime('%d-%m-%Y')}"
    return summary_block(
        open_tickets,
        done_tickets,
        header,
        done_label="Sudah selesai / ditutup hari ini",
        note="Belum selesai = seluruh ticket yang masih berjalan. "
        "Selesai = ticket yang ditutup hari ini.",
    )


def daily_summary(open_tickets, done_tickets, day: datetime | None = None) -> str:
    """Ringkasan harian yang dikirim otomatis ke grup."""
    day = day or now_local()
    header = f"Ringkasan ticket harian · {day.strftime('%d-%m-%Y')}"
    return summary_block(
        open_tickets,
        done_tickets,
        header,
        done_label="Sudah selesai / ditutup hari ini",
        note="Belum selesai = seluruh ticket yang masih berjalan. "
        "Selesai = ticket yang ditutup hari ini.",
    )


# ---------------------------------------------------------------------------
# Galat & validasi
# ---------------------------------------------------------------------------
def ticket_not_found(ticket_id: str) -> str:
    return f"🔎 Ticket *{ticket_id}* tidak ditemukan."


def format_error(usage: str, example: str = "") -> str:
    """Balasan saat format perintah salah."""
    lines = ["⚠️ Format perintah belum sesuai.", f"📝 Format: {usage}"]
    if example:
        lines.append(f"💡 Contoh: {example}")
    return "\n".join(lines)


def status_unknown(status: str, valid: list[str]) -> str:
    lines = [f"⚠️ Status *{status}* tidak dikenal."]
    lines.append(f"📌 Status tersedia: {', '.join(valid)}")
    return "\n".join(lines)


def status_transition_denied(current: str, target: str, allowed: list[str]) -> str:
    lines = [f"⚠️ Status {current} tidak bisa langsung ke {target}."]
    lines.append(f"➡️ Transisi yang diperbolehkan: {', '.join(allowed) if allowed else '(tidak ada)'}")
    return "\n".join(lines)


def ticket_final(ticket) -> str:
    """Ticket yang sudah final (tidak bisa diubah lagi)."""
    return (
        f"🔒 Ticket *{ticket.ticket_id}* sudah berstatus {ticket.status} "
        "dan tidak bisa diubah lagi."
    )


def unknown_command(prefix: str, name: str, help_text: str = "") -> str:
    head = f"❔ Perintah {prefix}{name} tidak dikenal."
    return f"{head}\n\n{help_text}" if help_text else head


def command_error(prefix: str, name: str, error: str) -> str:
    return f"⚠️ Gagal menjalankan {prefix}{name}: {error}"


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
    return f"Foto tersimpan 📷: {filename} ({_size_kb(size)})"


def media_failed(reason: str) -> str:
    """Balasan saat foto gagal diunduh/disimpan."""
    return f"⚠️ Foto gagal disimpan: {reason}"


def media_unavailable() -> str:
    """Balasan saat pesan punya media tapi tanpa tautan unduhan."""
    return "⚠️ Foto tidak bisa diproses: pesan tidak memuat tautan media."


def ticket_attachment_send_failed(ticket_id: str, filename: str) -> str:
    return f"⚠️ Lampiran {filename} dari ticket *{ticket_id}* gagal dikirim."


# ---------------------------------------------------------------------------
# Notifikasi lain
# ---------------------------------------------------------------------------
def dashboard_change(ticket_id: str, changes: list[str]) -> str:
    """Notifikasi perubahan ticket yang dilakukan dari dashboard."""
    lines = [f"🔔 Ticket *{ticket_id}* diperbarui dari dashboard:"]
    for item in changes:
        if item:
            lines.append(f"• {item}")
    return "\n".join(lines)
