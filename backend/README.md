# Backend skeleton

A minimal runnable backend proving the full chain:
browser → REST → background thread running ansible-runner → SSH → host → WebSocket live events.

## Run locally (dev)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Open http://localhost:8000 — the demo page is replaced by the React frontend in normal use.

## Run with Docker

```bash
docker compose up --build   # from the repo root
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

L1 API tests (no sshd needed, CI-safe). Full test strategy: `doc/testing.md`.
E2E smoke (needs a local sshd): `./scripts/e2e_smoke.sh` from the repo root.

## Database

SQLAlchemy. `DATABASE_URL` selects the backend:

- `postgresql://ansible_gui:ansible_gui@postgres:5432/ansible_gui` (docker compose)
- `sqlite:///./ansible_gui.db` (local dev default when the env var is unset)
- tests point it at a temp SQLite file

Tables are created at startup (`create_all`); migrations come later with the
production hardening step. Event history is stored per job in a JSON column so
late WebSocket joiners get the full replay from the database.

## API

- `POST /api/hosts` {name, address, port, username, private_key} → add host (runs ssh-keyscan probe)
- `GET /api/hosts` → host list (private key never returned)
- `DELETE /api/hosts/{id}`
- `POST /api/playbooks` {name, content} → register a playbook (inline YAML; Git sync comes with M2.1)
- `GET /api/playbooks` → list (without content); `GET /api/playbooks/{id}` → full
- `DELETE /api/playbooks/{id}` → blocked (400) while referenced by a template
- `POST /api/playbooks/{id}/syntax-check` → `{ok, output}` via `ansible-playbook --syntax-check`
- `POST /api/templates` {name, playbook_id, host_ids, extra_vars, check_mode} → job template
- `GET /api/templates` / `DELETE /api/templates/{id}`
- `POST /api/jobs` {template_id} or {host_ids} (+ optional check_mode / extra_vars overrides) → enqueue on Celery; the template's playbook content, vars and host list are frozen into a snapshot
- `GET /api/jobs` → job summaries; `GET /api/jobs/{id}` → status + snapshot + event history
- `POST /api/jobs/{id}/cancel` → revoke the Celery task (terminate), clean up the worker temp dir, mark `cancelled`
- `POST /api/jobs/{id}/retry` → new job with the same frozen snapshot, targeting only failed hosts
- `WS /ws/jobs/{id}` → live event stream (history replayed first for late joiners)

## Node maintenance (M5)

One-click rolling maintenance: `POST /api/maintenance`
{name, workflow: os-patch|kubelet-upgrade, node_ids (in rolling order),
 kubeconfig? (enables cordon/drain/uncordon + K8s preflight), params?, playbook_overrides?}.

- Orchestrator: `drydock.run_maintenance` (app/maintenance_tasks.py) walks nodes
  strictly serial=1; each step (cordon→drain→maintain→verify→uncordon) runs as
  one ansible job. State persists after every step; completed nodes never re-run.
- Preflight (ansible): ssh connectivity, disk space; with kubeconfig: control-plane
  Ready, version skew + single-replica warnings. Hard failure blocks the run.
- On node failure the batch pauses: `POST /api/maintenance/{id}/retry-node`
  (restarts the node from its first step), `/skip-node`, `/abort`
  (revokes + best-effort uncordon of stuck nodes), `/pause`, `/resume`.
- `WS /ws/maintenance/{id}`: live run events (history replayed for late joiners).
- Without kubeconfig, k8s steps are skipped: pure OS-level rolling workflow.

## Execution engine (Celery + Redis)

- `app/celery_app.py`: broker + result backend = Redis (`REDIS_URL`, default `redis://localhost:6379/0`); `task_acks_late` so a dead worker redelivers instead of losing the job
- `app/tasks.py`: `drydock.run_job` loads the snapshot + hosts from the DB, runs ansible-runner, and records the final status with an atomic `WHERE status='running'` transition (a concurrent cancel can't be overwritten)
- `app/events.py`: cross-process event bus — every event is persisted to the job row, pushed to local WS queues, and published to Redis pub/sub; the web process forwards pub/sub messages to live WebSocket clients
- Job statuses: `running → successful | failed | cancelled | interrupted` (`interrupted` = backend died mid-job with no live task; reconciled at startup)
- Cancel: `celery.control.revoke(terminate=True)` + temp-dir cleanup (the 600 SSH key file) + `cancelled` status
- Local dev: `./scripts/start-services.sh` (Redis via docker compose, or native `redis-server`); tests set `CELERY_EAGER=1` and run tasks inline, no Redis needed

## Schedules (cron)

- `POST /api/schedules` {name, template_id, cron, timezone="UTC", enabled}: cron validated + human preview (`POST /api/schedules/preview` → "Every 15 minutes" + next 3 runs)
- A Celery Beat process (`celery -A app.celery_app beat`, 60s tick) fires due schedules via `drydock.tick_schedules`; `docker-compose.yml` includes a `beat` service
- `next_run_at` is persisted: the cadence survives backend restarts
- Missed occurrences while the backend was down are counted (`missed_count`, `recent_missed`) — at most one catch-up run fires per tick, then the cadence resumes
- No overlapping runs: a schedule skips its tick while a previous run is still going (`skipped_overlap`)
- No duplicate enabled schedules for the same template + cron + timezone
- `POST /api/schedules/{id}/run-now` launches immediately without shifting the cadence; disabling clears `next_run_at`; scheduled jobs carry `schedule_id`

## Security design (done)

- SSH private keys are written to a 600 temp file only for the duration of a run, then deleted
- Private keys are encrypted at rest (Fernet) in the database; key from `ENCRYPTION_KEY`
  (dev: auto-generated to `.encryption_key`; production: inject via a secret manager, M7)
- Host key verification: `StrictHostKeyChecking=yes` + known_hosts from the keyscan taken at host-adding time; never blindly trusted

## To be replaced (skeleton simplifications)

- ~~in-memory dicts → PostgreSQL~~ **done**: SQLAlchemy; PostgreSQL in compose, SQLite file for local dev
- ~~threading → Celery + Redis (long jobs, retry, cancel)~~ **done**: Celery worker + Redis broker/pub-sub; cancel/retry/interrupted reconciliation (issue #1)
- no auth → login + RBAC
- ~~private key in DB cleartext → Vault-encrypted at rest (M7)~~ **done (skeleton)**: Fernet-encrypted at rest, key from `ENCRYPTION_KEY`; Vault/KMS integration is the M7 step
