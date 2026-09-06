"""Tests for Candidate endpoints (Steps 13-14 in architecture_flow.md)."""
import uuid


def _create_campaign(client, auth_headers):
    r = client.post(
        "/campaigns",
        json={"name": f"cand-camp-{uuid.uuid4().hex[:8]}"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_asset(client, auth_headers, campaign_id):
    r = client.post(
        "/assets",
        json={
            "campaign_id": campaign_id,
            "source_url": f"https://youtube.com/watch?v={uuid.uuid4().hex}",
            "source_provider": "youtube",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _payload(campaign_id, asset_id, **overrides):
    p = {
        "campaign_id": campaign_id,
        "asset_id": asset_id,
        "start_time": 10.0,
        "end_time": 30.0,
        "score": 0.85,
        "reasoning": "good hook",
    }
    p.update(overrides)
    return p


def test_create_candidate_success(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post(
        "/candidates", json=_payload(cid, aid), headers=auth_headers
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "pending"
    assert data["start_time"] == 10.0
    assert data["end_time"] == 30.0
    assert data["score"] == 0.85


def test_create_candidate_time_order_enforced(client, auth_headers):
    """end_time must be > start_time (CHECK constraint + Pydantic)."""
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post(
        "/candidates",
        json=_payload(cid, aid, start_time=30.0, end_time=10.0),
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_create_candidate_invalid_asset(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    r = client.post(
        "/candidates",
        json=_payload(cid, str(uuid.uuid4())),
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_create_candidate_asset_wrong_campaign(client, auth_headers):
    cid1 = _create_campaign(client, auth_headers)
    cid2 = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid1)
    r = client.post(
        "/candidates",
        json=_payload(cid2, aid),  # asset belongs to cid1, not cid2
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_bulk_create_candidates(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    payloads = [
        _payload(cid, aid, start_time=0.0, end_time=15.0),
        _payload(cid, aid, start_time=20.0, end_time=35.0),
        _payload(cid, aid, start_time=40.0, end_time=55.0),
    ]
    r = client.post("/candidates/bulk", json=payloads, headers=auth_headers)
    assert r.status_code == 201
    assert len(r.json()) == 3


def test_get_candidate(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post("/candidates", json=_payload(cid, aid), headers=auth_headers)
    cand_id = r.json()["id"]
    g = client.get(f"/candidates/{cand_id}", headers=auth_headers)
    assert g.status_code == 200
    assert g.json()["id"] == cand_id


def test_list_candidates_filter_by_campaign(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    client.post("/candidates", json=_payload(cid, aid), headers=auth_headers)
    r = client.get(f"/candidates?campaign_id={cid}", headers=auth_headers)
    assert r.status_code == 200
    assert all(c["campaign_id"] == cid for c in r.json())


def test_update_candidate_status(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post("/candidates", json=_payload(cid, aid), headers=auth_headers)
    cand_id = r.json()["id"]
    u = client.patch(
        f"/candidates/{cand_id}",
        json={"status": "approved"},
        headers=auth_headers,
    )
    assert u.status_code == 200
    assert u.json()["status"] == "approved"


def test_update_candidate_invalid_status(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post("/candidates", json=_payload(cid, aid), headers=auth_headers)
    cand_id = r.json()["id"]
    u = client.patch(
        f"/candidates/{cand_id}",
        json={"status": "inventado"},
        headers=auth_headers,
    )
    assert u.status_code == 422


def test_get_candidate_404(client, auth_headers):
    r = client.get(f"/candidates/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


def test_candidates_require_auth(client):
    r = client.post(
        "/candidates",
        json={
            "campaign_id": 1,
            "asset_id": str(uuid.uuid4()),
            "start_time": 0,
            "end_time": 10,
        },
    )
    assert r.status_code in (401, 403)
