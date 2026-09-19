#!/usr/bin/env python3
"""Classify transcribed assets and cut silent/gameplay videos without Grok.

Speech  = transcript has enough words → leave for Grok later
Silent  = empty / 1-2 tokens / no real dialogue → 1-2 windows by duration rules

Default: dry report. Use --cut to insert candidates. Use --approve to enqueue render.
Default --limit 1 so we do not flood the Worker.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))

MIN_SPEECH_CHARS = 24
MIN_SPEECH_WORDS = 6
DEFAULT_MIN = 15.0
DEFAULT_MAX = 35.0


def _tx_text(tx) -> str:
    if not isinstance(tx, dict):
        return ""
    parts = []
    if isinstance(tx.get("text"), str):
        parts.append(tx["text"])
    segs = tx.get("segments") or tx.get("chunks") or []
    if isinstance(segs, list):
        for s in segs:
            if isinstance(s, dict) and s.get("text"):
                parts.append(str(s["text"]))
            elif isinstance(s, str):
                parts.append(s)
    return " ".join(parts).strip()


def _tx_duration(tx, asset) -> float:
    if asset.duration_seconds:
        try:
            return float(asset.duration_seconds)
        except (TypeError, ValueError):
            pass
    if not isinstance(tx, dict):
        return 0.0
    segs = tx.get("segments") or []
    end = 0.0
    if isinstance(segs, list):
        for s in segs:
            if isinstance(s, dict):
                for key in ("end", "end_time"):
                    try:
                        end = max(end, float(s.get(key) or 0))
                    except (TypeError, ValueError):
                        pass
    return end


def _is_speech(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) < MIN_SPEECH_CHARS:
        return False
    words = re.findall(r"[A-Za-zÀ-ɏ0-9']+", cleaned)
    if len(words) < MIN_SPEECH_WORDS:
        return False
    return True


def _duration_window(campaign) -> tuple[float, float]:
    rules = {}
    spec = {}
    if campaign is not None:
        meta = campaign.source_metadata or {}
        if isinstance(meta, dict):
            rules = meta.get("rules") or {}
        spec = campaign.spec or {}
    dmin = (
        rules.get("duration_min")
        or rules.get("min_duration")
        or (spec.get("duration_min") if isinstance(spec, dict) else None)
        or DEFAULT_MIN
    )
    dmax = (
        rules.get("duration_max")
        or rules.get("max_duration")
        or (spec.get("duration_max") if isinstance(spec, dict) else None)
        or DEFAULT_MAX
    )
    try:
        dmin = float(dmin)
        dmax = float(dmax)
    except (TypeError, ValueError):
        dmin, dmax = DEFAULT_MIN, DEFAULT_MAX
    if dmax <= dmin:
        dmax = dmin + 10
    return dmin, dmax


def _windows(duration: float, dmin: float, dmax: float) -> list[tuple[float, float]]:
    if duration <= 1:
        return []
    target = min(dmax, max(dmin, min(dmax, duration)))
    if duration <= dmin + 1:
        return [(0.0, round(duration, 2))]
    first = (0.0, round(min(target, duration), 2))
    # second window later in the video if there is room
    start2 = max(0.0, duration * 0.45)
    end2 = min(duration, start2 + target)
    if end2 - start2 < dmin * 0.8:
        return [first]
    if abs(start2 - first[0]) < 3:
        return [first]
    return [first, (round(start2, 2), round(end2, 2))]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--campaign-id", type=int, default=6)
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--cut", action="store_true", help="insert candidates for silent assets")
    p.add_argument("--approve", action="store_true", help="approve those candidates (creates render)")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign
    from app.models.asset import Asset
    from app.models.candidate import Candidate
    from app.models.job import Job  # noqa: F401
    from app.services.candidate_lifecycle import approve_candidate

    db = SessionLocal()
    speech = silent = skipped = created = approved = 0
    try:
        campaign = db.get(Campaign, args.campaign_id)
        dmin, dmax = _duration_window(campaign)
        assets = (
            db.query(Asset)
            .filter(Asset.campaign_id == args.campaign_id)
            .filter(Asset.status == "transcribed")
            .order_by(Asset.created_at.asc())
            .all()
        )
        print(f"campaign={args.campaign_id} duration_window={dmin}-{dmax}s transcribed={len(assets)}")
        acted = 0
        for asset in assets:
            meta = dict(asset.extra_metadata or {})
            tx = meta.get("transcription") or {}
            text = _tx_text(tx)
            kind = "speech" if _is_speech(text) else "silent"
            dur = _tx_duration(tx, asset)
            preview = text[:80].replace("\n", " ")
            print(f"{kind:6} asset={asset.id} dur={dur:.1f}s words={len(text.split())} tx={preview!r}")
            if kind == "speech":
                speech += 1
                continue
            silent += 1
            wins = _windows(dur, dmin, dmax)
            if not wins:
                skipped += 1
                print("       skip: no duration")
                continue
            if not args.cut:
                print(f"       would cut {wins}")
                continue
            if acted >= args.limit:
                continue
            existing = (
                db.query(Candidate)
                .filter(Candidate.asset_id == asset.id)
                .count()
            )
            if existing:
                print(f"       skip: already {existing} candidates")
                skipped += 1
                continue
            cands = []
            for start, end in wins:
                c = Candidate(
                    campaign_id=asset.campaign_id,
                    asset_id=asset.id,
                    start_time=start,
                    end_time=end,
                    score=0.5,
                    reasoning="silent/gameplay: duration windows, no Grok",
                    extra_metadata={"source": "duration_cut", "kind": "silent"},
                    status="pending",
                )
                db.add(c)
                db.flush()
                cands.append(c)
                created += 1
                print(f"       candidate {c.id} {start}-{end}")
            if args.approve:
                for c in cands:
                    approve_candidate(db, c.id)
                    approved += 1
            acted += 1
        db.commit()
        print(
            f"done speech={speech} silent={silent} skipped={skipped} "
            f"created={created} approved={approved} cut={args.cut} approve={args.approve}"
        )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
