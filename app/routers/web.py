"""Router: dashboard & API internal."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import delete, desc, func, select

from ..config import BASE_DIR, get_settings
from ..db import session_scope
from ..media import build_url, dashboard_media_url, media_file_path, media_root
from ..models import GroupConfig, MessageLog, Ticket, utcnow
from ..services import get_sheets, get_waha, reset_clients
from ..store import (
    count_by_status,
    format_local_datetime,
    list_tickets,
    now_local,
    settings_store,
    tickets_on,
)

log = logging.getLogger("web")

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["now"] = now_local
templates.env.filters["local_datetime"] = format_local_datetime


def gallery_items(attachments: Any) -> str:
    """Daftar foto ticket sebagai JSON untuk galeri di dashboard.

    Dipakai lewat `data-gallery='...'` supaya klik ID ticket langsung
    membuka galeri foto. Hanya URL root-relative (host browser yang dipakai),
    bukan URL publik absolut — halaman dashboard tidak boleh membocorkan
    alamat internal ke HTML.
    """
    items = []
    for photo in attachments or []:
        items.append(
            {
                "url": dashboard_media_url(photo.media_path),
                "name": photo.media_filename or "",
            }
        )
    return json.dumps(items)


templates.env.globals["gallery_items"] = gallery_items

serializer = URLSafeTimedSerializer(get_settings().secret_key, salt="bot-dashboard")
COOKIE_NAME = "bot_dashboard_session"


# ---------------------------------------------------------------------------
# Auth sederhana (password opsional)
# ---------------------------------------------------------------------------
def is_authenticated(request: Request) -> bool:
    pwd = get_settings().dashboard_password
    if not pwd:
        return True
    cookie = request.cookies.get(COOKIE_NAME, "")
    if not cookie:
        return False
    try:
        value = serializer.loads(cookie, max_age=60 * 60 * 24 * 7)
    except BadSignature:
        return False
    return value == "ok"


def require_auth(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


def login_page(request: Request, error: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request, "login.html", {"request": request, "error": error}, status_code=401
    )


def _ticket_media_paths(ticket: Ticket) -> set[str]:
    return {
        item.media_path for item in ticket.media_attachments if item.media_path
    }


def _delete_ticket_media(media_paths: set[str]) -> int:
    errors = 0
    root = media_root().resolve()
    for rel_path in media_paths:
        normalized = rel_path.replace("\\", "/").lstrip("/")
        if normalized.startswith("media/"):
            normalized = normalized[len("media/") :]
        parent = None
        try:
            target = media_file_path(normalized).resolve()
            relative = target.relative_to(root)
            if not relative.parts or (target.exists() and not target.is_file()):
                raise ValueError("Path media bukan berkas")
            if not target.exists():
                continue
            parent = target.parent
            target.unlink()
        except FileNotFoundError:
            pass
        except ValueError:
            errors += 1
            log.error("Menolak menghapus path media di luar folder media: %s", rel_path)
        except OSError as exc:
            errors += 1
            log.warning("Gagal menghapus berkas media %s: %s", rel_path, exc)
        if parent is not None and parent != root:
            try:
                parent.rmdir()
            except OSError:
                pass
    return errors


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if not get_settings().dashboard_password:
        return RedirectResponse("/", status_code=302)
    return login_page(request)


@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if password != get_settings().dashboard_password:
        return login_page(request, "Password salah.")
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        COOKIE_NAME,
        serializer.dumps("ok"),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if (r := require_auth(request)) is not None:
        return r
    with session_scope() as s:
        counts = count_by_status(s)
        statuses = settings_store.statuses
        recent = list_tickets(s, limit=10)
        open_count = sum(
            c for k, c in counts.items() if settings_store.is_open_status(k)
        )
        total = sum(counts.values())
        today = now_local().date()
        today_count = tickets_on(s, now_local())
        groups = s.execute(select(GroupConfig).order_by(GroupConfig.chat_name)).scalars().all()

    waha = get_waha()
    waha_status = ""
    waha_error = ""
    try:
        waha_status = waha.status_text()
    except Exception as exc:
        waha_error = str(exc)

    # Ringkasan lengkap WAHA (live + last known + status webhook).
    wstate = _waha_state()
    if not waha_status:
        waha_status = wstate.get("status") or ""
    if not waha_error:
        waha_error = wstate.get("error") or ""

    sheets = get_sheets()
    sheets_ok = sheets.ready

    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "request": request,
            "counts": counts,
            "statuses": statuses,
            "recent": recent,
            "total": total,
            "open_count": open_count,
            "today_count": len(today_count),
            "groups": groups,
            "waha_status": waha_status or "tidak terhubung",
            "waha_error": waha_error,
            "wstate": wstate,
            "webhook_url": settings_store.webhook_url(fallback=str(request.base_url)),
            "sheets_ok": sheets_ok,
            "sheets_error": sheets.last_error,
        },
    )


def _waha_state() -> dict[str, Any]:
    """Status WAHA untuk dashboard (live + fallback last known)."""
    try:
        from ..waha_link import session_state

        return session_state()
    except Exception as exc:  # pragma: no cover - dashboard tidak boleh gagal
        return {
            "ok": False,
            "status": "",
            "error": str(exc),
            "webhook_ok": False,
            "webhook_url": "",
            "session": "",
            "base_url": "",
            "last_check": "",
            "last_known": "",
            "auto_sync": True,
        }


# ---------------------------------------------------------------------------
# Ticket
# ---------------------------------------------------------------------------
@router.get("/tickets", response_class=HTMLResponse)
async def tickets_page(
    request: Request,
    status: str = "",
    q: str = "",
    group_id: str = "",
    deleted: int | None = None,
    logs_deleted: int = 0,
    media_errors: int = 0,
    delete_error: str = "",
    ticket_deleted: str = "",
    ticket_delete_error: str = "",
    reminder_updated: str = "",
    reminder_cancelled: str = "",
    reminder_error: str = "",
    reminder_ticket_id: str = "",
):
    if (r := require_auth(request)) is not None:
        return r
    with session_scope() as s:
        items = list_tickets(
            s, status=status or None, query=q or None, group_id=group_id or None, limit=200
        )
        groups = s.execute(select(GroupConfig)).scalars().all()
        statuses = settings_store.statuses
    return templates.TemplateResponse(
        request, "tickets.html",
        {
            "request": request,
            "tickets": items,
            "groups": groups,
            "statuses": statuses,
            "open_statuses": {
                item["key"] for item in statuses if item.get("open")
            },
            "f_status": status,
            "f_q": q,
            "f_group": group_id,
            "reminder_timezone": settings_store.timezone(),
            "media_url": dashboard_media_url,
            "sheet_media_url": build_url,
            "deleted": deleted,
            "logs_deleted": logs_deleted,
            "media_errors": media_errors,
            "delete_error": delete_error,
            "ticket_deleted": ticket_deleted,
            "ticket_delete_error": ticket_delete_error,
            "reminder_updated": reminder_updated,
            "reminder_cancelled": reminder_cancelled,
            "reminder_error": reminder_error,
            "reminder_ticket_id": reminder_ticket_id,
        },
    )


@router.post("/tickets/{ticket_id}/reminder")
async def ticket_set_reminder(
    request: Request,
    ticket_id: str,
    scheduled_at: str = Form(""),
    cancel: str = Form(""),
    return_status: str = Form(""),
    return_q: str = Form(""),
    return_group_id: str = Form(""),
):
    if (r := require_auth(request)) is not None:
        return r

    def redirect_to_list(**result: str):
        params = {
            key: value
            for key, value in (
                ("status", return_status),
                ("q", return_q),
                ("group_id", return_group_id),
            )
            if value
        }
        params.update({key: value for key, value in result.items() if value})
        return RedirectResponse(
            "/tickets?" + urlencode(params), status_code=303
        )

    from ..reminders import clear_ticket_reminder, parse_reminder_datetime

    with session_scope() as s:
        ticket = s.execute(
            select(Ticket).where(func.lower(Ticket.ticket_id) == ticket_id.lower())
        ).scalar_one_or_none()
        if ticket is None:
            return redirect_to_list(
                reminder_error="not_found", reminder_ticket_id=ticket_id
            )

        if cancel:
            if ticket.reminder_at is None:
                return redirect_to_list(
                    reminder_error="not_set",
                    reminder_ticket_id=ticket.ticket_id,
                )
            clear_ticket_reminder(ticket)
            return redirect_to_list(reminder_cancelled=ticket.ticket_id)

        if not settings_store.is_open_status(ticket.status):
            return redirect_to_list(
                reminder_error="closed", reminder_ticket_id=ticket.ticket_id
            )
        if not ticket.group_id:
            return redirect_to_list(
                reminder_error="no_chat", reminder_ticket_id=ticket.ticket_id
            )

        try:
            ticket.reminder_at = parse_reminder_datetime(
                scheduled_at.strip().replace("T", " ")
            )
        except ValueError:
            return redirect_to_list(
                reminder_error="invalid", reminder_ticket_id=ticket.ticket_id
            )
        ticket.reminder_retry_at = None
        ticket.reminder_last_sent_at = None
        updated_ticket_id = ticket.ticket_id

    return redirect_to_list(reminder_updated=updated_ticket_id)


@router.post("/tickets/delete-all")
async def delete_all_tickets(request: Request, confirmation: str = Form("")):
    if (r := require_auth(request)) is not None:
        return r
    if confirmation.strip() != "HAPUS SEMUA TICKET":
        return RedirectResponse(
            "/tickets?delete_error=confirmation", status_code=303
        )

    media_paths: set[str] = set()
    with session_scope() as s:
        tickets = s.execute(select(Ticket)).scalars().all()
        ticket_ids = [ticket.ticket_id for ticket in tickets]
        logs_deleted = 0
        if ticket_ids:
            result = s.execute(
                delete(MessageLog).where(MessageLog.ticket_ref.in_(ticket_ids))
            )
            logs_deleted = result.rowcount or 0
        for ticket in tickets:
            media_paths.update(_ticket_media_paths(ticket))
            s.delete(ticket)
        deleted = len(tickets)

    media_errors = _delete_ticket_media(media_paths)

    return RedirectResponse(
        f"/tickets?deleted={deleted}&logs_deleted={logs_deleted}"
        f"&media_errors={media_errors}",
        status_code=303,
    )


@router.post("/tickets/{ticket_id}/delete")
async def delete_ticket(
    request: Request, ticket_id: str, confirmation: str = Form("")
):
    if (r := require_auth(request)) is not None:
        return r
    media_paths: set[str] = set()
    with session_scope() as s:
        ticket = s.execute(
            select(Ticket).where(func.lower(Ticket.ticket_id) == ticket_id.lower())
        ).scalar_one_or_none()
        if ticket is None:
            return RedirectResponse(
                "/tickets?ticket_delete_error=not_found", status_code=303
            )
        if confirmation.strip() != ticket.ticket_id:
            return RedirectResponse(
                "/tickets?ticket_delete_error=confirmation", status_code=303
            )
        deleted_ticket = ticket.ticket_id
        media_paths = _ticket_media_paths(ticket)
        result = s.execute(
            delete(MessageLog).where(MessageLog.ticket_ref == ticket.ticket_id)
        )
        logs_deleted = result.rowcount or 0
        s.delete(ticket)

    media_errors = _delete_ticket_media(media_paths)
    return RedirectResponse(
        f"/tickets?ticket_deleted={deleted_ticket}"
        f"&logs_deleted={logs_deleted}&media_errors={media_errors}",
        status_code=303,
    )


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail(request: Request, ticket_id: str):
    if (r := require_auth(request)) is not None:
        return r
    with session_scope() as s:
        ticket = s.execute(
            select(Ticket).where(func.lower(Ticket.ticket_id) == ticket_id.lower())
        ).scalar_one_or_none()
        if ticket is None:
            return templates.TemplateResponse(
                request, "ticket_detail.html",
                {"request": request, "ticket": None, "statuses": settings_store.statuses},
                status_code=404,
            )
        history = s.execute(
            select(MessageLog)
            .where(MessageLog.ticket_ref == ticket.ticket_id)
            .order_by(desc(MessageLog.created_at))
            .limit(50)
        ).scalars().all()
    return templates.TemplateResponse(
        request, "ticket_detail.html",
        {
            "request": request,
            "ticket": ticket,
            "history": history,
            "statuses": settings_store.statuses,
            "allowed": settings_store.allowed_transitions(ticket.status),
            "media_url": dashboard_media_url,
            "sheet_media_url": build_url,
        },
    )


@router.post("/tickets/{ticket_id}/status")
async def ticket_set_status(
    request: Request,
    background_tasks: BackgroundTasks,
    ticket_id: str,
    status: str = Form(...),
    note: str = Form(""),
):
    if (r := require_auth(request)) is not None:
        return r
    status = status.upper()
    changes: list[str] = []
    with session_scope() as s:
        ticket = s.execute(
            select(Ticket).where(func.lower(Ticket.ticket_id) == ticket_id.lower())
        ).scalar_one_or_none()
        if ticket is None:
            return JSONResponse({"ok": False, "error": "ticket tidak ditemukan"}, 404)
        if status not in settings_store.status_keys():
            return JSONResponse({"ok": False, "error": "status tidak valid"}, 400)
        old_status = ticket.status
        ticket.status = status
        if settings_store.is_open_status(status):
            ticket.closed_at = None
        else:
            ticket.closed_at = utcnow()
            ticket.reminder_at = None
            ticket.reminder_retry_at = None
        note = note.strip()
        if note:
            ticket.resolution = note
        changes.append(f"Status: {old_status} → {status}")
        if note:
            changes.append(f"Catatan: {note}")
        s.flush()
        tid = ticket.ticket_id

    # Sinkron Sheets + notifikasi grup dijalankan di background supaya halaman
    # langsung merespon (tidak menunggu koneksi Sheets/WhatsApp).
    background_tasks.add_task(_notify_ticket_update, tid, changes)
    return RedirectResponse(
        f"/tickets/{ticket_id.lower()}?saved=status", status_code=303
    )


@router.post("/tickets/{ticket_id}/pic")
async def ticket_set_pic(
    request: Request,
    background_tasks: BackgroundTasks,
    ticket_id: str,
    pic: str = Form(...),
):
    if (r := require_auth(request)) is not None:
        return r
    with session_scope() as s:
        ticket = s.execute(
            select(Ticket).where(func.lower(Ticket.ticket_id) == ticket_id.lower())
        ).scalar_one_or_none()
        if ticket is None:
            return JSONResponse({"ok": False}, 404)
        old_pic = ticket.pic
        ticket.pic = pic
        s.flush()
        tid = ticket.ticket_id

    background_tasks.add_task(_notify_ticket_update, tid, [f"PIC: {old_pic or '-'} → {pic}"])
    return RedirectResponse(
        f"/tickets/{ticket_id.lower()}?saved=pic", status_code=303
    )


def _notify_ticket_update(ticket_id: str, changes: list[str]) -> None:
    """Sinkron Sheets + kabari grup (background, tidak menahan response)."""
    from ..handlers import notify_ticket_change, sync_ticket_to_sheets

    try:
        with session_scope() as s:
            sync_ticket_to_sheets(s, ticket_id)
            ticket = s.execute(
                select(Ticket).where(func.lower(Ticket.ticket_id) == ticket_id.lower())
            ).scalar_one_or_none()
            if ticket is not None:
                notify_ticket_change(ticket, changes)
    except Exception as exc:  # pragma: no cover - background task tidak boleh gagal diam-diam
        log.warning("Gagal sinkron/notifikasi ticket %s: %s", ticket_id, exc)


# ---------------------------------------------------------------------------
# Grup
# ---------------------------------------------------------------------------
@router.get("/media/{rel_path:path}")
async def serve_media(request: Request, rel_path: str, t: str = "", download: int = 0):
    """Sajikan berkas media (foto ticket).

    Akses bisa lewat dua cara:

    * **token** (``?t=...``) - untuk tautan di Google Sheets, tanpa login
    * **login dashboard** - untuk dari halaman dashboard

    Berkas disimpan di folder data (volume), bukan di dalam image, jadi
    tetap ada meski container dibangun ulang.
    """
    from ..media import media_file_path, valid_token

    full_rel = f"media/{rel_path.lstrip('/')}"
    if not valid_token(full_rel, t):
        if (r := require_auth(request)) is not None:
            return r
    try:
        target = media_file_path(rel_path)
    except ValueError:
        return JSONResponse({"ok": False, "error": "Path tidak valid"}, 400)
    if not target.is_file():
        return JSONResponse({"ok": False, "error": "Berkas tidak ditemukan"}, 404)
    disposition = "attachment" if download else "inline"
    return FileResponse(
        target,
        headers={"Content-Disposition": f'{disposition}; filename="{target.name}"'},
    )


@router.get("/groups", response_class=HTMLResponse)
async def groups_page(request: Request, deleted: int = 0):
    if (r := require_auth(request)) is not None:
        return r
    with session_scope() as s:
        groups = s.execute(select(GroupConfig).order_by(GroupConfig.chat_id)).scalars().all()
        counts = count_by_status(s)
    return templates.TemplateResponse(
        request, "groups.html",
        {
            "request": request,
            "groups": groups,
            "counts": counts,
            "deleted": deleted,
        },
    )


@router.post("/groups/{chat_id}/toggle")
async def group_toggle(request: Request, chat_id: str):
    if (r := require_auth(request)) is not None:
        return r
    with session_scope() as s:
        grp = s.execute(
            select(GroupConfig).where(GroupConfig.chat_id == chat_id)
        ).scalar_one_or_none()
        if grp is None:
            return JSONResponse({"ok": False}, 404)
        grp.enabled = not grp.enabled
    return RedirectResponse("/groups", status_code=303)


@router.post("/groups/{chat_id}/update")
async def group_update(
    request: Request,
    chat_id: str,
    enabled: str = Form(""),
    prefixes: str = Form(""),
    daily_summary: str = Form(""),
    log_messages: str = Form(""),
    note: str = Form(""),
):
    if (r := require_auth(request)) is not None:
        return r
    with session_scope() as s:
        grp = s.execute(
            select(GroupConfig).where(GroupConfig.chat_id == chat_id)
        ).scalar_one_or_none()
        if grp is None:
            return JSONResponse({"ok": False}, 404)
        grp.enabled = enabled == "on"
        grp.prefixes = prefixes.strip()
        grp.daily_summary = daily_summary == "on"
        grp.log_messages = log_messages == "on"
        grp.note = note.strip()
    return RedirectResponse("/groups", status_code=303)


@router.post("/groups/{chat_id}/delete")
async def group_delete(request: Request, chat_id: str):
    if (r := require_auth(request)) is not None:
        return r
    with session_scope() as s:
        result = s.execute(delete(GroupConfig).where(GroupConfig.chat_id == chat_id))
        if not result.rowcount:
            return JSONResponse({"ok": False}, 404)
    return RedirectResponse("/groups?deleted=1", status_code=303)


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------
@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, q: str = "", only_cmd: str = ""):
    if (r := require_auth(request)) is not None:
        return r
    with session_scope() as s:
        stmt = select(MessageLog).order_by(desc(MessageLog.created_at)).limit(300)
        if q:
            stmt = stmt.where(MessageLog.body.ilike(f"%{q}%"))
        if only_cmd == "on":
            stmt = stmt.where(MessageLog.is_command.is_(True))
        logs = s.execute(stmt).scalars().all()
    return templates.TemplateResponse(
        request, "logs.html", {"request": request, "logs": logs, "f_q": q, "f_cmd": only_cmd}
    )


# ---------------------------------------------------------------------------
# Pengaturan
# ---------------------------------------------------------------------------
@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if (r := require_auth(request)) is not None:
        return r
    base = settings_store.public_url() or str(request.base_url).rstrip("/")
    webhook_url = settings_store.webhook_url(fallback=base)

    # Status live WAHA & Sheets supaya tombol di halaman ini punya konteks
    # (mis. "Tampilkan QR" hanya berguna saat session sudah ada).
    waha = get_waha()
    waha_status = ""
    waha_error = ""
    try:
        waha_status = waha.status_text()
    except Exception as exc:
        waha_error = str(exc)
    wstate = _waha_state()
    if not waha_status:
        waha_status = wstate.get("status") or ""
    if not waha_error:
        waha_error = wstate.get("error") or ""
    sheets = get_sheets()

    return templates.TemplateResponse(
        request, "settings.html",
        {
            "request": request,
            "settings": settings_store.all(),
            "commands": settings_store.commands,
            "statuses": settings_store.statuses,
            "webhook_url": webhook_url,
            "waha_status": waha_status or "tidak terhubung",
            "waha_error": waha_error,
            "wstate": wstate,
            "sheets_ok": sheets.ready,
            "sheets_error": sheets.last_error,
            "env": {
                "waha_base_url": get_settings().waha_base_url,
                "waha_session": get_settings().waha_session,
                "google_sa_file": get_settings().google_sa_file,
                "public_url": get_settings().public_url,
                "waha_api_key_configured": bool(get_settings().waha_api_key),
                "webhook_token_configured": bool(get_settings().webhook_token),
                "spreadsheet_id": get_settings().spreadsheet_id,
            },
        },
    )


def _collect_commands(form: dict[str, Any]) -> list[dict[str, Any]]:
    """Bentuk data perintah dari form (prefix ``cmd_``)."""
    names = form.getlist("cmd_name") if hasattr(form, "getlist") else []
    aliases = form.getlist("cmd_aliases") if hasattr(form, "getlist") else []
    actions = form.getlist("cmd_action") if hasattr(form, "getlist") else []
    usages = form.getlist("cmd_usage") if hasattr(form, "getlist") else []
    descs = form.getlist("cmd_description") if hasattr(form, "getlist") else []

    out: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        name = (name or "").strip()
        if not name:
            continue
        alias_raw = aliases[i] if i < len(aliases) else ""
        out.append(
            {
                "name": name.lower(),
                "aliases": [a.strip().lower() for a in alias_raw.replace(";", ",").split(",") if a.strip()],
                "description": descs[i] if i < len(descs) else "",
                "usage": usages[i] if i < len(usages) else "",
                "action": actions[i] if i < len(actions) else "unknown",
                "enabled": form.get(f"cmd_enabled_{i}") == "on",
                "group_only": False,
            }
        )
    return out


def _collect_statuses(form: dict[str, Any]) -> list[dict[str, Any]]:
    keys = form.getlist("st_key") if hasattr(form, "getlist") else []
    labels = form.getlist("st_label") if hasattr(form, "getlist") else []
    trans = form.getlist("st_transitions") if hasattr(form, "getlist") else []
    colors = form.getlist("st_color") if hasattr(form, "getlist") else []
    out: list[dict[str, Any]] = []
    for i, key in enumerate(keys):
        key = (key or "").strip().upper()
        if not key:
            continue
        trans_raw = trans[i] if i < len(trans) else ""
        out.append(
            {
                "key": key,
                "label": labels[i] if i < len(labels) else key,
                "open": form.get(f"st_open_{i}") == "on",
                "transitions": [
                    t.strip().upper()
                    for t in trans_raw.replace(";", ",").split(",")
                    if t.strip()
                ],
                "color": colors[i] if i < len(colors) else "#e2e8f0",
                "text_color": "#0f172a",
            }
        )
    return out


@router.post("/settings")
async def settings_save(request: Request):
    if (r := require_auth(request)) is not None:
        return r
    form = await request.form()
    prefix_raw = str(form.get("bot_prefixes") or "#,/").replace(";", ",")
    prefixes = [p.strip() for p in prefix_raw.split(",") if p.strip()]

    data: dict[str, Any] = {
        "bot.prefixes": prefixes or ["#"],
        "bot.timezone": str(form.get("bot_timezone") or "Asia/Makassar"),
        "bot.reply_in_group": form.get("bot_reply_in_group") == "on",
        "bot.log_all_messages": form.get("bot_log_all_messages") == "on",
        "bot.daily_summary_enabled": form.get("bot_daily_summary_enabled") == "on",
        "bot.daily_summary_hour": int(
            form.get("bot_daily_summary_hour")
            if form.get("bot_daily_summary_hour") not in (None, "")
            else 17
        ),
        "bot.daily_summary_minute": int(form.get("bot_daily_summary_minute") or 0),
        "bot.ticket_prefix": (str(form.get("bot_ticket_prefix") or "tkt")).strip().lower(),
        "bot.notify_dashboard_changes": form.get("bot_notify_dashboard_changes") == "on",
        "bot.ignore_own_messages": form.get("bot_ignore_own_messages") == "on",
        "bot.public_url": str(form.get("bot_public_url") or "").strip().rstrip("/"),
        "waha.base_url": str(form.get("waha_base_url") or "").rstrip("/"),
        "waha.api_key": str(form.get("waha_api_key") or ""),
        "waha.session": str(form.get("waha_session") or "engineering"),
        "waha.webhook_token": str(form.get("waha_webhook_token") or ""),
        "waha.hmac_key": str(form.get("waha_hmac_key") or ""),
        "waha.public_url": str(form.get("waha_public_url") or "").rstrip("/"),
        "waha.auto_sync": form.get("waha_auto_sync") == "on",
        "waha.sync_interval": max(10, int(form.get("waha_sync_interval") or 30)),
        "sheet.spreadsheet_id": str(form.get("sheet_spreadsheet_id") or ""),
        "sheet.credentials_file": str(form.get("sheet_credentials_file") or ""),
        "sheet.credentials_json": str(form.get("sheet_credentials_json") or "").strip(),
        "sheet.enabled": form.get("sheet_enabled") == "on",
        "sheet.auto_sync": form.get("sheet_auto_sync") == "on",
        "commands": _collect_commands(form),
        "statuses": _collect_statuses(form),
    }
    settings_store.set_many(data)
    reset_clients()
    from ..scheduler import reschedule_daily, reschedule_waha_sync

    reschedule_daily()
    reschedule_waha_sync()
    return RedirectResponse("/settings?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# API JSON
# ---------------------------------------------------------------------------
@router.get("/api/stats")
async def api_stats():
    with session_scope() as s:
        counts = count_by_status(s)
        total = sum(counts.values())
        today_count = len(tickets_on(s, now_local()))
    return {
        "total": total,
        "today": today_count,
        "by_status": counts,
        "statuses": settings_store.statuses,
    }


@router.get("/api/tickets")
async def api_tickets(status: str = "", q: str = "", limit: int = 50):
    with session_scope() as s:
        items = list_tickets(
            s, status=status or None, query=q or None, limit=min(limit, 200)
        )
        return [
            {
                "ticket_id": t.ticket_id,
                "issue": t.detail or t.title,
                "status": t.status,
                "status_label": settings_store.status_label(t.status),
                "priority": t.priority,
                "pic": t.pic,
                "reporter": t.reporter,
                "group": t.group_name or t.group_id,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                "reminder_at": t.reminder_at.isoformat() if t.reminder_at else None,
            }
            for t in items
        ]


@router.get("/api/settings")
async def api_settings():
    """Semua konfigurasi kecuali rahasia."""
    data = dict(settings_store.all())
    for key in ("waha.api_key", "waha.webhook_token", "sheet.credentials_json"):
        if key in data:
            data[key] = "***"
    return data


@router.post("/api/waha/start")
async def api_waha_start():
    try:
        waha = get_waha()
        res = waha.ensure_session()
        return {"ok": True, "result": res}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 500)


@router.get("/api/waha/status")
@router.post("/api/waha/status")
async def api_waha_status():
    """Status WAHA + status sinkronisasi webhook (dipakai auto-refresh dashboard)."""
    try:
        waha = get_waha()
        state = _waha_state()
        state["sessions"] = waha.list_sessions()
        state["ok"] = bool(state.get("ok")) and not state.get("error")
        return state
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 500)


@router.post("/api/waha/connect")
async def api_waha_connect():
    """Satu klik: deteksi WAHA -> buat/start session -> daftarkan webhook."""
    try:
        from ..waha_link import auto_connect

        result = auto_connect()
        state = _waha_state()
        result.update(
            {
                "webhook_ok": state.get("webhook_ok"),
                "webhook_error": state.get("webhook_error"),
                "checked_at": state.get("checked_at"),
            }
        )
        return result
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 500)


@router.post("/api/waha/sync")
async def api_waha_sync():
    """Sinkronkan sekarang: cek status + perbaiki webhook bila perlu."""
    try:
        from ..waha_link import auto_connect

        result = auto_connect()
        state = _waha_state()
        result.update(
            {
                "webhook_ok": state.get("webhook_ok"),
                "webhook_error": state.get("webhook_error"),
                "checked_at": state.get("checked_at"),
            }
        )
        return result
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 500)


@router.get("/api/waha/detect")
async def api_waha_detect():
    """Cari base URL WAHA yang bisa dijangkau (dari server aplikasi)."""
    try:
        from ..waha_link import detect_base_url

        url, error = detect_base_url(settings_store.waha_api_key())
        return {"ok": bool(url), "url": url, "error": error}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 500)


@router.post("/api/waha/webhook")
async def api_waha_webhook(url: str = Form("")):
    """Daftarkan webhook ke WAHA (biar pesan masuk terkirim ke aplikasi).

    Bila `url` kosong, URL disusun dari konfigurasi (public URL + token).
    """
    try:
        from ..waha_link import sync_webhook

        result = sync_webhook()
        waha = get_waha()
        return {
            "ok": bool(result.get("ok")),
            "result": result,
            "error": result.get("error", ""),
            "webhooks": waha.list_webhooks(),
        }
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 500)


@router.get("/api/waha/webhooks")
async def api_waha_webhooks():
    """Daftar webhook yang terdaftar di session WAHA."""
    try:
        waha = get_waha()
        return {"ok": True, "webhooks": waha.list_webhooks()}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 500)


@router.post("/api/waha/webhooks/clean")
async def api_waha_webhooks_clean(request: Request):
    """Hapus webhook yang URL-nya bukan URL kita (mis. 127.0.0.1)."""
    try:
        waha = get_waha()
        base = settings_store.public_url() or str(request.base_url).rstrip("/")
        current = settings_store.webhook_url(fallback=base)
        removed: list[str] = []
        for hook in waha.list_webhooks():
            url = str(hook.get("url") or "")
            if url and url != current:
                waha.remove_webhook(url)
                removed.append(url)
        return {"ok": True, "removed": removed, "webhooks": waha.list_webhooks()}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 500)


@router.post("/api/waha/restart")
async def api_waha_restart():
    """Restart session WAHA (berguna saat QR expired / perlu scan ulang)."""
    try:
        waha = get_waha()
        res = waha.restart_session()
        return {"ok": True, "result": res}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 500)


@router.get("/api/waha/qr")
async def api_waha_qr():
    """QR pairing WhatsApp (dikirim ke browser, tidak disimpan di server)."""
    try:
        waha = get_waha()
        data = waha.get_qr()
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 200)
    qr = str(data.get("qr") or "")
    mimetype = str(data.get("mimetype") or "image/png")
    return {
        "ok": bool(qr),
        "qr": qr,
        "mimetype": mimetype,
        "status": waha.status_text(),
        "error": "" if qr else "QR belum tersedia (status session: %s)" % waha.status_text(),
    }


@router.post("/api/sheets/test")
async def api_sheets_test():
    sheets = get_sheets()
    ok = sheets.connect()
    return {"ok": ok, "error": sheets.last_error}


@router.post("/api/sheets/sync")
async def api_sheets_sync():
    """Push ulang semua ticket ke Google Sheets."""
    sheets = get_sheets()
    if not sheets.connect():
        return JSONResponse({"ok": False, "error": sheets.last_error}, 500)
    tickets_tab, _, _ = settings_store.sheet_tabs()
    with session_scope() as s:
        items = list_tickets(s, limit=1000)
        count = sheets.sync_all_tickets(items, tickets_tab)
    return {"ok": True, "synced": count}


@router.get("/api/public-url/detect")
async def api_public_url_detect():
    """Deteksi alamat server yang bisa dipakai untuk tautan foto.

    Catatan: aplikasi berjalan di dalam container Docker, jadi yang terdeteksi
    adalah IP **internal** (jaringan bridge Docker). Rekan tim di jaringan LAN
    butuh IP LAN *host* - klik "Deteksi" lalu perbaiki manual bila perlu.
    """
    import socket

    hosts: list[str] = []
    try:
        # Trik umum: buka socket UDP ke luar, lalu baca alamat lokal.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.connect(("8.8.8.8", 80))
            hosts.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        # Gateway default = interface host pada jaringan bridge Docker.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.connect(("8.8.8.8", 80))
            gateway = sock.getsockname()[0]
            with open("/proc/net/route", encoding="utf-8") as fh:
                for line in fh.readlines()[1:]:
                    parts = line.split()
                    if len(parts) > 2 and parts[1] == "00000000":
                        raw = int(parts[2], 16)
                        hosts.append(
                            ".".join(str((raw >> shift) & 0xFF) for shift in (0, 8, 16, 24))
                        )
                        break
        _ = gateway
    except OSError:
        pass

    unique = [h for h in dict.fromkeys(hosts) if h]
    if not unique:
        return {
            "ok": False,
            "error": "Alamat jaringan tidak terdeteksi - isi manual IP LAN host",
        }
    port = get_settings().port
    return {
        "ok": True,
        "url": f"http://{unique[0]}:{port}",
        "candidates": [f"http://{h}:{port}" for h in unique],
        "note": (
            "IP di atas bersifat internal container. Untuk tautan yang bisa "
            "dibuka rekan tim, isi manual IP LAN host (cek dengan ipconfig)."
        ),
    }


@router.get("/api/health")
async def api_health():
    return {"ok": True, "time": now_local().isoformat()}
