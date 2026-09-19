#!/usr/bin/env python3
"""Paso 3b — expand Drive folders into file assets. No LLM.

Needs `gog` on the VPS (same tool the brief fallback used).

    python scripts/drive_resolver_tick.py --dry-run
    python scripts/drive_resolver_tick.py --limit 1
"""
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
FILE_RE = re.compile(r"(?:/d/|id=)([a-zA-Z0-9_-]{20,})")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _folder_id(url: str) -> str | None:
    m = FOLDER_RE.search(url or "")
    return m.group(1) if m else None


def _gog_env() -> dict:
    env = dict(os.environ)
    pw = env.get("GOG_KEYRING_PASSWORD")
    if not pw and Path("/etc/openclaw/cron-secrets.env").exists():
        for line in Path("/etc/openclaw/cron-secrets.env").read_text().splitlines():
            if line.startswith("GOG_KEYRING_PASSWORD="):
                env["GOG_KEYRING_PASSWORD"] = line.split("=", 1)[1].strip().strip('"')
    return env


def _gog_ls(folder_id: str) -> list[dict]:
    env = _gog_env()
    attempts = [
        ["gog", "drive", "ls", "--id", folder_id, "--json"],
        ["gog", "drive", "list", "--id", folder_id, "--json"],
        ["gog", "drive", "ls", folder_id],
    ]
    last = ""
    for cmd in attempts:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
        except FileNotFoundError:
            raise RuntimeError("gog binary not found on PATH")
        last = (p.stdout or "") + "\n" + (p.stderr or "")
        if p.returncode != 0:
            continue
        out = p.stdout.strip()
        if not out:
            continue
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            rows = []
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                fid = parts[0] if parts else ""
                name = parts[-1] if len(parts) > 1 else fid
                rows.append({"id": fid, "name": name, "mimeType": ""})
            return rows
        if isinstance(data, dict):
            data = data.get("files") or data.get("items") or data.get("entries") or []
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    raise RuntimeError(f"gog ls failed for {folder_id}: {last[:400]}")


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
    created = 0
    resolved = 0
    try:
        camps = (
            db.query(Campaign)
            .filter(Campaign.status == "briefed")
            .order_by(Campaign.id.asc())
            .limit(args.limit)
            .all()
        )
        print(f"drive_resolver_tick scanned={len(camps)} dry_run={args.dry_run}")
        for c in camps:
            existing_ids = {
                a.source_id for a in db.query(Asset).filter(Asset.campaign_id == c.id).all() if a.source_id
            }
            folders = []
            for a in db.query(Asset).filter(Asset.campaign_id == c.id).all():
                fid = _folder_id(a.source_url or "")
                kind = (a.extra_metadata or {}).get("kind")
                if fid or kind == "drive_folder":
                    folders.append((fid or _folder_id(a.source_url or ""), a))
            meta = c.source_metadata or {}
            discovered = meta.get("discovered") or {}
            for ref in discovered.get("reference_materials") or []:
                if not isinstance(ref, dict):
                    continue
                url = ref.get("url") or ""
                fid = _folder_id(url)
                if fid:
                    folders.append((fid, None))

            seen_f = set()
            file_rows = []
            errors = []
            for fid, _parent in folders:
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
                if not fid:
                    continue
                if "folder" in mime.lower():
                    continue
                kind = _kind_from_name(name)
                if kind == "file" and not mime.startswith("video/"):
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
                for a in db.query(Asset).filter(Asset.campaign_id == c.id).all():
                    if (a.extra_metadata or {}).get("kind") == "drive_folder" or _folder_id(a.source_url or ""):
                        meta_a = dict(a.extra_metadata or {})
                        meta_a["skip_download"] = True
                        a.extra_metadata = meta_a
                c.status = "assets_resolved"
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
