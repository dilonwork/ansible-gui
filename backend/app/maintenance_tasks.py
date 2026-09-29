"""M5 orchestrator: serial=1 rolling node maintenance.

A maintenance run walks its nodes strictly one at a time. Each step runs as
one ansible job (via runner_service.run_playbook), so every step gets its own
event log. State (per-node step states, preflight results) is persisted after
every step, so a dead worker redelivers the task (acks_late) and it continues
where it left off; completed nodes are never re-run.

Pause protocol: the run pauses on the first node failure and waits for an
operator decision (retry-node / skip-node / abort / pause / resume). Each
decision API just updates state and re-enqueues this task.
"""
import logging
import os
import shutil
import stat
import tempfile
import time

from . import events
from .celery_app import celery
from .crypto import decrypt_str, encrypt_str
from .db import session_scope
from .models import Host, MaintenanceRun
from .runner_service import run_playbook
from .workflows import (NODE_STEP_ORDER, STEPS, VERIFY_K8S_YML, WORKFLOWS,
                        node_steps_for, step_playbook)

log = logging.getLogger("drydock.maintenance")

TASK_KEY = "drydock:maint-task:{rid}"
RUNDIR_KEY = "drydock:maint-rundir:{rid}"

FINAL = ("completed", "failed", "aborted")
RUNNABLE = ("running",)


def _now() -> float:
    return round(time.time(), 2)


def _transition(run_id: str, from_statuses: tuple, to_status: str) -> bool:
    """Atomic status transition. Returns True if applied."""
    with session_scope() as s:
        n = s.query(MaintenanceRun).filter(
            MaintenanceRun.id == run_id,
            MaintenanceRun.status.in_(from_statuses),
        ).update(
            {"status": to_status,
             "finished_at": time.time() if to_status in FINAL else None},
            synchronize_session=False)
        return n > 0


def _get_run(run_id: str):
    with session_scope() as s:
        run = s.get(MaintenanceRun, run_id)
        if not run:
            return None
        # detach needed fields
        return {
            "id": run.id, "name": run.name, "workflow": run.workflow,
            "node_ids": list(run.node_ids or []),
            "status": run.status,
            "steps": [dict(n) for n in (run.steps or [])],
            "preflight": list(run.preflight or []),
            "snapshot": dict(run.snapshot or {}),
        }


def _save_steps(run_id: str, steps: list) -> None:
    with session_scope() as s:
        run = s.get(MaintenanceRun, run_id)
        if run:
            run.steps = steps


def _save_preflight(run_id: str, preflight: list) -> None:
    with session_scope() as s:
        run = s.get(MaintenanceRun, run_id)
        if run:
            run.preflight = preflight


def _emit(run_id: str, event: dict) -> None:
    event.setdefault("ts", _now())
    events.emit_run_event(run_id, event)


