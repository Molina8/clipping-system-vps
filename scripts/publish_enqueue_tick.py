#!/usr/bin/env python3
"""Publish enqueue tick — paso 20. No cron until a trial closes. Manual --limit 1."""
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
    level=os.environ.get("PUBLISH_ENQUEUE_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "publish_enqueue_tick.log")],
)
logger = logging.getLogger("publish_enqueue_tick")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="do not insert a job; print the candidate only",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="payload.dry_run=false (YouTube API). Default is dry_run=true.",
    )
    p.add_argument("--platform", default="youtube")
    args = p.parse_args()

    if args.live:
        os.environ["PUBLISH_DRY_RUN"] = "0"

    from app.db.database import SessionLocal
    from app.services.publish_enqueue import enqueue_publish_jobs

    db = SessionLocal()
    try:
        rows = enqueue_publish_jobs(
            db,
            limit=args.limit,
            platform=args.platform,
            dry_run=not args.live,
            create_jobs=not args.dry_run,
        )
        if not rows:
            print("publish_enqueue_tick enqueued=0")
            return 0
        for r in rows:
            print(
                "publish_enqueue_tick "
                + " ".join(f"{k}={v}" for k, v in r.items())
            )
        return 0
    except Exception as e:  # noqa: BLE001
        logger.exception("publish_enqueue_tick failed: %s", e)
        print(f"publish_enqueue_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
