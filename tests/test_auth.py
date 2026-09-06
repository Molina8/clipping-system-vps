"""Bearer token authentication tests."""
def test_system_info_without_token(client):
    r = client.get("/system/info")
    assert r.status_code in {401, 403}


def test_system_info_with_wrong_token(client):
    r = client.get(
        "/system/info",
        headers={"Authorization": "***"},
    )
    assert r.status_code == 401


def test_system_info_with_correct_token(client, auth_headers):
    r = client.get("/system/info", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "clipping-api"
    assert body["environment"] in {"production", "development", "staging"}
