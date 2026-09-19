"""Unified 3b backends: Drive folders, Dropbox, direct downloadable URLs."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
MAX_FILES = 12
MAX_DEPTH = 2
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

DRIVE_FOLDER_RE = re.compile(r"drive\.google\.com/(?:drive/(?:u/\d+/)?folders|drive/folders)/([a-zA-Z0-9_-]+)", re.I)
DRIVE_FILE_RE = re.compile(r"drive\.google\.com/(?:file/d|open)/([a-zA-Z0-9_-]+)", re.I)
DRIVE_UC_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]{20,})", re.I)
YT_RE = re.compile(
    r"(?:youtube\.com/watch\?[^\s]*v=|youtube\.com/shorts/|youtu\.be/)([A-Za-z0-9_-]{6,})",
    re.I,
)
VIMEO_RE = re.compile(r"vimeo\.com/(?:video/)?(\d+)", re.I)
TIKTOK_RE = re.compile(r"tiktok\.com/@[^/]+/video/(\d+)", re.I)
DROPBOX_FOLDER_RE = re.compile(r"dropbox\.com/scl/fo/([a-zA-Z0-9_-]+)", re.I)
DROPBOX_FILE_RE = re.compile(r"dropbox\.com/(?:scl/fi|s)/([a-zA-Z0-9_-]+)", re.I)

_SKIP_HOST_HINTS = (
    "mediasilo.com",
    "we.tl",
    "wetransfer.com",
    "frame.io",
    "mega.nz",
)


def classify(url: str) -> str:
    u = (url or "").lower()
    if not u.startswith("http"):
        return "invalid"
    if "docs.google.com/document" in u or "docs.google.com/spreadsheets" in u:
        return "brief_doc"
    if DRIVE_FOLDER_RE.search(url or ""):
        return "drive_folder"
    if "drive.google.com/uc?" in u or DRIVE_FILE_RE.search(url or ""):
        return "drive_file"
    if DROPBOX_FOLDER_RE.search(url or ""):
        return "dropbox_folder"
    if DROPBOX_FILE_RE.search(url or "") or ("dropbox.com" in u and "/scl/fo/" not in u):
        return "dropbox_file"
    if YT_RE.search(url or ""):
        return "youtube"
    if TIKTOK_RE.search(url or ""):
        return "tiktok"
    if VIMEO_RE.search(url or "") and "/user" not in u:
        return "vimeo"
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in VIDEO_EXT):
        return "direct"
    if any(h in u for h in _SKIP_HOST_HINTS):
        return "unsupported"
    if "youtube.com/@" in u or "youtube.com/channel" in u:
        return "profile"
    return "unknown"


def drive_file_id(url: str) -> str | None:
    m = DRIVE_FILE_RE.search(url or "")
    if m:
        return m.group(1)
    m = DRIVE_UC_RE.search(url or "")
    return m.group(1) if m else None


def drive_folder_id(url: str) -> str | None:
    m = DRIVE_FOLDER_RE.search(url or "")
    return m.group(1) if m else None


def _gog_env() -> dict:
    env = dict(os.environ)
    secret = Path("/etc/openclaw/cron-secrets.env")
    if not env.get("GOG_KEYRING_PASSWORD") and secret.exists():
        for line in secret.read_text().splitlines():
            if line.startswith("GOG_KEYRING_PASSWORD="):
                env["GOG_KEYRING_PASSWORD"] = line.split("=", 1)[1].strip().strip('"')
    return env


def _parse_gog_json(out: str) -> list[dict]:
    data = json.loads(out)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    val = data.get("files") or data.get("items") or []
    return [x for x in val if isinstance(x, dict)] if isinstance(val, list) else []


def gog_ls(folder_id: str) -> list[dict]:
    env = _gog_env()
    cmd = ["gog", "drive", "ls", "--parent", folder_id, "--json", "--max", "100"]
    if env.get("GOG_ACCOUNT"):
        cmd[3:3] = ["--account", env["GOG_ACCOUNT"]]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=90)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "gog failed")[:400])
    return _parse_gog_json((p.stdout or "").strip() or "{}")


def _is_drive_video(item: dict) -> bool:
    mime = str(item.get("mimeType") or "")
    name = str(item.get("name") or "")
    if mime.startswith("video/"):
        return True
    return Path(name).suffix.lower() in VIDEO_EXT


def walk_drive(folder_id: str, depth: int = 0, seen: set[str] | None = None) -> list[dict]:
    seen = seen if seen is not None else set()
    if not folder_id or folder_id in seen or depth > MAX_DEPTH:
        return []
    seen.add(folder_id)
    rows = gog_ls(folder_id)
    out: list[dict] = []
    for item in rows:
        mime = str(item.get("mimeType") or "")
        fid = str(item.get("id") or "")
        if mime == FOLDER_MIME and depth < MAX_DEPTH:
            out.extend(walk_drive(fid, depth + 1, seen))
        elif mime == SHORTCUT_MIME:
            details = item.get("shortcutDetails") or {}
            tid = details.get("targetId")
            tmime = details.get("targetMimeType") or ""
            if tmime == FOLDER_MIME and depth < MAX_DEPTH:
                out.extend(walk_drive(str(tid), depth + 1, seen))
            elif tid:
                item = dict(item)
                item["id"] = tid
                item["mimeType"] = tmime or mime
                out.append(item)
        else:
            out.append(item)
        if len(out) >= MAX_FILES:
            break
    return out[:MAX_FILES]


def _resolved(
    *,
    source_url: str,
    source_id: str,
    provider: str,
    name: str,
    kind: str,
) -> dict[str, Any]:
    return {
        "source_url": source_url,
        "source_id": source_id,
        "source_provider": provider,
        "name": name,
        "kind": kind,
    }


def expand_url(url: str) -> tuple[list[dict], str | None]:
    """Return (assets, error_kind_or_none). error only if this URL should fail the campaign when it is the only source."""
    kind = classify(url)
    if kind in {"brief_doc", "profile", "invalid"}:
        return [], None
    if kind == "unsupported":
        return [], "unsupported_source"
    if kind == "unknown":
        return [], "unsupported_source"
    if kind == "drive_folder":
        fid = drive_folder_id(url)
        if not fid:
            return [], "unsupported_source"
        try:
            items = walk_drive(fid)
        except Exception as e:
            return [], f"gog:{e}"
        out = []
        for item in items:
            if not _is_drive_video(item):
                continue
            iid = str(item.get("id") or "")
            name = str(item.get("name") or iid)
            if not iid:
                continue
            out.append(_resolved(
                source_url=f"https://drive.google.com/uc?export=download&id={iid}",
                source_id=iid,
                provider="gdrive",
                name=name,
                kind="video",
            ))
            if len(out) >= MAX_FILES:
                break
        return out, (None if out else "empty_drive_folder")
    if kind == "drive_file":
        iid = drive_file_id(url) or url
        return [_resolved(
            source_url=f"https://drive.google.com/uc?export=download&id={iid}" if drive_file_id(url) else url,
            source_id=str(iid),
            provider="gdrive",
            name=str(iid),
            kind="video",
        )], None
    if kind == "dropbox_file":
        dl = url if "dl=1" in url else (url + ("&dl=1" if "?" in url else "?dl=1"))
        mid = None
        m = DROPBOX_FILE_RE.search(url)
        if m:
            mid = m.group(1)
        return [_resolved(
            source_url=dl,
            source_id=mid or dl[-80:],
            provider="dropbox",
            name=Path(urlparse(url).path).name or "dropbox-file",
            kind="video",
        )], None
    if kind == "dropbox_folder":
        return [], "dropbox_folder_needs_list"
    if kind in {"youtube", "tiktok", "vimeo", "direct"}:
        ident = url
        m = YT_RE.search(url) or TIKTOK_RE.search(url) or VIMEO_RE.search(url)
        if m:
            ident = m.group(1)
        return [_resolved(
            source_url=url,
            source_id=ident,
            provider=kind,
            name=ident,
            kind=kind,
        )], None
    return [], "unsupported_source"


def expand_all(urls: list[str]) -> tuple[list[dict], list[str]]:
    found: list[dict] = []
    seen: set[str] = set()
    errors: list[str] = []
    had_ingest_attempt = False
    for url in urls:
        assets, err = expand_url(url)
        kind = classify(url)
        if kind in {"brief_doc", "profile", "invalid"}:
            continue
        had_ingest_attempt = True
        if err and err.startswith("gog:"):
            errors.append(err)
        elif err == "dropbox_folder_needs_list":
            errors.append("dropbox_folder_needs_list")
        elif err == "unsupported_source":
            errors.append(f"unsupported_source:{url[:120]}")
        for a in assets:
            key = a["source_id"] or a["source_url"]
            if key in seen:
                continue
            seen.add(key)
            found.append(a)
            if len(found) >= MAX_FILES:
                return found, errors
    if not had_ingest_attempt and not found:
        errors.append("no_ingestible_urls")
    return found, errors
