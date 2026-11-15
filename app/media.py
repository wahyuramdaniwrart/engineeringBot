"""Penyimpanan media (foto) yang dilampirkan pada sebuah ticket.

Alur:

1. WAHA mengirim event pesan berisi media (foto/video/dokumen).
2. :meth:`app.waha.WahaClient.download_media` mengunduh berkasnya.
3. Modul ini menyimpannya ke ``<data_dir>/media/<TICKET-ID>/`` supaya
   ter-mount di host (folder ``data/`` sudah berupa volume).
4. Datanya dicatat di tabel ``tickets`` (nama file, path, ukuran, tipe).
5. Kolom foto ikut ditulis ke Google Sheets.

Media tidak pernah disimpan di dalam image Docker, jadi tetap aman meski
container dibangun ulang.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from .config import get_settings
from .store import now_local, settings_store

log = logging.getLogger("media")

MEDIA_DIRNAME = "media"
# Batas ukuran unduhan (10 MB) - cukup untuk foto WhatsApp, mencegah
# file raksasa/https membebani server.
MAX_MEDIA_BYTES = 10 * 1024 * 1024

_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "application/pdf": ".pdf",
}


def media_root() -> Path:
    """Folder akar media (dalam container: /data/media)."""
    return Path(get_settings().data_dir) / MEDIA_DIRNAME


def safe_filename(name: str, fallback: str = "file") -> str:
    """Bersihkan nama file dari karakter berbahaya / panjang."""
    raw = (name or "").strip()
    raw = raw.replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > 80:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and len(ext) <= 6:
            cleaned = stem[: 80 - len(ext) - 1] + "." + ext
        else:
            cleaned = cleaned[:80]
    return cleaned


def guess_extension(mimetype: str, filename: str = "") -> str:
    """Ekstensi berkas: dari nama file dulu, lalu dari mimetype."""
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        if 2 < len(ext) <= 6 and ext.replace(".", "").isalnum():
            return ext
    return _EXT_BY_MIME.get((mimetype or "").split(";")[0].strip().lower(), "")


def save_media(
    ticket_id: str,
    data: bytes,
    filename: str = "",
    mimetype: str = "",
) -> dict[str, Any]:
    """Simpan media ke disk.

    Mengembalikan dict berisi ``path`` (relatif terhadap folder data),
    ``filename``, ``mimetype`` dan ``size``. Melempar ``ValueError`` bila
    data kosong atau terlalu besar.
    """
    if not data:
        raise ValueError("Berkas media kosong")
    if len(data) > MAX_MEDIA_BYTES:
        raise ValueError(
            f"Berkas terlalu besar ({len(data) // 1024} KB, "
            f"maksimum {MAX_MEDIA_BYTES // 1024} KB)"
        )

    ext = guess_extension(mimetype, filename)
    stem = safe_filename(filename or "foto", "foto")
    if ext and not stem.lower().endswith(ext):
        stem += ext

    stamp = now_local().strftime("%Y%m%d-%H%M%S")
    folder = media_root() / (safe_filename(ticket_id, "ticket") or "ticket")
    folder.mkdir(parents=True, exist_ok=True)

    final_name = f"{stamp}-{stem}"
    target = folder / final_name
    counter = 1
    while target.exists():
        target = folder / f"{stamp}-{counter}-{stem}"
        counter += 1

    target.write_bytes(data)
    rel_path = f"{MEDIA_DIRNAME}/{folder.name}/{target.name}"
    log.info(
        "Media tersimpan: %s (%d bytes, %s)", rel_path, len(data), mimetype or "?"
    )
    return {
        "path": rel_path,
        "filename": target.name,
        "mimetype": mimetype or "",
        "size": len(data),
    }


def shared_media_path(url: str) -> Path | None:
    """Cari berkas media di volume bersama (fallback bila HTTP gagal).

    WAHA menyimpan media di ``/waha/media/<session>/<nama>`` yang ter-mount
    dari ``./data/waha`` di host. Container aplikasi pun me-mount ``./data``,
    jadi berkas yang sama bisa dibaca langsung tanpa HTTP:

    ``http://waha:3000/api/files/engineering/abc.jpeg``
        -> ``<data_dir>/waha/engineering/abc.jpeg``
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if "files" not in segments:
        return None
    rest = segments[segments.index("files") + 1 :]
    if len(rest) < 2:
        return None
    session = rest[0]
    filename = Path(rest[-1]).name  # aman dari path traversal
    if not filename:
        return None
    candidate = Path(get_settings().data_dir) / "waha" / session / filename
    try:
        if candidate.is_file():
            return candidate
    except OSError:  # pragma: no cover - filesystem
        return None
    return None


def absolute_path(rel_path: str) -> Path:
    """Ubah path relatif menjadi path absolut di dalam folder data."""
    root = Path(get_settings().data_dir).resolve()
    target = (root / rel_path).resolve()
    # Pengaman: pastikan hasilnya tetap di dalam folder data.
    if not str(target).startswith(str(root)):
        raise ValueError("Path media di luar folder data")
    return target


def media_file_path(name: str) -> Path:
    """Path absolut berkas di dalam folder media.

    ``name`` adalah path relatif **tanpa** prefix ``media/`` - sama dengan
    yang dipakai pada URL ``/media/<name>``.
    """
    cleaned = (name or "").lstrip("/")
    return absolute_path(f"{MEDIA_DIRNAME}/{cleaned}")


def url_path(rel_path: str) -> str:
    """Path relatif tanpa prefix "media/" - dipakai untuk URL /media/..."""
    prefix = MEDIA_DIRNAME + "/"
    if rel_path.startswith(prefix):
        return rel_path[len(prefix) :]
    return rel_path


