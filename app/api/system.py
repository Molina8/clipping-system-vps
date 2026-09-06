"""Authenticated diagnostic endpoint."""
from fastapi import APIRouter, Depends

from app.auth import require_bearer
from app.config import settings


router = APIRouter()


@router.get("/system/info")
def system_info(_: bool = Depends(require_bearer)) -> dict:
    return {
        "service": "clipping-api",
        "version": "0.1.0",
        "environment": settings.environment,
        "api_host": settings.api_host,
        "api_port": settings.api_port,
        "log_level": settings.log_level,
    }
