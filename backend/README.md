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
- `POST /api/jobs` {template_id} or {host_ids} (+ optional check_mode / extra_vars overrides) → launch; the template's playbook content, vars and host list are frozen into a snapshot
- `GET /api/jobs` → job summaries; `GET /api/jobs/{id}` → status + snapshot + event history
- `WS /ws/jobs/{id}` → live event stream (history replayed first for late joiners)

## Security design (done)

- SSH private keys are written to a 600 temp file only for the duration of a run, then deleted
- Private keys are encrypted at rest (Fernet) in the database; key from `ENCRYPTION_KEY`
  (dev: auto-generated to `.encryption_key`; production: inject via a secret manager, M7)
- Host key verification: `StrictHostKeyChecking=yes` + known_hosts from the keyscan taken at host-adding time; never blindly trusted

## To be replaced (skeleton simplifications)

- ~~in-memory dicts → PostgreSQL~~ **done**: SQLAlchemy; PostgreSQL in compose, SQLite file for local dev
- threading → Celery + Redis (long jobs, retry, cancel)
- no auth → login + RBAC
- ~~private key in DB cleartext → Vault-encrypted at rest (M7)~~ **done (skeleton)**: Fernet-encrypted at rest, key from `ENCRYPTION_KEY`; Vault/KMS integration is the M7 step
