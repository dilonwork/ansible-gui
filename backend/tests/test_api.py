"""Backend API tests.

DATABASE_URL points at a temp SQLite file (set before the app is imported),
so tests run against a real database without needing PostgreSQL.
No real sshd / ansible needed:
- keyscan is monkeypatched (real probing is covered by scripts/e2e_smoke.sh)
- run_playbook is replaced with a fake emitter (tests the API + WS plumbing)
"""
import os
import tempfile
import time
import types

_tmp = tempfile.mkdtemp(prefix="ansible-gui-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["CELERY_EAGER"] = "1"  # celery tasks run inline; no Redis needed

import pytest
from fastapi.testclient import TestClient
from tests import login_as_admin, ws_url

from app import main
from app import runner_service
from app.db import init_db, session_scope
from app.main import app
from app.models import Host, Job, Playbook, Session, Template, User


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "keyscan", lambda address, port: "fake-host-key-line")
    init_db()  # tables must exist before the first clear (lifespan runs later)
    _clear_db()
    with TestClient(app) as c:
        login_as_admin(c)
        yield c
    _clear_db()


def _clear_db():
    with session_scope() as s:
        for m in (Session, User, Job, Template, Playbook, Host):
            s.query(m).delete()


def _add_host(client, name="h1"):
    r = client.post("/api/hosts", json={
        "name": name, "address": "127.0.0.1", "port": 22,
        "username": "root", "private_key": "FAKE-KEY",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _add_playbook(client, name="pb1", content=None):
    content = content or "- name: t\n  hosts: all\n  gather_facts: false\n  tasks:\n    - ansible.builtin.ping:\n"
    r = client.post("/api/playbooks", json={"name": name, "content": content})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _add_template(client, playbook_id, host_ids, name="tpl1", **kw):
    body = {"name": name, "playbook_id": playbook_id, "host_ids": host_ids,
            "extra_vars": {}, "check_mode": False}
    body.update(kw)
    r = client.post("/api/templates", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _wait_done(client, jid, timeout=10):
    for _ in range(int(timeout / 0.05)):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise TimeoutError("job did not finish in time")


def _fake_runner_ok(job_id, hosts, playbook_content, extra_vars, check_mode, emit, on_run_dir=None):
    emit({"type": "job_started", "ts": 1.0})
    emit({"type": "task_start", "task": "ping via ssh", "ts": 1.1})
    for h in hosts:
        emit({"type": "host_ok", "host": h["name"], "task": "ping via ssh", "ts": 1.2})
    emit({"type": "job_finished", "ts": 1.3})
    return True


# ---------- hosts ----------
def test_add_and_list_hosts(client):
    hid = _add_host(client)
    hosts = client.get("/api/hosts").json()
    assert len(hosts) == 1
    assert hosts[0]["id"] == hid
    assert hosts[0]["name"] == "h1"


def test_list_hosts_does_not_leak_private_key(client):
    _add_host(client)
    hosts = client.get("/api/hosts").json()
    assert "private_key" not in hosts[0]


def test_private_key_is_encrypted_at_rest(client):
    _add_host(client)
    with session_scope() as s:
        stored = s.query(Host).one().private_key
    assert stored != "FAKE-KEY"          # not plaintext in the DB
    assert stored.startswith("gAAAAA")   # Fernet token
    # ...but a launched job still gets the real key back
    from app.crypto import decrypt_str
    assert decrypt_str(stored) == "FAKE-KEY"


def test_legacy_plaintext_key_still_decrypts(client):
    # rows written before encryption existed keep working (migrated on next write)
    from app.crypto import decrypt_str
    assert decrypt_str("FAKE-KEY") == "FAKE-KEY"


def test_data_survives_across_api_calls(client):
    # regression test for the old in-memory dicts: data must persist
    hid = _add_host(client, name="persist")
    with session_scope() as s:
        assert s.get(Host, hid).name == "persist"


def test_add_host_keyscan_fail_returns_400(client, monkeypatch):
    def boom(address, port):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(main, "keyscan", boom)
    r = client.post("/api/hosts", json={
        "name": "x", "address": "1.2.3.4", "port": 22,
        "username": "root", "private_key": "k",
    })
    assert r.status_code == 400


def test_delete_host(client):
    hid = _add_host(client)
    assert client.delete(f"/api/hosts/{hid}").status_code == 200
    assert client.get("/api/hosts").json() == []


# ---------- playbooks ----------
def test_playbook_crud(client):
    pid = _add_playbook(client)
    lst = client.get("/api/playbooks").json()
    assert len(lst) == 1 and lst[0]["id"] == pid
    assert "content" not in lst[0]  # list view omits content
    full = client.get(f"/api/playbooks/{pid}").json()
    assert "ansible.builtin.ping" in full["content"]
    assert client.delete(f"/api/playbooks/{pid}").status_code == 200
    assert client.get("/api/playbooks").json() == []


def test_syntax_check_ok_uses_real_ansible(client):
    pid = _add_playbook(client)
    r = client.post(f"/api/playbooks/{pid}/syntax-check")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_syntax_check_reports_error_with_line_number(client):
    bad = "- name: broken\n  hosts: all\n tasks:\n    - bad indent\n"
    pid = _add_playbook(client, content=bad)
    r = client.post(f"/api/playbooks/{pid}/syntax-check")
    body = r.json()
    assert body["ok"] is False
    assert "3" in body["output"]  # the bad line number shows up


def test_delete_playbook_blocked_when_used_by_template(client):
    pid = _add_playbook(client)
    hid = _add_host(client)
    _add_template(client, pid, [hid])
    r = client.delete(f"/api/playbooks/{pid}")
    assert r.status_code == 400


# ---------- templates ----------
def test_template_requires_existing_playbook(client):
    hid = _add_host(client)
    r = client.post("/api/templates", json={
        "name": "t", "playbook_id": "nope", "host_ids": [hid]})
    assert r.status_code == 400


def test_template_requires_known_hosts(client):
    pid = _add_playbook(client)
    r = client.post("/api/templates", json={
        "name": "t", "playbook_id": pid, "host_ids": ["nope"]})
    assert r.status_code == 400


# ---------- jobs ----------
def test_create_job_needs_template_or_hosts(client):
    assert client.post("/api/jobs", json={}).status_code == 400
    hid = _add_host(client)
    pid = _add_playbook(client)
    tid = _add_template(client, pid, [hid])
    # both at once is ambiguous -> 400
    assert client.post("/api/jobs", json={"template_id": tid, "host_ids": [hid]}).status_code == 400


def test_create_job_unknown_host_returns_400(client):
    r = client.post("/api/jobs", json={"host_ids": ["nope"]})
    assert r.status_code == 400


def test_adhoc_ping_lifecycle(client, monkeypatch):
    monkeypatch.setattr("app.tasks.run_playbook", _fake_runner_ok)
    hid = _add_host(client)
    jid = client.post("/api/jobs", json={"host_ids": [hid]}).json()["job_id"]
    job = _wait_done(client, jid)
    assert job["status"] == "successful"
    assert job["snapshot"]["kind"] == "ad-hoc"
    types = [e["type"] for e in job["events"]]
    assert types == ["job_started", "task_start", "host_ok", "job_finished"]


def test_job_from_template_freezes_snapshot(client, monkeypatch):
    captured = {}

    def fake(job_id, hosts, playbook_content, extra_vars, check_mode, emit, on_run_dir=None):
        captured.update(playbook_content=playbook_content, extra_vars=extra_vars,
                        check_mode=check_mode)
        return _fake_runner_ok(job_id, hosts, playbook_content, extra_vars, check_mode, emit)

    monkeypatch.setattr("app.tasks.run_playbook", fake)
    hid = _add_host(client)
    pid = _add_playbook(client, content="- name: v1\n  hosts: all\n  gather_facts: false\n  tasks:\n    - ansible.builtin.ping:\n")
    tid = _add_template(client, pid, [hid], extra_vars={"a": 1}, check_mode=True)
    jid = client.post("/api/jobs", json={"template_id": tid}).json()["job_id"]

    # mutate the template and playbook after launch: the running job must be unaffected
    with session_scope() as s:
        s.get(Template, tid).extra_vars = {"a": 999}
        s.get(Playbook, pid).content = "- name: v2 changed\n"

    job = _wait_done(client, jid)
    assert job["status"] == "successful"
    snap = job["snapshot"]
    assert snap["kind"] == "template"
    assert snap["template_name"] == "tpl1"
    assert snap["extra_vars"] == {"a": 1}
    assert "v1" in snap["playbook_content"]
    assert captured["extra_vars"] == {"a": 1}
    assert captured["check_mode"] is True
    assert "v1" in captured["playbook_content"]


def test_job_launch_overrides_check_mode_and_extra_vars(client, monkeypatch):
    captured = {}

    def fake(job_id, hosts, playbook_content, extra_vars, check_mode, emit, on_run_dir=None):
        captured.update(extra_vars=extra_vars, check_mode=check_mode)
        return _fake_runner_ok(job_id, hosts, playbook_content, extra_vars, check_mode, emit)

    monkeypatch.setattr("app.tasks.run_playbook", fake)
    hid = _add_host(client)
    pid = _add_playbook(client)
    tid = _add_template(client, pid, [hid], extra_vars={"a": 1, "b": 2}, check_mode=False)
    jid = client.post("/api/jobs", json={
        "template_id": tid, "check_mode": True, "extra_vars": {"b": 20, "c": 30}}).json()["job_id"]
    _wait_done(client, jid)
    assert captured["check_mode"] is True
    assert captured["extra_vars"] == {"a": 1, "b": 20, "c": 30}


def test_failed_runner_marks_job_failed(client, monkeypatch):
    monkeypatch.setattr("app.tasks.run_playbook",
                        lambda *a, **k: False)
    hid = _add_host(client)
    jid = client.post("/api/jobs", json={"host_ids": [hid]}).json()["job_id"]
    job = _wait_done(client, jid)
    assert job["status"] == "failed"


def test_check_mode_reaches_ansible_runner(monkeypatch):
    """run_playbook must pass --check to ansible-runner when check_mode is on."""
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(status="successful", rc=0)

    monkeypatch.setattr(runner_service, "ansible_runner",
                        types.SimpleNamespace(run=fake_run))
    hosts = [{"name": "h1", "address": "127.0.0.1", "port": 22,
              "username": "root", "private_key": "K", "host_key": "HK"}]
    assert runner_service.run_playbook(
        "j1", hosts, "- name: t\n  hosts: all\n  tasks: []\n",
        {"x": 1}, True, lambda e: None) is True
    assert seen["cmdline"] == "--check"
    assert seen["extravars"] == {"x": 1}

    assert runner_service.run_playbook(
        "j2", hosts, "- name: t\n  hosts: all\n  tasks: []\n",
        {}, False, lambda e: None) is True
    assert seen["cmdline"] is None


# ---------- websocket ----------
def test_ws_late_joiner_gets_history_then_eof(client, monkeypatch):
    monkeypatch.setattr("app.tasks.run_playbook", _fake_runner_ok)
    hid = _add_host(client)
    jid = client.post("/api/jobs", json={"host_ids": [hid]}).json()["job_id"]
    _wait_done(client, jid)  # job finishes first, WS connects late
    with client.websocket_connect(ws_url(client, f"/ws/jobs/{jid}")) as ws:
        got = []
        while True:
            e = ws.receive_json()
            got.append(e["type"])
            if e["type"] == "eof":
                break
    assert got == ["job_started", "task_start", "host_ok", "job_finished", "eof"]


def test_ws_unknown_job_rejected(client):
    with pytest.raises(Exception):
        with client.websocket_connect(ws_url(client, "/ws/jobs/nope")):
            pass


def test_ws_without_token_rejected(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/jobs/nope"):
            pass


# ---------- cancel / retry (issue #1) ----------
def _add_running_job_row(job_id="cxl1"):
    with session_scope() as s:
        s.add(Job(id=job_id, host_ids=[], status="running",
                  snapshot={"kind": "ad-hoc", "host_ids": [],
                            "playbook_content": "x", "extra_vars": {},
                            "check_mode": False},
                  events=[], created_at=time.time(), finished_at=None))


def test_cancel_marks_running_job_cancelled(client):
    _add_running_job_row()
    r = client.post("/api/jobs/cxl1/cancel")
    assert r.status_code == 200, r.text
    job = client.get("/api/jobs/cxl1").json()
    assert job["status"] == "cancelled"
    assert job["finished_at"] is not None
    assert job["events"][-1]["type"] == "job_cancelled"


def test_cancel_unknown_job_returns_404(client):
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_cancel_finished_job_returns_400(client, monkeypatch):
    monkeypatch.setattr("app.tasks.run_playbook", _fake_runner_ok)
    hid = _add_host(client)
    jid = client.post("/api/jobs", json={"host_ids": [hid]}).json()["job_id"]
    _wait_done(client, jid)
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 400


def test_cancel_revokes_celery_task_and_cleans_rundir(client, monkeypatch, tmp_path):
    import fakeredis
    from app import events as events_mod
    from app import tasks as tasks_mod
    from app.celery_app import celery
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(events_mod, "get_redis", lambda: fake)
    revoked = {}
    monkeypatch.setattr(celery.control, "revoke",
                        lambda task_id, terminate=False: revoked.update(
                            task_id=task_id, terminate=terminate))
    _add_running_job_row("cxl2")
    rundir = tmp_path / "drydock-cxl2-xyz"
    rundir.mkdir()
    (rundir / "ssh_key").write_text("secret")
    fake.set(tasks_mod.TASK_KEY.format(jid="cxl2"), "task-123")
    fake.set(tasks_mod.RUNDIR_KEY.format(jid="cxl2"), str(rundir))

    assert client.post("/api/jobs/cxl2/cancel").status_code == 200
    assert revoked == {"task_id": "task-123", "terminate": True}
    assert not rundir.exists()  # temp key file cleaned up
    assert client.get("/api/jobs/cxl2").json()["status"] == "cancelled"


def test_retry_failed_nodes_creates_subset_job(client, monkeypatch):
    def fake_partial(job_id, hosts, playbook_content, extra_vars, check_mode, emit, on_run_dir=None):
        emit({"type": "job_started", "ts": 1.0})
        for h in hosts:
            if h["name"] == "bad":
                emit({"type": "host_failed", "host": "bad", "task": "t",
                      "msg": "boom", "ts": 1.1})
            else:
                emit({"type": "host_ok", "host": h["name"], "task": "t", "ts": 1.1})
        emit({"type": "job_finished", "ts": 1.2})
        return False

    monkeypatch.setattr("app.tasks.run_playbook", fake_partial)
    h1 = _add_host(client, name="good")
    h2 = _add_host(client, name="bad")
    pid = _add_playbook(client)
    tid = _add_template(client, pid, [h1, h2])
    jid = client.post("/api/jobs", json={"template_id": tid}).json()["job_id"]
    job = _wait_done(client, jid)
    assert job["status"] == "failed"

    r = client.post(f"/api/jobs/{jid}/retry")
    assert r.status_code == 200, r.text
    new_id = r.json()["job_id"]
    new_job = _wait_done(client, new_id)
    assert new_job["host_ids"] == [h2]  # only the failed host
    assert new_job["snapshot"]["retried_from"] == jid
    assert new_job["snapshot"]["playbook_content"] == job["snapshot"]["playbook_content"]
    assert new_job["snapshot"]["extra_vars"] == job["snapshot"]["extra_vars"]


def test_retry_successful_job_returns_400(client, monkeypatch):
    monkeypatch.setattr("app.tasks.run_playbook", _fake_runner_ok)
    hid = _add_host(client)
    jid = client.post("/api/jobs", json={"host_ids": [hid]}).json()["job_id"]
    _wait_done(client, jid)
    assert client.post(f"/api/jobs/{jid}/retry").status_code == 400


def test_retry_without_failed_hosts_returns_400(client, monkeypatch):
    # job failed but emitted no host_failed events (crashed early)
    monkeypatch.setattr("app.tasks.run_playbook", lambda *a, **k: False)
    hid = _add_host(client)
    jid = client.post("/api/jobs", json={"host_ids": [hid]}).json()["job_id"]
    _wait_done(client, jid)
    assert client.post(f"/api/jobs/{jid}/retry").status_code == 400


def test_reconcile_marks_orphaned_running_as_interrupted(client, monkeypatch):
    import fakeredis
    from app import events as events_mod
    from app import main as main_mod
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(events_mod, "get_redis", lambda: fake)
    _add_running_job_row("orph1")
    main_mod._reconcile_jobs()
    job = client.get("/api/jobs/orph1").json()
    assert job["status"] == "interrupted"
    assert job["finished_at"] is not None
    assert job["events"][-1]["type"] == "job_interrupted"


def test_reconcile_keeps_queued_job_running(client, monkeypatch):
    import fakeredis
    from app import events as events_mod
    from app import main as main_mod
    from app import tasks as tasks_mod
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(events_mod, "get_redis", lambda: fake)
    # task key present (enqueued, worker hasn't picked it up yet) -> not orphaned
    fake.set(tasks_mod.TASK_KEY.format(jid="q1"), "task-abc")
    _add_running_job_row("q1")
    main_mod._reconcile_jobs()
    assert client.get("/api/jobs/q1").json()["status"] == "running"
