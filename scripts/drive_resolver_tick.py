#!/usr/bin/env python3
"""Paso 3b — expand Drive folders into file assets. No LLM."""
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

FOLDER_RE = re.compile(r"/folders/([a-zA-Z0-9_-]+)")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


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
    for key in ("files", "items", "entries", "result", "data"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict):
            inner = val.get("files") or val.get("items") or []
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    return []


def _gog_ls(folder_id: str) -> list[dict]:
    env = _gog_env()
    attempts = [
        ["gog", "drive", "ls", "--parent", folder_id, "--json", "--max", "100"],
        ["gog", "ls", "--parent", folder_id, "--json", "--max", "100"],
    ]
    last = ""
    for cmd in attempts:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=90)
        except FileNotFoundError:
            raise RuntimeError("gog binary not found on PATH")
        last = (p.stdout or "") + "\n" + (p.stderr or "")
        if p.returncode != 0:
            continue
        out = (p.stdout or "").strip()
        if not out:
            continue
        try:
            rows = _parse_gog_json(out)
        except json.JSONDecodeError:
            continue
        if rows:
            return rows
    raise RuntimeError(f"gog ls failed for {folder_id}: {last[:500]}")


def _kind_from_name(name: str) -> str:
    ext = Path(name or "").suffix.lower()
    return ext.lstrip(".") if ext in VIDEO_EXT else "file"


def _uc(file_id: str) -> str:
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1)
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
        camps = (
            db.query(Campaign)
            .filter(Campaign.status.in_(("briefed", "failed_resolve")))
            .order_by(Campaign.id.asc())
            .limit(args.limit)
            .all()
        )
        print(f"drive_resolver_tick scanned={len(camps)} dry_run={args.dry_run}")
        for c in camps:
            existing_ids = {
                a.source_id
                for a in db.query(Asset).filter(Asset.campaign_id == c.id).all()
                if a.source_id
            }
            folders = []
            for a in db.query(Asset).filter(Asset.campaign_id == c.id).all():
                fid = _folder_id(a.source_url or "")
                kind = (a.extra_metadata or {}).get("kind")
                if fid or kind == "drive_folder":
                    folders.append(fid or _folder_id(a.source_url or ""))
            discovered = (c.source_metadata or {}).get("discovered") or {}
            for ref in discovered.get("reference_materials") or []:
                if isinstance(ref, dict):
                    fid = _folder_id(ref.get("url") or "")
                    if fid:
                        folders.append(fid)

            seen_f, file_rows, errors = set(), [], []
            for fid in folders:
                if not fid or fid in seen_f:
                    continue
                seen_f.add(fid)
                try:
                    file_rows.extend(_gog_ls(fid))
                except Exception as e:
                    errors.append(str(e))
                    print(f"campaign={c.id} folder={fid} ERROR {e}")

            new_assets = 0
            for item in file_rows:
                fid = str(item.get("id") or item.get("Id") or "")
                name = str(item.get("name") or item.get("Name") or fid)
                mime = str(item.get("mimeType") or item.get("mime") or "")
                if not fid or "folder" in mime.lower():
                    continue
                kind = _kind_from_name(name)
                if kind == "file" and not str(mime).startswith("video/"):
                    continue
                if fid in existing_ids:
                    continue
                print(f"campaign={c.id} file={fid} name={name}")
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
                            "kind": kind,
                            "name": name,
                            "mime_type": mime or f"video/{kind}",
                            "discovered_by": "drive_resolver_tick",
                        },
                    ),
                )
                existing_ids.add(fid)
                new_assets += 1
                created += 1

            if errors and new_assets == 0:
                if not args.dry_run:
                    c.status = "failed_resolve"
                    meta = dict(c.source_metadata or {})
                    meta["resolve_error"] = {"kind": "drive_auth_or_gog", "message": errors[0][:300]}
                    c.source_metadata = meta
                    db.commit()
                print(f"campaign={c.id} -> failed_resolve")
                continue

            if not args.dry_run:
                c.status = "assets_resolved"
                meta = dict(c.source_metadata or {})
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
