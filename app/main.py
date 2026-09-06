"""FastAPI application entrypoint."""
import logging

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


app = FastAPI(
    title="Clipping API",
    version="0.2.0",
    description="Backend coordinator for the clipping pipeline (VPS side).",
)


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
from app.models import candidate, clip  # noqa: E402,F401  # alembic model registration
from app.models import asset  # noqa: E402,F401  # model registration for alembic
from app.models import campaign  # noqa: E402,F401  # model registration for alembic
