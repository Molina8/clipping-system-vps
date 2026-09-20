"""Social accounts inventory. Tokens never live here."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.schemas.clip import SocialAccountOut
from app.services.publish_gate import list_social_accounts

router = APIRouter(prefix="/social_accounts", tags=["social_accounts"])


@router.get("", response_model=List[SocialAccountOut])
@router.get("/", response_model=List[SocialAccountOut])
def list_all(
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    return list_social_accounts(db)
