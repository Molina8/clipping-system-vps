"""Health endpoint tests."""
def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["database"] in {"ok", "error"}
    # Without secrets in the response
    for key in body:
        assert "password" not in key.lower()
        assert "token" not in key.lower()
