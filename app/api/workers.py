"""Worker registry endpoints.

Implements the contract expected by the Windows Worker (clipping-windows-worker):
    POST /worker/register   — WorkerRegistration payload, upsert worker
    POST /worker/heartbeat  — Heartbeat payload, update state
    GET  /worker            — list workers
    GET  /worker/{id}       — get one worker

All endpoints (except health) require Bearer token.
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.schemas.worker import (
    HeartbeatIn,
    WorkerOut,
    WorkerRegistrationIn,
)
from app.services.worker_service import (
    get_worker,
    list_workers,
    register_worker,
    update_heartbeat,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/worker", tags=["workers"])


@router.post("/register", response_model=WorkerOut)
def register(
    payload: WorkerRegistrationIn,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> WorkerOut:
    worker = register_worker(db, payload)
    return WorkerOut.model_validate(worker)


@router.post("/heartbeat", response_model=WorkerOut)
def heartbeat(
    payload: HeartbeatIn,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> WorkerOut:
    worker = update_heartbeat(db, payload)
    if worker is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Worker not registered. "
                "Call POST /worker/register first."
            ),
        )
    return WorkerOut.model_validate(worker)


@router.get("", response_model=List[WorkerOut])
@router.get("/", response_model=List[WorkerOut])
def list_all_workers(
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> List[WorkerOut]:
    return [WorkerOut.model_validate(w) for w in list_workers(db)]


@router.get("/{worker_id}", response_model=WorkerOut)
def get_one(
    worker_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> WorkerOut:
    worker = get_worker(db, worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return WorkerOut.model_validate(worker)
