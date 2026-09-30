"""Celery tasks: durable job execution.

A job is enqueued with its frozen snapshot; the worker loads hosts from the
DB (decrypting private keys), runs ansible-runner, and records the final
status with an atomic ``WHERE status='running'`` transition so a concurrent
cancel can't be overwritten.

Redis keys:
- ``drydock:job-task:{jid}`` -> celery task id (set at enqueue, deleted on finish)
- ``drydock:job-rundir:{jid}`` -> worker temp dir holding the 600 SSH key file,
  so cancel can clean it up after terminating the worker process.
"""
import json
import logging
import time
import urllib.request
import urllib.error

from .celery_app import celery
from .crypto import decrypt_str
from .db import session_scope
from . import events
from .models import Host, Job, NotificationChannel, Template
from .runner_service import run_playbook

log = logging.getLogger("drydock.tasks")

TASK_KEY = "drydock:job-task:{jid}"
RUNDIR_KEY = "drydock:job-rundir:{jid}"

FINAL_STATUSES = ("successful", "failed", "cancelled", "interrupted")

NOTIFY_POLICIES = ("always", "failure_only", "never")


def _finish_job(job_id: str, status: str) -> bool:
    """Atomically transition running -> final status. Returns True if applied."""
    assert status in FINAL_STATUSES
    with session_scope() as s:
        n = s.query(Job).filter(
            Job.id == job_id, Job.status == "running"
        ).update({"status": status, "finished_at": time.time()},
                 synchronize_session=False)
    # notify after commit so the summary sees the final state
    if n > 0:
        _maybe_notify(job_id, status)
    return n > 0


def _maybe_notify(job_id: str, status: str) -> None:
    """Enqueue a notification if the job's template policy wants one. (M8.1)"""
    with session_scope() as s:
        job = s.get(Job, job_id)
        snap = (job.snapshot or {}) if job else {}
        if snap.get("kind") != "template":
            return  # ad-hoc runs are watched interactively; never notify
        t = s.get(Template, snap.get("template_id"))
        policy = (t.notification_policy or "failure_only") if t else "failure_only"
    if policy == "never":
        return
    if status == "successful" and policy != "always":
        return
    try:
        send_notification.delay(job_id, status)
    except Exception as e:
        log.warning("could not enqueue notification for job %s: %s", job_id, e)


