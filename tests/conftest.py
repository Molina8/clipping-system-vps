"""Shared pytest fixtures."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": f"Bearer {settings.api_token}"}


@pytest.fixture(autouse=True)
def _clean_jobs_table():
    """Clean jobs table before every test to ensure isolation."""
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM jobs"))
        session.commit()
    finally:
        session.close()


@pytest.fixture
def db():
    """Database session with cleanup. Cleans jobs table before each test."""
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM jobs"))
        session.commit()
        yield session
    finally:
        session.close()
