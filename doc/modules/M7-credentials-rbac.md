# M7 — Credentials & RBAC

> Goal: a central vault for secrets + least-privilege people management; the principle is "the UI never emits plaintext, permissions default to minimal".

## 7.1 Credential management

**Description**: the single home for all secrets; injected only at Ansible run time, never transmitted in plaintext through the UI.

**Scenario**: Dylan adds his homelab SSH private key; after pasting, the system stores only the encrypted form; later he picks that key from a dropdown when building a Job Template, it's injected at run time, and even Dylan himself can't see the plaintext.

**Details**
- Types: SSH private key, username/password, Ansible Vault password, kubeconfig, (P1) cloud AK/SK
- Encryption at rest: AES-256-GCM, encryption key from an environment variable (initially) → KMS support later; useless even if the DB is exfiltrated
- The UI only shows: name, type, fingerprint (SSH keys), masked form (`ghp_****abcd`, last 4 chars), created/updated time and by whom; **no API ever returns plaintext** (editing = re-upload to overwrite)
- Binding scope: global / specific groups / specific hosts; resolution order at run time: host → group → global
- Usage records: every reference by a job is logged (who, when, which job); anomalous use (e.g. called at 3am) can alert (via M8, P1)

**Acceptance criteria**: inspecting all API responses in browser dev tools finds no private key/password plaintext; deleting a credential referenced by a template is blocked with the referrers listed.

## 7.2 RBAC (P1)

**Description**: with multiple users, who can touch what must be separable; can be disabled for single-user (default admin).

**Scenario**: a junior SRE joins the team with the `operator` role: can run the established patch templates, can't change template definitions, can't touch credentials, can't approve.

**Details**
- Model: user → role → permissions (resource × action); resources: host groups, Job Templates, clusters, credentials, system settings
- Built-in roles:
  - `admin`: everything
  - `operator`: run templates/ad-hoc, view logs; cannot add/modify templates, credentials, schedules
  - `viewer`: read-only (the UI hides all action buttons outright, not just backend blocking)
  - `approver`: approvals (M3.6) + read-only otherwise
- Custom roles: generated from a checkbox matrix; LDAP/OIDC integration (P2, identity outsourced)
- Permission changes take effect immediately (no re-login); all grant changes enter the audit log (M6.3)

**Acceptance criteria**: after a viewer logs in, no "run/add/delete" button exists anywhere on screen; an operator calling the add-template API gets a 403.

## 7.3 Cloud credentials (P1)

**Description**: credential types ready for dynamic inventory (M1.5) and future cloud resource operations.

**Details**
- Supports AWS AK/SK, Azure Service Principal, GCP Service Account (JSON)
- Each cloud credential is labeled by purpose (inventory-sync-only / general); inventory sync jobs can only pick "inventory-only" ones (least privilege)
- Cloud credentials never enter the Ansible execution environment — only the backend sync workers use them (isolated)

## 7.4 Rotation reminders (P2)

**Description**: secrets go stale; the system reminds, but never auto-rotates (auto-rotation is too risky — deliberately not built).

**Details**
- Each credential can carry an expiry; reminders at 30/7/1 days before (via M8)
- SSH key fingerprint audit: periodically compare each host's `authorized_keys` against registered keys; "foreign" keys raise an alert (could be a manually added backdoor, could be legitimate — either way, people should know)
- kubeconfig expiry pre-check (linked with M4.1)

---

**Non-goals of this module**: automatic secret rotation, enterprise PAM integration (CyberArk etc.), cross-system credential sync.
