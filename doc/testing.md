# Test strategy

> Principle: a dev task is done only when its tests are green. Tests are the acceptance gate, not an afterthought.

## Test layers

| Layer | Tool | What it covers | When it runs | How |
|---|---|---|---|---|
| L1 API tests | pytest + FastAPI TestClient | REST CRUD, job state machine, WS history replay, private key never leaked, snapshot freeze | on every backend change | `cd backend && python -m pytest tests/ -q` (runs against a temp SQLite DB via `DATABASE_URL`; no PostgreSQL needed) |
| L2 frontend build | tsc + vite build | type errors, build failures | on every frontend change | `cd frontend && npm run build` |
| L3 E2E smoke | `scripts/e2e_smoke.sh` (python3 stdlib only) | real keyscan → add host → real SSH ansible ping → playbook + template + snapshot launch → verify event sequence → cleanup | after touching the SSH/execution chain, local verification | `BACKEND_URL=http://localhost:8000 ./scripts/e2e_smoke.sh` |
| L4 manual acceptance | acceptance checklist (below) | MVP-level scenario: rolling patch across 3 workers, fully GUI-driven | end of each milestone | tick off against `doc/modules/` acceptance criteria |

Notes:

- L1 monkeypatches `keyscan` and `run_playbook`, so it **needs no sshd** and is CI-safe.
- The real SSH path is only covered by L3 (needs sshd on the test box), keeping CI non-flaky.
- L2 is build verification for now; React component tests (Vitest) come later.

## CI (GitHub Actions)

`.github/workflows/ci.yml`: runs on every push / PR

- `backend` job: installs `requirements.txt + requirements-dev.txt`, runs pytest
- `frontend` job: `npm ci && npm run build`

Red CI = not done. Fix CI before stacking new work.

## Test cases vs module acceptance criteria

Source of truth: acceptance criteria in `doc/modules/M*.md`. Covered in the skeleton stage:

| Case | Layer | Status |
|---|---|---|
| add host: keyscan must succeed before persisting | L1 (mocked keyscan), L3 (real keyscan) | ✅ |
| add host: keyscan failure → 400, nothing persisted | L1 | ✅ |
| host list never exposes the private key | L1 | ✅ |
| delete host | L1, L3 (cleanup verified) | ✅ |
| register playbook (inline YAML) | L1 | ✅ |
| syntax check: valid playbook → ok | L1 (real ansible), L3 | ✅ |
| syntax check: broken playbook → error with line number | L1 | ✅ |
| delete playbook blocked while a template uses it | L1 | ✅ |
| create template: unknown playbook/host → 400 | L1 | ✅ |
| ad-hoc ping runs to successful with correct event sequence | L1 (fake runner), L3 (real ansible+SSH) | ✅ |
| launch from template freezes snapshot (later template/playbook edits don't affect the running job) | L1 | ✅ |
| launch overrides: check_mode + extra_vars merge reach the runner | L1 | ✅ |
| check_mode → `--check` is passed to ansible-runner | L1 | ✅ |
| failed runner → job status failed | L1 | ✅ |
| template job end-to-end over real SSH, snapshot verified | L3 | ✅ |
| WS late joiner: history replayed, then eof | L1 | ✅ |
| WS to unknown job is rejected | L1 | ✅ |
| frontend build passes (zero tsc errors) | L2, CI | ✅ |

When a new M module is built, each of its acceptance criteria becomes a row in this table. No row = not done.

## Definition of Done

1. Implemented per the spec in `doc/modules/`
2. Tests written for it; **L1 green** (backend) / **L2 passes** (frontend)
3. Touched the SSH/execution chain → **L3 green**
4. Committed + pushed, CI green
5. The report includes test results (counts), not just "it's done"

## Supervision

- Every dev task follows the DoD above: run tests → green before commit/push → report includes results.
- The case table above is kept current; gaps are called out explicitly, never silently skipped.
- CI guards GitHub; a red CI is fixed before new features.
