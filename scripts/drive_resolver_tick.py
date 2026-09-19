#!/usr/bin/env python3
"""Paso 3b — expand Drive folders or MediaSilo reviews into file assets."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

FOLDER_RE = re.compile(r"/folders/([a-zA-Z0-9_-]+)")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
MAX_DEPTH = 2
MAX_FILES = 12


def _folder_id(url: str) -> str | None:
    m = FOLDER_RE.search(url or "")
    return m.group(1) if m else None


def _gog_env() -> dict:
    env = dict(os.environ)
    if not env.get("GOG_KEYRING_PASSWORD") and Path("/etc/openclaw/cron-secrets.env").exists():
        for line in Path("/etc/openclaw/cron-secrets.env").read_text().splitlines():
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


def _gog_ls(folder_id: str) -> list[dict]:
    env = _gog_env()
    cmd = ["gog", "drive", "ls", "--parent", folder_id, "--json", "--max", "100"]
    if env.get("GOG_ACCOUNT"):
        cmd[3:3] = ["--account", env["GOG_ACCOUNT"]]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=90)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "gog failed")[:400])
    return _parse_gog_json((p.stdout or "").strip() or "{}")


def _walk(folder_id: str, depth: int, seen: set[str]) -> list[dict]:
    if not folder_id or folder_id in seen or depth > MAX_DEPTH:
        return []
    seen.add(folder_id)
    rows = _gog_ls(folder_id)
    out = []
    for item in rows:
        mime = str(item.get("mimeType") or "")
        fid = str(item.get("id") or "")
        if mime == FOLDER_MIME and depth < MAX_DEPTH:
            out.extend(_walk(fid, depth + 1, seen))
        elif mime == SHORTCUT_MIME:
            details = item.get("shortcutDetails") or {}
            tid = details.get("targetId")
            tmime = details.get("targetMimeType") or ""
            if tmime == FOLDER_MIME and depth < MAX_DEPTH:
                out.extend(_walk(str(tid), depth + 1, seen))
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


def _is_video(item: dict) -> bool:
    mime = str(item.get("mimeType") or "")
    name = str(item.get("name") or "")
    if mime.startswith("video/"):
        return True
    return Path(name).suffix.lower() in VIDEO_EXT


def _kind_from_name(name: str) -> str:
    ext = Path(name or "").suffix.lower()
    return ext.lstrip(".") if ext in VIDEO_EXT else "video"


def _uc(file_id: str) -> str:
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def _candidate_urls(campaign) -> list[str]:
    meta = campaign.source_metadata or {}
    urls: list[str] = []
    for u in meta.get("asset_links") or []:
        if isinstance(u, str):
            urls.append(u)
    rules = meta.get("rules") or {}
    for u in rules.get("content_source_urls") or []:
        if isinstance(u, str):
            urls.append(u)
    discovered = meta.get("discovered") or {}
    for ref in discovered.get("reference_materials") or []:
        if isinstance(ref, dict) and ref.get("url"):
            urls.append(ref["url"])
        elif isinstance(ref, str):
            urls.append(ref)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _resolve_mediasilo(urls: list[str]) -> list[dict]:
    from app.services.mediasilo import list_review, parse_review_url
    found: list[dict] = []
    seen: set[str] = set()
    for url in urls:
        parsed = parse_review_url(url)
        if not parsed:
            continue
        rid, fid = parsed
        key = f"{rid}/{fid or ''}"
        if key in seen:
            continue
        seen.add(key)
        found.extend(list_review(rid, fid))
        if len(found) >= MAX_FILES:
            break
    return found[:MAX_FILES]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--campaign-id", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign
    from app.models.asset import Asset
    from app.schemas.asset import AssetCreate
    from app.services.asset_service import create_asset

    db = SessionLocal()
    created = resolved = 0
    try:
        q = db.query(Campaign)
        if args.campaign_id:
            camps = q.filter(Campaign.id == args.campaign_id).all()
        else:
            camps = (
                q.filter(Campaign.status.in_(("briefed", "failed_resolve", "assets_resolved")))
                .order_by(Campaign.id.asc())
                .limit(args.limit)
                .all()
            )
        print(f"drive_resolver_tick scanned={len(camps)} dry_run={args.dry_run}")
        for c in camps:
            meta = dict(c.source_metadata or {})
            urls = _candidate_urls(c)
            existing = db.query(Asset).filter(Asset.campaign_id == c.id).all()
            if c.status == "assets_resolved" and any(
                (a.extra_metadata or {}).get("kind") not in {"drive_folder", "brand_asset"} for a in existing
            ):
                print(f"campaign={c.id} skip already has files")
                continue
            existing_ids = {a.source_id for a in existing if a.source_id}
            roots = []
            for url in urls:
                fid = _folder_id(url)
                if fid and fid not in roots:
                    roots.append(fid)

            new_assets = 0
            errors: list[str] = []

            if roots:
                seen, videos = set(), []
                for fid in roots:
                    try:
                        videos.extend(_walk(fid, 0, seen))
                    except Exception as e:
                        errors.append(str(e))
                        print(f"campaign={c.id} folder={fid} ERROR {e}")
                for item in videos:
                    if not _is_video(item):
                        continue
                    fid = str(item.get("id") or "")
                    name = str(item.get("name") or fid)
                    if not fid or fid in existing_ids:
                        continue
                    print(f"campaign={c.id} drive file={fid} name={name}")
                    if args.dry_run:
                        new_assets += 1
                        continue
                    create_asset(
                        db,
                        AssetCreate(
                            campaign_id=c.id,
                            source_url=_uc(fid),
                            source_id=fid,
                            source_provider="gdrive",
                            asset_type="video",
                            extra_metadata={
                                "kind": _kind_from_name(name),
                                "name": name,
                                "mime_type": item.get("mimeType") or "video/mp4",
                                "discovered_by": "drive_resolver_tick",
                            },
                        ),
                    )
                    existing_ids.add(fid)
                    new_assets += 1
                    created += 1
                    if new_assets >= MAX_FILES:
                        break
            else:
                try:
                    ms_items = _resolve_mediasilo(urls)
                except Exception as e:
                    ms_items = []
                    errors.append(str(e))
                    print(f"campaign={c.id} mediasilo ERROR {e}")
                else:
                    print(f"campaign={c.id} mediasilo listed={len(ms_items)}")
                for item in ms_items:
                    aid = item["id"]
                    if aid in existing_ids:
                        continue
                    print(f"campaign={c.id} mediasilo file={aid} name={item['name']} size={item.get('file_size')}")
                    if args.dry_run:
                        new_assets += 1
                        continue
                    create_asset(
                        db,
                        AssetCreate(
                            campaign_id=c.id,
                            source_url=item["source_url"],
                            source_id=aid,
                            source_provider="mediasilo",
                            asset_type="video",
                            extra_metadata={
                                "kind": "video",
                                "name": item["name"],
                                "file_size": item.get("file_size"),
                                "discovered_by": "mediasilo_resolver",
                            },
                        ),
                    )
                    existing_ids.add(aid)
                    new_assets += 1
                    created += 1
                    if new_assets >= MAX_FILES:
                        break

            if new_assets == 0:
                if not args.dry_run:
                    c.status = "failed_resolve" if errors else "blocked_no_assets"
                    meta["resolve_error"] = {
                        "kind": "resolver" if errors else "no_videos",
                        "message": (errors[0] if errors else "no ingestible videos in Drive or MediaSilo")[:300],
                    }
                    c.source_metadata = meta
                    db.commit()
                print(f"campaign={c.id} new_files=0 status={c.status}")
                continue
            if not args.dry_run:
                c.status = "assets_resolved"
                meta["resolve_error"] = None
                c.source_metadata = meta
                db.commit()
                resolved += 1
            print(f"campaign={c.id} new_files={new_assets} -> assets_resolved")
        print(f"drive_resolver_tick created={created} resolved={resolved}")
        return 0
    except Exception as e:
        db.rollback()
        print(f"drive_resolver_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
