"""Job launching: build a frozen snapshot from a template and enqueue it.

Used by the REST API (main.py) and by the schedule tick task (tasks.py).
Kept in its own module so tasks.py and main.py can both use it without a
circular import.
"""
import time
import uuid

from . import events
from .db import session_scope
from .models import Job, Playbook, Template
from .tasks import TASK_KEY, run_job_task

PING_YML = """\
- name: ping
  hosts: all
  gather_facts: false
  tasks:
    - name: ping
      ansible.builtin.ping:
"""


def template_snapshot(template_id: str, extra_vars: dict | None = None,
                      check_mode: bool | None = None) -> dict:
    """Frozen snapshot for one template launch. Raises ValueError if broken."""
    with session_scope() as s:
        t = s.get(Template, template_id)
        if not t:
            raise ValueError("no such template")
        pb = s.get(Playbook, t.playbook_id)
        if not pb:
            raise ValueError("template references a deleted playbook")
        return {
            "kind": "template",
            "template_id": t.id, "template_name": t.name,
            "playbook_id": pb.id, "playbook_name": pb.name,
            "playbook_content": pb.content,  # frozen at launch time
            "host_ids": list(t.host_ids or []),
            "extra_vars": {**(t.extra_vars or {}), **(extra_vars or {})},
            "check_mode": check_mode if check_mode is not None else t.check_mode,
        }


def launch_job(snapshot: dict, schedule_id: str | None = None) -> str:
    """Persist a job row and enqueue it. Returns the job id."""
    jid = uuid.uuid4().hex[:8]
    with session_scope() as s:
        s.add(Job(id=jid, host_ids=snapshot["host_ids"], status="running",
                  snapshot=snapshot, events=[], schedule_id=schedule_id,
                  created_at=time.time(), finished_at=None))
    events.ws_queues[jid] = []
    result = run_job_task.delay(jid)
    r = events.get_redis()
    if r is not None:
        try:
            r.set(TASK_KEY.format(jid=jid), result.id, ex=86400)
        except Exception:
            pass
    return jid
