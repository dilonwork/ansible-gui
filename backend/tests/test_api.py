"""後端 API 測試。

不依賴真實 sshd / ansible：
- keyscan 用 monkeypatch 換成假回傳（真實探測由 scripts/e2e_smoke.sh 覆蓋）
- run_ping_job 換成假執行器，同步 emit 事件（測 API + WS 鏈路）
"""
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "keyscan", lambda address, port: "fake-host-key-line")
    main.hosts.clear()
    main.jobs.clear()
    main.ws_queues.clear()
    with TestClient(app) as c:
        yield c
    main.hosts.clear()
    main.jobs.clear()
    main.ws_queues.clear()


def _add_host(client, name="h1"):
    r = client.post("/api/hosts", json={
        "name": name, "address": "127.0.0.1", "port": 22,
        "username": "root", "private_key": "FAKE-KEY",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _wait_done(client, jid, timeout=10):
    for _ in range(int(timeout / 0.05)):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise TimeoutError("job did not finish in time")


def _fake_runner_ok(job_id, hosts, emit):
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


# ---------- jobs ----------
def test_create_job_unknown_host_returns_400(client):
    r = client.post("/api/jobs", json={"host_ids": ["nope"]})
    assert r.status_code == 400


def test_job_lifecycle_and_events(client, monkeypatch):
    monkeypatch.setattr(main, "run_ping_job", _fake_runner_ok)
    hid = _add_host(client)
    jid = client.post("/api/jobs", json={"host_ids": [hid]}).json()["job_id"]
    job = _wait_done(client, jid)
    assert job["status"] == "successful"
    types = [e["type"] for e in job["events"]]
    assert types == ["job_started", "task_start", "host_ok", "job_finished"]


def test_failed_runner_marks_job_failed(client, monkeypatch):
    monkeypatch.setattr(main, "run_ping_job", lambda jid, hosts, emit: False)
    hid = _add_host(client)
    jid = client.post("/api/jobs", json={"host_ids": [hid]}).json()["job_id"]
    job = _wait_done(client, jid)
    assert job["status"] == "failed"


# ---------- websocket ----------
def test_ws_late_joiner_gets_history_then_eof(client, monkeypatch):
    monkeypatch.setattr(main, "run_ping_job", _fake_runner_ok)
    hid = _add_host(client)
    jid = client.post("/api/jobs", json={"host_ids": [hid]}).json()["job_id"]
    _wait_done(client, jid)  # 任務先跑完，WS 晚連
    with client.websocket_connect(f"/ws/jobs/{jid}") as ws:
        got = []
        while True:
            e = ws.receive_json()
            got.append(e["type"])
            if e["type"] == "eof":
                break
    assert got == ["job_started", "task_start", "host_ok", "job_finished", "eof"]


def test_ws_unknown_job_rejected(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/jobs/nope"):
            pass
