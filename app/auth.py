"""Bearer token authentication for protected endpoints."""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings


security = HTTPBearer(auto_error=True)


def require_bearer(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> bool:
    expected = settings.api_token
    provided = credentials.credentials
    if not expected or not secrets.compare_digest(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True
