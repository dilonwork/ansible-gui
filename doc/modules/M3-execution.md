# M3 — Job Execution Engine

> Goal: bind "playbook + targets + credential + parameters" into a repeatable, schedulable, approvable unit of execution; fully isolated and observable throughout.

## 3.1 Job Template

**Description**: the basic unit of a job — bind everything an execution needs once, re-run with one click after.

**Scenario**: Dylan creates a "weekly OS security update" template: binds `os-security-update.yml` + `patch-group` + SSH credential + forks=5; the weekly schedule references it directly, no re-filling every time.

**Details**
- Fields: name, Project + playbook selection, inventory (multi-select groups + excludable single hosts), credential, forks, verbosity level, timeout
- Parameter form (survey): draft auto-generated from variables parsed out of the playbook (see M2.2); fields can be manually added/removed, marked required, given defaults/option lists; filling the form at run time feeds `extra_vars`
- "Dry run" button: runs once in `--check` dry-run mode first; results are labeled "dry run" and excluded from success-rate stats
- Pre-run snapshot: playbook commit hash, parameter values, inventory host list — all frozen into the job record (traceable)

**Data model notes**: `job_templates(id, name, project_id, playbook_path, inventory_scope JSON, credential_id, forks, survey_schema JSON, require_approval, created_by)`

**Acceptance criteria**: < 15 seconds from pressing "Run" to the first log line; dry-run jobs make zero changes to target machines.

## 3.2 Ad-hoc command

**Description**: one-off commands without building a template; answers "what's the state of these machines right now".

**Scenario**: suspecting kubelet version skew across some workers, run `kubelet --version` against `k3s-workers` and get a per-host summary within 30 seconds.

**Details**
- Pick hosts/groups → pick module: common ones as forms (ping, shell/command, copy, service, package), the rest via raw parameter input
- Results grouped by host: ok / changed / failed lists; failures show a stderr summary
- High-risk modules (shell containing rm, reboot, etc.) pop a double confirmation
- Ad-hoc runs are also written to job history and audit (M6) — no bypassing

**Acceptance criteria**: ping across 14 hosts returns fully and groups correctly within 30 seconds.

## 3.3 Concurrency & batching strategy

**Description**: controls "how many at once, stop after how many failures" — the core switch of operational safety.

**Details**
- `forks` (default 5, adjustable): how many SSH connections to open at once
- `serial`: batch size (number or percentage, e.g. `1`, `25%`); the M5 node maintenance workflow force-overrides it to `1`
- `max_fail_percentage`: abort the whole batch once the failure ratio hits the threshold (default 0, i.e. stop on any failure; can be relaxed)
- The UI phrases it in plain language: "1 host per batch / stop on any failure" — don't just throw Ansible jargon at the user

**Acceptance criteria**: run 3 hosts with serial=1; the log timeline proves sequential execution; when the second host fails, the third is never touched.

## 3.4 Execution Environment (containerized execution isolation)

**Description**: every job runs in a clean container with pinned ansible and collection versions — same behavior wherever it's deployed.

**Scenario**: Dylan runs a playbook in his homelab, then deploys the same EE image on another machine: identical behavior, never a "but the ansible version differs on my box" moment.

**Details**
- System default EE image: ansible-core + kubernetes collection + common collections, pinned versions
- Custom EE: tick collection checkboxes in the UI → definition file auto-generated → image built → pushed to the built-in registry
- Job Templates can pin an EE version; job history records the actual EE digest used
- Per-job container resource limits (CPU/memory) configurable, so big forks can't eat the host

**Technical notes**: the execution layer drives the official `ansible-runner` library inside the container; the event stream is forwarded over WebSocket (see M6.1).

**Acceptance criteria**: run the same template twice with EE v1.2.0 — collection versions identical both times; a failed custom EE build shows a clear build log.

## 3.5 Scheduling (P1)

**Description**: time triggers for routine ops work, e.g. Sunday-midnight OS patches.

**Scenario**: a "weekly OS security update" schedule: runs the template against `patch-group` every Sunday 02:00, pushes a LINE notification with the result.

**Details**
- Cron expression + graphical builder (click minute/hour/weekday/month), timezone configurable (default America/Phoenix)
- Schedules bind Job Templates; each run always uses the "latest" template definition, but the actual parameters of every run are written to history
- Missed runs are not made up: schedules missed during downtime only trigger a notification, never an automatic catch-up run (catching up on ops tasks is risky — deliberate design)
- Schedules can be paused/resumed; next run time always visible

**Acceptance criteria**: set a one-off schedule 2 minutes out — it fires on time; simulate a missed run via service restart — notification only, no catch-up run.

## 3.6 Approval flow (P1)

**Description**: one more pair of human eyes before high-risk jobs run.

**Scenario**: the `kubelet-upgrade` template is marked as requiring approval; Dylan gets a LINE approval request on his phone while out, taps the link to review the parameter summary, approves, and the job starts.

**Details**
- Per-template "requires approval" switch; triggering creates a request ticket (requester, template, parameter summary, target host count, estimated impact)
- Approvers come from the RBAC `approver` role; can approve/reject (rejection requires a reason); requesters can't approve their own tickets
- Approval requests go out via M8 (LINE/Email); tickets expire automatically after a timeout (default 4 hours, configurable)
- Approval records enter the audit chain (M6.3) with approver and timestamp

**Acceptance criteria**: pressing run on an approval-required template leaves the job "pending approval" with zero SSH connections made; after timeout the status becomes "expired".

## 3.7 Workflow (P1)

**Description**: chain multiple Job Templates into a branching workflow; the M5 one-click node maintenance is a built-in workflow.

**Scenario**: an "onboard new node" workflow: node-init → join cluster → verify; each step proceeds only on success, any failure takes the alert branch and sends a notification.

**Details**
- Visual orchestration: nodes = Job Templates, edges = success/failure branches; parallel branches (fork/join) supported
- Each node can override parameters (upstream node outputs as variables, basic string templating)
- Workflow runs get an overview timeline; each sub-job's status independently visible; overall status = the most severe sub-job status
- Built-in workflows: node maintenance (M5), new node onboarding; users can clone and modify

**Acceptance criteria**: in a three-node workflow the middle node fails → the failure branch fires and later success branches never run; the overview page makes it obvious where it's stuck.

---

**Non-goals of this module**: CI/CD pipelines (testing/building/deploying software), generic cross-system workflow engines (like n8n).
