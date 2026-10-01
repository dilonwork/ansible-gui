"""M5 orchestration tests.

CELERY_EAGER=1 runs the orchestrator inline. The ansible layer is faked at
_run_ansible (one step = one call returning (ok, events)); real SSH coverage
is in scripts/e2e_smoke.sh.
"""
import os
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="drydock-maint-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["CELERY_EAGER"] = "1"

import pytest
from fastapi.testclient import TestClient
from tests import login_as_admin

from app import main as main_mod
from app import maintenance_tasks as mt
from app.db import init_db, session_scope
from app.main import app
from app.models import Host, Job, MaintenanceRun, Playbook, Session, Template, User


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main_mod, "keyscan", lambda address, port: "fake-host-key-line")
    init_db()
    _clear_db()
    with TestClient(app) as c:
        login_as_admin(c)
        yield c
    _clear_db()


def _clear_db():
    with session_scope() as s:
        for m in (MaintenanceRun, Job, Template, Playbook, Host, Session, User):
            s.query(m).delete()


def _add_host(client, name):
    r = client.post("/api/hosts", json={
        "name": name, "address": "127.0.0.1", "port": 22,
        "username": "root", "private_key": "FAKE-KEY"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_fake(calls, fail=None, preflight_events=None):
    """fail: {(node_name, step)} -> fail that step. Records (node, step)."""
    fail = fail or {}

    def fake(run_id, step_label, hosts, playbook, extra_vars, env,
             node_name=None):
        calls.append((node_name, step_label))
        if step_label in ("preflight", "preflight_k8s"):
            return True, (preflight_events or [])
        if (node_name, step_label) in fail:
            return False, [{"type": "task_start", "task": step_label}]
        return True, []
    return fake


def _create_run(client, host_ids, **kw):
    body = {"name": "m1", "workflow": "os-patch", "node_ids": host_ids,
            "params": {}, "playbook_overrides": {}}
    body.update(kw)
    r = client.post("/api/maintenance", json=body)
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def _wait_status(client, rid, want=("completed", "failed", "paused", "aborted"),
                 timeout=15):
    for _ in range(int(timeout / 0.05)):
        st = client.get(f"/api/maintenance/{rid}").json()["status"]
        if st in want:
            return st
        time.sleep(0.05)
    raise TimeoutError(f"run {rid} did not reach {want}")


def test_full_run_serial_order(client, monkeypatch):
    calls = []
    monkeypatch.setattr(mt, "_run_ansible", _make_fake(calls))
    hids = [_add_host(client, f"n{i}") for i in (1, 2, 3)]
    rid = _create_run(client, hids)
    assert _wait_status(client, rid) == "completed"

    node_steps = [(n, s) for n, s in calls if s != "preflight"]
    # strict serial=1: all of n1's steps, then n2's, then n3's
    assert node_steps == (
        [("n1", s) for s in ("maintain", "verify")] +
        [("n2", s) for s in ("maintain", "verify")] +
        [("n3", s) for s in ("maintain", "verify")])
    run = client.get(f"/api/maintenance/{rid}").json()
    assert all(n["state"] == "done" for n in run["steps"])
    assert run["preflight"]  # preflight recorded


def test_k8s_steps_included_with_kubeconfig(client, monkeypatch):
    calls = []
    monkeypatch.setattr(mt, "_run_ansible", _make_fake(calls))
    hid = _add_host(client, "k1")
    rid = _create_run(client, [hid], kubeconfig="fake-kubeconfig")
    assert _wait_status(client, rid) == "completed"
    steps = [s for n, s in calls if n == "k1"]
    # os-patch + kubeconfig: verify also checks k8s Node Ready
    assert steps == ["cordon", "drain", "maintain", "verify", "verify_k8s",
                     "uncordon"]


def test_pause_on_failure_and_retry_node(client, monkeypatch):
    calls = []
    fake = _make_fake(calls, fail={("n2", "maintain")})
    monkeypatch.setattr(mt, "_run_ansible", fake)
    hids = [_add_host(client, f"n{i}") for i in (1, 2, 3)]
    rid = _create_run(client, hids)
    assert _wait_status(client, rid) == "paused"

    run = client.get(f"/api/maintenance/{rid}").json()
    states = {n["node_name"]: n["state"] for n in run["steps"]}
    assert states == {"n1": "done", "n2": "failed", "n3": "pending"}
    n2 = next(n for n in run["steps"] if n["node_name"] == "n2")
    assert n2["failed_step"] == "maintain"

    # fix the failure, retry the node: it restarts from cordon/first step,
    # n1 is NOT re-run
    monkeypatch.setattr(mt, "_run_ansible", _make_fake(calls))
    assert client.post(f"/api/maintenance/{rid}/retry-node").status_code == 200
    assert _wait_status(client, rid) == "completed"
    run = client.get(f"/api/maintenance/{rid}").json()
    attempts = {n["node_name"]: n["attempts"] for n in run["steps"]}
    assert attempts == {"n1": 1, "n2": 2, "n3": 1}
    n2_calls = [s for n, s in calls if n == "n2"]
    assert n2_calls.count("maintain") == 2  # failed once, retried once


def test_skip_node(client, monkeypatch):
    calls = []
    monkeypatch.setattr(mt, "_run_ansible",
                        _make_fake(calls, fail={("n1", "verify")}))
    hids = [_add_host(client, f"n{i}") for i in (1, 2)]
    rid = _create_run(client, hids)
    assert _wait_status(client, rid) == "paused"
    monkeypatch.setattr(mt, "_run_ansible", _make_fake(calls))
    assert client.post(f"/api/maintenance/{rid}/skip-node").status_code == 200
    assert _wait_status(client, rid) == "completed"
    run = client.get(f"/api/maintenance/{rid}").json()
    states = {n["node_name"]: n["state"] for n in run["steps"]}
    assert states == {"n1": "skipped", "n2": "done"}


def test_abort(client, monkeypatch):
    calls = []
    monkeypatch.setattr(mt, "_run_ansible",
                        _make_fake(calls, fail={("n1", "maintain")}))
    hid = _add_host(client, "n1")
    rid = _create_run(client, [hid])
    assert _wait_status(client, rid) == "paused"
    assert client.post(f"/api/maintenance/{rid}/abort").status_code == 200
    assert client.get(f"/api/maintenance/{rid}").json()["status"] == "aborted"
    # aborting a finished run is rejected
    assert client.post(f"/api/maintenance/{rid}/abort").status_code == 400


def test_pause_and_resume_mid_run(client, monkeypatch):
    calls = []
    paused_once = []

    def fake(run_id, step_label, hosts, playbook, extra_vars, env,
             node_name=None):
        calls.append((node_name, step_label))
        if step_label in ("preflight", "preflight_k8s"):
            return True, []
        if (node_name, step_label) == ("n1", "maintain") and not paused_once:
            # operator hits pause once while the run is in flight
            paused_once.append(True)
            r = client.post(f"/api/maintenance/{run_id}/pause")
            assert r.status_code == 200
        return True, []

    monkeypatch.setattr(mt, "_run_ansible", fake)
    hids = [_add_host(client, f"n{i}") for i in (1, 2)]
    rid = _create_run(client, hids)
    # n1 finished maintain, then the run noticed "pausing" before verify
    assert _wait_status(client, rid, want=("paused",)) == "paused"
    run = client.get(f"/api/maintenance/{rid}").json()
    n1 = next(n for n in run["steps"] if n["node_name"] == "n1")
    assert n1["state"] == "pending"  # not done: paused mid-node

    assert client.post(f"/api/maintenance/{rid}/resume").status_code == 200
    assert _wait_status(client, rid) == "completed"
    run = client.get(f"/api/maintenance/{rid}").json()
    assert all(n["state"] == "done" for n in run["steps"])


def test_preflight_failure_blocks_run(client, monkeypatch):
    calls = []

    def fake(run_id, step_label, hosts, playbook, extra_vars, env,
             node_name=None):
        calls.append((node_name, step_label))
        if step_label == "preflight":
            return False, [
                {"type": "task_start", "task": "check ssh connectivity"},
                {"type": "host_failed", "host": "n1",
                 "task": "check ssh connectivity", "msg": "boom"},
            ]
        return True, []

    monkeypatch.setattr(mt, "_run_ansible", fake)
    hid = _add_host(client, "n1")
    rid = _create_run(client, [hid])
    assert _wait_status(client, rid) == "failed"
    run = client.get(f"/api/maintenance/{rid}").json()
    assert run["preflight"][0]["status"] == "failed"
    # no node steps ran
    assert not [c for c in calls if c[0] == "n1"]
    assert run["steps"] == [] or all(
        n["state"] == "pending" for n in run["steps"])


def test_preflight_warning_does_not_block(client, monkeypatch):
    def fake(run_id, step_label, hosts, playbook, extra_vars, env,
             node_name=None):
        if step_label == "preflight":
            return True, [
                {"type": "task_start", "task": "check disk space"},
                {"type": "host_ok", "host": "n1", "task": "check disk space"},
                {"type": "task_start", "task": "report single-replica"},
                {"type": "check_warning", "host": "n1",
                 "task": "report single-replica",
                 "msg": "WARNING: single-replica: prometheus-0"},
            ]
        return True, []

    monkeypatch.setattr(mt, "_run_ansible", fake)
    hid = _add_host(client, "n1")
    rid = _create_run(client, [hid])
    assert _wait_status(client, rid) == "completed"
    run = client.get(f"/api/maintenance/{rid}").json()
    warn = next(c for c in run["preflight"] if c["status"] == "warning")
    assert "prometheus-0" in warn["detail"]


def test_create_validates_input(client):
    hid = _add_host(client, "n1")
    assert client.post("/api/maintenance", json={
        "name": "x", "workflow": "nope", "node_ids": [hid]}).status_code == 400
    assert client.post("/api/maintenance", json={
        "name": "x", "workflow": "os-patch", "node_ids": []}).status_code == 400
    assert client.post("/api/maintenance", json={
        "name": "x", "workflow": "os-patch",
        "node_ids": ["unknown"]}).status_code == 400


def test_derive_preflight_maps_events():
    from app.maintenance_tasks import _derive_preflight
    evts = [
        {"type": "task_start", "task": "check ssh connectivity"},
        {"type": "host_ok", "host": "n1", "task": "check ssh connectivity"},
        {"type": "task_start", "task": "check disk space"},
        {"type": "host_failed", "host": "n1", "task": "check disk space",
         "msg": "disk 95%"},
    ]
    checks = _derive_preflight(evts)
    assert [(c["name"], c["status"]) for c in checks] == [
        ("check ssh connectivity", "passed"),
        ("check disk space", "failed")]
