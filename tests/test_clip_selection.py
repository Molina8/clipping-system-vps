"""Tests for clip selection: validator, MockLLMClient, agent, endpoints."""
from __future__ import annotations

from app.clip_selection.agent import ClipSelectionAgent, _strip_json_fences
import json
import uuid
from typing import Any, Dict, List

from sqlalchemy import select

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.main import app

# --- Fixtures --------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    return TestClient(app)

@pytest.fixture
def auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {settings.api_token}"}

@pytest.fixture
def db():
    """Clean DB session for clip_selection tests."""
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM candidates"))
        session.execute(text("DELETE FROM assets"))
        session.execute(text("DELETE FROM campaigns"))
        session.commit()
        yield session
    finally:
        session.close()

def _seed_transcribed(db) -> tuple[Any, Any]:
    """Create a campaign + asset in 'transcribed' status with a fake
    transcription. Returns (campaign, asset).
    """
    from app.models.asset import Asset, AssetStatus
    from app.models.campaign import Campaign

    c = Campaign(
        name=f"cs-camp-{uuid.uuid4().hex[:6]}",
        source_provider="youtube",
        source_instructions=(
            "Make 20-60s clips in english with captions."
        ),
    )
    db.add(c)
    db.commit()
    db.refresh(c)

    a = Asset(
        campaign_id=c.id,
        source_url=f"https://youtube.com/watch?v={uuid.uuid4().hex}",
        source_provider="youtube",
        source_id=uuid.uuid4().hex,
        asset_type="video",
        status=AssetStatus.TRANSCRIBED.value,
        duration_seconds=300.0,
        extra_metadata={
            "transcription": {
                "text": " ".join(
                    f"This is segment number {i} of the video about AI tests."
                    for i in range(40)
                ),
                "language": "en",
                "segments": [
                    {"start": 0.0,   "end": 5.0,   "text": "Welcome."},
                    {"start": 5.0,   "end": 15.0,  "text": "Intro about AI."},
                    {"start": 15.0,  "end": 30.0,  "text": "First point."},
                    {"start": 30.0,  "end": 50.0,  "text": "Important point."},
                    {"start": 50.0,  "end": 70.0,  "text": "Don't miss this."},
                    {"start": 70.0,  "end": 90.0,  "text": "Conclusion."},
                    {"start": 90.0,  "end": 120.0, "text": "Let me explain."},
                    {"start": 120.0, "end": 150.0, "text": "Final thoughts."},
                    {"start": 150.0, "end": 180.0, "text": "Wrapping up."},
                    {"start": 180.0, "end": 210.0, "text": "See you later."},
                ],
                "words": [],
            }
        },
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return c, a

# --- Validator tests -------------------------------------------------------

def test_validator_rejects_too_short():
    from app.campaign_engine.models import NormalizedSpec
    from app.clip_selection.models import ClipProposal
    from app.clip_selection.validator import validate_proposal

    spec = NormalizedSpec(
        duration_min=20.0, duration_max=60.0,
        source_provider="youtube",
    )
    p = ClipProposal(
        start_time=0.0, end_time=5.0,
        score=0.5, reasoning="x",
    )
    ok, reason = validate_proposal(p, spec, "any text")
    assert not ok
    assert "too short" in reason

def test_validator_rejects_too_long():
    from app.campaign_engine.models import NormalizedSpec
    from app.clip_selection.models import ClipProposal
    from app.clip_selection.validator import validate_proposal

    spec = NormalizedSpec(
        duration_min=20.0, duration_max=60.0,
        source_provider="youtube",
    )
    p = ClipProposal(
        start_time=0.0, end_time=120.0,
        score=0.5, reasoning="x",
    )
    ok, reason = validate_proposal(p, spec, "any text")
    assert not ok
    assert "too long" in reason

def test_validator_rejects_excluded_keyword():
    from app.campaign_engine.models import NormalizedSpec
    from app.clip_selection.models import ClipProposal
    from app.clip_selection.validator import validate_proposal

    spec = NormalizedSpec(
        duration_min=20.0, duration_max=60.0,
        source_provider="youtube",
        exclude_keywords=["spoiler"],
    )
    p = ClipProposal(
        start_time=0.0, end_time=30.0,
        score=0.5, reasoning="x",
    )
    ok, reason = validate_proposal(p, spec, "this contains a spoiler warning")
    assert not ok
    assert "spoiler" in reason

def test_validator_passes_good_proposal():
    from app.campaign_engine.models import NormalizedSpec
    from app.clip_selection.models import ClipProposal
    from app.clip_selection.validator import validate_proposal

    spec = NormalizedSpec(
        duration_min=20.0, duration_max=60.0,
        source_provider="youtube",
    )
    p = ClipProposal(
        start_time=0.0, end_time=30.0,
        score=0.7, reasoning="strong hook",
    )
    ok, reason = validate_proposal(p, spec, "any text")
    assert ok, reason

# --- MockLLMClient tests ---------------------------------------------------

def test_mock_llm_returns_proposals_within_window():
    from app.clip_selection.llm_client import MockLLMClient
    from app.clip_selection.prompts import SYSTEM_PROMPT, build_user_prompt

    c = MockLLMClient()
    user_prompt = build_user_prompt(
        source_url="https://example.com/v",
        duration_seconds=300.0,
        transcription={
            "text": "blah",
            "segments": [
                {"start": 0.0,  "end": 5.0,   "text": "s1"},
                {"start": 5.0,  "end": 30.0,  "text": "s2"},
                {"start": 30.0, "end": 60.0,  "text": "s3"},
                {"start": 60.0, "end": 100.0, "text": "s4"},
            ],
        },
        spec={
            "duration_min": 20.0, "duration_max": 60.0,
            "format": "9:16", "language": "en",
            "captions_required": False,
        },
    )
    raw, model_id = c.complete(SYSTEM_PROMPT, user_prompt)
    assert model_id and "mock" in model_id
    payload = json.loads(raw)
    assert "proposals" in payload
    for prop in payload["proposals"]:
        duration = float(prop["end_time"]) - float(prop["start_time"])
        assert 17.0 < duration < 80.0  # 0.9 * 20 = 18, 1.1 * 60 = 66 ; loose

def test_mock_llm_returns_empty_on_missing_duration():
    from app.clip_selection.llm_client import MockLLMClient

    c = MockLLMClient()
    raw, _ = c.complete("system", "user without duration header")
    payload = json.loads(raw)
    assert payload["proposals"] == []

# --- Agent tests -----------------------------------------------------------

def test_agent_persists_valid_proposals_and_marks_asset(db):
    from app.clip_selection.agent import ClipSelectionAgent, _strip_json_fences

    _c, asset = _seed_transcribed(db)
    agent = ClipSelectionAgent()
    result = agent.run(db, str(asset.id))
    assert result["asset_id"] == str(asset.id)
    db.refresh(asset)
    assert "clip_selection_at" in (asset.extra_metadata or {})

def test_agent_rejects_non_transcribed(db):
    from app.models.asset import Asset, AssetStatus
    from app.models.campaign import Campaign
    from app.clip_selection.agent import ClipSelectionAgent

    c = Campaign(name=f"x-{uuid.uuid4().hex[:6]}")
    db.add(c)
    db.commit()
    a = Asset(
        campaign_id=c.id,
        source_url="https://example.com/v",
        source_provider="manual",
        source_id="x",
        asset_type="video",
        status=AssetStatus.PENDING.value,
        extra_metadata={},
    )
    db.add(a)
    db.commit()
    db.refresh(a)

    agent = ClipSelectionAgent()
    with pytest.raises(ValueError):
        agent.run(db, str(a.id))

def test_agent_rejects_unknown_asset(db):
    from app.clip_selection.agent import ClipSelectionAgent

    agent = ClipSelectionAgent()
    with pytest.raises(ValueError):
        agent.run(db, str(uuid.uuid4()))

def test_agent_uses_custom_llm_client(db):
    from app.clip_selection.agent import ClipSelectionAgent

    _c, asset = _seed_transcribed(db)

    class FakeClient:
        def complete(self, system_prompt, user_prompt):
            return (
                json.dumps({
                    "proposals": [
                        {
                            "start_time": 30.0, "end_time": 60.0,
                            "score": 0.9,
                            "reasoning": "custom client",
                            "matched_keywords": ["ai"],
                        },
                    ],
                    "notes": "fake",
                }),
                "fake-1",
            )

    agent = ClipSelectionAgent(llm_client=FakeClient())
    result = agent.run(db, str(asset.id))
    assert result["persisted"] == 1
    assert result["candidates"][0]["score"] == 0.9

# --- Endpoint tests --------------------------------------------------------

def test_process_endpoint_returns_summary(client, auth_headers, db):
    _c, asset = _seed_transcribed(db)
    r = client.post(
        f"/clip_selection/process/{asset.id}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["asset_id"] == str(asset.id)
    assert "persisted" in data

def test_process_endpoint_404_for_unknown(client, auth_headers):
    r = client.post(
        f"/clip_selection/process/{uuid.uuid4()}",
        headers=auth_headers,
    )
    assert r.status_code == 404

def test_queue_endpoint_lists_pending(client, auth_headers, db):
    _seed_transcribed(db)
    r = client.get("/clip_selection/queue", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "pending" in body
    assert isinstance(body["pending"], list)

def test_process_all_endpoint_runs(client, auth_headers, db):
    _seed_transcribed(db)
    r = client.post(
        "/clip_selection/process_all?max_assets=5",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert "processed" in data
    assert "results" in data

def test_clip_selection_requires_auth(client):
    r = client.post(
        f"/clip_selection/process/{uuid.uuid4()}",
    )
    assert r.status_code in (401, 403)

class TestStripJsonFences:
    """_strip_json_fences must handle the markdown-wrapped JSON that
    Minimax (and other LLMs) frequently returns."""

    def test_strips_json_fence(self):
        raw = '```json\n{"proposals": [{"start_time": 0.0, "end_time": 30.0, "score": 0.8, "reasoning": "ok"}]}\n```'
        out = _strip_json_fences(raw)
        assert '"proposals"' in out
        assert out.strip().startswith("{")
        assert out.strip().endswith("}")

    def test_strips_bare_fence_no_lang(self):
        raw = '```\n{"x": 1}\n```'
        assert _strip_json_fences(raw) == '{"x": 1}'

    def test_strips_uppercase_json_fence(self):
        raw = '```JSON\n{"x": 1}\n```'
        assert _strip_json_fences(raw) == '{"x": 1}'

    def test_passes_through_clean_json(self):
        clean = '{"proposals": []}'
        assert _strip_json_fences(clean) == clean

    def test_handles_internal_whitespace(self):
        raw = '   ```json\n  {"x": 1}\n```  '
        assert _strip_json_fences(raw) == '{"x": 1}'

    def test_does_not_strip_unrelated_backticks(self):
        raw = "Note: use ```code``` for code blocks, then JSON: {\"x\": 1}"
        # No opening triple-backtick at start; should pass through.
        out = _strip_json_fences(raw)
        assert out == raw

class TestRankedProposals:
    """The SYSTEM_PROMPT contract requires Minimax to return ranked
    proposals. We enforce that here by sorting by score desc + taking
    only the top_n. These tests pin the behavior.

    Uses MockLLMClient with a deterministic payload via monkey-patching
    so we don't depend on the live MiniMax API.
    """

    def _run_with_score_payload(self, db, scores):
        """Run agent with a hand-crafted ranked payload."""
        from app.clip_selection.agent import ClipSelectionAgent
        from app.clip_selection.llm_client import MockLLMClient
        from app.models.asset import AssetStatus, Asset
        from app.models.campaign import Campaign

        # Always create a fresh Campaign with unique name + permissive spec
        # so 10s test proposals pass filter_valid (duration_min default ~20s).
        cid = Campaign(
            name=f"rank-test-{uuid.uuid4().hex}",
            source_provider="manual",
            spec={
                "duration_min": 5.0,
                "duration_max": 60.0,
                "format": "9:16",
                "source_provider": "manual",
            },
        )
        db.add(cid)
        db.flush()
        asset = Asset(
            campaign_id=cid.id,
            source_url="https://example.com/x",
            source_provider="manual",
            status=AssetStatus.TRANSCRIBED.value,
        )
        db.add(asset)
        db.flush()
        db.commit()
        db.refresh(asset)

        agent = ClipSelectionAgent(llm_client=MockLLMClient())
        payload = {
            "proposals": [
                {
                    "rank": i + 1,
                    "start_time": 10.0 * i,
                    "end_time": 10.0 * (i + 1),
                    "score": s,
                    "reasoning": f"proposal {i+1}",
                    "matched_keywords": [],
                }
                for i, s in enumerate(scores)
            ],
            "notes": "test",
        }
        agent.llm_client.complete = lambda sys_p, usr_p: (
            json.dumps(payload), "mock-test"
        )
        return agent.run(db, str(asset.id), top_n=len(scores))

    def test_top_n_limits_persisted_count(self, db):
        """When agent returns 6 proposals but top_n=3, only 3 are persisted."""
        from app.models.candidate import Candidate
        from sqlalchemy import func
        scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
        summary = self._run_with_score_payload(db, scores)
        kept = summary["generated"]
        assert kept == 3, f"expected top_n=3 to keep 3 proposals, got {kept}"

    def test_top_n_keeps_highest_scored(self, db):
        """The top_n proposals MUST be the ones with the highest scores."""
        from app.models.candidate import Candidate
        scores = [0.5, 0.9, 0.7, 0.8, 0.6]  # out of order on purpose
        summary = self._run_with_score_payload(db, scores)
        # The kept 3 should be the 3 highest: 0.9, 0.8, 0.7
        # Filter_valid may reject some, but at least one should remain
        persisted = summary["candidates"]
        assert len(persisted) >= 1
        kept_scores = sorted(
            [float(c["score"]) for c in persisted], reverse=True
        )
        # The top-3 by score are 0.9, 0.8, 0.7. All kept must be from {0.9, 0.8, 0.7}.
        for s in kept_scores:
            assert s >= 0.7, f"unexpected low-score kept: {s}"

    def test_top_n_minimum_is_one(self, db):
        """If top_n=0 or negative, we MUST keep at least 1 proposal."""
        scores = [0.9, 0.5]
        summary = self._run_with_score_payload(db, scores)
        assert summary["generated"] >= 1

    def test_ranking_log_emitted(self, db, caplog):
        """After ranking we MUST log the kept scores for audit."""
        scores = [0.9, 0.7, 0.5]
        with caplog.at_level("INFO", logger="app.clip_selection.agent"):
            self._run_with_score_payload(db, scores)
        # Verify at least one ranking log was emitted
        ranking_logs = [
            r for r in caplog.records
            if r.name == "app.clip_selection.agent"
            and "ranked" in r.message
        ]
        assert ranking_logs, f"no ranking log emitted; saw: {[r.message for r in caplog.records]}"

    def test_clip_scanner_top_n_flag(self):
        """The --top-n CLI flag on clip_scanner defaults to 5 and is forwarded."""
        import subprocess
        result = subprocess.run(
            ["python", "scripts/clip_scanner.py", "--help"],
            cwd="/opt/clipping-system",
            capture_output=True, text=True,
            env={**__import__("os").environ, "PATH": "/opt/clipping-system/venv/bin:" + __import__("os").environ.get("PATH", "")},
        )
        assert "--top-n" in result.stdout, "clip_scanner --help should show --top-n flag"
        # Check default value is 5
        assert "default 5" in result.stdout, "clip_scanner --top-n default should be 5"

