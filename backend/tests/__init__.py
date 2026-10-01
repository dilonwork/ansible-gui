"""Shared test helpers."""


def login_as_admin(client, username="admin", password="testpass123"):
    """Run first-time setup and attach the admin bearer token to the client."""
    r = client.post("/api/auth/setup",
                    json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.headers.update(
        {"Authorization": f"Bearer {r.json()['token']}"})


def ws_url(client, path):
    """WebSocket URL with the client's auth token as query param."""
    token = client.headers["Authorization"].split(" ", 1)[1]
    return f"{path}?token={token}"
