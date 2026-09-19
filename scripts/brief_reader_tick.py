#!/usr/bin/env python3
"""Paso 3a — brief-reader. One Grok JSON call per campaign.

Reads source_metadata.discovered. Fetches public Google Docs.
Success always status=briefed (rules + score_preview written).
Blocking/scoring is 3c; 3b no-ops if there is no Drive folder.

    python scripts/brief_reader_tick.py --limit 1
    python scripts/brief_reader_tick.py --campaign-id 8
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

PROMPT = """Extract clip-campaign rules from discovery JSON plus fetched brief documents.
The documents are the source of truth when present.
Return JSON with keys:
  duration_min (number seconds),
  duration_max (number seconds or null),
  format (string, default "9:16"),
  platforms (array),
  language (string or null),
  captions_required (boolean),
  caption_must_include (string or null; exact required caption phrase),
  watermark_required (boolean),
  logo_urls (array),
  on_screen_text_required (boolean),
  on_screen_text_must_include (array of required phrases),
  tagging_required (boolean),
  tags_required (array, e.g. ["@callofduty"]),
  extra_music_forbidden (boolean),
  ftc_disclosure_required (boolean),
  heavy_source_files (boolean),
  content_source_urls (array of urls where footage lives),
  extra_rules (string; bullet list of requirements),
  difficulty_notes (short string)
If a duration minimum is stated (e.g. at least 10 seconds) use it for duration_min.
Default duration_min=15 duration_max=45 only when the brief is silent on length.
"""


def _truthy(rules: dict, key: str, fallback: bool) -> bool:
    v = rules.get(key)
    if isinstance(v, bool):
        return v
    return fallback


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--campaign-id", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign
    from app.services.grok_client import grok_chat_json
    from app.services.brief_materials import (
        classify_url,
        collect_reference_urls,
        extract_urls,
        fetch_google_doc_text,
        heuristic_flags,
    )
    from app.services.campaign_score import score_campaign

    db = SessionLocal()
    try:
        q = db.query(Campaign)
        if args.campaign_id:
            rows = q.filter(Campaign.id == args.campaign_id).all()
        else:
            rows = (
                q.filter(Campaign.status == "discovered")
                .order_by(Campaign.id.asc())
                .limit(args.limit)
                .all()
            )
        print(f"brief_reader_tick scanned={len(rows)} dry_run={args.dry_run}")
        for c in rows:
            meta = dict(c.source_metadata or {})
            discovered = meta.get("discovered") or {}
            refs = collect_reference_urls(discovered)
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

            fetched_docs: list[dict] = []
            brief_text_parts: list[str] = []
            all_urls = list(refs)
            for url in refs:
                if classify_url(url) != "google_doc":
                    continue
                text, err = fetch_google_doc_text(url)
                fetched_docs.append({
                    "url": url,
                    "ok": text is not None,
                    "error": err,
                    "chars": len(text or ""),
                })
                print(f"campaign={c.id} google_doc ok={text is not None} err={err} chars={len(text or '')}")
                if text:
                    brief_text_parts.append(text[:12000])
                    all_urls.extend(extract_urls(text))

            heur = heuristic_flags("\n".join(brief_text_parts) + "\n" + (discovered.get("description") or ""))
            kinds = sorted({classify_url(u) for u in all_urls})
            payload = {
                "name": c.name,
                "description": discovered.get("description"),
                "platforms": discovered.get("platforms"),
                "categories": discovered.get("categories"),
                "payouts": discovered.get("payouts"),
                "reference_urls": refs[:20],
                "heuristic_flags": heur,
                "brief_documents": brief_text_parts,
            }
            print(f"campaign={c.id} docs={len(brief_text_parts)} urls={len(all_urls)} kinds={kinds}")
            if args.dry_run:
                continue
            try:
                blob = json.dumps(payload, ensure_ascii=False)
                rules = grok_chat_json(PROMPT + blob[:14000])
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

            if heur.get("min_seconds_hint") and not rules.get("duration_min"):
                rules["duration_min"] = heur["min_seconds_hint"]
            for k in (
                "watermark_required",
                "captions_required",
                "on_screen_text_required",
                "tagging_required",
                "extra_music_forbidden",
            ):
                rules[k] = _truthy(rules, k, bool(heur.get(k)))

            content_urls = rules.get("content_source_urls") or []
            if not isinstance(content_urls, list):
                content_urls = []
            for u in all_urls:
                kind = classify_url(u)
                if kind in {"drive_folder", "drive_file", "mediasilo", "unsupported_host", "youtube"} and u not in content_urls:
                    content_urls.append(u)
            rules["content_source_urls"] = content_urls[:30]
            rules["content_source_kinds"] = sorted({classify_url(u) for u in content_urls}) or kinds
            unsupported = any(
                k in {"mediasilo", "unsupported_host"} for k in rules["content_source_kinds"]
            ) and not any(k in {"drive_folder", "drive_file"} for k in rules["content_source_kinds"])
            rules["unsupported_video_host"] = unsupported
            if unsupported:
                rules["heavy_source_files"] = True

            spec = dict(c.spec or {})
            spec["duration_min"] = float(rules.get("duration_min") or heur.get("min_seconds_hint") or 15)
            spec["duration_max"] = float(rules.get("duration_max") or 45)
            spec["format"] = rules.get("format") or "9:16"
            spec["language"] = rules.get("language")
            extra = dict(spec.get("extra") or {})
            extra["qa_rules"] = extra.get("qa_rules") or {}
            extra["brief_rules"] = rules
            spec["extra"] = extra
            c.spec = spec
            notes = rules.get("extra_rules") or rules.get("difficulty_notes") or ""
            c.source_instructions = str(notes)[:4000]

            preview = score_campaign(
                real_assets=0 if unsupported else 1,
                cpm=float(discovered.get("cpm_usd_per_1k") or 0),
                prize=float(discovered.get("prize_pool_usd") or 0),
                verified=bool(discovered.get("organization_verified")),
                rules=rules,
                content_kinds=rules.get("content_source_kinds"),
            )
            meta["rules"] = rules
            meta["asset_links"] = content_urls
            meta["brief_docs"] = fetched_docs
            meta["score_preview"] = preview
            meta["briefing_error"] = None
            c.source_metadata = meta
            c.status = "briefed"
            db.commit()
            print(
                f"campaign={c.id} -> briefed "
                f"duration={spec['duration_min']}-{spec['duration_max']} "
                f"wm={rules.get('watermark_required')} cap={rules.get('captions_required')} "
                f"ost={rules.get('on_screen_text_required')} tag={rules.get('tagging_required')} "
                f"kinds={rules.get('content_source_kinds')} "
                f"score_preview={preview['value']} pen={preview['penalties']}"
            )
        return 0
    except Exception as e:
        db.rollback()
        print(f"brief_reader_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
