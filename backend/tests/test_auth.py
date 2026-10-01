"""Auth + RBAC tests: setup, login, roles, user management."""
import os
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="drydock-auth-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["CELERY_EAGER"] = "1"

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_mod
from app.db import init_db, session_scope
from app.main import app
from app.models import Session, User


@pytest.fixture
def client():
    init_db()
    _clear_db()
    with TestClient(app) as c:
        yield c
    _clear_db()


def _clear_db():
    with session_scope() as s:
        for m in (Session, User):
            s.query(m).delete()


def _setup(c, username="admin", password="testpass123"):
    r = c.post("/api/auth/setup", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _login(c, username="admin", password="testpass123"):
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(c, token):
    return {"Authorization": f"Bearer {token}"}


def test_status_and_setup(client):
    assert client.get("/api/auth/status").json() == {"setup_required": True}
    body = _setup(client)
    assert body["username"] == "admin" and body["role"] == "admin"
    assert client.get("/api/auth/status").json() == {"setup_required": False}
    # second setup is rejected
    r = client.post("/api/auth/setup", json={"username": "x", "password": "testpass123"})
    assert r.status_code == 400


def test_setup_validates(client):
    r = client.post("/api/auth/setup", json={"username": "", "password": "testpass123"})
    assert r.status_code == 400
    r = client.post("/api/auth/setup", json={"username": "a", "password": "short"})
    assert r.status_code == 400


def test_login(client):
    _setup(client)
    token = _login(client)
    r = client.get("/api/auth/me", headers=_auth(client, token))
    assert r.json() == {"username": "admin", "role": "admin", "can_write": True}
    # wrong password / unknown user
    assert client.post("/api/auth/login",
                       json={"username": "admin", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"username": "ghost", "password": "testpass123"}).status_code == 401


def test_password_is_hashed(client):
    _setup(client, password="testpass123")
    with session_scope() as s:
        u = s.query(User).filter_by(username="admin").first()
        assert u.password_hash != "testpass123"
        assert auth_mod.verify_password("testpass123", u.password_hash)
        assert not auth_mod.verify_password("wrong", u.password_hash)


def test_unauthenticated_is_rejected(client):
    _setup(client)
    assert client.get("/api/hosts").status_code == 401
    assert client.post("/api/hosts", json={}).status_code == 401
    r = client.get("/api/hosts", headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401


def test_viewer_is_read_only(client):
    _setup(client)
    admin = _login(client)
    # admin creates a viewer
    r = client.post("/api/users", headers=_auth(client, admin),
                    json={"username": "v", "password": "testpass123", "role": "viewer"})
    assert r.status_code == 200, r.text
    vtoken = _login(client, "v")
    vh = _auth(client, vtoken)
    assert client.get("/api/hosts", headers=vh).status_code == 200
    r = client.post("/api/hosts", headers=vh, json={"name": "h"})
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"]
    r = client.delete("/api/hosts/x", headers=vh)
    assert r.status_code == 403


def test_operator_can_write_but_not_manage_users(client):
    _setup(client)
    admin = _login(client)
    client.post("/api/users", headers=_auth(client, admin),
                json={"username": "op", "password": "testpass123", "role": "operator"})
    otoken = _login(client, "op")
    oh = _auth(client, otoken)
    assert client.get("/api/users", headers=oh).status_code == 403
    r = client.post("/api/users", headers=oh,
                    json={"username": "x", "password": "testpass123"})
    assert r.status_code == 403


def test_admin_user_crud(client):
    _setup(client)
    admin = _login(client)
    ah = _auth(client, admin)
    # create
    r = client.post("/api/users", headers=ah,
                    json={"username": "op2", "password": "testpass123", "role": "operator"})
    assert r.status_code == 200
    uid = r.json()["id"]
    assert r.json()["role"] == "operator"
    assert "password_hash" not in r.json()
    # duplicate username
    r = client.post("/api/users", headers=ah,
                    json={"username": "op2", "password": "testpass123"})
    assert r.status_code == 400
    # bad role
    r = client.post("/api/users", headers=ah,
                    json={"username": "x", "password": "testpass123", "role": "root"})
    assert r.status_code == 400
    # list (no hashes leaked)
    users = client.get("/api/users", headers=ah).json()
    assert {u["username"] for u in users} == {"admin", "op2"}
    assert all("password_hash" not in u for u in users)
    # change role
    r = client.patch(f"/api/users/{uid}", headers=ah, json={"role": "viewer"})
    assert r.json()["role"] == "viewer"
    # delete
    assert client.delete(f"/api/users/{uid}", headers=ah).status_code == 200
    assert {u["username"] for u in client.get("/api/users", headers=ah).json()} == {"admin"}


def test_cannot_delete_self_or_last_admin(client):
    _setup(client)
    admin = _login(client)
    ah = _auth(client, admin)
    me = client.get("/api/auth/me", headers=ah).json()
    admin_id = next(u["id"] for u in client.get("/api/users", headers=ah).json()
                    if u["username"] == "admin")
    assert me["username"] == "admin"
    # cannot delete yourself
    r = client.delete(f"/api/users/{admin_id}", headers=ah)
    assert r.status_code == 400
    # cannot demote the last admin
    r = client.patch(f"/api/users/{admin_id}", headers=ah, json={"role": "viewer"})
    assert r.status_code == 400
    # add a second admin, then demote/delete works
    r = client.post("/api/users", headers=ah,
                    json={"username": "a2", "password": "testpass123", "role": "admin"})
    a2id = r.json()["id"]
    assert client.patch(f"/api/users/{admin_id}", headers=ah, json={"role": "viewer"}).status_code == 200
    # now a2 is the last admin: deleting them is blocked
    a2token = _login(client, "a2")
    r = client.delete(f"/api/users/{a2id}", headers=_auth(client, a2token))
    assert r.status_code == 400


def test_logout_revokes(client):
    _setup(client)
    token = _login(client)
    h = _auth(client, token)
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_expired_token_rejected(client):
    _setup(client)
    token = _login(client)
    with session_scope() as s:
        sess = s.get(Session, token)
        sess.expires_at = time.time() - 1
    assert client.get("/api/auth/me", headers=_auth(client, token)).status_code == 401


def test_password_change_kills_other_sessions(client):
    _setup(client)
    admin = _login(client)
    ah = _auth(client, admin)
    r = client.post("/api/users", headers=ah,
                    json={"username": "op", "password": "testpass123", "role": "operator"})
    uid = r.json()["id"]
    t1 = _login(client, "op")
    t2 = _login(client, "op")
    # admin resets op's password: other sessions die
    client.patch(f"/api/users/{uid}", headers=ah, json={"password": "newpass456"})
    assert client.get("/api/auth/me", headers=_auth(client, t1)).status_code == 401
    assert client.get("/api/auth/me", headers=_auth(client, t2)).status_code == 401
    # new password works
    t3 = _login(client, "op", "newpass456")
    assert client.get("/api/auth/me", headers=_auth(client, t3)).status_code == 200
