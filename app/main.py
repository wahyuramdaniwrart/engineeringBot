"""Entry point aplikasi (FastAPI)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR
from .db import init_db
from .routers import web_router, webhook_router
from .scheduler import reschedule_daily, run_sheets_sync, start as start_scheduler
from .store import settings_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-10s %(message)s",
)
log = logging.getLogger("app")

app = FastAPI(title="Bot WhatsApp Engineering", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(webhook_router)
app.include_router(web_router)


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    settings_store.load()
    start_scheduler()
    # Sambungkan WAHA otomatis di background (gagal tidak boleh menghentikan app).
    asyncio.create_task(_prepare_waha())


async def _prepare_waha(attempts: int = 10, delay: float = 6.0) -> None:
    """Sambungkan WAHA otomatis: deteksi URL, buat session, daftarkan webhook."""
    from .waha_link import auto_connect

    for i in range(attempts):
        try:
            result = auto_connect()
            if result.get("ok"):
                log.info(
                    "WAHA tersambung otomatis: %s (session=%s, status=%s)",
                    result.get("base_url"),
                    result.get("session"),
                    result.get("status"),
                )
            else:
                log.warning("Auto-connect WAHA: %s", result.get("error"))
            return
        except Exception as exc:
            log.warning(
                "WAHA belum bisa dihubungi (%d/%d): %s", i + 1, attempts, exc
            )
            if i + 1 < attempts:
                await asyncio.sleep(delay)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    from .scheduler import shutdown

    shutdown()


@app.get("/health")
async def health():
    return {"ok": True, "service": "bot-whatsapp-engineering"}


@app.post("/admin/reschedule")
async def admin_reschedule():
    """Dipanggil dashboard setelah mengubah jam ringkasan."""
    reschedule_daily()
    return {"ok": True}
