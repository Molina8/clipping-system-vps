"""FastAPI application entrypoint.

NOTE (2026-09-11): The previous in-process `_analyze_loop` and
`_discovery_loop` asyncio tasks have been removed. Both responsibilities
have moved to the OpenClaw scheduler:

  - Step 1 (Whop discovery): OpenClaw cron `whop-discovery-cron` →
    scripts/whop_discovery.py (every 6h).
  - Steps 2/3 (analyze + drain): OpenClaw cron `vps-pipeline-tick` →
    scripts/vps_pipeline_tick.py (every 10m).

The API now has zero background tasks. All cadence lives in the scheduler,
which gives us a run ledger, retry, and inspection.
"""
import logging

from fastapi import FastAPI

from app.api.jobs import router as jobs_router
from app.api.mission_control import router as mission_control_router
from app.api.system import router as system_router
from app.config import settings
from app.db.database import check_database_connection


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("clipping-api")


app = FastAPI(
    title="Clipping API",
    version="0.3.0",
    description="Backend coordinator for the clipping pipeline (VPS side). "
    "All scheduled work runs from OpenClaw automations.",
)
import traceback as _tb
_DEBUG_LOG_PATH = "/tmp/discovery_debug.log"


@app.middleware("http")
async def _debug_log_middleware(request, call_next):
    """Capture tracebacks to /tmp/discovery_debug.log for debugging 500s."""
    try:
        response = await call_next(request)
        if response.status_code >= 500:
            with open(_DEBUG_LOG_PATH, "a") as f:
                f.write(f"=== {request.method} {request.url.path} returned {response.status_code} ===\n")
        return response
    except Exception as e:  # noqa: BLE001
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(f"=== {request.method} {request.url.path} raised {type(e).__name__}: {e} ===\n")
            _tb.print_exc(file=f)
        raise



@app.get("/health")
def health() -> dict:
    db_ok = check_database_connection()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
    }


app.include_router(system_router)
app.include_router(jobs_router)
from app.api.workers import router as workers_router  # noqa: E402
app.include_router(workers_router)
from app.api.campaigns import router as campaigns_router  # noqa: E402
app.include_router(campaigns_router)
from app.api.assets import router as assets_router  # noqa: E402
app.include_router(assets_router)
from app.api.candidates import router as candidates_router  # noqa: E402
app.include_router(candidates_router)
from app.api.clips import router as clips_router  # noqa: E402
app.include_router(clips_router)
from app.api.clip_selection import router as clip_selection_router  # noqa: E402
app.include_router(clip_selection_router)
from app.api.discovery import router as discovery_router  # noqa: E402
app.include_router(discovery_router)
from app.api.social_accounts import router as social_accounts_router  # noqa: E402
app.include_router(social_accounts_router)

# Optional read-only dashboard. Disabled by default (404 from the gate inside
# each endpoint). The router is ALWAYS mounted so tests can flip the flag
# without re-importing the app. StaticFiles mount also gated at import time.
app.include_router(mission_control_router)
import pathlib as _pathlib
_mc_dir = _pathlib.Path(__file__).parent / "static" / "mission-control"
if _mc_dir.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount(
        "/mission-control",
        StaticFiles(directory=str(_mc_dir), html=True),
        name="mission-control-static",
    )
if settings.mission_control_enabled:
    logger.info("Mission Control enabled at /mission-control")
else:
    logger.info(
        "Mission Control disabled (MISSION_CONTROL_ENABLED=false); "
        "endpoints return 404 and static files are still served but auth-protected"
    )

from app.models import candidate, clip  # noqa: E402,F401  # alembic model registration
from app.models import asset  # noqa: E402,F401  # model registration for alembic
from app.models import campaign  # noqa: E402,F401  # model registration for alembic
from app.models import social_account, clip_publication  # noqa: E402,F401
