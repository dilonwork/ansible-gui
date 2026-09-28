"""Ansible GUI 後端骨架（skeleton）。

鏈路：瀏覽器 → REST 建立主機/任務 → 背景執行緒跑 ansible-runner →
event_handler 即時 emit → WebSocket 推給前端。

刻意簡化（之後要換掉的）：
- 主機與任務放記憶體 dict（之後換 PostgreSQL / SQLite）
- 背景執行用 threading（之後換 Celery + Redis）
- 無登入驗證（之後上 RBAC）
"""
import asyncio
import subprocess
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from contextlib import asynccontextmanager

from .runner_service import run_ping_job


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="Ansible GUI (skeleton)", lifespan=lifespan)

hosts: dict[str, dict] = {}
jobs: dict[str, dict] = {}
ws_queues: dict[str, list[asyncio.Queue]] = {}  # job_id -> [queues]
loop: asyncio.AbstractEventLoop | None = None


# ---------- models ----------
class HostIn(BaseModel):
    name: str
    address: str
    port: int = 22
    username: str = "root"
    private_key: str


class JobIn(BaseModel):
    host_ids: list[str]


# ---------- helpers ----------
def keyscan(address: str, port: int) -> str:
    """SSH 探測：抓 host key，之後執行時做 StrictHostKeyChecking 用。"""
    p = subprocess.run(
        ["ssh-keyscan", "-p", str(port), address],
        capture_output=True, text=True, timeout=20,
    )
    lines = [l for l in p.stdout.splitlines() if l and not l.startswith("#")]
    if not lines:
        raise RuntimeError(f"keyscan 失敗：{p.stderr.strip()[:200]}")
    return "\n".join(lines)


def emit(job_id: str, event: dict):
    job = jobs.get(job_id)
    if not job:
        return
    job["events"].append(event)
    for q in ws_queues.get(job_id, []):
        loop.call_soon_threadsafe(q.put_nowait, event)


def _run_job_thread(job_id: str, job_hosts: list[dict]):
    ok = run_ping_job(job_id, job_hosts, lambda e: emit(job_id, e))
    jobs[job_id]["status"] = "successful" if ok else "failed"
    jobs[job_id]["finished_at"] = time.time()


# ---------- hosts ----------
@app.post("/api/hosts")
def add_host(h: HostIn):
    try:
        host_key = keyscan(h.address, h.port)
    except Exception as e:
        raise HTTPException(400, f"主機探測失敗（keyscan）：{e}")
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


# ---------- jobs ----------
@app.post("/api/jobs")
def create_job(j: JobIn):
    missing = [i for i in j.host_ids if i not in hosts]
    if missing:
        raise HTTPException(400, f"未知主機：{missing}")
    jid = uuid.uuid4().hex[:8]
    jobs[jid] = {"id": jid, "host_ids": j.host_ids, "status": "running",
                 "events": [], "created_at": time.time(), "finished_at": None}
    ws_queues[jid] = []
    t = threading.Thread(target=_run_job_thread,
                         args=(jid, [hosts[i] for i in j.host_ids]),
                         daemon=True)
    t.start()
    return {"job_id": jid}


@app.get("/api/jobs")
def list_jobs():
    return [{"id": v["id"], "host_ids": v["host_ids"], "status": v["status"],
             "created_at": v["created_at"]} for v in jobs.values()]


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "no such job")
    return {**job, "events": job["events"]}


# ---------- websocket：即時事件串流 ----------
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
        # 先補歷史（晚連進來也不漏）
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
