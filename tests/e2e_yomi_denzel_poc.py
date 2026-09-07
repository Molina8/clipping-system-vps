"""
tests/e2e_yomi_denzel_poc.py
=============================
End-to-end Proof of Concept: drive a single campaign from Whop through
the full data extraction → DB → Worker pipeline.

Flow (architecture_flow.md, Steps 1-11):
  1+2+3.  Fetch the Yomi Denzel campaign from Whop via the public
          /discover + /campaigns/<id> endpoints (no auth).
          - Whop: Discover fetcher (dde1e49)
          - Whop: Campaign detail fetcher (f3a6d5a)
          - Whop: Drive fetcher (8772161) for referenceMaterials
  4.      POST /campaigns with extracted data + auto-generated spec
  6.      POST /assets with the user-provided YouTube URL as source_url
  7.      POST /jobs (job_type=download) referencing the asset
  8-11.   Poll /jobs/{id} to observe Worker progress:
          - Worker picks up via /worker/jobs/next
          - Worker downloads → calls /worker/jobs/{id}/result
          - Backend auto-creates TRANSCRIBE job (state_transitions.py)
          - Worker transcribes → calls /worker/jobs/{id}/result

Not a pytest test — runs against the LIVE API at 100.109.27.21:8080.

Usage:
    cd /opt/clipping-system
    source venv/bin/activate
    python tests/e2e_yomi_denzel_poc.py [--full] [--timeout 300]

Flags:
    --full       Also drive the candidate/render/QA part of the flow
                 (manually, since Steps 12-19 of architecture_flow.md
                 are owned by the OpenClaw CRON which isn't built yet).
    --timeout    Max seconds to wait for the Worker (default 180).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# --- Backend imports ---
BACKEND = "/opt/clipping-system"
sys.path.insert(0, BACKEND)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://clipping_api:bCzyQQngSXko6dPIPEoH4ho3xiPVUnvr@127.0.0.1:5432/clipping",
)

from app.whop_integration.campaign_detail_fetcher import CampaignDetailFetcher
from app.whop_integration.discover_fetcher import DiscoverFetcher
from app.whop_integration.drive_fetcher import DriveFetcher


# --- Constants ---
API_BASE = "http://100.109.27.21:8080"
API_TOKEN = "4e3895c5f591d4df30f4510b6268c37149d08e6bdc2314b00f13feada0304493"
WORKER_ID = "windows-worker-01"
VIDEO_URL = "https://www.youtube.com/watch?v=be7dKHOK4NQ"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
DEFAULT_TIMEOUT = 180


# --- Pretty printing ---
def hr(title: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n  {title}\n{bar}")


def step(n: int, title: str) -> None:
    print(f"\n--- Step {n}: {title} ---")


def info(msg: str) -> None:
    print(f"  {msg}")


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def warn(msg: str) -> None:
    print(f"  ⚠️  {msg}")


def fail(msg: str) -> None:
    print(f"  ❌ {msg}")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- HTTP helpers ---
def api(method: str, path: str, **kw) -> requests.Response:
    """Authenticated request to the live API."""
    url = f"{API_BASE}{path}"
    kw.setdefault("headers", {}).update(HEADERS)
    kw.setdefault("timeout", 30)
    return requests.request(method, url, **kw)


def must(r: requests.Response, expected: int | tuple[int, ...], label: str) -> dict:
    if isinstance(expected, int):
        expected = (expected,)
    if r.status_code not in expected:
        raise RuntimeError(
            f"{label} failed: {r.status_code} {r.text[:500]}"
        )
    if r.status_code == 204 or not r.content:
        return {}
    return r.json()


# --- Step 1+2: Discover ---
def run_discover() -> dict:
    """Fetch the Yomi Denzel card from /discover."""
    step(1, "Whop /discover (DiscoverFetcher)")
    fetcher = DiscoverFetcher()
    campaigns = fetcher.fetch_campaigns()
    info(f"total campaigns: {len(campaigns)}")
    yomi = next(
        (c for c in campaigns if "yomi denzel" in c.title.lower()), None
    )
    if not yomi:
        raise RuntimeError("Yomi Denzel not found in /discover")
    ok(f"found: {yomi.title!r}  id={yomi.id}")
    info(f"  brand: {yomi.brand}  type={yomi.campaign_type}  cpm={yomi.cpm_label}")
    info(f"  budget_total: {yomi.budget_total_label}  spent: {yomi.budget_spent_label}")
    info(f"  creators: {yomi.creator_count_raw}  verified: {yomi.is_verified}")
    info(f"  org_experience_id: {yomi.organization_experience_id}")
    return yomi.to_dict()


# --- Step 3: Detail (requirements) ---
def run_detail(discovered: dict) -> dict:
    """Fetch the full Yomi Denzel campaign object via CampaignDetailFetcher."""
    step(2, "Whop /campaigns/<id> (CampaignDetailFetcher)")
    fetcher = CampaignDetailFetcher()
    detail = fetcher.fetch_campaign(discovered["id"])
    ok(f"status={detail.status}  private={detail.private}")
    info(f"  platforms: {detail.platforms}")
    info(f"  requires_application: {detail.requires_application}")
    info(f"  description: {detail.description[:100]!r}")
    info(f"  payouts: {len(detail.payouts)} rules")
    for p in detail.payouts:
        info(f"    - {p.platform:>10s}  rate=${p.rate_per_1k_usd:.2f}/1k  min=${p.min_payout_usd:.2f}  max=${p.max_payout_usd:.2f}")
    info(f"  referenceMaterials: {len(detail.reference_materials)}")
    for rm in detail.reference_materials:
        info(f"    - [{rm.type}/{rm.media_type}] {rm.url}")
    info(f"  budget: total=${detail.budget_usd:,.0f} spent=${detail.budget_spent_usd:,.0f}")
    info(f"  creators={detail.creator_count}  paid_out=${detail.paid_out_usd:,.0f}")
    return detail.to_dict()


# --- Drive fetcher: list the reference materials folders ---
def run_drive_list(detail: dict) -> list[dict]:
    """List the contents of each referenceMaterials Drive folder."""
    step(3, "Whop Drive /folders/<id> (DriveFetcher) — list referenceMaterials")
    fetcher = DriveFetcher()
    folders = []
    for rm in detail["reference_materials"]:
        info(f"  fetching: {rm['url']}")
        try:
            files = fetcher.list_folder(rm["url"])
            ok(f"    {len(files)} entries in folder")
            for f in files[:5]:
                info(f"      - {f.name!r}  id={f.id}")
            folders.append({"ref": rm, "files": [f.to_dict() for f in files]})
        except Exception as exc:
            warn(f"    list failed: {exc}")
            folders.append({"ref": rm, "files": [], "error": str(exc)})
    return folders


# --- Step 4: Create Campaign in DB ---
def run_create_campaign(detail: dict) -> dict:
    """POST /campaigns with the extracted data + auto-generated spec."""
    step(4, "VPS POST /campaigns")
    body = {
        "name": f"whop-yomi-denzel-{int(time.time())}",
        "source_provider": "other",
        "source_id": detail["id"],
        "source_url": f"https://whop.com/codiant/{detail['organization_experience_id']}/app/",
        "source_metadata": {
            "organization_name": detail["organization_name"],
            "organization_id": detail["organization_id"],
            "is_verified": detail["organization_verified"],
            "available_budget_raw": detail["budget_cents"] - detail["budget_spent_cents"],
            "creator_count": detail["creator_count"],
            "top_earners_count": len(detail["top_earners"]),
        },
        "source_instructions": (
            f"Whop Content Rewards campaign '{detail['name']}'. "
            f"Description: {detail['description']}. "
            f"Platforms: {','.join(detail['platforms'])}. "
            f"CPM per platform: "
            + ", ".join(
                f"{p['platform']}=${p['rate_cents']/100:.2f}/1k"
                for p in detail["payouts"]
            )
            + ". Reference materials: "
            + "; ".join(rm["url"] for rm in detail["reference_materials"])
        ),
        # spec is auto-derived by the API from source_instructions via
        # app/campaign_engine/ (commit bf456ed)
    }
    r = api("POST", "/campaigns", json=body)
    data = must(r, 201, "POST /campaigns")
    ok(f"campaign_id={data['id']} status={data['status']}")
    info(f"  source_provider={data['source_provider']}  name={data['name']!r}")
    spec = data.get("spec") or {}
    info(f"  spec (auto-derived): {json.dumps(spec, default=str)}")
    return data


# --- Step 6: Create Asset ---
def run_create_asset(campaign_id: int) -> dict:
    """POST /assets with the user-provided YouTube URL as source_url."""
    step(5, "VPS POST /assets")
    body = {
        "campaign_id": campaign_id,
        "source_url": VIDEO_URL,
        "source_id": "be7dKHOK4NQ",
        "source_provider": "youtube",
        "asset_type": "video",
        "extra_metadata": {
            "added_by": "e2e_yomi_denzel_poc.py",
            "description": "Test video for Yomi Denzel E2E POC",
            "is_test_asset": True,
        },
    }
    r = api("POST", "/assets", json=body)
    data = must(r, 201, "POST /assets")
    ok(f"asset_id={data['id']}  status={data['status']}")
    info(f"  source_url={data['source_url']}")
    info(f"  source_provider={data['source_provider']}  asset_type={data['asset_type']}")
    return data


# --- Step 7: Create DOWNLOAD job ---
def run_create_download_job(asset_id: str) -> dict:
    """POST /jobs with job_type=download."""
    step(6, "VPS POST /jobs (job_type=download)")
    body = {
        "job_type": "download",
        # Worker contract (per Molina logs 2026-09-07 11:03-11:05 UTC):
        # expects `payload.url` (NOT `source_url`) for download jobs.
        # We keep `source_url` for our own audit trail and include `asset_id`
        # so the state_transitions layer can correlate.
        "payload": {
            "asset_id": asset_id,
            "url": VIDEO_URL,
            "source_url": VIDEO_URL,
            "destination": "/tmp/cs_yomi_denzel_video.mp4",
        },
        "priority": 5,
    }
    r = api("POST", "/jobs", json=body)
    data = must(r, 201, "POST /jobs")
    ok(f"job_id={data['id']}  status={data['status']}  priority={data['priority']}")
    info(f"  job_type={data['job_type']}  payload={json.dumps(data['payload'])}")
    return data


# --- Steps 8-11: poll + observe ---
def run_observe(job_id: int, timeout: int) -> dict:
    """Poll the job + worker /jobs/next + assets for state transitions."""
    step(7, "Observe Worker state transitions (poll until timeout)")
    start = time.time()
    seen_states = []
    seen_jobs = []

    def elapsed() -> str:
        return f"{int(time.time() - start)}s"

    while time.time() - start < timeout:
        # 1. Check the original download job status
        r = api("GET", f"/jobs/{job_id}")
        if r.status_code == 200:
            data = r.json()
            state = data["status"]
            if state not in seen_states:
                seen_states.append(state)
                info(f"  [{elapsed()}] download job {job_id} → status={state}")

        # 2. List recent jobs (look for auto-created transcribe)
        r = api("GET", "/jobs?limit=10")
        if r.status_code == 200:
            jobs = r.json()
            if isinstance(jobs, dict):
                jobs = jobs.get("data", [])
            for j in jobs:
                if j["id"] not in seen_jobs:
                    seen_jobs.append(j["id"])
                    if j["id"] != job_id:
                        info(f"  [{elapsed()}] new job appeared: "
                             f"id={j['id']}  type={j['job_type']}  status={j['status']}  "
                             f"payload_asset={j['payload'].get('asset_id', '?')}")

        # 3. Check asset status (might be 'downloaded' or 'transcribed' after Worker)
        # We don't know the asset_id without it being tracked — let's list assets
        # for the campaign
        # (skip — handled in run_summary)

        # Exit early if we see the natural end state
        if "completed" in seen_states:
            info(f"  [{elapsed()}] download completed, looking for transcribe...")
            # Allow a bit more time for transcribe job to appear
            recent = api("GET", "/jobs?limit=10")
            recent_jobs = []
            if recent.status_code == 200:
                recent_jobs = recent.json()
                if isinstance(recent_jobs, dict):
                    recent_jobs = recent_jobs.get("data", [])
            if time.time() - start > 30 and any(
                j.get("job_type") == "transcribe" for j in recent_jobs
            ):
                info(f"  [{elapsed()}] transcribe job detected, exiting early")
                break

        time.sleep(5)

    info(f"  observed states for download job: {seen_states}")
    info(f"  total jobs observed: {len(seen_jobs)}")
    return {
        "download_states_seen": seen_states,
        "jobs_seen": seen_jobs,
        "elapsed_seconds": int(time.time() - start),
    }


# --- Optional: drive the candidate/render/QA part of the flow ---
def run_extended_flow(campaign_id: int, observed: dict, timeout: int) -> dict:
    """If --full, also drive the candidate proposal + render + QA part."""
    step(8, "Extended flow: candidates + render + QA")
    info("This part requires Step 12+ (OpenClaw CRON) which isn't built yet.")
    info("Skipping unless --full is passed AND the Worker is actively running.")
    return {"skipped": True}


# --- Summary ---
def run_summary(
    discovered: dict,
    detail: dict,
    drive_folders: list[dict],
    campaign: dict,
    asset: dict,
    download_job: dict,
    observed: dict,
    worker_alive: bool,
) -> None:
    hr("SUMMARY")
    print("  Whop extraction:")
    info(f"    discover title : {discovered['title']!r}")
    info(f"    detail status  : {detail['status']}  private={detail['private']}")
    info(f"    drive folders  : {len(drive_folders)} referenceMaterials")
    for d in drive_folders:
        n = len(d.get("files", []))
        info(f"      - {n} entries")
    print()
    print("  DB created:")
    info(f"    campaign_id={campaign['id']}  status={campaign['status']}")
    info(f"    asset_id={asset['id']}  status={asset['status']}  source={asset['source_url']}")
    info(f"    download_job_id={download_job['id']}  status={download_job['status']}")
    print()
    print("  Worker observation:")
    if worker_alive:
        info(f"    Worker {WORKER_ID} is alive ✅")
    else:
        warn(f"    Worker {WORKER_ID} last heartbeat > 2 min ago — may not be polling")
    info(f"    download job states seen: {observed['download_states_seen']}")
    info(f"    total jobs in DB observed: {len(observed['jobs_seen'])}")
    info(f"    elapsed: {observed['elapsed_seconds']}s")
    print()
    if worker_alive and "completed" in observed["download_states_seen"]:
        ok("E2E POC PASSED: download job completed by Worker")
    elif not worker_alive:
        warn("E2E POC PARTIAL: Worker not actively polling — jobs created but not processed")
        info("       Ask Molina to verify the Worker loop is running on her PC.")
    else:
        warn(f"E2E POC INCONCLUSIVE: download job did not complete within {observed['elapsed_seconds']}s")
        info("       Worker may be slow, stuck, or down. Check /jobs/{id} for status updates.")


def check_worker_alive() -> bool:
    """Quick check if the Worker has heartbeated in the last 2 minutes."""
    try:
        r = api("GET", f"/worker/{WORKER_ID}")
        if r.status_code != 200:
            return False
        data = r.json()
        last_hb = data.get("last_heartbeat_at")
        if not last_hb:
            return False
        last_hb_dt = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
        delta = (datetime.now(timezone.utc) - last_hb_dt).total_seconds()
        return delta < 120
    except Exception:
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--full", action="store_true", help="Drive the full flow")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    p.add_argument("--skip-drive", action="store_true", help="Skip Drive folder listing")
    args = p.parse_args()

    hr(f"E2E POC: Yomi Denzel — {now_utc_iso()}")
    info(f"API: {API_BASE}")
    info(f"Worker: {WORKER_ID}")
    info(f"Video: {VIDEO_URL}")
    info(f"Timeout: {args.timeout}s  full={args.full}")

    # Pre-flight: Worker alive?
    worker_alive = check_worker_alive()
    if worker_alive:
        ok(f"Worker {WORKER_ID} heartbeated within last 2 min")
    else:
        warn(f"Worker {WORKER_ID} last heartbeat > 2 min ago")

    # 1+2+3: Whop extraction
    discovered = run_discover()
    detail = run_detail(discovered)
    if not args.skip_drive:
        drive_folders = run_drive_list(detail)
    else:
        drive_folders = []

    # 4+6+7: VPS side — campaign, asset, job
    campaign = run_create_campaign(detail)
    asset = run_create_asset(str(campaign["id"]))
    download_job = run_create_download_job(asset["id"])

    # 8-11: observe
    observed = run_observe(download_job["id"], args.timeout)

    # Optional: extended flow
    if args.full:
        run_extended_flow(int(campaign["id"]), observed, args.timeout)

    # Final summary
    run_summary(discovered, detail, drive_folders, campaign, asset, download_job, observed, worker_alive)

    hr("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
