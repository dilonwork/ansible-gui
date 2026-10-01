"""Drydock backend.

Flow: browser -> REST (hosts / playbooks / templates / jobs) ->
Celery task runs ansible-runner in a worker process -> events flow back via
the event bus (DB + Redis pub/sub) -> WebSocket pushes to the frontend.

Persistence: SQLAlchemy; PostgreSQL under docker compose,
SQLite file for local dev (DATABASE_URL selects).

Auth: local accounts with bcrypt passwords, opaque bearer tokens,
roles admin/operator/viewer (viewers are read-only).
"""
import asyncio
import logging
import shutil
import subprocess
import time
import uuid

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth as auth_mod
from . import events
from .celery_app import celery
from .db import init_db, session_scope
from .crypto import decrypt_str, encrypt_str
from .launch import PING_YML, launch_job, template_snapshot
from .models import (Host, Job, MaintenanceRun, NotificationChannel, Playbook,
                     Schedule, Session, Template, Cluster, User)
from .runner_service import syntax_check_playbook
from .schedules import (count_missed, describe_cron, next_occurrence,
                        preview_next, validate_cron, validate_timezone)
from .tasks import TASK_KEY, RUNDIR_KEY, _finish_job, run_job_task
from . import k8s as k8s_mod
from .workflows import WORKFLOWS
from .maintenance_tasks import (
    TASK_KEY as MAINT_TASK_KEY,
    RUNDIR_KEY as MAINT_RUNDIR_KEY,
    _transition as _maint_transition,
    maintenance_abort_cleanup,
    run_maintenance_task,
)

log = logging.getLogger("drydock.main")


