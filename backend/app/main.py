"""Drydock backend.

Flow: browser -> REST (hosts / playbooks / templates / jobs) ->
Celery task runs ansible-runner in a worker process -> events flow back via
the event bus (DB + Redis pub/sub) -> WebSocket pushes to the frontend.

Persistence: SQLAlchemy; PostgreSQL under docker compose,
SQLite file for local dev (DATABASE_URL selects).

Deliberate simplifications (to be replaced later):
- no auth (later: login + RBAC)
"""
import asyncio
import logging
import shutil
import subprocess
import time
import uuid

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import events
from .celery_app import celery
from .db import init_db, session_scope
from .crypto import encrypt_str
from .models import Host, Job, MaintenanceRun, Playbook, Template
from .runner_service import syntax_check_playbook
from .tasks import TASK_KEY, RUNDIR_KEY, _finish_job, run_job_task
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

PING_YML = """\
- name: connectivity check
  hosts: all
  gather_facts: false
  tasks:
    - name: ping via ssh
      ansible.builtin.ping:
"""


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


def _launch_job(snapshot: dict) -> str:
    """Persist a job row and enqueue it. Returns the job id."""
    jid = uuid.uuid4().hex[:8]
    with session_scope() as s:
        s.add(Job(id=jid, host_ids=snapshot["host_ids"], status="running",
                  snapshot=snapshot, events=[], created_at=time.time(),
                  finished_at=None))
    events.ws_queues[jid] = []
    result = run_job_task.delay(jid)
    r = events.get_redis()
    if r is not None:
        try:
            r.set(TASK_KEY.format(jid=jid), result.id, ex=86400)
        except Exception:
            pass
    return jid


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
                       check_mode=t.check_mode, created_at=time.time()))
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
                        "check_mode": t.check_mode, "created_at": t.created_at})
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
            t = s.get(Template, j.template_id)
            if not t:
                raise HTTPException(404, "no such template")
            pb = s.get(Playbook, t.playbook_id)
            if not pb:
                raise HTTPException(400, "template references a deleted playbook")
            snapshot = {
                "kind": "template",
                "template_id": t.id, "template_name": t.name,
                "playbook_id": pb.id, "playbook_name": pb.name,
                "playbook_content": pb.content,  # frozen at launch time
                "host_ids": list(t.host_ids or []),
                "extra_vars": {**(t.extra_vars or {}), **(j.extra_vars or {})},
                "check_mode": j.check_mode if j.check_mode is not None else t.check_mode,
            }
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
    return {"job_id": _launch_job(snapshot)}


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
    return {"job_id": _launch_job(snapshot)}


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
