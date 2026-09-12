#!/usr/bin/env python3
"""Whop discovery cron entrypoint — invoked by OpenClaw cron `whop-discovery-cron`.

Standalone script (no FastAPI context) so the OpenClaw scheduler can run it
as a `command` payload with a working directory of `/opt/clipping-system`.

What it does (architecture_flow.md steps 1+3):
  1. Discover campaigns from the Whop public JSON API.
  2. Upsert into `campaigns` (representative names: "[whop] $X/1k · $Yk · Name").
  3. Run `analyze_due_campaigns` so draft campaigns get a spec → ready.

Output policy (kept tight to avoid spamming Telegram):
  - Logs go to `/opt/clipping-system/logs/whop_discovery.log`.
  - Stderr is silenced on success.
  - Stdout emits a single short line ONLY when there's something to report:
    * new campaigns upserted, OR
    * error.
    No-op runs (everything already up to date) print nothing and exit 0,
    so the cron announces no message to Telegram.

Usage:
    cd /opt/clipping-system
    source venv/bin/activate
    python scripts/whop_discovery.py                # full sweep (limit=50)
    python scripts/whop_discovery.py --limit 10     # smaller sweep
    python scripts/whop_discovery.py --dry-run      # discover + report only, no DB writes
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

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=os.environ.get("WHOP_DISCOVERY_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "whop_discovery.log"),
    ],
)
logger = logging.getLogger("whop_discovery")


def _build_whop_provider():
    """Return a WhopProvider instance configured from env (or registry fallback)."""
    from app.services.discovery.providers.whop import WhopProvider
    from app.services.discovery.registry import get_provider

    tenant = os.environ.get(
        "WHOP_TENANT_URL",
        "https://b4e0vdqv6zgqeqj4pfgm.apps.whop.com",
    )
    # Try registry first (respects multi-tenant config), but construct
    # directly with the env-overridden tenant so this script can target
    # a specific tenant without changing code.
    registered = get_provider("whop")
    if registered is not None and getattr(registered, "tenant_url", None) == tenant:
        return registered
    return WhopProvider(tenant_url=tenant)


def main() -> int:
    p = argparse.ArgumentParser(description="Whop discovery cron entrypoint")
    p.add_argument("--limit", type=int, default=50, help="max campaigns per run")
    p.add_argument(
        "--fetch-detail",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="fetch detail page for each card (slower, more data)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="discover only, don't touch the DB",
    )
    p.add_argument(
        "--analyze",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="run analyze_due_campaigns after upsert",
    )
    args = p.parse_args()

    from app.db.database import SessionLocal

    db = SessionLocal()
    summary: dict = {
        "provider": "whop",
        "discovered": 0,
        "upserted": 0,
        "assets_created": 0,
        "analyzed": 0,
        "campaigns": [],
        "dry_run": args.dry_run,
        "limit": args.limit,
    }
    try:
        provider = _build_whop_provider()
        try:
            discovered = provider.discover(limit=args.limit)
        except Exception as e:  # noqa: BLE001
            logger.exception("whop discover failed: %s", e)
            summary["error"] = f"discover_failed: {e}"
            print(json.dumps(summary))
            return 2

        summary["discovered"] = len(discovered)
        logger.info("whop discover returned %d campaigns", len(discovered))

        if args.fetch_detail:
            for d in discovered:
                try:
                    provider.fetch_detail(d)
                except Exception as e:  # noqa: BLE001
                    logger.info("fetch_detail failed for %s: %s", d.external_id, e)

        if args.dry_run:
            summary["campaigns"] = [
                {
                    "name": d.name,
                    "cpm": d.cpm_usd_per_1k,
                    "prize_pool_usd": d.prize_pool_usd,
                    "detail_url": d.detail_url,
                }
                for d in discovered
            ]
            print(json.dumps(summary))
            return 0

        from app.services.discovery.asset_resolver import resolve_assets_for_campaign
        from app.services.discovery.upsert import upsert_campaign

        upserted = 0
        assets_created = 0
        for d in discovered:
            c = upsert_campaign(db, d)
            upserted += 1
            if d.asset_links:
                new_assets = resolve_assets_for_campaign(
                    db, c.id, d.asset_links, discovery_provider="whop"
                )
                assets_created += len(new_assets)
            summary["campaigns"].append(
                {"id": c.id, "name": c.name, "status": c.status}
            )

        summary["upserted"] = upserted
        summary["assets_created"] = assets_created

        if args.analyze:
            from app.services.campaign_analyzer import analyze_due_campaigns

            results = analyze_due_campaigns(db, limit=args.limit)
            summary["analyzed"] = len(results)
            logger.info("analyze_due_campaigns processed %d campaigns", len(results))

        print(json.dumps(summary))
        logger.info(
            "whop_discovery done: %s",
            {k: v for k, v in summary.items() if k != "campaigns"},
        )
        return 0
    except Exception as e:  # noqa: BLE001
        logger.exception("whop_discovery failed: %s", e)
        summary["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(summary))
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
