"""Tests for campaign endpoints (Step 4 in architecture_flow.md).

Covers: CRUD, source_provider filter, status transitions, validation.
"""
import uuid


def _payload(name="test-campaign", **overrides):
    p = {
        "name": name,
        "source_instructions": "Make vertical clips with subtitles",
        "spec": {
            "duration_min": 20,
            "duration_max": 60,
            "captions_required": True,
            "format": "9:16",
            "keywords": ["ai", "tech"],
        },
    }
    p.update(overrides)
    return p


def test_create_campaign_success(client, auth_headers):
    r = client.post(
        "/campaigns",
        json=_payload(name=f"c1-{uuid.uuid4().hex[:8]}"),
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "draft"
    assert data["source_provider"] == "manual"  # default
    assert data["spec"]["format"] == "9:16"
    assert data["spec"]["captions_required"] is True
    assert data["assets_count"] == 0


def test_create_campaign_duplicate_returns_409(client, auth_headers):
    name = f"dup-{uuid.uuid4().hex[:8]}"
    r1 = client.post("/campaigns", json=_payload(name=name), headers=auth_headers)
    assert r1.status_code == 201
    r2 = client.post("/campaigns", json=_payload(name=name), headers=auth_headers)
    assert r2.status_code == 409


def test_create_campaign_with_source(client, auth_headers):
    r = client.post(
        "/campaigns",
        json=_payload(
            name=f"src-{uuid.uuid4().hex[:8]}",
            source_provider="youtube",
            source_id="dQw4w9WgXcQ",
            source_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
            source_metadata={"channel": "test", "duration_s": 213},
        ),
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["source_provider"] == "youtube"
    assert data["source_id"] == "dQw4w9WgXcQ"
    assert data["source_url"] == "https://youtube.com/watch?v=dQw4w9WgXcQ"
    assert data["source_metadata"]["channel"] == "test"


def test_create_campaign_invalid_source_rejected(client, auth_headers):
    r = client.post(
        "/campaigns",
        json=_payload(
            name=f"inv-{uuid.uuid4().hex[:8]}",
            source_provider="inventado",
        ),
        headers=auth_headers,
    )
    assert r.status_code == 422  # Pydantic validation error


def test_filter_by_source_provider(client, auth_headers):
    name_y = f"yt-{uuid.uuid4().hex[:8]}"
    name_t = f"tw-{uuid.uuid4().hex[:8]}"
    client.post(
        "/campaigns",
        json=_payload(name=name_y, source_provider="youtube"),
        headers=auth_headers,
    )
    client.post(
        "/campaigns",
        json=_payload(name=name_t, source_provider="twitter"),
        headers=auth_headers,
    )

    r_y = client.get("/campaigns?source_provider=youtube", headers=auth_headers)
    assert r_y.status_code == 200
    names_y = [c["name"] for c in r_y.json()]
    assert name_y in names_y
    assert name_t not in names_y

    r_t = client.get("/campaigns?source_provider=twitter", headers=auth_headers)
    names_t = [c["name"] for c in r_t.json()]
    assert name_t in names_t
    assert name_y not in names_t


def test_get_campaign(client, auth_headers):
    name = f"get-{uuid.uuid4().hex[:8]}"
    r = client.post("/campaigns", json=_payload(name=name), headers=auth_headers)
    cid = r.json()["id"]
    g = client.get(f"/campaigns/{cid}", headers=auth_headers)
    assert g.status_code == 200
    assert g.json()["name"] == name
    assert g.json()["spec"]["duration_max"] == 60


def test_list_campaigns(client, auth_headers):
    name = f"list-{uuid.uuid4().hex[:8]}"
    client.post("/campaigns", json=_payload(name=name), headers=auth_headers)
    r = client.get("/campaigns", headers=auth_headers)
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    assert name in names


def test_list_campaigns_filter_by_status(client, auth_headers):
    name = f"flt-{uuid.uuid4().hex[:8]}"
    r = client.post("/campaigns", json=_payload(name=name), headers=auth_headers)
    cid = r.json()["id"]
    r1 = client.get("/campaigns?status=ready", headers=auth_headers)
    assert all(c["id"] != cid for c in r1.json())
    r2 = client.get("/campaigns?status=draft", headers=auth_headers)
    assert any(c["id"] == cid for c in r2.json())


def test_update_campaign_status(client, auth_headers):
    name = f"upd-{uuid.uuid4().hex[:8]}"
    r = client.post("/campaigns", json=_payload(name=name), headers=auth_headers)
    cid = r.json()["id"]
    u = client.patch(
        f"/campaigns/{cid}",
        json={"status": "ready"},
        headers=auth_headers,
    )
    assert u.status_code == 200
    assert u.json()["status"] == "ready"


def test_update_campaign_invalid_status(client, auth_headers):
    name = f"inv-{uuid.uuid4().hex[:8]}"
    r = client.post("/campaigns", json=_payload(name=name), headers=auth_headers)
    cid = r.json()["id"]
    u = client.patch(
        f"/campaigns/{cid}",
        json={"status": "inventado"},
        headers=auth_headers,
    )
    assert u.status_code == 400


def test_update_campaign_spec(client, auth_headers):
    name = f"spec-{uuid.uuid4().hex[:8]}"
    r = client.post("/campaigns", json=_payload(name=name), headers=auth_headers)
    cid = r.json()["id"]
    u = client.patch(
        f"/campaigns/{cid}",
        json={
            "spec": {
                "duration_min": 30,
                "duration_max": 90,
                "format": "1:1",
            },
        },
        headers=auth_headers,
    )
    assert u.status_code == 200
    assert u.json()["spec"]["format"] == "1:1"
    assert u.json()["spec"]["duration_max"] == 90


def test_update_campaign_source_metadata_merges(client, auth_headers):
    """source_metadata PATCH debe mergear sobre el existente, no reemplazar."""
    name = f"merge-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/campaigns",
        json=_payload(
            name=name,
            source_provider="youtube",
            source_metadata={"channel": "x", "sub_count": 100},
        ),
        headers=auth_headers,
    )
    cid = r.json()["id"]
    u = client.patch(
        f"/campaigns/{cid}",
        json={"source_metadata": {"likes": 999}},
        headers=auth_headers,
    )
    assert u.status_code == 200
    meta = u.json()["source_metadata"]
    assert meta["channel"] == "x"        # preserved
    assert meta["sub_count"] == 100     # preserved
    assert meta["likes"] == 999        # added


def test_get_campaign_404(client, auth_headers):
    r = client.get("/campaigns/9999999", headers=auth_headers)
    assert r.status_code == 404


def test_campaigns_require_auth(client):
    r = client.post("/campaigns", json=_payload(name="x"))
    assert r.status_code in (401, 403)
    r2 = client.get("/campaigns")
    assert r2.status_code in (401, 403)
