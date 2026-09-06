"""Job CRUD + auth tests via the FastAPI client."""
from uuid import uuid4


def test_create_job_requires_auth(client):
    r = client.post("/jobs", json={"job_type": "transcribe", "payload": {}})
    assert r.status_code in {401, 403}


def test_create_job_success(client, auth_headers):
    r = client.post(
        "/jobs",
        json={"job_type": "transcribe", "payload": {"video": "C:\\CODIANT\\clipping\\test.mp4", "language": "es"}, "priority": 7},
        headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["job_type"] == "transcribe"
    assert body["status"] == "pending"
    assert body["priority"] == 7
    assert body["payload"]["video"] == "C:\\CODIANT\\clipping\\test.mp4"
    assert body["attempts"] == 0
    assert body["max_attempts"] == 3


def test_get_job_requires_auth(client, db):
    # create directly via DB
    from app.services import job_service
    job = job_service.create_job(db, job_type="qa", payload={})
    r = client.get(f"/jobs/{job.id}")
    assert r.status_code in {401, 403}


def test_get_job_success(client, auth_headers, db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="qa", payload={"x": 1})
    r = client.get(f"/jobs/{job.id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["job_type"] == "qa"


def test_get_job_404(client, auth_headers):
    r = client.get(f"/jobs/{uuid4()}", headers=auth_headers)
    assert r.status_code == 404


def test_list_jobs_requires_auth(client):
    r = client.get("/jobs")
    assert r.status_code in {401, 403}


def test_list_jobs_filter_by_status(client, auth_headers, db):
    from app.services import job_service
    job_service.create_job(db, job_type="render", payload={})
    job_service.create_job(db, job_type="qa", payload={})
    r = client.get("/jobs?status=pending", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_jobs_filter_by_job_type(client, auth_headers, db):
    from app.services import job_service
    job_service.create_job(db, job_type="render", payload={})
    job_service.create_job(db, job_type="qa", payload={})
    r = client.get("/jobs?job_type=render", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["job_type"] == "render"


def test_list_jobs_filter_by_worker_id(client, auth_headers, db):
    from app.services import job_service
    job_service.create_job(db, job_type="render", payload={})
    r = client.get("/jobs?worker_id=nonexistent", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_worker_next_requires_auth(client):
    r = client.get("/worker/jobs/next?worker_id=w1")
    assert r.status_code in {401, 403}


def test_worker_next_returns_204_when_empty(client, auth_headers):
    r = client.get("/worker/jobs/next?worker_id=w1", headers=auth_headers)
    assert r.status_code == 204


def test_worker_next_claims_pending_job(client, auth_headers, db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="render", payload={"src": "x"})
    r = client.get("/worker/jobs/next?worker_id=w1", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(job.id)
    assert body["job_type"] == "render"
    assert body["payload"]["src"] == "x"


def test_worker_result_validates_ownership(client, auth_headers, db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="render", payload={})
    # w1 claims and starts
    client.get("/worker/jobs/next?worker_id=w1", headers=auth_headers)
    client.post(f"/worker/jobs/{job.id}/start?worker_id=w1", headers=auth_headers)
    # w2 tries to complete
    r = client.post(
        f"/worker/jobs/{job.id}/result?worker_id=w2",
        json={"result": {"ok": True}},
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_worker_fail_validates_ownership(client, auth_headers, db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="render", payload={})
    client.get("/worker/jobs/next?worker_id=w1", headers=auth_headers)
    client.post(f"/worker/jobs/{job.id}/start?worker_id=w1", headers=auth_headers)
    r = client.post(
        f"/worker/jobs/{job.id}/fail?worker_id=w2",
        json={"error_message": "boom"},
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_heartbeat_requires_auth(client):
    r = client.post("/worker/jobs/00000000-0000-0000-0000-000000000000/heartbeat?worker_id=w1")
    assert r.status_code in {401, 403}


def test_heartbeat_404_for_unknown_job(client, auth_headers):
    r = client.post(
        "/worker/jobs/00000000-0000-0000-0000-000000000000/heartbeat?worker_id=w1",
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_heartbeat_403_for_wrong_worker(client, auth_headers, db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="transcribe", payload={})
    job_service.claim_next_job(db, worker_id="w1")
    r = client.post(
        f"/worker/jobs/{job.id}/heartbeat?worker_id=w2",
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_heartbeat_renews_lease_on_assigned(client, auth_headers, db):
    from datetime import datetime, timedelta, timezone
    from app.services import job_service
    job = job_service.create_job(db, job_type="transcribe", payload={})
    claimed = job_service.claim_next_job(db, worker_id="w1")
    initial_lease = claimed.lease_until
    claimed.lease_until = datetime.now(timezone.utc) + timedelta(seconds=10)
    db.commit()
    r = client.post(
        f"/worker/jobs/{job.id}/heartbeat?worker_id=w1",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "assigned"
    new_lease = datetime.fromisoformat(body["lease_until"].replace("Z", "+00:00"))
    assert new_lease > initial_lease


def test_heartbeat_renews_lease_on_processing(client, auth_headers, db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="transcribe", payload={})
    job_service.claim_next_job(db, worker_id="w1")
    job_service.start_processing(db, job.id, worker_id="w1")
    r = client.post(
        f"/worker/jobs/{job.id}/heartbeat?worker_id=w1",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "processing"


def test_heartbeat_409_on_pending_job(client, auth_headers, db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="transcribe", payload={})
    job.worker_id = "w1"  # edge case: pending with worker_id
    db.commit()
    r = client.post(
        f"/worker/jobs/{job.id}/heartbeat?worker_id=w1",
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_heartbeat_409_on_completed_job(client, auth_headers, db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="transcribe", payload={})
    job_service.claim_next_job(db, worker_id="w1")
    job_service.start_processing(db, job.id, worker_id="w1")
    job_service.complete_job(db, job.id, worker_id="w1", result={"ok": True})
    r = client.post(
        f"/worker/jobs/{job.id}/heartbeat?worker_id=w1",
        headers=auth_headers,
    )
    assert r.status_code == 409


# --- transcribe pre-enqueue validation ---------------------------------------


def test_transcribe_windows_path_accepted(client, auth_headers):
    r = client.post(
        "/jobs",
        json={"job_type": "transcribe", "payload": {"video": "C:\\CODIANT\\clipping\\test.mp4", "language": "es"}},
        headers=auth_headers,
    )
    assert r.status_code == 201


def test_transcribe_tailscale_path_accepted(client, auth_headers):
    r = client.post(
        "/jobs",
        json={"job_type": "transcribe", "payload": {"video": "\\\\VPS\\share\\clip.mp4", "language": "es"}},
        headers=auth_headers,
    )
    assert r.status_code == 201


def test_transcribe_local_file_present_accepted(client, auth_headers, tmp_path):
    f = tmp_path / "video.mp4"
    f.write_bytes(b"\x00" * 32)
    r = client.post(
        "/jobs",
        json={"job_type": "transcribe", "payload": {"video": str(f), "language": "es"}},
        headers=auth_headers,
    )
    assert r.status_code == 201


def test_transcribe_local_file_missing_rejected(client, auth_headers, tmp_path):
    missing = tmp_path / "nope.mp4"
    r = client.post(
        "/jobs",
        json={"job_type": "transcribe", "payload": {"video": str(missing), "language": "es"}},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "not found" in r.json()["detail"].lower()


def test_transcribe_http_url_unreachable_rejected(client, auth_headers):
    r = client.post(
        "/jobs",
        json={"job_type": "transcribe", "payload": {"video": "http://127.0.0.1:1/test.mp4", "language": "es"}},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "unreachable" in r.json()["detail"].lower() or "http" in r.json()["detail"].lower()


def test_transcribe_missing_video_field_rejected(client, auth_headers):
    r = client.post(
        "/jobs",
        json={"job_type": "transcribe", "payload": {"language": "es"}},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "video" in r.json()["detail"].lower()


def test_transcribe_empty_video_rejected(client, auth_headers):
    r = client.post(
        "/jobs",
        json={"job_type": "transcribe", "payload": {"video": "", "language": "es"}},
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_transcribe_non_string_video_rejected(client, auth_headers):
    r = client.post(
        "/jobs",
        json={"job_type": "transcribe", "payload": {"video": 12345, "language": "es"}},
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_non_transcribe_job_skips_video_validation(client, auth_headers):
    r = client.post(
        "/jobs",
        json={"job_type": "render", "payload": {"some_other_field": "value"}},
        headers=auth_headers,
    )
    assert r.status_code == 201


# --- transcribe end-to-end: result.data shape & error_message persistence -----


def test_transcribe_result_data_shape_preserved(client, auth_headers, db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="transcribe", payload={"video": "C:\\v.mp4"})
    job_service.claim_next_job(db, worker_id="w1")
    job_service.start_processing(db, job.id, worker_id="w1")
    result_data = {
        "language": "es",
        "duration": 598.0,
        "segments": [{"start": 0.0, "end": 4.2, "text": "Hola mundo", "speaker": None}],
        "words": [{"word": "Hola", "start": 0.12, "end": 0.45}],
    }
    r = client.post(
        f"/worker/jobs/{job.id}/result?worker_id=w1",
        json={"result": result_data},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["result"] == result_data
    assert body["result"]["language"] == "es"
    assert body["result"]["duration"] == 598.0
    assert len(body["result"]["segments"]) == 1
    assert len(body["result"]["words"]) == 1


def test_transcribe_fail_with_error_message_persists_in_failed_state(client, auth_headers, db):
    from app.services import job_service
    # max_attempts=1 so the first failure is terminal (no retry -> pending)
    job = job_service.create_job(
        db, job_type="transcribe", payload={"video": "C:\\missing.mp4"}, max_attempts=1
    )
    job_service.claim_next_job(db, worker_id="w1")
    job_service.start_processing(db, job.id, worker_id="w1")
    error_msg = "video not found: C:\\missing.mp4"
    r = client.post(
        f"/worker/jobs/{job.id}/fail?worker_id=w1",
        json={"error_message": error_msg},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error_message"] == error_msg
    # worker_id is intentionally KEPT for traceability (who failed the job)
    assert body["worker_id"] == "w1"
    assert body["lease_until"] is None
