# M4 — Kubernetes Cluster Management

> Goal: read-only observability across clusters + controlled node operations (cordon/drain/uncordon); workloads are look-but-don't-touch — changing machines is Ansible's job.

## 4.1 Cluster onboarding

**Description**: hand the system a kubeconfig; every K8s view afterwards comes from here.

**Scenario**: Dylan pastes his homelab k3s kubeconfig; after the connectivity check passes, the cluster appears in the top-bar switcher and nodes start syncing into inventory (M1.4).

**Details**
- Import via pasted YAML / file upload; after parsing show cluster name, server address, certificate expiry pre-check
- Connectivity check: actually hit the API once (list nodes); failures are categorized (certificate expired / network unreachable / insufficient RBAC)
- kubeconfig stored as an M7 credential (encrypted at rest, never returned in plaintext by the UI); multiple clusters coexist, each with its own credential
- Expiring certificates (within 30 days) trigger reminders (via M8)

**Acceptance criteria**: pasting a kubeconfig with an expired certificate yields an explicit "certificate expired" message, not a generic connection error; deleting a cluster also cleans up the inventory marks its sync created.

## 4.2 Cluster overview

**Description**: one screen answering "is this cluster healthy right now".

**Details**
- Card fields: K8s version, nodes Ready x/y, abnormal Pod count (CrashLoopBackOff, Pending, ImagePullBackOff counted separately), API latency
- Abnormal Pods grouped by namespace, top 5; clicking jumps to the 4.5 workload view (before P1, show a kubectl command hint instead)
- 30-second refresh; P0 uses polling, P1 switches to watch + WebSocket push
- Multiple clusters can be compared side by side (the dual cards in the dashboard mockup are this spec)

**Acceptance criteria**: manually cordon a node — the overview Ready count updates correctly within 30 seconds; when the API is unreachable the card shows "data stale" instead of silently showing old numbers.

## 4.3 Node list

**Description**: the battle map for worker nodes; the dashboard's first-screen core table is the slim version of this spec.

**Scenario**: before an upgrade, Dylan filters "workers below v1.31.2" and bulk-adds them to the maintenance queue (M5.6).

**Details**
- Columns: hostname, role (control-plane/worker, from labels), K8s version, kubelet version, containerd/CRI version, Ready status, CPU/memory usage, OS image, uptime
- Filters: role, status (Ready/NotReady/drifted), version, group; keyword search on hostname
- Click a hostname → node detail drawer: full labels/taints, conditions timeline, allocated resources (allocatable vs requests), recent Events, quick SSH connection entry
- Version drift badge: amber when the actual version is below the group expected version (M5.4); that row's action button becomes "Upgrade"

**Acceptance criteria**: 14-node list loads in < 2 seconds; filters can be saved as named views (e.g. "workers pending upgrade").

## 4.4 cordon / drain / uncordon

**Description**: the standard preamble to node maintenance, as a controlled operation rather than bare kubectl.

**Scenario**: rke2-worker-04 needs a disk swap; Dylan clicks "drain" on the node list; the system first warns that 2 Pods are protected by PDB and would block, then executes after confirmation with live eviction progress.

**Details**
- Pre-drain checks displayed up front: Pod count to be evicted, whether any PDB would block (listing the blocking PDBs), a note that DaemonSets will be ignored
- Parameters: `--ignore-daemonsets` and `--delete-emptydir-data` on by default and clearly labeled; grace period adjustable; timeout default 300 seconds
- High-risk operations go through approval (M3.6, if the template/operation is flagged); every operation enters the audit log (M6.3)
- Live drain progress (x/y Pods evicted); can be canceled when stuck; one-click uncordon restores scheduling

**Acceptance criteria**: draining a test node protected by PDB warns correctly in pre-check and respects the PDB during execution (no force-kill); the whole operation is auditable as "who, when, against which machine".

## 4.5 Read-only workload view (P1)

**Description**: quickly locate "which workload is misbehaving" when things break — but no editing (edits go through GitOps/CI; that's another tool's job).

**Details**
- Lists: Deployment / StatefulSet / DaemonSet / Pod, with status, ready replicas x/y, restart counts (restart counts highlighted and sortable, handy for catching CrashLoops)
- Click a Pod for its last 200 log lines, switchable across containers; click a Deployment for its events
- Namespace filter + keyword search; kube-system hidden by default (expandable) to cut noise

**Acceptance criteria**: a CrashLoopBackOff Pod's logs are reachable within 3 clicks.

## 4.6 Node event timeline (P2)

**Description**: aggregate scattered Events per node, for answering "what happened on this machine yesterday".

**Details**
- Per-node aggregation of the last 7 days of Events: evictions, OOMKilled, disk pressure, kubelet restarts, etc., colored by severity
- Cross-referenced with M6 job history: jobs that ran on the node in the same window are auto-annotated on the timeline ("OS patch ran 10 minutes before this NotReady")

---

**Non-goals of this module**: workload create/update/delete, Helm management, GitOps, cluster creation (kubeadm/k3s install wizards).
