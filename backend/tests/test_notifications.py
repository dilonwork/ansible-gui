"""M8.1 job notification tests.

CELERY_EAGER=1 runs send_notification inline. The webhook HTTP layer is
faked at tasks._post_webhook (call recording); one test uses a real local
HTTP server for the channel test endpoint.
"""
import json
import os
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

_tmp = tempfile.mkdtemp(prefix="drydock-notify-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["CELERY_EAGER"] = "1"

import pytest
from fastapi.testclient import TestClient

from app import main as main_mod
from app import tasks as tasks_mod
from app.db import init_db, session_scope
from app.main import app
from app.models import (Host, Job, MaintenanceRun, NotificationChannel,
                        Playbook, Schedule, Template)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main_mod, "keyscan", lambda address, port: "fake-host-key-line")
    init_db()
    _clear_db()
    with TestClient(app) as c:
        yield c
    _clear_db()


@pytest.fixture
def webhook_calls(monkeypatch):
    calls = []

    def fake_post(url, headers, payload):
        calls.append({"url": url, "headers": headers, "payload": payload})

    monkeypatch.setattr(tasks_mod, "_post_webhook", fake_post)
    return calls


def _clear_db():
    with session_scope() as s:
        for m in (NotificationChannel, Schedule, MaintenanceRun, Job,
                  Template, Playbook, Host):
            s.query(m).delete()


def _template(client, policy="failure_only") -> str:
    hid = client.post("/api/hosts", json={
        "name": "web1", "address": "127.0.0.1", "port": 22,
        "username": "root", "private_key": "FAKE"}).json()["id"]
    pb = client.post("/api/playbooks", json={
        "name": "pb1", "content": "- name: x\n  hosts: all\n  tasks: []\n"}).json()["id"]
    r = client.post("/api/templates", json={
        "name": "t1", "playbook_id": pb, "host_ids": [hid],
        "notification_policy": policy})
    assert r.status_code == 200, r.text
    return r.json()["id"], hid


def _channel(client, url="http://example.com/hook") -> str:
    r = client.post("/api/notification-channels", json={
        "name": "ops", "type": "webhook", "config": {"url": url}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _job_row(template_id: str, host_ids=None, events=None) -> str:
    jid = uuid.uuid4().hex[:8]
    with session_scope() as s:
        s.add(Job(
            id=jid, host_ids=host_ids or [], status="running",
            snapshot={"kind": "template", "template_id": template_id,
                      "template_name": "t1", "playbook_name": "pb1",
                      "host_ids": host_ids or [], "extra_vars": {},
                      "check_mode": False},
            events=events or [], created_at=time.time(), finished_at=None))
    return jid


def test_policy_failure_only(client, webhook_calls):
    tid, hid = _template(client, "failure_only")
    _channel(client)
    jid = _job_row(tid, [hid], [
        {"type": "host_failed", "host": "web1", "task": "x", "msg": "boom"}])
    assert tasks_mod._finish_job(jid, "failed") is True
    assert len(webhook_calls) == 1
    assert webhook_calls[0]["payload"]["status"] == "failed"

    jid2 = _job_row(tid, [hid])
    assert tasks_mod._finish_job(jid2, "successful") is True
    assert len(webhook_calls) == 1  # success not notified


def test_policy_always_notifies_success(client, webhook_calls):
    tid, hid = _template(client, "always")
    _channel(client)
    jid = _job_row(tid, [hid])
    tasks_mod._finish_job(jid, "successful")
    assert len(webhook_calls) == 1
    assert webhook_calls[0]["payload"]["status"] == "successful"


def test_policy_never_mutes_all(client, webhook_calls):
    tid, hid = _template(client, "never")
    _channel(client)
    jid = _job_row(tid, [hid])
    tasks_mod._finish_job(jid, "failed")
    assert webhook_calls == []


def test_adhoc_jobs_never_notify(client, webhook_calls):
    _channel(client)
    jid = uuid.uuid4().hex[:8]
    with session_scope() as s:
        s.add(Job(id=jid, host_ids=[], status="running",
                  snapshot={"kind": "ad-hoc", "playbook_name": "ping"},
                  events=[], created_at=time.time(), finished_at=None))
    tasks_mod._finish_job(jid, "failed")
    assert webhook_calls == []


def test_payload_content(client, webhook_calls):
    tid, hid = _template(client, "failure_only")
    _channel(client, url="http://example.com/hook2")
    jid = _job_row(tid, [hid], [
        {"type": "task_start", "task": "x"},
        {"type": "host_failed", "host": "web1", "task": "x", "msg": "boom"},
    ])
    tasks_mod._finish_job(jid, "failed")
    p = webhook_calls[0]["payload"]
    assert p["event"] == "job_finished"
    assert p["job_id"] == jid
    assert p["template_name"] == "t1"
    assert p["failed_hosts"] == ["web1"]
    assert p["hosts_total"] == 1
    assert p["hosts_succeeded"] == 0
    assert p["duration_s"] >= 0
    assert p["link"] == f"/jobs/{jid}"
    assert webhook_calls[0]["url"] == "http://example.com/hook2"


def test_cancelled_counts_as_failure(client, webhook_calls):
    tid, hid = _template(client, "failure_only")
    _channel(client)
    jid = _job_row(tid, [hid])
    tasks_mod._finish_job(jid, "cancelled")
    assert len(webhook_calls) == 1
    assert webhook_calls[0]["payload"]["status"] == "cancelled"


def test_disabled_channel_skipped(client, webhook_calls):
    tid, hid = _template(client, "always")
    cid = _channel(client)
    with session_scope() as s:
        s.get(NotificationChannel, cid).enabled = False
    jid = _job_row(tid, [hid])
    tasks_mod._finish_job(jid, "successful")
    assert webhook_calls == []


def test_no_channel_no_crash(client, webhook_calls):
    tid, hid = _template(client, "always")
    jid = _job_row(tid, [hid])
    tasks_mod._finish_job(jid, "successful")  # no channels configured
    assert webhook_calls == []


def test_template_policy_validation(client):
    hid = client.post("/api/hosts", json={
        "name": "h", "address": "127.0.0.1", "port": 22,
        "username": "root", "private_key": "FAKE"}).json()["id"]
    pb = client.post("/api/playbooks", json={
        "name": "pb", "content": "x"}).json()["id"]
    r = client.post("/api/templates", json={
        "name": "t", "playbook_id": pb, "host_ids": [hid],
        "notification_policy": "sometimes"})
    assert r.status_code == 400
    assert client.post("/api/notification-channels",
                       json={"name": "c", "config": {}}).status_code == 400


def test_channel_test_endpoint_real_http(client):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(json.loads(body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cid = _channel(client, f"http://127.0.0.1:{server.server_port}/hook")
        r = client.post(f"/api/notification-channels/{cid}/test")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == 200
        assert received and received[0]["event"] == "test"
    finally:
        server.shutdown()
    assert client.delete(f"/api/notification-channels/{cid}").status_code == 200
    assert client.get("/api/notification-channels").json() == []
