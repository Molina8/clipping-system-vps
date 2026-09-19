#!/usr/bin/env python3
"""Paso 0 — Whop discovery. Inserta solo si hay hueco bajo MAX_ACTIVE.

Activas = discovered | briefed | assets_resolved | scored | ready.
Fallidas / bloqueadas no cuentan. Ya existentes se ignoran (no consumen hueco).

    python scripts/whop_discovery.py --no-fetch-detail --max-active 3
    python scripts/whop_discovery.py --dry-run --no-fetch-detail
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=os.environ.get("WHOP_DISCOVERY_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "whop_discovery.log")],
)
logger = logging.getLogger("whop_discovery")

ACTIVE_STATUSES = (
    "discovered",
    "briefed",
    "assets_resolved",
    "scored",
    "ready",
)
DEFAULT_MAX_ACTIVE = int(os.environ.get("DISCOVERY_MAX_ACTIVE", "3"))


def _build_whop_provider():
    from app.services.discovery.providers.whop import WhopProvider
    from app.services.discovery.registry import get_provider

    tenant = os.environ.get(
        "WHOP_TENANT_URL",
        "https://b4e0vdqv6zgqeqj4pfgm.apps.whop.com",
    )
    registered = get_provider("whop")
    if registered is not None and getattr(registered, "tenant_url", None) == tenant:
        return registered
    return WhopProvider(tenant_url=tenant)


def _active_count(db) -> int:
    from app.models.campaign import Campaign

    return (
        db.query(Campaign)
        .filter(Campaign.status.in_(ACTIVE_STATUSES))
        .count()
    )


def _already_known(db, detail_url: str) -> bool:
    from app.models.campaign import Campaign

    if not detail_url:
        return False
    return (
        db.query(Campaign.id)
        .filter(Campaign.source_url == detail_url)
        .first()
        is not None
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Whop discovery cron entrypoint")
    p.add_argument("--limit", type=int, default=10, help="max cards to fetch from Whop")
    p.add_argument("--max-active", type=int, default=DEFAULT_MAX_ACTIVE)
    p.add_argument(
        "--fetch-detail",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="fetch_detail is broken; default off",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal

    db = SessionLocal()
    summary: dict = {
        "provider": "whop",
        "discovered": 0,
        "upserted": 0,
        "skipped_full": 0,
        "skipped_existing": 0,
        "active": 0,
        "slots": 0,
        "campaigns": [],
        "dry_run": args.dry_run,
        "limit": args.limit,
        "max_active": args.max_active,
    }
    try:
        active = _active_count(db)
        slots = max(0, args.max_active - active)
        summary["active"] = active
        summary["slots"] = slots
        if slots == 0:
            logger.info("discovery skip: active=%s max=%s", active, args.max_active)
            print(json.dumps(summary))
            return 0

        provider = _build_whop_provider()
        try:
            discovered = provider.discover(limit=max(args.limit, 3))
        except Exception as e:
            logger.exception("whop discover failed: %s", e)
            summary["error"] = f"discover_failed: {e}"
            print(json.dumps(summary))
            return 2

        summary["discovered"] = len(discovered)
        if args.fetch_detail:
            for d in discovered:
                try:
                    provider.fetch_detail(d)
                except Exception as e:
                    logger.info("fetch_detail failed for %s: %s", d.external_id, e)

        if args.dry_run:
            summary["campaigns"] = [
                {
                    "name": d.name,
                    "cpm": d.cpm_usd_per_1k,
                    "prize_pool_usd": d.prize_pool_usd,
                    "detail_url": d.detail_url,
                    "known": _already_known(db, d.detail_url),
                }
                for d in discovered
            ]
            print(json.dumps(summary))
            return 0

        from app.services.discovery.upsert import upsert_campaign

        upserted = 0
        for d in discovered:
            if _already_known(db, d.detail_url):
                summary["skipped_existing"] += 1
                continue
            if upserted >= slots:
                summary["skipped_full"] += 1
                continue
            c = upsert_campaign(db, d, status="discovered")
            upserted += 1
            summary["campaigns"].append(
                {"id": c.id, "name": c.name, "status": c.status}
            )

        summary["upserted"] = upserted
        summary["active"] = _active_count(db)
        summary["slots"] = max(0, args.max_active - summary["active"])
        print(json.dumps(summary))
        logger.info(
            "whop_discovery done: %s",
            {k: v for k, v in summary.items() if k != "campaigns"},
        )
        return 0
    except Exception as e:
        logger.exception("whop_discovery failed: %s", e)
        summary["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(summary))
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
