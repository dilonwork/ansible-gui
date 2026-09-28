# M2 — Playbook Management

> Goal: Git is the only source of playbooks; the UI only does "sync, browse, check, publish" — no online editor (edit in Git; deliberate design).

## 2.1 Git Project sync

**Description**: maps to AWX's Project concept — one Project is one git repo.

**Scenario**: Dylan pushes his ops playbooks to GitHub; the system syncs automatically, and the new `os-security-update.yml` shows up in the Playbook list ready to build a Job Template.

**Details**
- Fields: name, git URL, branch/tag (default main), credential (deploy key for private repos, stored in M7), sync schedule (default: check before every run + periodic)
- On sync, list playbook files in the repo (heuristic: `*.yml` containing `hosts:` + manual marking)
- New commits in the repo: opt into auto-sync or notify-only; sync failures (auth/network) must report clearly
- Each Project records the commit hash it has synced to; job history links to it (traceable: "which version of the playbook did this run use")

**Acceptance criteria**: a pushed playbook appears in the UI within 1 minute; job history shows the commit hash at run time.

## 2.2 Playbook browsing & syntax check

**Description**: look before you run, check before you run — fewer rookie failures.

**Details**
- Tree-browse repo files; click a playbook to view YAML (read-only + syntax highlighting)
- "Syntax check" button runs `ansible-playbook --syntax-check`; errors show line numbers and messages
- Static analysis summary: hosts, task count, referenced roles, whether `become: yes` is present (flagged high-risk)
- When building a Job Template, parsed variables are auto-filled as the parameter form draft (M3.1)

**Acceptance criteria**: a playbook with an indentation error gets its line number pointed out within 5 seconds of checking.

## 2.3 Galaxy roles / collections management

**Description**: playbook dependencies must be versioned too, or nothing runs when you switch environments.

**Details**
- Each Project may include a `requirements.yml`, editable in the UI (one of the few things allowed to be edited online, because it's trivial)
- Install/update buttons, versions pinned; install results feed into execution environment builds (linked to M3.4)
- Show installed collections and versions (ansible-galaxy collection list)

## 2.4 Variables & Vault

**Description**: three variable layers + encrypted variables; the UI never leaks plaintext.

**Details**
- Variable layers: Project defaults → group → host (wired into the M1.2 effective vars preview)
- Two edit modes: form (key/value) and raw YAML
- Vault: vault password stored as an M7 credential; encrypted values display as `!vault (encrypted)` in the UI; no plaintext in any list/log
- "Add encrypted variable" flow: type plaintext in the UI → backend encrypts → only ciphertext is stored (plaintext never lands on disk, never enters logs)

**Acceptance criteria**: searching the entire UI (including logs) by keyword finds no vault plaintext.

## 2.5 Built-in official templates

**Description**: out-of-the-box best practices that lower the first-use barrier; also the default playbooks for M5 workflows.

**Details**
- Shipped with the system: `os-security-update`, `kubelet-upgrade`, `rolling-reboot`, `node-init` (new node init: create user, install containerd, disable swap, etc.)
- Each template ships parameter docs and sane defaults; users "Copy as mine" to modify; official templates themselves are read-only
- Template versions follow system updates; diffs are shown on update

## 2.6 Version diff (P1)

**Description**: answers "what changed between this run and the last one".

**Details**
- Diff view of two commits of the same playbook (built on git diff)
- The job history page links directly to a "this run vs last successful run" playbook diff

---

**Non-goals of this module**: online YAML editor, playbook marketplace/sharing platform.
