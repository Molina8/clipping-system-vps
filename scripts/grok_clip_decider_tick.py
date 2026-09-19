#!/usr/bin/env python3
"""Speech clip-decider. One Grok JSON call per transcribed asset without candidates."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

PROMPT = """Pick 1 or 2 clip windows from this transcript for short-form video.
Rules: duration_min={dmin}, duration_max={dmax} seconds. Format 9:16.
Return JSON: {{"clips":[{{"start":0.0,"end":20.0,"reason":"short"}}]}}
Prefer complete phrases. Do not exceed duration_max. Stay inside video_duration={duration}.
Transcript segments (start,end,text):
"""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--approve", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.asset import Asset
    from app.models.campaign import Campaign
    from app.models.candidate import Candidate
    from app.models.job import Job  # noqa: F401
    from app.services.grok_client import grok_chat_json
    from app.services.silent_clip_cutter import campaign_duration_window, is_speech, transcript_text
    from app.services.candidate_lifecycle import approve_candidate

    db = SessionLocal()
    try:
        assets = (
            db.query(Asset)
            .filter(Asset.status == "transcribed")
            .order_by(Asset.transcribed_at.asc().nullsfirst())
            .all()
        )
        done = 0
        for asset in assets:
            if db.query(Candidate).filter(Candidate.asset_id == asset.id).count():
                continue
            tx = (asset.extra_metadata or {}).get("transcription") or {}
            text = transcript_text(tx)
            if not is_speech(text):
                continue
            campaign = db.get(Campaign, asset.campaign_id)
            dmin, dmax = campaign_duration_window(campaign)
            segs = tx.get("segments") or []
            lines = []
            for s in segs[:80]:
                if not isinstance(s, dict):
                    continue
                lines.append(f"{s.get('start',0):.1f}-{s.get('end',0):.1f} {(s.get('text') or '').strip()}")
            duration = float(asset.duration_seconds or 0) or dmax
            prompt = PROMPT.format(dmin=dmin, dmax=dmax, duration=duration) + "\n".join(lines)[:5000]
            print(f"asset={asset.id} campaign={asset.campaign_id} calling grok segs={len(lines)}")
            if args.dry_run:
                done += 1
                if done >= args.limit:
                    break
                continue
            data = grok_chat_json(prompt)
            clips = data.get("clips") if isinstance(data, dict) else None
            if not clips:
                print("no clips", data)
                continue
            created = []
            for clip in clips[:2]:
                start = float(clip.get("start") or 0)
                end = float(clip.get("end") or 0)
                if end - start < max(3.0, dmin * 0.5):
                    continue
                if end - start > dmax + 1:
                    end = start + dmax
                c = Candidate(
                    campaign_id=asset.campaign_id,
                    asset_id=asset.id,
                    start_time=round(start, 2),
                    end_time=round(end, 2),
                    score=0.7,
                    reasoning=str(clip.get("reason") or "grok")[:500],
                    extra_metadata={"source": "grok_clip_decider", "kind": "speech"},
                    status="pending",
                )
                db.add(c)
                db.flush()
                created.append(c)
                print(f"candidate {c.id} {start}-{end}")
            db.commit()
            if args.approve and created:
                print(approve_candidate(db, created[0].id))
            done += 1
            if done >= args.limit:
                break
        print(f"grok_clip_decider_tick done={done}")
        return 0
    except Exception as e:
        db.rollback()
        print(f"ERROR {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
