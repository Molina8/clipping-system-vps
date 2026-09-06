"""Tests for worker registry endpoints: register, heartbeat, list, get."""
import os
import uuid


def _registration_payload(worker_id: str = "test-worker-01") -> dict:
    return {
        "worker_id": worker_id,
        "system": {
            "os": "Windows",
            "os_version": "11",
            "cpu": "AMD Ryzen 9",
            "ram_total_gb": 32.0,
            "ram_available_gb": 16.0,
            "python_version": "3.11.5",
        },
        "gpu": {
            "name": "NVIDIA RTX 5070 Ti",
            "available": True,
            "vram_total_gb": 16.0,
            "cuda_available": True,
            "cuda_version": "12.4",
        },
        "tools": {"ffmpeg": True, "ffprobe": True, "whisperx": True},
        "capabilities": ["transcribe", "render", "qa", "download", "health"],
    }


def test_register_worker_success(client, auth_headers):
    wid = f"test-worker-{uuid.uuid4().hex[:8]}"
    r = client.post("/worker/register", json=_registration_payload(wid), headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["worker_id"] == wid
    assert data["status"] == "online"
    assert data["gpu_name"] == "NVIDIA RTX 5070 Ti"
    assert data["gpu_available"] is True
    assert "transcribe" in data["capabilities"]
    assert data["last_heartbeat_at"] is not None
    assert data["last_registered_at"] is not None


def test_register_worker_requires_auth(client):
    r = client.post("/worker/register", json=_registration_payload("x"))
    assert r.status_code in (401, 403)


def test_register_worker_idempotent(client, auth_headers):
    wid = f"test-worker-{uuid.uuid4().hex[:8]}"
    payload = _registration_payload(wid)
    r1 = client.post("/worker/register", json=payload, headers=auth_headers)
    assert r1.status_code == 200
    # second register should still succeed and update fields
    payload["gpu"]["available"] = False
    payload["gpu"]["name"] = "Disabled-GPU"
    r2 = client.post("/worker/register", json=payload, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["gpu_available"] is False
    assert r2.json()["gpu_name"] == "Disabled-GPU"


def test_heartbeat_updates_last_seen(client, auth_headers):
    wid = f"test-worker-{uuid.uuid4().hex[:8]}"
    client.post("/worker/register", json=_registration_payload(wid), headers=auth_headers)

    hb = {
        "worker_id": wid,
        "status": "busy",
        "current_job": "12345678-1234-1234-1234-123456789012",
        "gpu": {"name": "NVIDIA RTX 5070 Ti", "available": True},
        "system": {"cpu_load": 0.42},
    }
    r = client.post("/worker/heartbeat", json=hb, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "busy"
    # system payload merges into stored JSON
    assert data["gpu_name"] == "NVIDIA RTX 5070 Ti"


def test_heartbeat_unknown_worker_returns_404(client, auth_headers):
    r = client.post(
        "/worker/heartbeat",
        json={"worker_id": "ghost-worker", "status": "online"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_list_workers(client, auth_headers):
    # register at least one
    wid = f"list-worker-{uuid.uuid4().hex[:8]}"
    client.post("/worker/register", json=_registration_payload(wid), headers=auth_headers)
    r = client.get("/worker", headers=auth_headers)
    assert r.status_code == 200
    ids = [w["worker_id"] for w in r.json()]
    assert wid in ids


def test_get_worker(client, auth_headers):
    wid = f"get-worker-{uuid.uuid4().hex[:8]}"
    client.post("/worker/register", json=_registration_payload(wid), headers=auth_headers)
    r = client.get(f"/worker/{wid}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["worker_id"] == wid


def test_get_worker_404_when_missing(client, auth_headers):
    r = client.get("/worker/no-such-worker-zz", headers=auth_headers)
    assert r.status_code == 404
