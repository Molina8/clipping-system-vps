#!/usr/bin/env python3
"""Paso 3a — brief-reader. One Grok JSON call per discovered campaign.

Uses source_metadata.discovered already stored by whop_discovery.
Does not fetch the Whop HTML page.

    python scripts/brief_reader_tick.py --dry-run
    python scripts/brief_reader_tick.py --limit 1
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

PROMPT = """Extract clip-campaign rules from this Whop discovery payload.
Return JSON with keys:
  duration_min (number seconds, default 15),
  duration_max (number seconds, default 45),
  format (string, default "9:16"),
  platforms (array of strings),
  extra_rules (short string),
  language (string or null),
  captions_required (boolean)
Discovery JSON:
"""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign
    from app.services.grok_client import grok_chat_json

    db = SessionLocal()
    try:
        rows = (
            db.query(Campaign)
            .filter(Campaign.status == "discovered")
            .order_by(Campaign.id.asc())
            .limit(args.limit)
            .all()
        )
        print(f"brief_reader_tick scanned={len(rows)} dry_run={args.dry_run}")
        for c in rows:
            meta = dict(c.source_metadata or {})
            discovered = meta.get("discovered") or {}
            refs = discovered.get("reference_materials") or []
            if not discovered and not refs:
                print(f"campaign={c.id} failed_brief no_materials")
                if not args.dry_run:
                    c.status = "failed_brief"
                    meta["briefing_error"] = {
                        "kind": "no_materials",
                        "message": "no discovered payload",
                        "at": datetime.now(timezone.utc).isoformat(),
                    }
                    c.source_metadata = meta
                    db.commit()
                continue
            payload = {
                "name": c.name,
                "description": discovered.get("description"),
                "platforms": discovered.get("platforms"),
                "categories": discovered.get("categories"),
                "payouts": discovered.get("payouts"),
                "reference_names": [
                    r.get("name") if isinstance(r, dict) else r for r in refs[:20]
                ],
            }
            print(f"campaign={c.id} calling grok on discovery fields")
            if args.dry_run:
                continue
            try:
                rules = grok_chat_json(PROMPT + json.dumps(payload)[:6000])
            except Exception as e:
                print(f"campaign={c.id} failed_brief {e}")
                c.status = "failed_brief"
                meta["briefing_error"] = {
                    "kind": "llm_timeout" if "timeout" in str(e).lower() else "other",
                    "message": str(e)[:300],
                    "at": datetime.now(timezone.utc).isoformat(),
                }
                c.source_metadata = meta
                db.commit()
                continue
            spec = dict(c.spec or {})
            spec["duration_min"] = float(rules.get("duration_min") or 15)
            spec["duration_max"] = float(rules.get("duration_max") or 45)
            spec["format"] = rules.get("format") or "9:16"
            spec["language"] = rules.get("language")
            extra = dict(spec.get("extra") or {})
            extra["qa_rules"] = extra.get("qa_rules") or {}
            extra["brief_rules"] = rules
            spec["extra"] = extra
            c.spec = spec
            c.source_instructions = (rules.get("extra_rules") or "")[:4000]
            meta["rules"] = {
                "duration_min": spec["duration_min"],
                "duration_max": spec["duration_max"],
                "extra": rules.get("extra_rules"),
            }
            meta["briefing_error"] = None
            meta["resolve_error"] = None
            c.source_metadata = meta
            c.status = "briefed"
            db.commit()
            print(f"campaign={c.id} -> briefed duration={spec['duration_min']}-{spec['duration_max']}")
        return 0
    except Exception as e:
        db.rollback()
        print(f"brief_reader_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
