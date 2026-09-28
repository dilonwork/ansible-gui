"""Ansible execution layer: runs one playbook job, streaming simplified events to an emit callback.

Design notes (from the feasibility spike):
- Each run gets its own private_data_dir so concurrent jobs don't interfere.
- The SSH private key is written to a 600 temp file and deleted after the run; never persisted.
- Host key verification uses StrictHostKeyChecking=yes with a known_hosts file built
  from the keyscan taken when the host was added.
"""
import ansible_runner
import os
import shutil
import stat
import subprocess
import tempfile
import time


def _write_key_file(path: str, private_key: str) -> None:
    with open(path, "w") as f:
        f.write(private_key.strip() + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600


def _build_inventory(hosts, key_path: str, known_hosts_path: str) -> str:
    lines = ["[targets]"]
    for h in hosts:
        lines.append(
            f"{h['name']} "
            f"ansible_host={h['address']} "
            f"ansible_port={h.get('port', 22)} "
            f"ansible_user={h['username']} "
            f"ansible_ssh_private_key_file={key_path} "
            f"ansible_ssh_common_args='-o UserKnownHostsFile={known_hosts_path} -o StrictHostKeyChecking=yes'"
        )
    return "\n".join(lines) + "\n"


def _simplify(event: dict) -> dict | None:
    """Convert an ansible-runner event into a small frontend-friendly object; None to skip."""
    etype = event.get("event")
    data = event.get("event_data", {}) or {}
    host = data.get("host")
    task = data.get("task")

    if etype == "playbook_on_start":
        return {"type": "job_started"}
    if etype == "playbook_on_task_start":
        return {"type": "task_start", "task": task}
    if etype == "runner_on_ok":
        return {"type": "host_ok", "host": host, "task": task}
    if etype == "runner_on_failed":
        res = data.get("res", {}) or {}
        return {"type": "host_failed", "host": host, "task": task,
                "msg": str(res.get("msg", res))[:500]}
    if etype == "runner_on_unreachable":
        res = data.get("res", {}) or {}
        return {"type": "host_unreachable", "host": host,
                "msg": str(res.get("msg", res))[:500]}
    if etype == "playbook_on_stats":
        return {"type": "job_finished"}
    return None


def run_playbook(job_id: str, hosts: list[dict], playbook_content: str,
                 extra_vars: dict, check_mode: bool, emit) -> bool:
    """Runs in a background thread. emit(event_dict) is called live. Returns success."""
    run_dir = tempfile.mkdtemp(prefix=f"ansible-gui-{job_id}-")
    try:
        key_path = os.path.join(run_dir, "ssh_key")
        known_hosts_path = os.path.join(run_dir, "known_hosts")
        # Skeleton assumption: one key for the whole batch.
        _write_key_file(key_path, hosts[0]["private_key"])
        with open(known_hosts_path, "w") as f:
            for h in hosts:
                f.write(h["host_key"].strip() + "\n")

        inv_dir = os.path.join(run_dir, "inventory")
        os.makedirs(inv_dir, exist_ok=True)
        with open(os.path.join(inv_dir, "hosts"), "w") as f:
            f.write(_build_inventory(hosts, key_path, known_hosts_path))

        project_dir = os.path.join(run_dir, "project")
        os.makedirs(project_dir, exist_ok=True)
        with open(os.path.join(project_dir, "playbook.yml"), "w") as f:
            f.write(playbook_content)

        def handler(event: dict):
            simple = _simplify(event)
            if simple:
                simple["ts"] = round(time.time(), 2)
                emit(simple)

        r = ansible_runner.run(
            private_data_dir=run_dir,
            playbook="playbook.yml",
            project_dir=project_dir,
            inventory=os.path.join(inv_dir, "hosts"),
            extravars=extra_vars or {},
            cmdline="--check" if check_mode else None,
            event_handler=handler,
            quiet=True,
        )
        return r.status == "successful" and r.rc == 0
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)  # key, known_hosts and playbook go away together


def syntax_check_playbook(playbook_content: str) -> tuple[bool, str]:
    """Run `ansible-playbook --syntax-check` on the given YAML. Returns (ok, output)."""
    run_dir = tempfile.mkdtemp(prefix="ansible-gui-syntax-")
    try:
        pb_path = os.path.join(run_dir, "playbook.yml")
        with open(pb_path, "w") as f:
            f.write(playbook_content)
        p = subprocess.run(
            ["ansible-playbook", "--syntax-check", "-i", "localhost,", pb_path],
            capture_output=True, text=True, timeout=60,
        )
        output = (p.stdout + p.stderr).strip()[-2000:]
        return p.returncode == 0, output
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
