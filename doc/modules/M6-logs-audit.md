# M6 — Live Logs & Audit

> Goal: see what's happening while a job runs; after an incident, trace who touched which machine when — with tamper-evident records.

## 6.1 Live log streaming

**Description**: the source of a job's live feel; ansible's native output is too noisy, so the system translates it into readable form.

**Scenario**: Dylan watches worker-03's OS patch progress on his phone; log lines stream by, and seeing `changed: 14 updated` tells him it's almost done.

**Details**
- WebSocket push; the backend translates `ansible-runner` events into readable lines (`TASK [name]` / `ok` / `changed` / `failed` / `skipping`), with raw output expandable
- Filters: by host (one machine's output only), keyword, failed/changed only
- Auto-scroll toggle; reconnects resume missed lines automatically (by event sequence number)
- Sensitive-word masking: output matching vault variable names or credential patterns is auto-masked (`*` substituted)

**Technical notes**: runner event → backend normalization → Redis pub/sub → WebSocket; concurrent viewers of one job share the same event stream — no duplicate execution.

**Acceptance criteria**: forks=5 across 14 hosts, log latency < 2 seconds; kill -9 the browser tab and reopen — history lines fully restored.

## 6.2 Job history

**Description**: the complete case file of every run; the entry point for debugging and post-mortems.

**Details**
- List columns: job name, type (template/ad-hoc/workflow/maintenance), target scope, status, duration, run by, start time; filters: status, runner, time range
- Detail page: full log (downloadable), per-host results (ok/changed/failed/unreachable groups), execution snapshot (playbook commit hash, parameter values, EE digest, inventory host list)
- "Re-run" button: run again with the identical snapshot; "re-run failed nodes only": auto-targets the failed/unreachable hosts as a new target set
- Retention: 90 days by default (configurable); expired logs are compressed to archive (still downloadable, slightly slower to read), metadata kept forever

**Acceptance criteria**: a three-month-old job still shows its parameter snapshot and commit hash; "re-run failed nodes only" never touches hosts that succeeded.

## 6.3 Hash-chained audit trail (P1)

**Description**: proof that "records haven't been altered" — something to show security/compliance in the future.

**Scenario**: a manager asks "who ran reboot against production workers last week"; Dylan pulls the audit records, the other side verifies the hash chain is intact, confirming no tampering.

**Details**
- Each audit record includes the previous record's hash (SHA-256), forming a chain; a verification API takes a range and reports intact / break position
- Recorded events: login/logout, job runs (parameter summaries, no vault plaintext), approval approve/reject, node operations (cordon/drain/uncordon), credential add/delete/use, group & variable changes, schedule changes
- Audit writes are separated from the business DB (separate table, append-only); not even admins can delete or modify (enforced at the app layer)
- Export: a range exports as signed JSONL

**Acceptance criteria**: hand-edit one audit record in the DB — the verification API correctly points at the break; the exported JSONL is independently verifiable against public docs.

## 6.4 Log search (P2)

**Description**: find clues across jobs — "what did all failed apt upgrades last week look like".

**Details**
- Full-text search over historical logs (keyword + host + time range + job type multi-dimensional filters)
- Saved searches as templates (e.g. "find all dpkg lock errors")
- Hits highlighted, 5 lines of context on each side

---

**Non-goals of this module**: system-level log collection (Loki/ELK style), application log management on hosts, SIEM integration.