# ---------------------------------------------------------------------------
# URL publik & token
# ---------------------------------------------------------------------------
def sign_path(rel_path: str) -> str:
    """Token HMAC untuk sebuah path media.

    Token membuat tautan foto bisa dibuka langsung dari Google Sheets
    tanpa perlu login dashboard, tanpa membuat media jadi publik. Orang
    lain tetap harus punya tautannya.
    """
    secret = get_settings().secret_key or "ganti-secret-key-ini"
    digest = hmac.new(
        secret.encode("utf-8"), rel_path.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest[:24]


def valid_token(rel_path: str, token: str) -> bool:
    """True bila token cocok dengan path tersebut."""
    if not token:
        return False
    return hmac.compare_digest(sign_path(rel_path), token)


def public_base() -> str:
    """Alamat server yang bisa dijangkau orang lain (untuk tautan Sheets)."""
    base = settings_store.media_public_url or settings_store.public_url()
    return str(base or "").rstrip("/")


def build_url(rel_path: str, with_token: bool = True) -> str:
    """URL lengkap media yang siap ditempel di Google Sheets."""
    base = public_base()
    if not base:
        return rel_path
    url = f"{base}/media/{url_path(rel_path)}"
    if with_token:
        url += f"?t={sign_path(rel_path)}"
    return url


def dashboard_media_url(rel_path: str) -> str:
    """URL media root-relative agar gambar dashboard memakai host browser saat ini."""
    full_path = rel_path
    if not full_path.startswith(MEDIA_DIRNAME + "/"):
        full_path = f"{MEDIA_DIRNAME}/{full_path.lstrip('/')}"
    return f"/media/{quote(url_path(full_path), safe='/')}?t={sign_path(full_path)}"


def display_url(ticket) -> str:
    """URL foto milik sebuah ticket (dihitung ulang, selalu up-to-date)."""
    path = getattr(ticket, "media_path", "") or ""
    if not path:
        return ""
    return build_url(path)


def public_url(rel_path: str) -> str:
    """URL publik untuk media (dipakai di Google Sheets).

    Bila alamat publik belum dikonfigurasi, dikembalikan path relatif
    supaya tetap informatif di spreadsheet.
    """
    base = public_base()
    if not base:
        return rel_path
    return f"{base}/media/{url_path(rel_path)}"


def attach_to_ticket(ticket, info: dict[str, Any], url: str = "") -> None:
    """Simpan lampiran pertama di kolom lama agar integrasi tetap kompatibel."""
    if getattr(ticket, "media_path", ""):
        return
    ticket.media_filename = info.get("filename", "")
    ticket.media_path = info.get("path", "")
    ticket.media_mimetype = info.get("mimetype", "")
    ticket.media_size = int(info.get("size", 0) or 0)
    ticket.media_url = url or build_url(info.get("path", ""))
    if not ticket.media_caption:
        ticket.media_caption = info.get("caption", "")


def human_size(size: int) -> str:
    """Ukuran berkas yang enak dibaca manusia."""
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


# ---------------------------------------------------------------------------
# Foto langsung di dalam sel Google Sheets
# ---------------------------------------------------------------------------
# Host yang jelas-jelas internal; tidak akan pernah bisa dipakai.
INTERNAL_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "app",
        "waha",
        "host.docker.internal",
    }
)

# Host yang hanya internal kalau digabung dengan port tertentu
# (mis. "app:8080" = container aplikasi, "waha:3000" = container WAHA).
INTERNAL_HOST_PORTS = {
    ("app", "8080"),
    ("waha", "3000"),
}


def is_public_url(url: str) -> bool:
    """True bila alamat ini terjangkau **server Google** (bukan browser).

    Penting: fungsi ``IMAGE()`` di Google Sheets menarik gambar di sisi
    server Google, bukan dari browser pembaca. Bukti: export PDF tab
    berisi ``=IMAGE("https://.../gambar.jpg")`` memuat gambarnya,
    sedangkan URL IP LAN tidak pernah bisa ditarik Google.

    Jadi foto **wajib** punya alamat publik (mis. domain + reverse proxy
    atau Cloudflare Tunnel). Alamat container (``http://app:8080``) dan
    IP LAN/privat tidak akan pernah tampil di dalam sel.
    """
    if not url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    if not host:
        return False
    try:
        port = str(parts.port or "")
    except ValueError:
        return False
    if host in INTERNAL_HOSTS or host.endswith(".local"):
        return False
    if (host, port) in INTERNAL_HOST_PORTS:
        return False
    # alamat privat / loopback hanya bisa dijangkau jaringan internal
    if host in ("localhost", "127.0.0.1", "::1"):
        return False
    if host.startswith("192.168.") or host.startswith("10."):
        return False
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
        except (IndexError, ValueError):
            return False
        if 16 <= second <= 31:
            return False
    if host.startswith("169.254."):
        return False
    return True


def image_formula(ticket: Any) -> str:
    """Formula ``IMAGE(...)`` supaya foto tampil langsung di dalam sel.

    Bentuknya satu argumen (``=IMAGE("...")``) tanpa mode, karena locale
    spreadsheet bisa memakai titik koma sebagai pemisah argumen
    (``in_ID``) - koma di sana berarti desimal dan formula jadi error.

    Mengembalikan string kosong bila ticket tidak punya foto atau alamat
    server-nya tidak publik (kolom Foto dibiarkan kosong; tautan di kolom
    "Link Foto" tetap bisa dipakai).
    """
    rel_path = getattr(ticket, "media_path", "") or ""
    if not rel_path:
        return ""

    base = public_base()
    if not is_public_url(base):
        return ""
    url = build_url(rel_path)
    if not url.startswith("http"):
        return ""
    return f'=IMAGE("{url}")'
