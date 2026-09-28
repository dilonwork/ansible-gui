# M9 — Dashboard

> Goal: the first glance answers three things — is anything broken, what's next, what's happening right now. Layout is in `doc/dashboard-design.md`; this doc only specifies each number (definition, source, refresh rate).

## 9.1 Overview dashboard

**Description**: the system home page; every KPI has an explicit definition so nobody asks "how is this number even computed".

**Scenario**: Dylan opens it every morning, scans the KPI row: are nodes pending upgrade at 0, how did last night's scheduled success rate look — then decides whether today needs action.

**Details** (KPI definitions and sources)
- Managed hosts: online x/y — source: M1.3 reachability probing; 5-minute refresh (matches probe cadence)
- K8s clusters: n clusters, m nodes total — source: M4.1/M4.2; 30-second refresh
- Nodes pending upgrade: deduplicated node count from version drift (M5.4) + pending OS patches (M1.6); recomputed on daily comparison, "check now" refreshes manually
- Running jobs: count of jobs with running status; click through to the live log (M6.1); real-time over WebSocket
- 7-day job success rate: successful jobs / total jobs (dry runs excluded); recomputed hourly
- Blocks: cluster health card (M4.2), worker node table slim version (M4.3, first 5 rows + anomalies pinned top), running-job live log (M6.1, last 6 lines), ops todo (M5.4 + M1.6 + next schedule), quick actions (add host / create job / node maintenance / add schedule)
- Every todo is an action entry: clicking "3 nodes with version drift" jumps to the maintenance page with the 3 nodes pre-selected (fewer steps by design)

**Acceptance criteria**: every KPI has a ⓘ; hover shows the definition and update time; every number is clickable into its detail (no dead numbers).

## 9.2 Trend charts (P1)

**Description**: extend "now" into "how have things been lately" — spot degradation trends.

**Details**
- Job success-rate curve (daily granularity, last 30 days), run-count bars, average-duration line; the three charts share a linked time axis
- Range switch: 7 / 30 / 90 days; filterable by template (trends for one template only)
- Anomaly markers: dates with sharp success-rate drops are auto-marked; hover shows links to that day's failed jobs

**Acceptance criteria**: switching ranges re-renders in < 1 second; markers link correctly to that day's failed jobs.

## 9.3 Capacity trends (P2)

**Description**: answers "how much longer can these nodes hold" — the basis for scaling decisions.

**Details**
- Metrics: per-node CPU/memory/disk usage history lines; cluster-level aggregation + per-node drill-down
- Sources: metrics-server (lightweight default) or Prometheus (for those who already have it); 90 days retained, downsampled to daily
- Threshold lines: nodes with memory > 85% for 7 straight days auto-list in a "consider scale-out/migration" list (suggest only, never auto-act)

**Acceptance criteria**: a test cluster with metrics-server shows a continuous curve after 24 hours; Prometheus mode and metrics-server mode agree (±5%).

---

**Non-goals of this module**: custom drag-and-drop dashboard builder, multi-tenant personalized home pages, TV wall mode.
