#!/usr/bin/env python3
"""Paso 3c — campaign scorer. Deterministic. No LLM."""
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


def _size_of(asset) -> int:
    if asset.file_size:
        try:
            return int(asset.file_size)
        except (TypeError, ValueError):
            pass
    meta = asset.extra_metadata or {}
    for k in ("file_size", "size"):
        v = meta.get(k)
        if v:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return 0


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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign
    from app.models.asset import Asset
    from app.services.campaign_score import MIN_SCORE_TO_RUN, score_campaign

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
            sizes = [_size_of(a) for a in real]
            meta = dict(c.source_metadata or {})
            discovered = dict(meta.get("discovered") or {})
            rules = dict(meta.get("rules") or {})
            cpm = _cpm_usd(meta)
            try:
                prize = float(discovered.get("prize_pool_usd") or 0)
            except (TypeError, ValueError):
                prize = 0.0
            verified = bool(discovered.get("organization_verified"))
            scored = score_campaign(
                real_assets=len(real),
                cpm=cpm,
                prize=prize,
                verified=verified,
                rules=rules,
                content_kinds=rules.get("content_source_kinds"),
                file_sizes=sizes,
            )
            score = scored["value"]
            if not real:
                new_status = "blocked_no_assets"
            elif score < MIN_SCORE_TO_RUN or not scored["eligible"]:
                new_status = "blocked_no_assets"
            else:
                new_status = "scored"
            print(
                f"campaign={c.id} real_assets={len(real)} cpm={cpm} "
                f"prize={prize} score={score} pen={scored['penalties']} -> {new_status}"
            )
            if args.dry_run:
                continue
            c.assets_count = len(real)
            spec = dict(c.spec or {})
            extra = dict(spec.get("extra") or {})
            extra["score"] = score
            extra["score_breakdown"] = scored
            extra["scored_at"] = datetime.now(timezone.utc).isoformat()
            extra["real_assets"] = len(real)
            spec["extra"] = extra
            c.spec = spec
            meta["score"] = {
                "value": score,
                "real_assets": len(real),
                "cpm_usd": cpm,
                "prize_pool_usd": prize,
                "breakdown": scored,
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
