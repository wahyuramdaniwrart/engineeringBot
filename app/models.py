"""Model database (SQLite via SQLAlchemy 2.0)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Ticket(Base):
    """Ticket / tugas engineering."""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    # Isi ticket
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, default="")
    ticket_type: Mapped[str] = mapped_column(String(32), default="TASK")
    priority: Mapped[str] = mapped_column(String(16), default="NORMAL")
    status: Mapped[str] = mapped_column(String(32), default="BARU", index=True)

    # Pelapor & asal
    reporter: Mapped[str] = mapped_column(String(128), default="")
    reporter_phone: Mapped[str] = mapped_column(String(32), default="")
    group_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    group_name: Mapped[str] = mapped_column(String(128), default="")

    # PIC / penyelesaian
    pic: Mapped[str] = mapped_column(String(128), default="")
    resolution: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="whatsapp")

    # Media (foto) yang dilampirkan
    media_filename: Mapped[str] = mapped_column(String(255), default="")
    media_path: Mapped[str] = mapped_column(String(512), default="")
    media_mimetype: Mapped[str] = mapped_column(String(128), default="")
    media_size: Mapped[int] = mapped_column(Integer, default=0)
    media_url: Mapped[str] = mapped_column(String(512), default="")
    media_caption: Mapped[str] = mapped_column(Text, default="")
    media_items: Mapped[list[TicketMedia]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketMedia.id",
        lazy="selectin",
    )

    # Waktu
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Sinkronisasi Google Sheets
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_open(self) -> bool:
        return self.status.strip().upper() not in (
            "SELESAI",
            "DIBATALKAN",
            "DONE",
            "COMPLETE",
            "COMPLETED",
            "CLOSED",
            "CANCELED",
            "CANCELLED",
        )

    @property
    def media_attachments(self) -> list[Ticket | TicketMedia]:
        items: list[Ticket | TicketMedia] = list(self.media_items)
        if self.media_path and all(
            item.media_path != self.media_path for item in items
        ):
            items.insert(0, self)
        return items


class TicketMedia(Base):
    """Satu lampiran media milik sebuah ticket."""

    __tablename__ = "ticket_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    media_filename: Mapped[str] = mapped_column(String(255), default="")
    media_path: Mapped[str] = mapped_column(String(512), default="")
    media_mimetype: Mapped[str] = mapped_column(String(128), default="")
    media_size: Mapped[int] = mapped_column(Integer, default=0)
    media_url: Mapped[str] = mapped_column(String(512), default="")
    media_caption: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="media_items")


class MessageLog(Base):
    """Audit semua pesan yang masuk (atau minimal pesan perintah)."""

    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    event: Mapped[str] = mapped_column(String(64), default="message")
    chat_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    chat_name: Mapped[str] = mapped_column(String(128), default="")
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    sender: Mapped[str] = mapped_column(String(128), default="")
    sender_phone: Mapped[str] = mapped_column(String(64), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    command: Mapped[str] = mapped_column(String(64), default="")
    is_command: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[str] = mapped_column(String(255), default="")
    ticket_ref: Mapped[str] = mapped_column(String(32), default="")
    raw: Mapped[str] = mapped_column(Text, default="")


class GroupConfig(Base):
    """Konfigurasi per grup WhatsApp."""

    __tablename__ = "group_configs"
    __table_args__ = (UniqueConstraint("chat_id", name="uq_group_chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    chat_name: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    prefixes: Mapped[str] = mapped_column(String(64), default="#,/")
    daily_summary: Mapped[bool] = mapped_column(Boolean, default=True)
    log_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Setting(Base):
    """Konfigurasi key/value yang bisa diubah dari dashboard."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WahaSession(Base):
    """Status session WAHA yang pernah dibuat."""

    __tablename__ = "waha_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="")
    last_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str] = mapped_column(Text, default="")
