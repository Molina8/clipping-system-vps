"""Business logic for worker registration and heartbeat."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.worker import Worker, WorkerStatus
from app.schemas.worker import HeartbeatIn, WorkerRegistrationIn

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register_worker(
    db: Session, payload: WorkerRegistrationIn
) -> Worker:
    """Upsert a worker node. Idempotent — overwrites on re-register."""
    now = _now()
    existing = db.get(Worker, payload.worker_id)

    if existing is None:
        worker = Worker(
            worker_id=payload.worker_id,
            name=payload.worker_id,
            status=WorkerStatus.ONLINE.value,
            gpu_name=payload.gpu.name,
            gpu_available=payload.gpu.available,
            capabilities=payload.capabilities,
            system_info=payload.system.model_dump(),
            tools_info=payload.tools.model_dump(),
            last_heartbeat_at=now,
            last_registered_at=now,
        )
        db.add(worker)
        logger.info("worker registered: %s", payload.worker_id)
    else:
        existing.status = WorkerStatus.ONLINE.value
        existing.gpu_name = payload.gpu.name
        existing.gpu_available = payload.gpu.available
        existing.capabilities = payload.capabilities
        existing.system_info = payload.system.model_dump()
        existing.tools_info = payload.tools.model_dump()
        existing.last_heartbeat_at = now
        existing.last_registered_at = now
        worker = existing
        logger.info("worker re-registered: %s", payload.worker_id)

    db.commit()
    db.refresh(worker)
    return worker


def update_heartbeat(
    db: Session, payload: HeartbeatIn
) -> Optional[Worker]:
    """Update heartbeat. Returns None if worker not yet registered."""
    worker = db.get(Worker, payload.worker_id)
    if worker is None:
        return None

    now = _now()
    worker.status = payload.status or WorkerStatus.ONLINE.value
    worker.last_heartbeat_at = now

    gpu = payload.gpu or {}
    if isinstance(gpu, dict):
        if gpu.get("name"):
            worker.gpu_name = gpu["name"]
        if "available" in gpu:
            worker.gpu_available = bool(gpu["available"])

    if isinstance(payload.system, dict) and payload.system:
        merged = {**(worker.system_info or {}), **payload.system}
        worker.system_info = merged

    db.commit()
    db.refresh(worker)
    return worker


def get_worker(db: Session, worker_id: str) -> Optional[Worker]:
    return db.get(Worker, worker_id)


def list_workers(db: Session) -> list[Worker]:
    return list(
        db.execute(select(Worker).order_by(Worker.worker_id)).scalars()
    )
