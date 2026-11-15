"""Koneksi database & session."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            future=True,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            # WAL tidak boleh dipakai di sini: database-nya berada di bind-mount
            # Docker Desktop (Windows), dan file -wal/-shm di mount itu bisa
            # "hilang"/tidak sinkron -> database jadi "malformed"
            # (dilihat 2026-11-15: index settings rusak, dashboard gagal simpan).
            # Rollback journal (DELETE) aman untuk bind-mount ini.
            cur.execute("PRAGMA journal_mode=DELETE")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def init_db() -> None:
    """Buat tabel bila belum ada, lalu jalankan migrasi ringan."""
    get_engine()
    assert _engine is not None
    Base.metadata.create_all(_engine)
    # Kolom baru & perbaikan data tidak otomatis dilakukan oleh create_all.
    from .migrate import run_migrations

    run_migrations(_engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """Dependency FastAPI."""
    with session_scope() as s:
        yield s
