"""Tests for the read-only Mission Control dashboard.

Verifies:
  * auth gate (401/403 without bearer)
  * feature flag gate (404 when MISSION_CONTROL_ENABLED=false)
  * shape of overview / campaigns / campaigns/{id} / jobs/recent / clips responses
  * LIMIT cap respected (requests for >500 do not crash)
  * NO write verb exposed by the router (no POST/PUT/PATCH/DELETE declared)
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.main import app


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "mission_control_enabled", True, raising=False)


def _disabled(monkeypatch):
    monkeypatch.setattr(settings, "mission_control_enabled", False, raising=False)


@pytest.fixture
def enabled_client(monkeypatch):
    _enabled(monkeypatch)
    # The router is mounted only at import time, but since `_enabled_or_404`
    # checks `settings.mission_control_enabled` at request time, the
    # feature flag is respected without re-importing.
    return TestClient(app)


@pytest.fixture
def disabled_client(monkeypatch):
    _disabled(monkeypatch)
    return TestClient(app)


def _create_campaign(name: str | None = None):
    s = SessionLocal()
    try:
        n = name or f"mc-test-{uuid.uuid4().hex[:8]}"
        s.execute(
            text(
                """
                INSERT INTO campaigns (name, status, source_provider, spec)
                VALUES (:n, 'draft', 'manual', '{}'::jsonb)
                """
            ),
            {"n": n},
        )
        s.commit()
        return s.execute(text("SELECT id, name FROM campaigns WHERE name = :n"), {"n": n}).first()
    finally:
        s.close()


def _cleanup_campaign(cid: int):
    s = SessionLocal()
    try:
        # CASCADE removes assets; clips are also FK-cascaded.
        s.execute(text("DELETE FROM campaigns WHERE id = :id"), {"id": cid})
        s.commit()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

def test_overview_requires_auth(enabled_client):
    r = enabled_client.get("/mission-control/overview")
    assert r.status_code in (401, 403)


def test_overview_requires_auth_when_disabled(disabled_client):
    r = disabled_client.get("/mission-control/overview")
    # 404 (feature off) takes precedence; never reveals existence.
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def test_disabled_returns_404(disabled_client, auth_headers):
    r = disabled_client.get(
        "/mission-control/overview", headers=auth_headers
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Shape checks
# ---------------------------------------------------------------------------

def test_overview_shape(enabled_client, auth_headers):
    r = enabled_client.get(
        "/mission-control/overview", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "generated_at", "total_campaigns", "total_jobs_last_24h",
        "total_clips_last_24h", "disk_unavailable_videos",
        "campaigns_by_status", "assets_by_status",
        "clips_by_qa_status", "clips_by_status",
        "recent_errors",
    ):
        assert key in body, f"missing key: {key}"


def test_campaigns_list_includes_all_statuses(enabled_client, auth_headers):
    # Seed one campaign of each status we know is in the CHECK constraint.
    statuses = ["draft", "ready", "active", "paused", "completed", "archived"]
    created_ids = []
    for st in statuses:
        n = f"mc-{st}-{uuid.uuid4().hex[:6]}"
        s = SessionLocal()
        try:
            s.execute(
                text(
                    "INSERT INTO campaigns (name, status, source_provider, spec) "
                    "VALUES (:n, :st, 'manual', '{}'::jsonb)"
                ),
                {"n": n, "st": st},
            )
            s.commit()
            row = s.execute(
                text("SELECT id FROM campaigns WHERE name=:n"), {"n": n}
            ).first()
            created_ids.append(row[0])
        finally:
            s.close()

    try:
        r = enabled_client.get(
            "/mission-control/campaigns", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        names_back = {it["name"] for it in body["items"]}
        for n in [f"mc-{st}-{uuid.uuid4().hex[:6]}" for st in statuses]:
            pass  # names are random — we just check we got something
        # Make sure at least the campaigns we just created are there
        # (we re-fetch their names from the DB to be precise):
        s = SessionLocal()
        try:
            names_seeded = [
                r[0] for r in s.execute(
                    text("SELECT name FROM campaigns WHERE id = ANY(:ids)"),
                    {"ids": created_ids},
                ).all()
            ]
        finally:
            s.close()
        for n in names_seeded:
            assert n in names_back, f"campaign {n} missing from response"
    finally:
        for cid in created_ids:
            _cleanup_campaign(cid)


def test_campaign_detail_404_for_missing(enabled_client, auth_headers):
    r = enabled_client.get(
        "/mission-control/campaigns/999999999", headers=auth_headers
    )
    assert r.status_code == 404


def test_campaign_detail_ok(enabled_client, auth_headers):
    row = _create_campaign()
    try:
        r = enabled_client.get(
            f"/mission-control/campaigns/{row[0]}", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["campaign"]["name"] == row[1]
        assert "assets" in body
        assert "active_jobs" in body
        assert "clips" in body
    finally:
        _cleanup_campaign(row[0])


# ---------------------------------------------------------------------------
# Limit cap
# ---------------------------------------------------------------------------

def test_jobs_recent_limit_capped(enabled_client, auth_headers):
    # Request way more than the cap; should not raise.
    r = enabled_client.get(
        "/mission-control/jobs/recent?limit=5000", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert len(body["items"]) <= 500


# ---------------------------------------------------------------------------
# Stricter test: no POST/PUT/PATCH/DELETE declared
# ---------------------------------------------------------------------------

def test_no_write_verbs_in_router():
    """Static assertion that mission_control_router exposes only GET."""
    from app.api.mission_control import router as mc_router
    allowed = {"GET"}
    seen = set()
    for route in mc_router.routes:
        if hasattr(route, "methods"):
            for m in route.methods:
                seen.add(m)
                assert m in allowed, f"mission_control exposes {m} on {route.path}"
    # And we expect at least the documented endpoints.
    paths = {r.path for r in mc_router.routes if hasattr(r, "path")}
    expected = {
        "/mission-control/overview",
        "/mission-control/campaigns",
        "/mission-control/campaigns/{campaign_id}",
        "/mission-control/jobs/recent",
        "/mission-control/pipeline/{campaign_id}",
        "/mission-control/videos",
        "/mission-control/clips",
    }
    missing = expected - paths
    assert not missing, f"missing endpoints: {missing}; saw: {paths}"
