#!/usr/bin/env python3
"""Paso 3c — campaign scorer. Deterministic. No LLM. No OpenClaw.

Reads campaigns in assets_resolved, counts real video assets, writes score,
sets status scored | blocked_no_assets.

    python scripts/campaign_scorer_tick.py --dry-run
    python scripts/campaign_scorer_tick.py --limit 1

Systemd (VPS):
    [Timer] OnUnitActiveSec=5min
    [Service] ExecStart=/opt/clipping-system/venv/bin/python /opt/clipping-system/scripts/campaign_scorer_tick.py --limit 5
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))

_SKIP_KINDS = frozenset({
    "drive_folder", "dropbox_folder", "brand_asset",
    "youtube_profile", "twitter_profile", "tiktok_profile",
    "instagram_profile", "profile",
})


def _is_real_asset(asset) -> bool:
    if asset.asset_type in {"folder", "brand_asset"}:
        return False
    meta = asset.extra_metadata or {}
    if meta.get("kind") in _SKIP_KINDS:
        return False
    url = (asset.source_url or "").lower()
    if "/drive/folders/" in url or "/document/d/" in url:
        return False
    return True


def _cpm_usd(meta: dict) -> float:
    discovered = (meta or {}).get("discovered") or {}
    if discovered.get("cpm_usd_per_1k"):
        try:
            return float(discovered["cpm_usd_per_1k"])
        except (TypeError, ValueError):
            pass
    payouts = discovered.get("payouts") or []
    best = 0.0
    for p in payouts:
        if not isinstance(p, dict):
            continue
        cents = p.get("rate_cents") or 0
        try:
            best = max(best, float(cents) / 100.0)
        except (TypeError, ValueError):
            pass
    return best


def _score(real_assets: int, cpm: float, prize: float, verified: bool) -> float:
    s = 15.0
    s += min(real_assets, 12) * 4.0
    s += min(cpm, 10.0) * 4.0
    s += min(prize / 10000.0, 20.0)
    if verified:
        s += 8.0
    return round(min(100.0, max(0.0, s)), 2)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign
    from app.models.asset import Asset

    db = SessionLocal()
    changed = 0
    try:
        rows = (
            db.query(Campaign)
            .filter(Campaign.status == "assets_resolved")
            .order_by(Campaign.id.asc())
            .limit(args.limit)
            .all()
        )
        print(f"campaign_scorer_tick scanned={len(rows)} dry_run={args.dry_run}")
        for c in rows:
            assets = db.query(Asset).filter(Asset.campaign_id == c.id).all()
            real = [a for a in assets if _is_real_asset(a)]
            meta = dict(c.source_metadata or {})
            discovered = dict(meta.get("discovered") or {})
            cpm = _cpm_usd(meta)
            try:
                prize = float(discovered.get("prize_pool_usd") or 0)
            except (TypeError, ValueError):
                prize = 0.0
            verified = bool(discovered.get("organization_verified"))
            score = _score(len(real), cpm, prize, verified)
            new_status = "scored" if real else "blocked_no_assets"
            print(
                f"campaign={c.id} real_assets={len(real)} cpm={cpm} "
                f"prize={prize} score={score} -> {new_status}"
            )
            if args.dry_run:
                continue
            c.assets_count = len(real)
            spec = dict(c.spec or {})
            extra = dict(spec.get("extra") or {})
            extra["score"] = score
            extra["scored_at"] = datetime.now(timezone.utc).isoformat()
            extra["real_assets"] = len(real)
            spec["extra"] = extra
            c.spec = spec
            meta["score"] = {
                "value": score,
                "real_assets": len(real),
                "cpm_usd": cpm,
                "prize_pool_usd": prize,
            }
            c.source_metadata = meta
            c.status = new_status
            changed += 1
        if not args.dry_run:
            db.commit()
        print(f"campaign_scorer_tick changed={changed}")
        return 0
    except Exception as e:
        db.rollback()
        print(f"campaign_scorer_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
