"""Pydantic schemas for worker registration and heartbeat.

Mirrors the Worker's app/models/worker.py on the client side so that
POST /worker/register and POST /worker/heartbeat accept exactly the
shapes the Worker sends.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SystemInfoIn(BaseModel):
    """System info sent during worker registration."""

    os: str
    os_version: Optional[str] = None
    cpu: Optional[str] = None
    ram_total_gb: Optional[float] = None
    ram_available_gb: Optional[float] = None
    python_version: Optional[str] = None


class GPUInfoIn(BaseModel):
    """GPU info sent during worker registration."""

    name: Optional[str] = None
    available: bool = False
    vram_total_gb: Optional[float] = None
    cuda_available: bool = False
    cuda_version: Optional[str] = None


class ToolsInfoIn(BaseModel):
    """Tool availability sent during worker registration."""

    ffmpeg: bool = False
    ffprobe: bool = False
    whisperx: bool = False


class WorkerRegistrationIn(BaseModel):
    """Payload for POST /worker/register."""

    worker_id: str
    system: SystemInfoIn
    gpu: GPUInfoIn
    tools: ToolsInfoIn
    capabilities: list[str] = Field(default_factory=list)


class HeartbeatIn(BaseModel):
    """Payload for POST /worker/heartbeat."""

    worker_id: str
    status: str = "online"
    current_job: Optional[str] = None
    gpu: dict[str, Any] = Field(default_factory=dict)
    system: dict[str, Any] = Field(default_factory=dict)


class WorkerOut(BaseModel):
    """Public worker representation."""

    model_config = ConfigDict(from_attributes=True)

    worker_id: str
    name: Optional[str] = None
    status: str
    gpu_name: Optional[str] = None
    gpu_available: bool
    capabilities: list[str] = Field(default_factory=list)
    last_heartbeat_at: Optional[datetime] = None
    last_registered_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