def _write_kubeconfig(run_id: str, kubeconfig_cipher: str | None) -> str | None:
    """Write the decrypted kubeconfig to a 600 temp file; record dir in Redis."""
    if not kubeconfig_cipher:
        return None
    run_dir = tempfile.mkdtemp(prefix=f"drydock-maint-{run_id}-")
    path = os.path.join(run_dir, "kubeconfig")
    with open(path, "w") as f:
        f.write(decrypt_str(kubeconfig_cipher).strip() + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    r = events.get_redis()
    if r is not None:
        try:
            r.set(RUNDIR_KEY.format(rid=run_id), run_dir, ex=86400)
        except Exception:
            pass
    return path


def _cleanup_rundir(run_id: str) -> None:
    r = events.get_redis()
    rundir = None
    if r is not None:
        try:
            raw = r.get(RUNDIR_KEY.format(rid=run_id))
            rundir = raw.decode() if isinstance(raw, bytes) else raw
            r.delete(RUNDIR_KEY.format(rid=run_id), TASK_KEY.format(rid=run_id))
        except Exception:
            pass
    if rundir:
        shutil.rmtree(rundir, ignore_errors=True)


def _load_hosts(node_ids: list[str]) -> list[dict]:
    with session_scope() as s:
        rows = s.query(Host).filter(Host.id.in_(node_ids)).all()
        by_id = {h.id: h for h in rows}
    hosts = []
    for hid in node_ids:
        h = by_id.get(hid)
        if h:
            hosts.append({"id": h.id, "name": h.name, "address": h.address,
                          "port": h.port, "username": h.username,
                          "private_key": decrypt_str(h.private_key),
                          "host_key": h.host_key})
    return hosts


def _derive_preflight(events_list: list[dict]) -> list[dict]:
    """Map ansible events of the preflight playbooks to check results."""
    order: list[str] = []
    by_task: dict[str, dict] = {}
    for e in events_list:
        t = e.get("task")
        et = e.get("type")
        if et == "task_start" and t and t not in by_task:
            order.append(t)
            by_task[t] = {"name": t, "status": "passed", "detail": ""}
        elif t in by_task:
            if et in ("host_failed", "host_unreachable"):
                by_task[t]["status"] = "failed"
                by_task[t]["detail"] = (e.get("msg") or "")[:300]
            elif et == "check_warning" and by_task[t]["status"] == "passed":
                by_task[t]["status"] = "warning"
                by_task[t]["detail"] = (e.get("msg") or "")[:300]
    return [by_task[t] for t in order]


def _run_ansible(run_id: str, step_label: str, hosts: list[dict],
                 playbook: str, extra_vars: dict, env: dict | None,
                 node_name: str | None = None) -> tuple[bool, list[dict]]:
    """Run one step; return (ok, collected events)."""
    collected: list[dict] = []

    def emit(e: dict):
        e = dict(e)
        if node_name:
            e["node"] = node_name
        e["mstep"] = step_label
        collected.append(e)
        _emit(run_id, e)

    ok = run_playbook(f"{run_id}-{step_label}", hosts, playbook,
                      extra_vars, False, emit, env=env)
    return ok, collected


def _run_preflight(run_id: str, run: dict, hosts: list[dict],
                   kubeconfig_path: str | None) -> bool:
    snap = run["snapshot"]
    overrides = snap.get("playbook_overrides") or {}

    _emit(run_id, {"type": "maint_preflight_start"})
    ok, collected = _run_ansible(
        run_id, "preflight", hosts,
        overrides.get("preflight", STEPS["preflight"]["playbook"]),
        {}, None)
    checks = _derive_preflight(collected)

    if kubeconfig_path and ok:
        ok2, collected2 = _run_ansible(
            run_id, "preflight_k8s", [],
            overrides.get("preflight_k8s", STEPS["preflight_k8s"]["playbook"]),
            {}, {"KUBECONFIG": kubeconfig_path})
        checks += _derive_preflight(collected2)
        ok = ok and ok2
    elif not kubeconfig_path:
        checks.append({"name": "kubernetes checks", "status": "warning",
                       "detail": "skipped: no kubeconfig attached"})

    _save_preflight(run_id, checks)
    if not ok or any(c["status"] == "failed" for c in checks):
        failed = [c["name"] for c in checks if c["status"] == "failed"]
        _emit(run_id, {"type": "maint_preflight_failed", "failed": failed})
        _transition(run_id, RUNNABLE, "failed")
        return False
    _emit(run_id, {"type": "maint_preflight_done"})
    return True


def _init_steps(run: dict, has_k8s: bool) -> list[dict]:
    step_names = node_steps_for(run["workflow"], has_k8s)
    k8s_names = (run["snapshot"].get("params") or {}).get("k8s_names", {})
    hosts = {h["id"]: h for h in _load_hosts(run["node_ids"])}
    steps = []
    for hid in run["node_ids"]:
        h = hosts.get(hid, {})
        steps.append({
            "node_id": hid,
            "node_name": h.get("name", hid),
            "k8s_name": k8s_names.get(hid, h.get("name", hid)),
            "state": "pending",
            "attempts": 0,
            "step_states": {s: "pending" for s in step_names},
            "failed_step": None,
        })
    return steps


def _run_node(run_id: str, run: dict, node: dict, hosts_by_id: dict,
              kubeconfig_path: str | None, has_k8s: bool) -> bool:
    """Run all steps for one node. Returns False if the run must pause."""
    snap = run["snapshot"]
    params = snap.get("params") or {}
    overrides = snap.get("playbook_overrides") or {}
    workflow = run["workflow"]
    step_names = node_steps_for(workflow, has_k8s)
    host = hosts_by_id[node["node_id"]]

    node["state"] = "running"
    node["attempts"] += 1
    _save_steps(run_id, run["steps"])
    _emit(run_id, {"type": "maint_node_start", "node": node["node_name"]})

    for step in step_names:
        # operator asked to pause?
        if _get_run(run_id)["status"] != "running":
            node["state"] = "pending"
            _save_steps(run_id, run["steps"])
            _transition(run_id, ("pausing",), "paused")
            _emit(run_id, {"type": "maint_paused", "node": node["node_name"]})
            return False

        # verify runs ssh checks on the node plus k8s Ready when applicable
        sub_runs = [(step, overrides.get(step, step_playbook(workflow, step)),
                     STEPS[step]["localhost"])]
        if step == "verify" and workflow == "os-patch" and has_k8s:
            sub_runs.append(("verify_k8s",
                             overrides.get("verify_k8s", VERIFY_K8S_YML),
                             True))

        node["step_states"][step] = "running"
        _save_steps(run_id, run["steps"])
        _emit(run_id, {"type": "maint_step_start", "node": node["node_name"],
                       "step": step})

        ok = True
        for sub, playbook, on_localhost in sub_runs:
            extra = {"k8s_node": node["k8s_name"],
                     "drain_timeout": params.get("drain_timeout", 300),
                     "verify_timeout": params.get("verify_timeout", 300),
                     "kubelet_version": params.get("kubelet_version", "")}
            env = {"KUBECONFIG": kubeconfig_path} if on_localhost else None
            sub_hosts = [] if on_localhost else [host]
            sub_ok, _ = _run_ansible(run_id, sub, sub_hosts, playbook,
                                    extra, env, node_name=node["node_name"])
            ok = ok and sub_ok

        node["step_states"][step] = "done" if ok else "failed"
        _save_steps(run_id, run["steps"])
        _emit(run_id, {"type": "maint_step_done", "node": node["node_name"],
                       "step": step, "ok": ok})
        if not ok:
            node["state"] = "failed"
            node["failed_step"] = step
            _save_steps(run_id, run["steps"])
            _emit(run_id, {"type": "maint_node_failed",
                           "node": node["node_name"], "step": step})
            _transition(run_id, RUNNABLE, "paused")
            return False

    node["state"] = "done"
    node["failed_step"] = None
    _save_steps(run_id, run["steps"])
    _emit(run_id, {"type": "maint_node_done", "node": node["node_name"]})
    return True


@celery.task(bind=True, name="drydock.run_maintenance", max_retries=0)
def run_maintenance_task(self, run_id: str) -> None:
    run = _get_run(run_id)
    if not run:
        log.warning("maintenance run %s not found", run_id)
        return
    if run["status"] != "running":
        log.info("maintenance run %s is %s, skipping", run_id, run["status"])
        return

    _emit(run_id, {"type": "maint_run_start", "name": run["name"],
                   "workflow": run["workflow"]})
    try:
        with session_scope() as s:
            db_run = s.get(MaintenanceRun, run_id)
            kubeconfig_cipher = db_run.kubeconfig if db_run else None
        kubeconfig_path = _write_kubeconfig(run_id, kubeconfig_cipher)
        has_k8s = kubeconfig_path is not None

        hosts = _load_hosts(run["node_ids"])
        hosts_by_id = {h["id"]: h for h in hosts}
        if len(hosts) != len(run["node_ids"]):
            _emit(run_id, {"type": "maint_error",
                           "msg": "some nodes are missing from inventory"})
            _transition(run_id, RUNNABLE, "failed")
            return

        # (re)initialize step records on first start
        if not run["steps"]:
            run["steps"] = _init_steps(run, has_k8s)
            _save_steps(run_id, run["steps"])

        if not run["preflight"]:
            if not _run_preflight(run_id, run, hosts, kubeconfig_path):
                return

        while True:
            run = _get_run(run_id)
            if run["status"] != "running":
                return
            node = next((n for n in run["steps"]
                         if n["state"] in ("pending", "running")), None)
            if node is None:
                _transition(run_id, RUNNABLE, "completed")
                _emit(run_id, {"type": "maint_run_done"})
                return
            if not _run_node(run_id, run, node, hosts_by_id,
                             kubeconfig_path, has_k8s):
                return  # paused (failure or operator request)
    except Exception as e:
        log.exception("maintenance run %s crashed", run_id)
        _emit(run_id, {"type": "maint_error", "msg": str(e)[:500]})
        _transition(run_id, RUNNABLE + ("pausing", "paused"), "failed")
    finally:
        _cleanup_rundir(run_id)
        r = events.get_redis()
        if r is not None:
            try:
                r.delete(TASK_KEY.format(rid=run_id))
            except Exception:
                pass


@celery.task(bind=True, name="drydock.maintenance_abort_cleanup", max_retries=0)
def maintenance_abort_cleanup(self, run_id: str) -> None:
    """Best-effort uncordon of nodes left cordoned by an aborted run."""
    run = _get_run(run_id)
    if not run:
        return
    with session_scope() as s:
        db_run = s.get(MaintenanceRun, run_id)
        kubeconfig_cipher = db_run.kubeconfig if db_run else None
    kubeconfig_path = _write_kubeconfig(run_id, kubeconfig_cipher)
    if not kubeconfig_path:
        return
    try:
        for node in run["steps"]:
            ss = node.get("step_states", {})
            if ss.get("cordon") == "done" and ss.get("uncordon") != "done" \
                    and node.get("state") != "skipped":
                _emit(run_id, {"type": "maint_step_start",
                               "node": node["node_name"], "step": "uncordon"})
                ok, _ = _run_ansible(
                    run_id, "uncordon", [],
                    (run["snapshot"].get("playbook_overrides") or {}).get(
                        "uncordon", STEPS["uncordon"]["playbook"]),
                    {"k8s_node": node["k8s_name"]},
                    {"KUBECONFIG": kubeconfig_path},
                    node_name=node["node_name"])
                node["step_states"]["uncordon"] = "done" if ok else "failed"
                _emit(run_id, {"type": "maint_step_done",
                               "node": node["node_name"],
                               "step": "uncordon", "ok": ok})
        _save_steps(run_id, run["steps"])
    finally:
        _cleanup_rundir(run_id)
