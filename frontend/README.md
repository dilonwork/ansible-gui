# Frontend

React + TypeScript + Vite. Dark theme matching the dashboard mockup in `doc/mockups/`.

## Run locally (dev)

```bash
npm install
npm run dev   # http://localhost:5173; /api and /ws are proxied to the backend on :8000
```

## Build

```bash
npm run build   # tsc + vite build; also runs in CI
```

## Docker

Built into an nginx image (`frontend/Dockerfile`); `/api` and `/ws` are proxied to the `backend` service. Served on port 3000 via docker compose.

## Pages

- `/` Dashboard — KPIs from the real API, recent jobs, worker-node placeholder (M4)
- `/hosts` Hosts — add (with keyscan probe), list, delete
- `/playbooks` Playbooks — register inline YAML, view, syntax check, delete
- `/templates` Job Templates — bind playbook + hosts + extra_vars + check mode
- `/jobs` Jobs — launch from a template (with check-mode override) or ad-hoc ping
- `/jobs/:id` Job detail — frozen snapshot, per-host stats, live WebSocket log
