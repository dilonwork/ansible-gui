"""Schedule tests (issue #3).

CELERY_EAGER=1 runs the tick inline. The ansible layer is not faked here:
scheduled jobs launch through the real _launch_job path, but templates use a
trivial playbook and hosts use keyscan mocks, so nothing touches SSH (the job
fails fast at SSH time; the tick assertions only need the job row).
"""
import os
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="drydock-sched-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["CELERY_EAGER"] = "1"

import pytest
from fastapi.testclient import TestClient
from tests import login_as_admin

from app import main as main_mod
from app.db import init_db, session_scope
from app.main import app
from app.models import Host, Job, MaintenanceRun, Playbook, Schedule, Session, Template, User
from app.schedules import count_missed, describe_cron, next_occurrence


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
        for m in (Schedule, MaintenanceRun, Job, Template, Playbook, Host, Session, User):
            s.query(m).delete()


def _template(client) -> str:
    hid = client.post("/api/hosts", json={
        "name": "h1", "address": "127.0.0.1", "port": 22,
        "username": "root", "private_key": "FAKE"}).json()["id"]
    pb = client.post("/api/playbooks", json={
        "name": "pb1", "content": "- name: x\n  hosts: all\n  tasks: []\n"}).json()["id"]
    return client.post("/api/templates", json={
        "name": "t1", "playbook_id": pb, "host_ids": [hid]}).json()["id"]


def _mk_schedule(client, tid, cron="0 2 * * *", **kw):
    body = {"name": "s1", "template_id": tid, "cron": cron,
            "timezone": "UTC", "enabled": True}
    body.update(kw)
    r = client.post("/api/schedules", json=body)
    assert r.status_code == 200, r.text
    return r.json()["schedule_id"]


def _tick():
    from app.tasks import tick_schedules
    tick_schedules.apply()


def _get_schedule(client, sid):
    return client.get(f"/api/schedules/{sid}").json()


def test_preview_validates_and_describes(client):
    r = client.post("/api/schedules/preview",
                    json={"cron": "*/15 * * * *", "timezone": "UTC"})
    assert r.status_code == 200
    assert r.json()["human"] == "Every 15 minutes"
    assert len(r.json()["next_runs"]) == 3
    bad = client.post("/api/schedules/preview",
                      json={"cron": "not a cron", "timezone": "UTC"})
    assert bad.status_code == 400
    bad_tz = client.post("/api/schedules/preview",
                         json={"cron": "* * * * *", "timezone": "Mars/Olympus"})
    assert bad_tz.status_code == 400


def test_create_sets_next_run_and_rejects_bad_input(client):
    tid = _template(client)
    assert client.post("/api/schedules", json={
        "name": "x", "template_id": tid,
        "cron": "bad", "timezone": "UTC"}).status_code == 400
    assert client.post("/api/schedules", json={
        "name": "x", "template_id": "nope",
        "cron": "* * * * *", "timezone": "UTC"}).status_code == 404
    sid = _mk_schedule(client, tid)
    s = _get_schedule(client, sid)
    assert s["next_run_at"] > time.time()
    assert s["human"] == "At 02:00"


def test_duplicate_enabled_schedule_rejected(client):
    tid = _template(client)
    _mk_schedule(client, tid, cron="0 2 * * *")
    r = client.post("/api/schedules", json={
        "name": "s2", "template_id": tid, "cron": "0 2 * * *",
        "timezone": "UTC", "enabled": True})
    assert r.status_code == 400
    # disabled duplicates are fine; enabling the second is rejected
    sid2 = _mk_schedule(client, tid, cron="0 2 * * *", enabled=False,
                        name="s2")
    assert client.put(f"/api/schedules/{sid2}", json={
        "name": "s2", "template_id": tid, "cron": "0 2 * * *",
        "timezone": "UTC", "enabled": True}).status_code == 400


def test_tick_fires_due_schedule(client):
    tid = _template(client)
    sid = _mk_schedule(client, tid, cron="* * * * *")
    with session_scope() as s:
        r = s.get(Schedule, sid)
        r.next_run_at = time.time() - 5  # due now
    _tick()
    s = _get_schedule(client, sid)
    assert s["last_job_id"]
    assert s["last_status"] == "launched"
    assert s["next_run_at"] > time.time()
    assert s["missed_count"] == 0
    with session_scope() as sess:
        job = sess.get(Job, s["last_job_id"])
        assert job.schedule_id == sid
        assert job.snapshot["schedule_name"] == "s1"


