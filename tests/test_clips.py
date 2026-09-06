"""Tests for Clip endpoints (Steps 16-19 in architecture_flow.md)."""
import uuid


def _create_campaign(client, auth_headers):
    r = client.post(
        "/campaigns",
        json={"name": f"clip-camp-{uuid.uuid4().hex[:8]}"},
        headers=auth_headers,
    )
    assert r.status_code == 201
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
    assert r.status_code == 201
    return r.json()["id"]


def _payload(campaign_id, asset_id, **overrides):
    p = {
        "campaign_id": campaign_id,
        "asset_id": asset_id,
        "file_path": "C:\\clips\\test.mp4",
        "duration_seconds": 28.5,
        "file_size": 1024000,
    }
    p.update(overrides)
    return p


def test_create_clip_success(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post("/clips", json=_payload(cid, aid), headers=auth_headers)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "created"
    assert data["qa_status"] == "pending"
    assert data["duration_seconds"] == 28.5


def test_create_clip_invalid_qa_status(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post(
        "/clips",
        json=_payload(cid, aid, qa_status="inventado"),
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_create_clip_invalid_status(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post(
        "/clips",
        json=_payload(cid, aid, status="inventado"),
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_create_clip_invalid_asset(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    r = client.post(
        "/clips", json=_payload(cid, str(uuid.uuid4())), headers=auth_headers
    )
    assert r.status_code == 409


def test_get_clip(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post("/clips", json=_payload(cid, aid), headers=auth_headers)
    clip_id = r.json()["id"]
    g = client.get(f"/clips/{clip_id}", headers=auth_headers)
    assert g.status_code == 200
    assert g.json()["id"] == clip_id


def test_list_clips_filter_by_status(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r1 = client.post("/clips", json=_payload(cid, aid), headers=auth_headers)
    clip_id = r1.json()["id"]
    client.patch(
        f"/clips/{clip_id}",
        json={"qa_status": "pass", "status": "approved"},
        headers=auth_headers,
    )
    r = client.get("/clips?qa_status=pass", headers=auth_headers)
    assert r.status_code == 200
    assert all(c["qa_status"] == "pass" for c in r.json())


def test_update_clip_qa_pass_sets_status(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post("/clips", json=_payload(cid, aid), headers=auth_headers)
    clip_id = r.json()["id"]
    u = client.patch(
        f"/clips/{clip_id}",
        json={"qa_status": "pass", "qa_result": {"duration_ok": True, "resolution_ok": True}},
        headers=auth_headers,
    )
    assert u.status_code == 200
    data = u.json()
    assert data["qa_status"] == "pass"
    assert data["qa_result"]["duration_ok"] is True
    assert data["qa_at"] is not None


def test_update_clip_qa_fail(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post("/clips", json=_payload(cid, aid), headers=auth_headers)
    clip_id = r.json()["id"]
    u = client.patch(
        f"/clips/{clip_id}",
        json={"qa_status": "fail", "qa_result": {"reason": "duration_too_short"}},
        headers=auth_headers,
    )
    assert u.status_code == 200
    assert u.json()["qa_status"] == "fail"


def test_update_clip_qa_invalid(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    aid = _create_asset(client, auth_headers, cid)
    r = client.post("/clips", json=_payload(cid, aid), headers=auth_headers)
    clip_id = r.json()["id"]
    u = client.patch(
        f"/clips/{clip_id}",
        json={"qa_status": "inventado"},
        headers=auth_headers,
    )
    assert u.status_code == 422


def test_get_clip_404(client, auth_headers):
    r = client.get(f"/clips/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


def test_clips_require_auth(client):
    r = client.post(
        "/clips",
        json={"campaign_id": 1, "asset_id": str(uuid.uuid4())},
    )
    assert r.status_code in (401, 403)
