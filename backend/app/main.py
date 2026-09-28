"""Ansible GUI backend (skeleton).

Flow: browser -> REST (hosts / playbooks / templates / jobs) ->
background thread runs ansible-runner -> event_handler emits live ->
WebSocket pushes to the frontend.

Persistence: SQLAlchemy; PostgreSQL under docker compose,
SQLite file for local dev (DATABASE_URL selects).

Deliberate simplifications (to be replaced later):
- background execution uses threading (later: Celery + Redis)
- no auth (later: login + RBAC)
- private keys stored in cleartext (later: Vault-encrypted, M7)
"""
import asyncio
import subprocess
import threading
import time
import uuid

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import init_db, session_scope
from .crypto import decrypt_str, encrypt_str
from .models import Host, Job, Playbook, Template
from .runner_service import run_playbook, syntax_check_playbook


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    init_db()
    loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="Ansible GUI (skeleton)", lifespan=lifespan)

ws_queues: dict[str, list[asyncio.Queue]] = {}  # job_id -> [queues] (ephemeral)
loop: asyncio.AbstractEventLoop | None = None

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


def emit(job_id: str, event: dict):
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job:
            job.events = (job.events or []) + [event]
    if loop is not None:
        for q in ws_queues.get(job_id, []):
            loop.call_soon_threadsafe(q.put_nowait, event)


def _run_job_thread(job_id: str, job_hosts: list[dict], snapshot: dict):
    ok = run_playbook(
        job_id, job_hosts,
        snapshot["playbook_content"],
        snapshot["extra_vars"],
        snapshot["check_mode"],
        lambda e: emit(job_id, e),
    )
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job:
            job.status = "successful" if ok else "failed"
            job.finished_at = time.time()


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
        host_rows = s.query(Host).filter(Host.id.in_(snapshot["host_ids"])).all()
        job_hosts = [{"name": h.name, "address": h.address, "port": h.port,
                      "username": h.username,
                      "private_key": decrypt_str(h.private_key),
                      "host_key": h.host_key} for h in host_rows]
        jid = uuid.uuid4().hex[:8]
        s.add(Job(id=jid, host_ids=snapshot["host_ids"], status="running",
                  snapshot=snapshot, events=[], created_at=time.time(),
                  finished_at=None))

    ws_queues[jid] = []
    t = threading.Thread(target=_run_job_thread,
                         args=(jid, job_hosts, snapshot),
                         daemon=True)
    t.start()
    return {"job_id": jid}


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


# ---------- websocket: live event stream ----------
@app.websocket("/ws/jobs/{jid}")
async def job_stream(ws: WebSocket, jid: str):
    with session_scope() as s:
        job = s.get(Job, jid)
        if not job:
            await ws.close(code=4404)
            return
        history = list(job.events or [])
        running = job.status == "running"
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    ws_queues[jid].append(q)
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
            if e.get("type") == "job_finished":
                await asyncio.sleep(0.2)
                break
    except WebSocketDisconnect:
        pass
    finally:
        if q in ws_queues.get(jid, []):
            ws_queues[jid].remove(q)
        await ws.close()


# ---------- demo GUI ----------
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def index():
    return FileResponse("app/static/index.html")
