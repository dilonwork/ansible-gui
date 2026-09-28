"""Ansible GUI backend (skeleton).

Flow: browser -> REST (create hosts / playbooks / templates / jobs) ->
background thread runs ansible-runner -> event_handler emits live ->
WebSocket pushes to the frontend.

Deliberate simplifications (to be replaced later):
- hosts / playbooks / templates / jobs live in memory dicts (later: PostgreSQL)
- background execution uses threading (later: Celery + Redis)
- no auth (later: login + RBAC)
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

from .runner_service import run_playbook, syntax_check_playbook


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="Ansible GUI (skeleton)", lifespan=lifespan)

hosts: dict[str, dict] = {}
playbooks: dict[str, dict] = {}
templates: dict[str, dict] = {}
jobs: dict[str, dict] = {}
ws_queues: dict[str, list[asyncio.Queue]] = {}  # job_id -> [queues]
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
    job = jobs.get(job_id)
    if not job:
        return
    job["events"].append(event)
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
    jobs[job_id]["status"] = "successful" if ok else "failed"
    jobs[job_id]["finished_at"] = time.time()


def _job_summary(v: dict) -> dict:
    s = v["snapshot"]
    return {"id": v["id"], "kind": s["kind"],
            "template_name": s.get("template_name"),
            "playbook_name": s["playbook_name"],
            "check_mode": s["check_mode"],
            "host_ids": v["host_ids"], "status": v["status"],
            "created_at": v["created_at"]}


# ---------- hosts ----------
@app.post("/api/hosts")
def add_host(h: HostIn):
    try:
        host_key = keyscan(h.address, h.port)
    except Exception as e:
        raise HTTPException(400, f"host probe failed (keyscan): {e}")
    hid = uuid.uuid4().hex[:8]
    hosts[hid] = {"id": hid, "name": h.name, "address": h.address,
                  "port": h.port, "username": h.username,
                  "private_key": h.private_key, "host_key": host_key,
                  "added_at": time.time()}
    return {"id": hid, "name": h.name, "address": h.address}


@app.get("/api/hosts")
def list_hosts():
    return [{"id": v["id"], "name": v["name"], "address": v["address"],
             "port": v["port"], "username": v["username"]}
            for v in hosts.values()]


@app.delete("/api/hosts/{hid}")
def del_host(hid: str):
    hosts.pop(hid, None)
    return {"ok": True}


# ---------- playbooks ----------
@app.post("/api/playbooks")
def add_playbook(p: PlaybookIn):
    if not p.content.strip():
        raise HTTPException(400, "playbook content is empty")
    pid = uuid.uuid4().hex[:8]
    playbooks[pid] = {"id": pid, "name": p.name, "content": p.content,
                      "created_at": time.time()}
    return {"id": pid}


@app.get("/api/playbooks")
def list_playbooks():
    return [{"id": v["id"], "name": v["name"], "created_at": v["created_at"]}
            for v in playbooks.values()]


@app.get("/api/playbooks/{pid}")
def get_playbook(pid: str):
    pb = playbooks.get(pid)
    if not pb:
        raise HTTPException(404, "no such playbook")
    return pb


@app.delete("/api/playbooks/{pid}")
def del_playbook(pid: str):
    used_by = [t["name"] for t in templates.values() if t["playbook_id"] == pid]
    if used_by:
        raise HTTPException(400, f"playbook is used by templates: {used_by}")
    playbooks.pop(pid, None)
    return {"ok": True}


@app.post("/api/playbooks/{pid}/syntax-check")
def playbook_syntax_check(pid: str):
    pb = playbooks.get(pid)
    if not pb:
        raise HTTPException(404, "no such playbook")
    ok, output = syntax_check_playbook(pb["content"])
    return {"ok": ok, "output": output}


# ---------- job templates ----------
@app.post("/api/templates")
def add_template(t: TemplateIn):
    if t.playbook_id not in playbooks:
        raise HTTPException(400, "unknown playbook_id")
    missing = [i for i in t.host_ids if i not in hosts]
    if missing:
        raise HTTPException(400, f"unknown hosts: {missing}")
    tid = uuid.uuid4().hex[:8]
    templates[tid] = {"id": tid, "name": t.name, "playbook_id": t.playbook_id,
                      "host_ids": list(t.host_ids), "extra_vars": dict(t.extra_vars),
                      "check_mode": t.check_mode, "created_at": time.time()}
    return {"id": tid}


@app.get("/api/templates")
def list_templates():
    out = []
    for v in templates.values():
        pb = playbooks.get(v["playbook_id"])
        out.append({**v, "playbook_name": pb["name"] if pb else "(deleted)"})
    return out


@app.delete("/api/templates/{tid}")
def del_template(tid: str):
    templates.pop(tid, None)
    return {"ok": True}


# ---------- jobs ----------
@app.post("/api/jobs")
def create_job(j: JobIn):
    if bool(j.template_id) == bool(j.host_ids):
        raise HTTPException(400, "provide either template_id or host_ids")

    if j.template_id:
        t = templates.get(j.template_id)
        if not t:
            raise HTTPException(404, "no such template")
        pb = playbooks.get(t["playbook_id"])
        if not pb:
            raise HTTPException(400, "template references a deleted playbook")
        snapshot = {
            "kind": "template",
            "template_id": t["id"], "template_name": t["name"],
            "playbook_id": pb["id"], "playbook_name": pb["name"],
            "playbook_content": pb["content"],  # frozen at launch time
            "host_ids": list(t["host_ids"]),
            "extra_vars": {**t["extra_vars"], **(j.extra_vars or {})},
            "check_mode": j.check_mode if j.check_mode is not None else t["check_mode"],
        }
    else:
        missing = [i for i in j.host_ids if i not in hosts]
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

    jid = uuid.uuid4().hex[:8]
    jobs[jid] = {"id": jid, "host_ids": snapshot["host_ids"], "status": "running",
                 "events": [], "snapshot": snapshot,
                 "created_at": time.time(), "finished_at": None}
    ws_queues[jid] = []
    t = threading.Thread(target=_run_job_thread,
                         args=(jid, [hosts[i] for i in snapshot["host_ids"]], snapshot),
                         daemon=True)
    t.start()
    return {"job_id": jid}


@app.get("/api/jobs")
def list_jobs():
    return [_job_summary(v) for v in jobs.values()]


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "no such job")
    return job


# ---------- websocket: live event stream ----------
@app.websocket("/ws/jobs/{jid}")
async def job_stream(ws: WebSocket, jid: str):
    job = jobs.get(jid)
    if not job:
        await ws.close(code=4404)
        return
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    ws_queues[jid].append(q)
    try:
        # replay history first so late joiners miss nothing
        for e in job["events"]:
            await ws.send_json(e)
        if job["status"] != "running":
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
