"""Tests for asset endpoints (Steps 5-6 in architecture_flow.md)."""
import uuid


def _campaign_payload(name=None):
    return {
        "name": name or f"asset-camp-{uuid.uuid4().hex[:8]}",
        "source_provider": "youtube",
    }


def _asset_payload(campaign_id, source_url=None):
    return {
        "campaign_id": campaign_id,
        "source_url": source_url or f"https://youtube.com/watch?v={uuid.uuid4().hex}",
        "source_id": uuid.uuid4().hex[:11],
        "source_provider": "youtube",
        "extra_metadata": {"title": "test video"},
    }


def _create_campaign(client, auth_headers):
    r = client.post(
        "/campaigns", json=_campaign_payload(), headers=auth_headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_create_asset_success(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    r = client.post("/assets", json=_asset_payload(cid), headers=auth_headers)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "pending"
    assert data["campaign_id"] == cid
    assert data["source_provider"] == "youtube"
    assert data["asset_type"] == "video"


def test_create_asset_invalid_campaign(client, auth_headers):
    r = client.post(
        "/assets", json=_asset_payload(9999999), headers=auth_headers
    )
    assert r.status_code == 409  # campaign not found


def test_create_asset_invalid_url(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    bad = _asset_payload(cid)
    bad["source_url"] = "ftp://invalid"
    r = client.post("/assets", json=bad, headers=auth_headers)
    assert r.status_code == 422


def test_create_asset_source_provider_inherited_from_campaign(client, auth_headers):
    """If source_provider omitted in asset, inherits from campaign."""
    cid = _create_campaign(client, auth_headers)
    payload = _asset_payload(cid)
    payload["source_provider"] = None  # explicit None
    r = client.post("/assets", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["source_provider"] == "youtube"  # inherited


def test_bulk_create_assets(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    payload = {
        "assets": [
            _asset_payload(cid),
            _asset_payload(cid),
            _asset_payload(cid),
        ]
    }
    r = client.post("/assets/bulk", json=payload, headers=auth_headers)
    assert r.status_code == 201
    assert len(r.json()) == 3


def test_resolve_endpoint_returns_empty_stub(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    r = client.post(f"/assets/resolve/{cid}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []  # stub


def test_resolve_unknown_campaign(client, auth_headers):
    r = client.post("/assets/resolve/9999999", headers=auth_headers)
    assert r.status_code == 404


def test_get_asset(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    r = client.post("/assets", json=_asset_payload(cid), headers=auth_headers)
    aid = r.json()["id"]
    g = client.get(f"/assets/{aid}", headers=auth_headers)
    assert g.status_code == 200
    assert g.json()["id"] == aid


def test_list_assets(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    for _ in range(3):
        client.post("/assets", json=_asset_payload(cid), headers=auth_headers)
    r = client.get("/assets", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) >= 3


def test_list_assets_filter_by_campaign(client, auth_headers):
    cid1 = _create_campaign(client, auth_headers)
    cid2 = _create_campaign(client, auth_headers)
    client.post("/assets", json=_asset_payload(cid1), headers=auth_headers)
    client.post("/assets", json=_asset_payload(cid2), headers=auth_headers)
    r1 = client.get(f"/assets?campaign_id={cid1}", headers=auth_headers)
    assert all(a["campaign_id"] == cid1 for a in r1.json())
    assert len(r1.json()) == 1


def test_list_assets_filter_by_status(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    r = client.post("/assets", json=_asset_payload(cid), headers=auth_headers)
    aid = r.json()["id"]
    client.patch(f"/assets/{aid}", json={"status": "downloaded"}, headers=auth_headers)
    r2 = client.get("/assets?status=downloaded", headers=auth_headers)
    assert all(a["status"] == "downloaded" for a in r2.json())
    assert any(a["id"] == aid for a in r2.json())


def test_update_asset_status_transitions(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    r = client.post("/assets", json=_asset_payload(cid), headers=auth_headers)
    aid = r.json()["id"]

    # pending -> downloaded (should set downloaded_at)
    u = client.patch(
        f"/assets/{aid}",
        json={"status": "downloaded", "local_path": "C:\\data\\vid.mp4", "file_size": 1024},
        headers=auth_headers,
    )
    assert u.status_code == 200
    data = u.json()
    assert data["status"] == "downloaded"
    assert data["downloaded_at"] is not None
    assert data["local_path"] == "C:\\data\\vid.mp4"
    assert data["file_size"] == 1024

    # downloaded -> transcribed (should set transcribed_at)
    u = client.patch(
        f"/assets/{aid}",
        json={"status": "transcribed", "duration_seconds": 213.5},
        headers=auth_headers,
    )
    assert u.status_code == 200
    data = u.json()
    assert data["status"] == "transcribed"
    assert data["transcribed_at"] is not None
    assert data["duration_seconds"] == 213.5


def test_update_asset_invalid_status(client, auth_headers):
    """Pydantic field_validator catches invalid status before the service,
    so we get 422 (Unprocessable Entity) not 400."""
    cid = _create_campaign(client, auth_headers)
    r = client.post("/assets", json=_asset_payload(cid), headers=auth_headers)
    aid = r.json()["id"]
    u = client.patch(f"/assets/{aid}", json={"status": "inventado"}, headers=auth_headers)
    assert u.status_code == 422


def test_update_asset_extra_metadata_merges(client, auth_headers):
    cid = _create_campaign(client, auth_headers)
    payload = _asset_payload(cid)
    payload["extra_metadata"] = {"channel": "x", "views": 100}
    r = client.post("/assets", json=payload, headers=auth_headers)
    aid = r.json()["id"]
    u = client.patch(
        f"/assets/{aid}",
        json={"extra_metadata": {"likes": 999}},
        headers=auth_headers,
    )
    assert u.status_code == 200
    meta = u.json()["extra_metadata"]
    assert meta["channel"] == "x"   # preserved
    assert meta["views"] == 100     # preserved
    assert meta["likes"] == 999    # added


def test_get_asset_404(client, auth_headers):
    r = client.get(f"/assets/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


def test_assets_require_auth(client):
    r = client.post(
        "/assets", json={"campaign_id": 1, "source_url": "https://x.com/y"}
    )
    assert r.status_code in (401, 403)
    r2 = client.get("/assets")
    assert r2.status_code in (401, 403)
