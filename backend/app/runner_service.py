"""ansible 執行層：把一次 ping 任務跑完，事件即時丟給 emit callback。

設計要點（對應可行性驗證的發現）：
- 每次執行用獨立的 private_data_dir，多任務不互相干擾
- SSH 私鑰寫成 600 權限暫存檔，跑完即刪，不經 DB
- host key 驗證走 StrictHostKeyChecking=yes + 自建 known_hosts（主機新增時 keyscan 取得）
"""
import ansible_runner
import os
import shutil
import stat
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
    """把 ansible-runner event 轉成前端好 render 的小物件；不關心的回 None。"""
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


def run_ping_job(job_id: str, hosts: list[dict], emit) -> bool:
    """在背景執行緒跑。emit(event_dict) 會被即時呼叫。回傳是否成功。"""
    run_dir = tempfile.mkdtemp(prefix=f"ansible-gui-{job_id}-")
    try:
        key_path = os.path.join(run_dir, "ssh_key")
        known_hosts_path = os.path.join(run_dir, "known_hosts")
        _write_key_file(key_path, hosts[0]["private_key"])  # 同一批任務共用同一把 key（skeleton 假設）
        with open(known_hosts_path, "w") as f:
            for h in hosts:
                f.write(h["host_key"].strip() + "\n")

        inv_dir = os.path.join(run_dir, "inventory")
        os.makedirs(inv_dir, exist_ok=True)
        with open(os.path.join(inv_dir, "hosts"), "w") as f:
            f.write(_build_inventory(hosts, key_path, known_hosts_path))

        def handler(event: dict):
            simple = _simplify(event)
            if simple:
                simple["ts"] = round(time.time(), 2)
                emit(simple)

        r = ansible_runner.run(
            private_data_dir=run_dir,
            playbook="ping.yml",
            project_dir=os.path.join(os.path.dirname(__file__), "playbooks"),
            inventory=os.path.join(inv_dir, "hosts"),
            event_handler=handler,
            quiet=True,
        )
        return r.status == "successful" and r.rc == 0
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)  # key 與已知主機檔一起清掉