def test_tick_marks_missed_not_silently_skipped(client):
    tid = _template(client)
    sid = _mk_schedule(client, tid, cron="*/15 * * * *")
    with session_scope() as s:
        r = s.get(Schedule, sid)
        # backend "down" for ~1h: 4 occurrences missed, latest fires now
        r.next_run_at = time.time() - 3600
    _tick()
    s = _get_schedule(client, sid)
    # next_run_at was ~1h stale: the stale marker itself + 3 quarter-hours
    # are counted missed; exactly one catch-up run fires
    assert s["missed_count"] == 4
    assert len(s["recent_missed"]) == 4
    assert s["last_job_id"]  # exactly one catch-up run fired
    assert s["next_run_at"] > time.time()
    with session_scope() as sess:
        n = sess.query(Job).filter(Job.schedule_id == sid).count()
        assert n == 1


def test_tick_skips_overlapping_run(client):
    tid = _template(client)
    sid = _mk_schedule(client, tid, cron="* * * * *")
    with session_scope() as s:
        # a previous scheduled run still going
        s.add(Job(id="stuckjob", host_ids=[], status="running",
                  snapshot={}, events=[], schedule_id=sid,
                  created_at=time.time(), finished_at=None))
        s.get(Schedule, sid).next_run_at = time.time() - 5
    _tick()
    s = _get_schedule(client, sid)
    assert s["last_job_id"] is None
    assert s["last_status"] == "skipped (overlap)"
    assert s["skipped_overlap"] == 1
    assert s["next_run_at"] > time.time()  # cadence advances, no pile-up


def test_run_now_launches_without_shifting_cadence(client):
    tid = _template(client)
    sid = _mk_schedule(client, tid, cron="0 2 * * *")
    before = _get_schedule(client, sid)["next_run_at"]
    r = client.post(f"/api/schedules/{sid}/run-now")
    assert r.status_code == 200
    jid = r.json()["job_id"]
    s = _get_schedule(client, sid)
    assert s["last_job_id"] == jid
    assert s["next_run_at"] == before  # cadence untouched
    assert any(j["id"] == jid for j in s["recent_jobs"])


def test_pause_resume_and_delete(client):
    tid = _template(client)
    sid = _mk_schedule(client, tid)
    assert client.put(f"/api/schedules/{sid}", json={
        "name": "s1", "template_id": tid, "cron": "0 2 * * *",
        "timezone": "UTC", "enabled": False}).status_code == 200
    s = _get_schedule(client, sid)
    assert s["enabled"] is False and s["next_run_at"] is None
    with session_scope() as sess:
        sess.get(Schedule, sid).next_run_at = time.time() - 5
    _tick()  # disabled: must not fire
    assert _get_schedule(client, sid)["last_job_id"] is None
    assert client.delete(f"/api/schedules/{sid}").status_code == 200
    assert client.get(f"/api/schedules/{sid}").status_code == 404


def test_tick_disables_schedule_with_deleted_template(client):
    tid = _template(client)
    sid = _mk_schedule(client, tid, cron="* * * * *")
    with session_scope() as s:
        s.query(Template).delete()
        s.get(Schedule, sid).next_run_at = time.time() - 5
    _tick()
    s = _get_schedule(client, sid)
    assert s["enabled"] is False
    assert s["last_job_id"] is None
    assert "template missing" in (s["last_status"] or "")


def test_count_missed_unit():
    now = 1_699_999_200.0  # 2023-11-14 22:00:00 UTC, on a */15 boundary
    missed, nxt = count_missed("*/15 * * * *", "UTC", now - 3600, now)
    assert len(missed) == 5  # 4 truly missed + 1 due now
    assert nxt > now
    # input must be a true cron occurrence; `now` itself is one for * * * * *
    missed2, nxt2 = count_missed("* * * * *", "UTC", now, now)
    assert len(missed2) == 1 and nxt2 > now


def test_next_occurrence_respects_timezone():
    # 09:00 Asia/Taipei == 01:00 UTC
    after = 1_700_000_000.0
    nxt = next_occurrence("0 9 * * *", "Asia/Taipei", after)
    import datetime
    dt = datetime.datetime.fromtimestamp(nxt, tz=datetime.timezone.utc)
    assert (dt.hour, dt.minute) == (1, 0)
    assert describe_cron("0 9 * * *") == "At 09:00"