def _reconcile_jobs() -> None:
    """Mark jobs left 'running' with no live task as interrupted.

    Happens when the web process died mid-job. A queued-but-unstarted task
    still has its task key in Redis, so it is NOT marked interrupted.
    """
    r = events.get_redis()
    with session_scope() as s:
        running = s.query(Job).filter(Job.status == "running").all()
    for job in running:
        alive = r is not None and bool(r.exists(TASK_KEY.format(jid=job.id)))
        if not alive:
            if _finish_job(job.id, "interrupted"):
                events.emit_event(job.id, {"type": "job_interrupted",
                                           "msg": "backend restarted while job was running",
                                           "ts": round(time.time(), 2)})
                log.info("job %s marked interrupted (no live task)", job.id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    events.register_loop(asyncio.get_running_loop())
    events.start_event_forwarder()
    _reconcile_jobs()
    yield


app = FastAPI(title="Drydock API", lifespan=lifespan)


# ---------- auth ----------
AUTH_OPEN = {"/api/auth/login", "/api/auth/setup", "/api/auth/status"}
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in AUTH_OPEN:
        user = auth_mod.get_user_by_token(_bearer_token(request))
        if not user:
            return JSONResponse({"detail": "authentication required"}, 401)
        if user.role == "viewer" and request.method not in READ_METHODS:
            return JSONResponse({"detail": "viewers are read-only"}, 403)
        request.state.user = user
    return await call_next(request)


def _require_admin(request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.role != "admin":
        raise HTTPException(403, "admin only")
    return user


# ---------- models ----------
class HostIn(BaseModel):
    name: str
    address: str
    port: int = 22
    username: str = "root"
    private_key: str


class PlaybookIn(BaseModel):
    name: str
    content: str


class TemplateIn(BaseModel):
    name: str
    playbook_id: str
    host_ids: list[str]
    extra_vars: dict = {}
    check_mode: bool = False
    notification_policy: str = "failure_only"  # always | failure_only | never


class JobIn(BaseModel):
    host_ids: list[str] | None = None      # ad-hoc ping
    template_id: str | None = None         # launch from a template
    check_mode: bool | None = None         # override template default
    extra_vars: dict | None = None         # merged over template extra_vars


# ---------- helpers ----------
def keyscan(address: str, port: int) -> str:
    """SSH probe: fetch host keys for StrictHostKeyChecking at run time."""
    p = subprocess.run(
        ["ssh-keyscan", "-p", str(port), address],
        capture_output=True, text=True, timeout=20,
    )
    lines = [l for l in p.stdout.splitlines() if l and not l.startswith("#")]
    if not lines:
        raise RuntimeError(f"keyscan failed: {p.stderr.strip()[:200]}")
    return "\n".join(lines)


def _host_public(h: Host) -> dict:
    return {"id": h.id, "name": h.name, "address": h.address,
            "port": h.port, "username": h.username}


def _job_summary(j: Job) -> dict:
    s = j.snapshot or {}
    return {"id": j.id, "kind": s.get("kind"),
            "template_name": s.get("template_name"),
            "playbook_name": s.get("playbook_name"),
            "check_mode": s.get("check_mode", False),
            "host_ids": j.host_ids, "status": j.status,
            "created_at": j.created_at}


def _job_full(j: Job) -> dict:
    return {"id": j.id, "host_ids": j.host_ids, "status": j.status,
            "snapshot": j.snapshot, "events": j.events or [],
            "created_at": j.created_at, "finished_at": j.finished_at}


# ---------- auth ----------
class AuthIn(BaseModel):
    username: str
    password: str


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "operator"


class UserPatch(BaseModel):
    password: str | None = None
    role: str | None = None


@app.get("/api/auth/status")
def auth_status():
    return {"setup_required": auth_mod.user_count() == 0}


@app.post("/api/auth/setup")
def auth_setup(body: AuthIn):
    """Create the first admin account. Only works when no users exist."""
    if auth_mod.user_count() > 0:
        raise HTTPException(400, "setup already completed")
    try:
        u = auth_mod.create_user(body.username, body.password, role="admin")
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = auth_mod.create_session(u.id)
    return {"token": token, "username": u.username, "role": u.role}


@app.post("/api/auth/login")
def auth_login(body: AuthIn):
    with session_scope() as s:
        u = s.query(User).filter_by(username=body.username.strip()).first()
        if not u or not auth_mod.verify_password(body.password, u.password_hash):
            raise HTTPException(401, "invalid username or password")
        uid = u.id
    token = auth_mod.create_session(uid)
    with session_scope() as s:
        u = s.get(User, uid)
        return {"token": token, "username": u.username, "role": u.role}


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    auth_mod.revoke_token(_bearer_token(request))
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    u = request.state.user
    return {"username": u.username, "role": u.role,
            "can_write": u.role != "viewer"}


@app.get("/api/users")
def list_users(request: Request):
    _require_admin(request)
    with session_scope() as s:
        return [auth_mod.public_user(u) for u in s.query(User).all()]


@app.post("/api/users")
def create_user(body: UserIn, request: Request):
    _require_admin(request)
    try:
        u = auth_mod.create_user(body.username, body.password, body.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return auth_mod.public_user(u)


@app.patch("/api/users/{uid}")
def update_user(uid: str, body: UserPatch, request: Request):
    me = _require_admin(request)
    with session_scope() as s:
        u = s.get(User, uid)
        if not u:
            raise HTTPException(404, "no such user")
        if body.role is not None:
            if body.role not in auth_mod.ROLES:
                raise HTTPException(400, f"role must be one of {auth_mod.ROLES}")
            if u.role == "admin" and body.role != "admin" \
                    and auth_mod.admin_count() <= 1:
                raise HTTPException(400, "cannot demote the last admin")
            u.role = body.role
        if body.password is not None:
            try:
                auth_mod.validate_new_password(body.password)
            except ValueError as e:
                raise HTTPException(400, str(e))
            u.password_hash = auth_mod.hash_password(body.password)
            # changing a password kills all other sessions
            s.query(Session).filter(Session.user_id == u.id,
                                    Session.token != _bearer_token(request)).delete()
        return auth_mod.public_user(u)


@app.delete("/api/users/{uid}")
def delete_user(uid: str, request: Request):
    me = _require_admin(request)
    if uid == me.id:
        raise HTTPException(400, "cannot delete yourself")
    with session_scope() as s:
        u = s.get(User, uid)
        if not u:
            raise HTTPException(404, "no such user")
        if u.role == "admin" and auth_mod.admin_count() <= 1:
            raise HTTPException(400, "cannot delete the last admin")
        s.query(Session).filter_by(user_id=uid).delete()
        s.delete(u)
    return {"ok": True}


# ---------- hosts ----------
@app.post("/api/hosts")
def add_host(h: HostIn):
    try:
        host_key = keyscan(h.address, h.port)
    except Exception as e:
        raise HTTPException(400, f"host probe failed (keyscan): {e}")
    hid = uuid.uuid4().hex[:8]
    with session_scope() as s:
        s.add(Host(id=hid, name=h.name, address=h.address, port=h.port,
                   username=h.username, private_key=encrypt_str(h.private_key),
                   host_key=host_key, added_at=time.time()))
    return {"id": hid, "name": h.name, "address": h.address}


@app.get("/api/hosts")
def list_hosts():
    with session_scope() as s:
        return [_host_public(h) for h in s.query(Host).all()]


@app.delete("/api/hosts/{hid}")
def del_host(hid: str):
    with session_scope() as s:
        h = s.get(Host, hid)
        if h:
            s.delete(h)
    return {"ok": True}


# ---------- playbooks ----------
@app.post("/api/playbooks")
def add_playbook(p: PlaybookIn):
    if not p.content.strip():
        raise HTTPException(400, "playbook content is empty")
    pid = uuid.uuid4().hex[:8]
    with session_scope() as s:
        s.add(Playbook(id=pid, name=p.name, content=p.content,
                       created_at=time.time()))
    return {"id": pid}


@app.get("/api/playbooks")
def list_playbooks():
    with session_scope() as s:
        return [{"id": p.id, "name": p.name, "created_at": p.created_at}
                for p in s.query(Playbook).all()]


@app.get("/api/playbooks/{pid}")
def get_playbook(pid: str):
    with session_scope() as s:
        p = s.get(Playbook, pid)
        if not p:
            raise HTTPException(404, "no such playbook")
        return {"id": p.id, "name": p.name, "content": p.content,
                "created_at": p.created_at}


@app.delete("/api/playbooks/{pid}")
def del_playbook(pid: str):
    with session_scope() as s:
        used_by = [t.name for t in s.query(Template).all()
                   if t.playbook_id == pid]
        if used_by:
            raise HTTPException(400, f"playbook is used by templates: {used_by}")
        p = s.get(Playbook, pid)
        if p:
            s.delete(p)
    return {"ok": True}


@app.post("/api/playbooks/{pid}/syntax-check")
def playbook_syntax_check(pid: str):
    with session_scope() as s:
        p = s.get(Playbook, pid)
        if not p:
            raise HTTPException(404, "no such playbook")
        content = p.content
    ok, output = syntax_check_playbook(content)
    return {"ok": ok, "output": output}


# ---------- job templates ----------
@app.post("/api/templates")
def add_template(t: TemplateIn):
    if t.notification_policy not in ("always", "failure_only", "never"):
        raise HTTPException(400, "notification_policy must be always|failure_only|never")
    with session_scope() as s:
        if not s.get(Playbook, t.playbook_id):
            raise HTTPException(400, "unknown playbook_id")
        known = {h.id for h in s.query(Host).all()}
        missing = [i for i in t.host_ids if i not in known]
        if missing:
            raise HTTPException(400, f"unknown hosts: {missing}")
        tid = uuid.uuid4().hex[:8]
        s.add(Template(id=tid, name=t.name, playbook_id=t.playbook_id,
                       host_ids=list(t.host_ids), extra_vars=dict(t.extra_vars),
                       check_mode=t.check_mode,
                       notification_policy=t.notification_policy,
                       created_at=time.time()))
    return {"id": tid}


@app.get("/api/templates")
def list_templates():
    with session_scope() as s:
        out = []
        for t in s.query(Template).all():
            pb = s.get(Playbook, t.playbook_id)
            out.append({"id": t.id, "name": t.name, "playbook_id": t.playbook_id,
                        "playbook_name": pb.name if pb else "(deleted)",
                        "host_ids": t.host_ids, "extra_vars": t.extra_vars,
                        "check_mode": t.check_mode,
                        "notification_policy": t.notification_policy or "failure_only",
                        "created_at": t.created_at})
        return out


@app.delete("/api/templates/{tid}")
def del_template(tid: str):
    with session_scope() as s:
        t = s.get(Template, tid)
        if t:
            s.delete(t)
    return {"ok": True}


# ---------- jobs ----------
@app.post("/api/jobs")
def create_job(j: JobIn):
    if bool(j.template_id) == bool(j.host_ids):
        raise HTTPException(400, "provide either template_id or host_ids")

    with session_scope() as s:
        if j.template_id:
            try:
                snapshot = template_snapshot(j.template_id, j.extra_vars,
                                             j.check_mode)
            except ValueError as e:
                raise HTTPException(400, str(e))
        else:
            known = {h.id for h in s.query(Host).all()}
            missing = [i for i in j.host_ids if i not in known]
            if missing:
                raise HTTPException(400, f"unknown hosts: {missing}")
            snapshot = {
                "kind": "ad-hoc",
                "playbook_name": "ping",
                "playbook_content": PING_YML,
                "host_ids": list(j.host_ids),
                "extra_vars": j.extra_vars or {},
                "check_mode": False,
            }
    return {"job_id": launch_job(snapshot)}


@app.get("/api/jobs")
def list_jobs():
    with session_scope() as s:
        return [_job_summary(j) for j in s.query(Job).all()]


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    with session_scope() as s:
        job = s.get(Job, jid)
        if not job:
            raise HTTPException(404, "no such job")
        return _job_full(job)


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    with session_scope() as s:
        job = s.get(Job, jid)
        if not job:
            raise HTTPException(404, "no such job")
        if job.status != "running":
            raise HTTPException(400, f"job is not running (status={job.status})")

    r = events.get_redis()
    task_id = None
    if r is not None:
        try:
            raw = r.get(TASK_KEY.format(jid=jid))
            task_id = raw.decode() if isinstance(raw, bytes) else raw
        except Exception:
            pass
    if task_id:
        try:
            celery.control.revoke(task_id, terminate=True)
        except Exception as e:
            log.warning("revoke failed for job %s: %s", jid, e)

    # a terminated worker can't clean up; remove its temp dir (holds the 600 key file)
    if r is not None:
        try:
            raw = r.get(RUNDIR_KEY.format(jid=jid))
            rundir = raw.decode() if isinstance(raw, bytes) else raw
            if rundir:
                shutil.rmtree(rundir, ignore_errors=True)
            r.delete(TASK_KEY.format(jid=jid), RUNDIR_KEY.format(jid=jid))
        except Exception:
            pass

    if not _finish_job(jid, "cancelled"):
        raise HTTPException(400, "job already finished")
    events.emit_event(jid, {"type": "job_cancelled",
                            "ts": round(time.time(), 2)})
    return {"ok": True}


@app.post("/api/jobs/{jid}/retry")
def retry_job(jid: str):
    with session_scope() as s:
        job = s.get(Job, jid)
        if not job:
            raise HTTPException(404, "no such job")
        if job.status != "failed":
            raise HTTPException(400,
                                f"only failed jobs can be retried (status={job.status})")
        snapshot = dict(job.snapshot or {})
        failed_names = {e.get("host") for e in (job.events or [])
                        if e.get("type") in ("host_failed", "host_unreachable")
                        and e.get("host")}
        rows = s.query(Host).filter(
            Host.id.in_(snapshot.get("host_ids", []))).all()
        failed_ids = [h.id for h in rows if h.name in failed_names]
    if not failed_ids:
        raise HTTPException(400, "no failed hosts to retry")
    snapshot["host_ids"] = failed_ids
    snapshot["retried_from"] = jid
    return {"job_id": launch_job(snapshot)}


# ---------- maintenance runs (M5) ----------
class MaintenanceIn(BaseModel):
    name: str
    workflow: str = "os-patch"
    node_ids: list[str]
    kubeconfig: str | None = None       # optional: enables k8s steps
    params: dict = {}                   # kubelet_version, drain_timeout, ...
    playbook_overrides: dict = {}       # testing seam: {step_name: content}


def _maint_summary(r: MaintenanceRun) -> dict:
    steps = r.steps or []
    done = sum(1 for n in steps if n.get("state") == "done")
    return {"id": r.id, "name": r.name, "workflow": r.workflow,
            "node_ids": r.node_ids, "status": r.status,
            "nodes_done": done, "nodes_total": len(steps),
            "created_at": r.created_at, "finished_at": r.finished_at}


def _maint_full(r: MaintenanceRun) -> dict:
    return {"id": r.id, "name": r.name, "workflow": r.workflow,
            "node_ids": r.node_ids, "status": r.status,
            "steps": r.steps or [], "preflight": r.preflight or [],
            "snapshot": r.snapshot or {}, "events": r.events or [],
            "created_at": r.created_at, "finished_at": r.finished_at,
            "has_k8s": bool(r.kubeconfig)}


def _get_maint_or_404(rid: str) -> MaintenanceRun:
    with session_scope() as s:
        run = s.get(MaintenanceRun, rid)
        if not run:
            raise HTTPException(404, "no such maintenance run")
        s.expunge(run)
        return run


def _enqueue_maintenance(rid: str) -> None:
    result = run_maintenance_task.delay(rid)
    r = events.get_redis()
    if r is not None:
        try:
            r.set(MAINT_TASK_KEY.format(rid=rid), result.id, ex=86400)
        except Exception:
            pass


@app.post("/api/maintenance")
def create_maintenance(m: MaintenanceIn):
    if m.workflow not in WORKFLOWS:
        raise HTTPException(400, f"unknown workflow: {m.workflow}")
    if not m.node_ids:
        raise HTTPException(400, "node_ids is empty")
    with session_scope() as s:
        known = {h.id for h in s.query(Host).all()}
        missing = [i for i in m.node_ids if i not in known]
        if missing:
            raise HTTPException(400, f"unknown hosts: {missing}")
        rid = uuid.uuid4().hex[:8]
        snapshot = {"workflow": m.workflow,
                    "node_ids": list(m.node_ids),
                    "params": dict(m.params or {}),
                    "playbook_overrides": dict(m.playbook_overrides or {})}
        s.add(MaintenanceRun(
            id=rid, name=m.name, workflow=m.workflow,
            node_ids=list(m.node_ids), status="running",
            steps=[], preflight=[], snapshot=snapshot, events=[],
            kubeconfig=encrypt_str(m.kubeconfig) if m.kubeconfig else None,
            created_at=time.time(), finished_at=None))
    events.ws_queues[rid] = []
    _enqueue_maintenance(rid)
    return {"run_id": rid}


@app.get("/api/maintenance")
def list_maintenance():
    with session_scope() as s:
        return [_maint_summary(r) for r in s.query(MaintenanceRun).all()]


@app.get("/api/maintenance/{rid}")
def get_maintenance(rid: str):
    return _maint_full(_get_maint_or_404(rid))


@app.post("/api/maintenance/{rid}/pause")
def pause_maintenance(rid: str):
    _get_maint_or_404(rid)
    if not _maint_transition(rid, ("running",), "pausing"):
        raise HTTPException(400, "run is not running")
    return {"ok": True}


@app.post("/api/maintenance/{rid}/resume")
def resume_maintenance(rid: str):
    _get_maint_or_404(rid)
    if not _maint_transition(rid, ("paused", "pausing"), "running"):
        raise HTTPException(400, "run is not paused")
    _enqueue_maintenance(rid)
    return {"ok": True}


@app.post("/api/maintenance/{rid}/retry-node")
def retry_maintenance_node(rid: str):
    run = _get_maint_or_404(rid)
    if run.status != "paused":
        raise HTTPException(400, f"run is not paused (status={run.status})")
    steps = [dict(n) for n in (run.steps or [])]
    node = next((n for n in steps if n.get("state") == "failed"), None)
    if not node:
        raise HTTPException(400, "no failed node to retry")
    step_names = [s for s in node.get("step_states", {})]
    node["step_states"] = {s: "pending" for s in step_names}
    node["state"] = "pending"
    node["failed_step"] = None
    with session_scope() as s:
        s.get(MaintenanceRun, rid).steps = steps
    events.emit_run_event(rid, {"type": "maint_node_retry",
                                "node": node["node_name"],
                                "ts": round(time.time(), 2)})
    if not _maint_transition(rid, ("paused",), "running"):
        raise HTTPException(400, "run is not paused")
    _enqueue_maintenance(rid)
    return {"ok": True}


@app.post("/api/maintenance/{rid}/skip-node")
def skip_maintenance_node(rid: str):
    run = _get_maint_or_404(rid)
    if run.status != "paused":
        raise HTTPException(400, f"run is not paused (status={run.status})")
    steps = [dict(n) for n in (run.steps or [])]
    node = next((n for n in steps if n.get("state") == "failed"), None)
    if not node:
        raise HTTPException(400, "no failed node to skip")
    node["state"] = "skipped"
    with session_scope() as s:
        s.get(MaintenanceRun, rid).steps = steps
    events.emit_run_event(rid, {"type": "maint_node_skipped",
                                "node": node["node_name"],
                                "ts": round(time.time(), 2)})
    if not _maint_transition(rid, ("paused",), "running"):
        raise HTTPException(400, "run is not paused")
    _enqueue_maintenance(rid)
    return {"ok": True}


@app.post("/api/maintenance/{rid}/abort")
def abort_maintenance(rid: str):
    run = _get_maint_or_404(rid)
    if run.status not in ("running", "pausing", "paused"):
        raise HTTPException(400, f"run is not active (status={run.status})")
    r = events.get_redis()
    if r is not None:
        try:
            raw = r.get(MAINT_TASK_KEY.format(rid=rid))
            task_id = raw.decode() if isinstance(raw, bytes) else raw
            if task_id:
                celery.control.revoke(task_id, terminate=True)
        except Exception as e:
            log.warning("revoke failed for maintenance run %s: %s", rid, e)
    # best-effort uncordon of nodes left cordoned (k8s workflows)
    if run.kubeconfig:
        maintenance_abort_cleanup.delay(rid)
    if r is not None:
        try:
            raw = r.get(MAINT_RUNDIR_KEY.format(rid=rid))
            rundir = raw.decode() if isinstance(raw, bytes) else raw
            if rundir:
                shutil.rmtree(rundir, ignore_errors=True)
        except Exception:
            pass
    if not _maint_transition(rid, ("running", "pausing", "paused"), "aborted"):
        raise HTTPException(400, "run already finished")
    events.emit_run_event(rid, {"type": "maint_aborted",
                                "ts": round(time.time(), 2)})
    return {"ok": True}


# ---------- schedules ----------
class ScheduleIn(BaseModel):
    name: str
    template_id: str
    cron: str
    timezone: str = "UTC"
    enabled: bool = True


class SchedulePreviewIn(BaseModel):
    cron: str
    timezone: str = "UTC"


def _check_cron_tz(cron: str, timezone: str) -> None:
    try:
        validate_cron(cron)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        validate_timezone(timezone)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _sched_public(s: Schedule, template_name: str) -> dict:
    return {"id": s.id, "name": s.name, "template_id": s.template_id,
            "template_name": template_name, "cron": s.cron,
            "timezone": s.timezone, "enabled": s.enabled,
            "human": describe_cron(s.cron),
            "next_run_at": s.next_run_at, "last_run_at": s.last_run_at,
            "last_job_id": s.last_job_id, "last_status": s.last_status,
            "missed_count": s.missed_count or 0,
            "recent_missed": s.recent_missed or [],
            "skipped_overlap": s.skipped_overlap or 0,
            "created_at": s.created_at}


def _template_names(s) -> dict:
    return {t.id: t.name for t in s.query(Template).all()}


def _no_duplicate(s, template_id: str, cron: str, timezone: str,
                  exclude: str | None = None) -> None:
    q = s.query(Schedule).filter(
        Schedule.enabled == True,  # noqa: E712
        Schedule.template_id == template_id,
        Schedule.cron == cron, Schedule.timezone == timezone)
    if exclude:
        q = q.filter(Schedule.id != exclude)
    if q.count():
        raise HTTPException(
            400, "an enabled schedule with the same template + cron + "
                 "timezone already exists")


@app.post("/api/schedules/preview")
def preview_schedule(p: SchedulePreviewIn):
    _check_cron_tz(p.cron, p.timezone)
    now = time.time()
    return {"human": describe_cron(p.cron),
            "next_runs": preview_next(p.cron, p.timezone, now)}


@app.post("/api/schedules")
def create_schedule(sch: ScheduleIn):
    _check_cron_tz(sch.cron, sch.timezone)
    with session_scope() as s:
        if not s.get(Template, sch.template_id):
            raise HTTPException(404, "no such template")
        if sch.enabled:
            _no_duplicate(s, sch.template_id, sch.cron, sch.timezone)
        sid = uuid.uuid4().hex[:8]
        now = time.time()
        s.add(Schedule(
            id=sid, name=sch.name, template_id=sch.template_id,
            cron=sch.cron, timezone=sch.timezone, enabled=sch.enabled,
            next_run_at=next_occurrence(sch.cron, sch.timezone, now)
            if sch.enabled else None,
            created_at=now))
    return {"schedule_id": sid}


@app.get("/api/schedules")
def list_schedules():
    with session_scope() as s:
        names = _template_names(s)
        return [_sched_public(r, names.get(r.template_id, "(deleted)"))
                for r in s.query(Schedule).all()]


@app.get("/api/schedules/{sid}")
def get_schedule(sid: str):
    with session_scope() as s:
        r = s.get(Schedule, sid)
        if not r:
            raise HTTPException(404, "no such schedule")
        out = _sched_public(r, _template_names(s).get(r.template_id,
                                                     "(deleted)"))
        jobs = (s.query(Job).filter(Job.schedule_id == sid)
                .order_by(Job.created_at.desc()).limit(5).all())
        out["recent_jobs"] = [
            {"id": j.id, "status": j.status, "created_at": j.created_at,
             "finished_at": j.finished_at} for j in jobs]
        return out


@app.put("/api/schedules/{sid}")
def update_schedule(sid: str, sch: ScheduleIn):
    _check_cron_tz(sch.cron, sch.timezone)
    with session_scope() as s:
        r = s.get(Schedule, sid)
        if not r:
            raise HTTPException(404, "no such schedule")
        if not s.get(Template, sch.template_id):
            raise HTTPException(404, "no such template")
        if sch.enabled:
            _no_duplicate(s, sch.template_id, sch.cron, sch.timezone,
                          exclude=sid)
        cadence_changed = (sch.cron != r.cron or sch.timezone != r.timezone
                           or (sch.enabled and not r.enabled))
        r.name = sch.name
        r.template_id = sch.template_id
        r.cron = sch.cron
        r.timezone = sch.timezone
        r.enabled = sch.enabled
        if not sch.enabled:
            r.next_run_at = None
        elif cadence_changed:
            r.next_run_at = next_occurrence(sch.cron, sch.timezone, time.time())
    return {"ok": True}


@app.delete("/api/schedules/{sid}")
def delete_schedule(sid: str):
    with session_scope() as s:
        r = s.get(Schedule, sid)
        if not r:
            raise HTTPException(404, "no such schedule")
        s.delete(r)
    return {"ok": True}


@app.post("/api/schedules/{sid}/run-now")
def run_schedule_now(sid: str):
    with session_scope() as s:
        r = s.get(Schedule, sid)
        if not r:
            raise HTTPException(404, "no such schedule")
        try:
            snapshot = template_snapshot(r.template_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        snapshot["schedule_id"] = sid
        snapshot["schedule_name"] = r.name
        jid = launch_job(snapshot, schedule_id=sid)
        r.last_run_at = time.time()
        r.last_job_id = jid
        r.last_status = "launched (manual)"
    return {"job_id": jid}


# ---------- notification channels (M8.1) ----------
class ChannelIn(BaseModel):
    name: str
    type: str = "webhook"
    config: dict = {}   # webhook: {url, headers}
    enabled: bool = True


def _channel_public(c: NotificationChannel) -> dict:
    return {"id": c.id, "name": c.name, "type": c.type,
            "config": c.config or {}, "enabled": c.enabled,
            "created_at": c.created_at}


@app.get("/api/notification-channels")
def list_channels():
    with session_scope() as s:
        return [_channel_public(c) for c in s.query(NotificationChannel).all()]


@app.post("/api/notification-channels")
def add_channel(ch: ChannelIn):
    if ch.type != "webhook":
        raise HTTPException(400, "only 'webhook' channels are supported for now")
    if not (ch.config or {}).get("url"):
        raise HTTPException(400, "config.url is required")
    with session_scope() as s:
        cid = uuid.uuid4().hex[:8]
        s.add(NotificationChannel(id=cid, name=ch.name, type=ch.type,
                                  config=dict(ch.config), enabled=ch.enabled,
                                  created_at=time.time()))
    return {"id": cid}


@app.delete("/api/notification-channels/{cid}")
def delete_channel(cid: str):
    with session_scope() as s:
        c = s.get(NotificationChannel, cid)
        if c:
            s.delete(c)
    return {"ok": True}


@app.post("/api/notification-channels/{cid}/test")
def test_channel(cid: str):
    """Send a test payload to the channel; returns what the endpoint said."""
    import urllib.request, urllib.error, json as _json
    with session_scope() as s:
        c = s.get(NotificationChannel, cid)
        if not c:
            raise HTTPException(404, "no such channel")
        url = (c.config or {}).get("url")
        headers = (c.config or {}).get("headers", {})
    payload = {"event": "test", "message": "Drydock test notification",
               "link": "/"}
    try:
        req = urllib.request.Request(
            url, data=_json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"ok": True, "status": resp.status}
    except urllib.error.HTTPError as e:
        raise HTTPException(400, f"webhook returned HTTP {e.code}")
    except Exception as e:
        raise HTTPException(400, f"webhook unreachable: {e}")


# ---------- clusters (M4) ----------
class ClusterIn(BaseModel):
    name: str
    kubeconfig: str


def _cluster_public(c: Cluster) -> dict:
    return {"id": c.id, "name": c.name, "server": c.server,
            "k8s_version": c.k8s_version, "status": c.status,
            "last_error": c.last_error, "last_sync_at": c.last_sync_at,
            "created_at": c.created_at}


@app.post("/api/clusters")
def add_cluster(c: ClusterIn):
    if not c.kubeconfig.strip():
        raise HTTPException(400, "kubeconfig is empty")
    try:
        info = k8s_mod.parse_kubeconfig(c.kubeconfig)
    except ValueError as e:
        raise HTTPException(400, str(e))
    result = k8s_mod.test_connection(c.kubeconfig)
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    with session_scope() as s:
        cid = uuid.uuid4().hex[:8]
        now = time.time()
        s.add(Cluster(id=cid, name=c.name,
                      kubeconfig=encrypt_str(c.kubeconfig),
                      server=result["server"],
                      k8s_version=result.get("k8s_version"),
                      status="ok", last_error=None,
                      last_sync_at=now, created_at=now))
    return {"id": cid, "server": result["server"],
            "k8s_version": result.get("k8s_version")}


@app.get("/api/clusters")
def list_clusters():
    with session_scope() as s:
        return [_cluster_public(c) for c in s.query(Cluster).all()]


def _get_cluster_or_404(s, cid: str) -> Cluster:
    c = s.get(Cluster, cid)
    if not c:
        raise HTTPException(404, "no such cluster")
    return c


@app.get("/api/clusters/{cid}")
def get_cluster(cid: str):
    with session_scope() as s:
        return _cluster_public(_get_cluster_or_404(s, cid))


@app.delete("/api/clusters/{cid}")
def delete_cluster(cid: str):
    with session_scope() as s:
        c = _get_cluster_or_404(s, cid)
        s.delete(c)
    return {"ok": True}


@app.post("/api/clusters/{cid}/test")
def test_cluster(cid: str):
    """Re-run the connectivity check; updates stored status."""
    with session_scope() as s:
        c = _get_cluster_or_404(s, cid)
        result = k8s_mod.test_connection(decrypt_str(c.kubeconfig))
        c.status = "ok" if result["ok"] else "error"
        c.last_error = None if result["ok"] else result["error"]
        if result["ok"]:
            c.k8s_version = result.get("k8s_version") or c.k8s_version
            c.last_sync_at = time.time()
        return result


def _live_or_stale(cid: str, fetch):
    """Run fetch(kubeconfig); on failure mark the cluster and report stale
    instead of silently serving old numbers (4.2 acceptance criterion)."""
    with session_scope() as s:
        c = _get_cluster_or_404(s, cid)
        kubeconfig = decrypt_str(c.kubeconfig)
        try:
            data = fetch(kubeconfig)
        except Exception as e:
            err = k8s_mod.categorize_error(e)
            c.status = "error"
            c.last_error = err
            return {"stale": True, "error": err,
                    "last_sync_at": c.last_sync_at}
        c.status = "ok"
        c.last_error = None
        c.last_sync_at = time.time()
        data["stale"] = False
        return data


@app.get("/api/clusters/{cid}/overview")
def cluster_overview(cid: str):
    return _live_or_stale(cid, k8s_mod.get_overview)


@app.get("/api/clusters/{cid}/nodes")
def cluster_nodes(cid: str):
    return _live_or_stale(cid, lambda kc: {"nodes": k8s_mod.list_nodes(kc)})


@app.get("/api/clusters/{cid}/workloads")
def cluster_workloads(cid: str):
    return _live_or_stale(cid, lambda kc: {"workloads": k8s_mod.list_workloads(kc)})


@app.get("/api/clusters/{cid}/events")
def cluster_events(cid: str):
    return _live_or_stale(cid, lambda kc: {"events": k8s_mod.list_events(kc)})


# ---------- websocket: live event stream ----------
@app.websocket("/ws/jobs/{jid}")
async def job_stream(ws: WebSocket, jid: str):
    await _event_stream(ws, jid, Job, ("job_finished", "job_cancelled"))


@app.websocket("/ws/maintenance/{rid}")
async def maintenance_stream(ws: WebSocket, rid: str):
    await _event_stream(ws, rid, MaintenanceRun,
                        ("maint_run_done", "maint_preflight_failed"))


async def _event_stream(ws: WebSocket, rid: str, model,
                        terminal_types: tuple) -> None:
    # bearer token via query param (browsers can't set WS headers)
    if not auth_mod.get_user_by_token(ws.query_params.get("token", "")):
        await ws.close(code=4401)
        return
    with session_scope() as s:
        obj = s.get(model, rid)
        if not obj:
            await ws.close(code=4404)
            return
        history = list(obj.events or [])
        running = obj.status in ("running", "pausing")
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    events.ws_queues.setdefault(rid, []).append(q)
    try:
        # replay history first so late joiners miss nothing
        for e in history:
            await ws.send_json(e)
        if not running:
            await ws.send_json({"type": "eof"})
            return
        while True:
            e = await q.get()
            await ws.send_json(e)
            if e.get("type") in terminal_types:
                await asyncio.sleep(0.2)
                break
    except WebSocketDisconnect:
        pass
    finally:
        if q in events.ws_queues.get(rid, []):
            events.ws_queues[rid].remove(q)
        await ws.close()


# ---------- demo GUI ----------
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def index():
    return FileResponse("app/static/index.html")
