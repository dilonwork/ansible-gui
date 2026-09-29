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
import logging
import time

from .celery_app import celery
from .crypto import decrypt_str
from .db import session_scope
from . import events
from .models import Host, Job
from .runner_service import run_playbook

log = logging.getLogger("drydock.tasks")

TASK_KEY = "drydock:job-task:{jid}"
RUNDIR_KEY = "drydock:job-rundir:{jid}"

FINAL_STATUSES = ("successful", "failed", "cancelled", "interrupted")


def _finish_job(job_id: str, status: str) -> bool:
    """Atomically transition running -> final status. Returns True if applied."""
    assert status in FINAL_STATUSES
    with session_scope() as s:
        n = s.query(Job).filter(
            Job.id == job_id, Job.status == "running"
        ).update({"status": status, "finished_at": time.time()},
                 synchronize_session=False)
        return n > 0


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
