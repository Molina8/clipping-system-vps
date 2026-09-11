"""FastAPI application entrypoint."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.jobs import router as jobs_router
from app.api.system import router as system_router
from app.config import settings
from app.db.database import check_database_connection


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("clipping-api")


# --- Steps 1 + 2 + 3 cron (architecture_flow.md) --------------------------
# Step 1 [OPENCLAW CRON]: every N minutes, scan draft campaigns with
# source_instructions and trigger analyze_campaign() so they get a spec.
# (Campaign *discovery* itself is still manual / OpenClaw-side; we only
# automate the analyze step on the VPS here.)
_ANALYZE_INTERVAL_S = 600  # 10 min


async def _analyze_loop() -> None:
    from app.db.database import SessionLocal
    from app.services.campaign_analyzer import analyze_due_campaigns
    while True:
        try:
            await asyncio.sleep(_ANALYZE_INTERVAL_S)
            db = SessionLocal()
            try:
                results = analyze_due_campaigns(db, limit=50)
                if results:
                    logger.info(
                        "analyze_loop processed %d draft campaigns",
                        len(results),
                    )
                # Backlog drain: enqueue download+transcribe+render for any
                # 'ready' campaign without open jobs. Closes the wiring gap
                # discovered when the Worker ran without an explicit enqueue.
                from app.api.campaigns import enqueue_all_ready
                drain = enqueue_all_ready(db=db, limit=50)
                logger.info(
                    "backlog drain: scanned=%d enqueued=%d skipped=%d",
                    drain["scanned"], drain["enqueued"], drain["skipped"],
                )
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("analyze_loop iteration failed: %s", e)


# --- Step 1 discovery cron (architecture_flow.md) --------------------------
# Every DISCOVERY_INTERVAL_S hours: run all providers, upsert campaigns,
# resolve assets, then analyze the new draft campaigns.
_DISCOVERY_INTERVAL_S = 6 * 3600  # 6h


async def _discovery_loop() -> None:
    from app.db.database import SessionLocal
    from app.services.campaign_analyzer import analyze_due_campaigns
    from app.services.discovery.upsert import run_discovery
    while True:
        try:
            await asyncio.sleep(_DISCOVERY_INTERVAL_S)
            db = SessionLocal()
            try:
                summary = run_discovery(db, fetch_detail=True, limit=50)
                analyzed = analyze_due_campaigns(db, limit=50)
                summary["analyzed"] = len(analyzed)
                logger.info(
                    "discovery_loop: %s",
                    summary,
                )
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("discovery_loop iteration failed: %s", e)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    analyze_task = asyncio.create_task(
        _analyze_loop(), name="campaign-analyze-loop"
    )
    discovery_task = asyncio.create_task(
        _discovery_loop(), name="campaign-discovery-loop"
    )
    try:
        yield
    finally:
        for t in (analyze_task, discovery_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


app = FastAPI(
    title="Clipping API",
    version="0.2.0",
    description="Backend coordinator for the clipping pipeline (VPS side).",
    lifespan=lifespan,
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
from app.models import candidate, clip  # noqa: E402,F401  # alembic model registration
from app.models import asset  # noqa: E402,F401  # model registration for alembic
from app.models import campaign  # noqa: E402,F401  # model registration for alembic
