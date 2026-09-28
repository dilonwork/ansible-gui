# M1 — Host / Inventory Management

> Goal: a single source of truth for all managed machines; once K8s nodes are auto-synced in, the operations view and the Ansible execution view share the same inventory.

## 1.1 Host CRUD

**Description**: a host is the smallest unit of management. Each host records connection info and ownership, serving as the execution target of all jobs.

**Scenario**: Dylan adds a worker in his homelab: fills in the IP and SSH info, clicks "Test connection" to confirm ansible ping works, then assigns it to the `k3s-workers` group.

**Details**
- Fields: display name, hostname/IP, SSH port (default 22), SSH user, credential (optional; inherits group/global when blank), group membership (multi-select), tags, notes
- "Test connection" on add/edit: runs a real connectivity test; failures must be categorized (connection timeout / authentication failure / host unreachable) — don't just dump a raw traceback
- Deleting a host requires double confirmation; if the host is a target of a running job, block deletion with an explanation
- Host list supports keyword search and filtering by group/status

**Data model notes**: `hosts(id, name, address, port, ssh_user, credential_id, group_ids[], vars JSON, tags[], reachable, last_seen)`

**Acceptance criteria**: reachability status shows within 10 seconds of adding a host; each of the three connection failure types has a clear Chinese error message.

## 1.2 Group management

**Description**: groups are the unit of batch operations, support nesting, and variables follow Ansible precedence semantics.

**Scenario**: three layers `all → k8s-cluster → k3s-workers`; the expected K8s version is declared in the `k8s-cluster` group variables, inherited by all workers, with per-host exceptions overriding at the host level.

**Details**
- Nested groups (parent/children); variable resolution order: all → parent groups → child groups → host
- The host page offers an "effective vars preview" that flattens the inheritance chain for easy debugging
- Groups created by K8s sync are marked "auto-synced" and read-only locked (see 1.4) so manual edits can't be overwritten

**Acceptance criteria**: three-layer variable override results match `ansible-inventory --host` from the CLI.

## 1.3 SSH reachability probing

**Description**: background heartbeat; the data source for the dashboard "online rate" KPI.

**Details**
- Every N minutes (configurable, default 5) probe all hosts with TCP 22 + SSH handshake — lightweight, no full ansible run
- Status flips (online↔offline) are written as events for M8 notifications (P1) and the dashboard
- Host detail shows a small 24-hour online-rate trend

**Acceptance criteria**: power off a test machine and its status flips to offline within 5 minutes; flips back to online automatically after recovery.

## 1.4 K8s node auto-sync (P0 core)

**Description**: automatically map K8s cluster nodes into inventory hosts/groups — the key to merging the "K8s view + Ansible execution".

**Scenario**: a scale-out adds `k3s-worker-04`; within 60 seconds it appears in the inventory worker group, and drift detection (M5.4) immediately sees its version.

**Details**
- After binding a cluster, read nodes from the K8s API every 60 seconds (configurable)
- Mapping rules: node name → host name; connection IP prefers an annotation (e.g. `ops.ansible-gui/ssh-ip`), falls back to InternalIP; hosts needing manual input are marked "connection info pending"
- Auto-grouping: split control-plane/worker by `node-role.kubernetes.io/*`; custom label→group rules are configurable (e.g. `gpu=true` → `gpu-nodes`)
- Node leaving the cluster: marked "left cluster" but not deleted outright — manual confirmation required (prevents accidental deletion)
- Synced fields are read-only locked; the UI clearly marks the source cluster

**Acceptance criteria**: adding/removing a node in the cluster is reflected in inventory within 60 seconds; manually editing an auto-synced group is blocked with the reason explained.

## 1.5 Dynamic inventory (P1)

**Description**: use external sources as inventory instead of building hosts by hand.

**Details**
- Support official Ansible dynamic inventory plugins (aws_ec2, azure_rm to start)
- NetBox connector: sync regularly with NetBox as the source of truth
- Each dynamic source gets its own sync schedule + manual trigger; sync results show a diff preview (added/removed/changed) before applying

## 1.6 OS / kernel inventory scan (P1)

**Description**: collect facts on a schedule to answer "what's the OS version distribution of these machines" and "who has uninstalled security updates".

**Details**
- Daily (configurable) facts collection per group: distro version, kernel, pending package count, security update count
- Feeds the dashboard "pending OS updates" number and the M5 maintenance queue
- Scan results keep history; per-machine version timelines viewable

---

**Non-goals of this module**: CMDB finance/warranty management, automated OS installation (PXE/image deployment).
