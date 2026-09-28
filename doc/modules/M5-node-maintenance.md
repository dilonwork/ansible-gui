# M5 — Worker Node Maintenance

> Goal: turn the manual "cordon→drain→patch/upgrade→verify→uncordon" grind into a one-click workflow; this is the core differentiator against AWX and Rancher.

## 5.1 One-click node maintenance workflow

**Description**: pick nodes, pick a maintenance type, press start — the playbook runs itself; humans just watch and decide on anomalies.

**Scenario**: k3s-worker-03 has version drift (v1.30.5, expected v1.31.2). Dylan clicks "Upgrade" on the node list, confirms parameters on the maintenance page, presses start; the system runs cordon→drain→kubelet upgrade→verify→uncordon node by node, with live progress on the page.

**Details**
- Fixed six-step play: ① preflight checks (5.3) → ② cordon → ③ drain → ④ run maintenance playbook → ⑤ health verification → ⑥ uncordon → next node
- Three maintenance types: OS security update (built-in `os-security-update`), K8s component upgrade (built-in `kubelet-upgrade`, target version required), custom (any Job Template as step ④)
- Page layout: left step rail (current step highlighted, completed steps checked), center live log of the current node, right node queue (four states: pending/in-progress/done/failed)
- Step ⑤ health verification covers: Node Ready=True, kubelet version as expected, key DaemonSets' Pods on the node back to Running; any failure marks the node failed
- A summary report after the batch: per-node duration, what changed (e.g. 14 packages upgraded), failed nodes with reasons

**Data model notes**: `maintenance_runs(id, type, target_version, node_queue JSON, status, current_step, per_node_result JSON, created_by)`; implemented on top of the M3.7 Workflow — the six steps are six nodes.

**Acceptance criteria**: a rolling OS patch across 3 test workers completes fully in the GUI with zero manual SSH; closing and reopening the browser mid-run restores the progress view correctly.

## 5.2 Execution control

**Description**: ops' biggest fear is losing control; a human must be able to call a stop at any moment.

**Details**
- Forced `serial=1`: always one node at a time — a non-overridable safety bottom line
- Any node failure → the whole batch auto-pauses (not "continue to next node"), offering three choices: retry this node / skip this node and continue / abort the batch
- Manual pause/resume: pause takes effect only "between steps" — it won't kill a drain mid-flight; resume continues from the breakpoint
- Finished nodes can't be re-run individually (avoids double-patching); re-runs must restart the whole batch, clearly labeled
- After abort: already-uncordoned nodes stay as-is; the node in progress goes through a safe wind-down (finish current step → uncordon → stop)

**Acceptance criteria**: pressing pause mid-drain lets the drain finish, then halts before the next step; after "skip", the next node starts normally.

## 5.3 Pre-maintenance checklist

**Description**: surface everything that could go wrong before starting — block on failures instead of letting problems blow up overnight.

**Details**
- Auto-checks (all run once before starting):
  1. control plane health (API reachable, etcd quorum OK)
  2. PDB block analysis: list PDBs that would block drain and affected workloads, with "handle first" advice
  3. single-replica workload warning: call out by name the Pods that would lose service after drain (replicas=1 and not a DaemonSet)
  4. target node disk space (upgrade needs > 2GB free, otherwise blocked)
  5. version diff: current vs target; cross-minor jumps (e.g. 1.30→1.32) raise an extra warning (K8s only supports sequential upgrades)
  6. spare capacity: whether remaining schedulable resources can absorb the evicted Pods after drain
- Results in three tiers: pass (green) / warning (amber, can be waived with "I acknowledge") / blocker (red, must be resolved before starting)
- The check report is exportable and attached to the maintenance summary

**Acceptance criteria**: starting maintenance on a node with a single-replica critical service correctly calls out that service as a warning; insufficient disk triggers a red blocker that prevents starting.

## 5.4 Version drift detection (P1)

**Description**: continuously compare "expected" vs "actual" versions so the to-upgrade list computes itself instead of relying on manual `kubectl get nodes`.

**Scenario**: Dylan declares `k8s_version: v1.31.2` in group variables; a week later the system finds k3s-worker-03 still on v1.30.5; the dashboard shows an amber badge, and the todo area can throw those 3 nodes into the maintenance queue with one click.

**Details**
- Expected versions are declared in group variables (`k8s_version`, `kubelet_version`, `os_patch_level`); "latest" means the majority version across the cluster
- Full comparison daily (configurable); dimensions: K8s version, kubelet, containerd, OS security update count
- Drift tiers: patch behind (1.31.1→1.31.2, amber) / minor behind (1.30→1.31, orange-red) / ahead (someone upgraded manually, blue — asks for confirmation)
- Todo area "add to maintenance queue in one click": auto-creates a 5.1 maintenance run with the expected value as target version

**Acceptance criteria**: manually downgrade a test node — it shows up in the drift list with the correct tier within 24 hours.

## 5.5 Rolling reboot orchestration (P1)

**Description**: the standard post-kernel-upgrade reboot, same safe playbook.

**Details**
- Play: drain → reboot → wait for SSH to come back → wait for Node Ready (timeout 10 min, adjustable) → uncordon → next node
- Trigger: after an OS patch maintenance, auto-detect "reboot required" (`/var/run/reboot-required` exists) → ask whether to run rolling reboot next; can also be started manually
- Before rebooting a node, take a snapshot record of its Pod distribution; compare after reboot to confirm services recovered

**Acceptance criteria**: 3 nodes needing reboot complete; services stay uninterrupted during each reboot (proven by continuous probing of a test service).

## 5.6 Batched maintenance queue (P1)

**Description**: plan once, execute in batches — maintenance queues spanning days and clusters.

**Details**
- The queue accepts nodes from different clusters and different maintenance types; drag-to-reorder, configurable interval between batches (e.g. 30 minutes between nodes, leaving an observation window for monitoring)
- Queues can be saved as "maintenance plan" templates (e.g. "monthly Patch Tuesday plan") and loaded with one click next month
- A running queue supports queue-jumping (emergency nodes go next) and removing pending nodes

**Acceptance criteria**: build a 6-node queue with 30-minute intervals — execution order and spacing match the settings; inserting an emergency node mid-run makes it the next one up.

---

**Non-goals of this module**: automated self-healing (not doing the operator thing), cross-cloud node replacement (blue-green machine swaps), firmware/BMC-layer operations.