def _job_summary(job_id: str) -> dict | None:
    """Build the notification payload for a finished job."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            return None
        snap = job.snapshot or {}
        host_ids = list(job.host_ids or [])
        names = {h.id: h.name for h in
                 s.query(Host).filter(Host.id.in_(host_ids)).all()}
        all_names = [names.get(i, i) for i in host_ids]
        failed = sorted({e.get("host") for e in (job.events or [])
                         if e.get("type") in ("host_failed", "host_unreachable")
                         and e.get("host")})
        succeeded = [n for n in all_names if n not in failed]
        created = job.created_at or time.time()
        finished = job.finished_at or time.time()
        return {
            "event": "job_finished",
            "job_id": job_id,
            "status": job.status,
            "template_name": snap.get("template_name"),
            "playbook_name": snap.get("playbook_name"),
            "schedule_name": snap.get("schedule_name"),
            "hosts_total": len(host_ids),
            "hosts_succeeded": len(succeeded),
            "failed_hosts": failed,
            "duration_s": round(finished - created, 1),
            "link": f"/jobs/{job_id}",
        }


def _post_webhook(url: str, headers: dict, payload: dict) -> None:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status >= 400:
            raise urllib.error.URLError(f"webhook returned {resp.status}")


@celery.task(bind=True, name="drydock.send_notification",
             autoretry_for=(Exception,), retry_kwargs={"max_retries": 3},
             retry_backoff=60, max_retries=3)
def send_notification(self, job_id: str, status: str) -> None:
    """POST the job outcome to every enabled webhook channel. (M8.1)"""
    payload = _job_summary(job_id)
    if payload is None:
        return
    with session_scope() as s:
        channels = s.query(NotificationChannel).filter(
            NotificationChannel.enabled == True,  # noqa: E712
            NotificationChannel.type == "webhook").all()
        targets = [(c.name, (c.config or {}).get("url"),
                    (c.config or {}).get("headers", {})) for c in channels]
    for name, url, headers in targets:
        if not url:
            log.warning("notification channel %s has no url, skipping", name)
            continue
        log.info("notifying channel %s for job %s (%s)", name, job_id, status)
        _post_webhook(url, headers, payload)


@celery.task(bind=True, name="drydock.run_job", max_retries=0)
def run_job_task(self, job_id: str) -> None:
    r = events.get_redis()
    try:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                log.warning("job %s not found, skipping", job_id)
                return
            if job.status != "running":
                log.info("job %s already %s, skipping", job_id, job.status)
                return
            snapshot = dict(job.snapshot or {})
            host_rows = s.query(Host).filter(
                Host.id.in_(snapshot.get("host_ids", []))).all()
            job_hosts = [{"name": h.name, "address": h.address, "port": h.port,
                          "username": h.username,
                          "private_key": decrypt_str(h.private_key),
                          "host_key": h.host_key} for h in host_rows]
        if not job_hosts:
            events.emit_event(job_id, {"type": "job_error",
                                "msg": "no hosts left to run on",
                                "ts": round(time.time(), 2)})
            _finish_job(job_id, "failed")
            return

        def _record_rundir(path: str) -> None:
            rr = events.get_redis()
            if rr is not None:
                try:
                    rr.set(RUNDIR_KEY.format(jid=job_id), path, ex=86400)
                except Exception:
                    pass

        ok = run_playbook(
            job_id, job_hosts,
            snapshot["playbook_content"],
            snapshot.get("extra_vars") or {},
            snapshot.get("check_mode", False),
            lambda e: events.emit_event(job_id, e),
            on_run_dir=_record_rundir,
        )
        _finish_job(job_id, "successful" if ok else "failed")
    except Exception as e:
        log.exception("job %s crashed", job_id)
        try:
            events.emit_event(job_id, {"type": "job_error", "msg": str(e)[:500],
                                "ts": round(time.time(), 2)})
        except Exception:
            pass
        _finish_job(job_id, "failed")
    finally:
        rr = events.get_redis()
        if rr is not None:
            try:
                rr.delete(TASK_KEY.format(jid=job_id),
                          RUNDIR_KEY.format(jid=job_id))
            except Exception:
                pass


@celery.task(bind=True, name="drydock.tick_schedules", max_retries=0)
def tick_schedules(self) -> None:
    """Beat tick (every 60s): fire due schedules.

    Lazy-imports launch helpers to avoid a circular import at module load.
    """
    from .launch import launch_job, template_snapshot
    from .models import Schedule
    from .schedules import count_missed, next_occurrence

    now = time.time()
    with session_scope() as s:
        due_ids = [r.id for r in s.query(Schedule).filter(
            Schedule.enabled == True,  # noqa: E712
            Schedule.next_run_at.isnot(None),
            Schedule.next_run_at <= now).all()]
    for sid in due_ids:
        try:
            _fire_schedule(sid, now, launch_job, template_snapshot,
                           count_missed, next_occurrence)
        except Exception:
            log.exception("schedule %s tick failed", sid)


def _fire_schedule(sid: str, now: float, launch_job, template_snapshot,
                   count_missed, next_occurrence) -> None:
    from .models import Schedule
    # Phase 1: read-only — decide what to do, then close the session.
    # (Never hold a dirty session across nested writes: SQLite would deadlock
    #  on autoflush, and short transactions are correct anyway.)
    with session_scope() as s:
        r = s.get(Schedule, sid)
        if not r or not r.enabled:
            return
        if r.next_run_at is None or r.next_run_at > now:
            return
        state = {"cron": r.cron, "timezone": r.timezone,
                 "template_id": r.template_id, "name": r.name,
                 "next_run_at": r.next_run_at,
                 "missed_count": r.missed_count or 0,
                 "recent_missed": r.recent_missed or [],
                 "skipped_overlap": r.skipped_overlap or 0}
        overlap = s.query(Job).filter(
            Job.schedule_id == sid, Job.status == "running").count() > 0

    missed, nxt = count_missed(state["cron"], state["timezone"],
                               state["next_run_at"], now)
    truly_missed = missed[:-1]  # the latest due occurrence fires now
    if truly_missed:
        state["missed_count"] += len(truly_missed)
        state["recent_missed"] = (state["recent_missed"] +
                                  truly_missed[-20:])[-20:]

    # Phase 2: fire (its own sessions).
    jid = None
    status = None
    if overlap:
        state["skipped_overlap"] += 1
        status = "skipped (overlap)"
        state["next_run_at"] = next_occurrence(state["cron"],
                                               state["timezone"], now)
    else:
        try:
            snapshot = template_snapshot(state["template_id"])
        except ValueError as e:
            status = f"template missing: {e}"
            state["enabled"] = False
            state["next_run_at"] = None
        else:
            snapshot["schedule_id"] = sid
            snapshot["schedule_name"] = state["name"]
            jid = launch_job(snapshot, schedule_id=sid)
            status = "launched"
            state["next_run_at"] = next_occurrence(state["cron"],
                                                   state["timezone"], now)

    # Phase 3: persist the outcome in a fresh short transaction.
    with session_scope() as s:
        r = s.get(Schedule, sid)
        if not r:
            return
        r.missed_count = state["missed_count"]
        r.recent_missed = state["recent_missed"]
        r.skipped_overlap = state["skipped_overlap"]
        r.next_run_at = state["next_run_at"]
        r.last_status = status
        if "enabled" in state:
            r.enabled = state["enabled"]
        if jid:
            r.last_run_at = now
            r.last_job_id = jid
