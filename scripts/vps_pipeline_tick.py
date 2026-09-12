#!/usr/bin/env python3
"""VPS pipeline tick — invoked by OpenClaw cron `vps-pipeline-tick`.

Standalone script (no FastAPI context) so the OpenClaw scheduler can run it
as a `command` payload. Runs every 10 minutes.

What it does (architecture_flow.md steps 3, 15, 17, 19):
  1. `analyze_due_campaigns` — turn draft campaigns with
     source_instructions into `ready` campaigns with a populated spec.
  2. `enqueue_all_ready` — for each ready campaign that doesn't yet have
     open jobs, enqueue the download → transcribe → render → qa chain.

Output policy (kept tight to avoid spamming Telegram):
  - Logs go to `/opt/clipping-system/logs/vps_pipeline_tick.log`.
  - Stderr is silenced on success.
  - Stdout emits a single short line ONLY when there's something to report:
    * new jobs enqueued, OR
    * new campaigns analyzed, OR
    * error.
    No-op runs (everything already up to date) print nothing and exit 0,
    so the cron announces no message to Telegram.

Usage:
    cd /opt/clipping-system && source venv/bin/activate
    python scripts/vps_pipeline_tick.py --limit 50
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=os.environ.get("VPS_PIPELINE_TICK_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "vps_pipeline_tick.log"),
    ],
)
logger = logging.getLogger("vps_pipeline_tick")


def main() -> int:
    p = argparse.ArgumentParser(description="VPS pipeline tick")
    p.add_argument("--limit", type=int, default=50, help="max campaigns per run")
    args = p.parse_args()

    from app.api.campaigns import enqueue_all_ready
    from app.db.database import SessionLocal
    from app.services.campaign_analyzer import analyze_due_campaigns

    db = SessionLocal()
    try:
        results = analyze_due_campaigns(db, limit=args.limit)
        logger.info("analyze_due_campaigns processed %d campaigns", len(results))

        drain = enqueue_all_ready(db=db, limit=args.limit)
        logger.info(
            "backlog drain: scanned=%d enqueued=%d skipped=%d",
            drain["scanned"], drain["enqueued"], drain["skipped"],
        )

        analyzed = len(results)
        enqueued = drain["enqueued"]
        scanned = drain["scanned"]

        # Only print if something happened.
        if enqueued > 0 or analyzed > 0:
            print(f"vps_pipeline_tick: analyzed={analyzed} enqueued={enqueued} scanned={scanned}")
        # else: silent — no-op tick, no Telegram spam.

        return 0
    except Exception as e:  # noqa: BLE001
        logger.exception("vps_pipeline_tick failed: %s", e)
        # Print to stderr so the cron surfaces it as a failure announcement.
        print(f"vps_pipeline_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
