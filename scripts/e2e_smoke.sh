#!/bin/bash
# E2E smoke test: end-to-end verification against a running backend.
#
# What it covers:
#   add host (real ssh-keyscan) -> ad-hoc ping job -> poll until done ->
#   playbook + syntax check + template -> launch job from template ->
#   verify snapshot and successful run -> clean up test data.
# Only python3 stdlib is used, no extra dependencies.
#
# Requirements:
#   1. backend running at $BACKEND_URL (default http://localhost:8000)
#   2. Redis running on localhost:6379 (Celery broker; use scripts/start-services.sh)
#   3. a Celery worker running:  celery -A app.celery_app worker --loglevel=info
#      (from backend/, with the same DATABASE_URL / REDIS_URL / ENCRYPTION_KEY)
#   4. local sshd on 127.0.0.1:22 (test target; SKIP if missing)
#   5. the script may append a temp pubkey to $SSH_USER's authorized_keys (removed on exit)
#
# Usage: BACKEND_URL=http://localhost:8000 ./scripts/e2e_smoke.sh
set -u

BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
SSH_USER="${SSH_USER:-root}"   # SSH user of the test target
TMPDIR_WORK="$(mktemp -d)"
KEY="$TMPDIR_WORK/id_e2e"
PUBLINE=""
# Resolve the user's home via getent (do NOT use $HOME: it may not match SSH_USER here)
USER_HOME="$(getent passwd "$SSH_USER" | cut -d: -f6)"
[ -n "$USER_HOME" ] || { echo "FAIL: unknown user $SSH_USER"; exit 1; }
AUTH_KEYS="$USER_HOME/.ssh/authorized_keys"

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; exit 1; }
skip() { echo "SKIP: $1"; exit 0; }

cleanup() {
  # remove test template / playbook / host and the temp pubkey
  if [ -n "${TPL_ID:-}" ]; then
    curl -s -X DELETE "$BACKEND_URL/api/templates/$TPL_ID" > /dev/null 2>&1
  fi
  if [ -n "${PB_ID:-}" ]; then
    curl -s -X DELETE "$BACKEND_URL/api/playbooks/$PB_ID" > /dev/null 2>&1
  fi
  if [ -n "${HOST_ID:-}" ]; then
    curl -s -X DELETE "$BACKEND_URL/api/hosts/$HOST_ID" > /dev/null 2>&1
  fi
  if [ -n "$PUBLINE" ] && [ -f "$AUTH_KEYS" ]; then
    grep -v "e2e-smoke-test" "$AUTH_KEYS" > "$AUTH_KEYS.tmp" 2>/dev/null \
      && mv "$AUTH_KEYS.tmp" "$AUTH_KEYS"
  fi
  rm -rf "$TMPDIR_WORK"
}
trap cleanup EXIT

echo "== E2E smoke =="
echo "backend: $BACKEND_URL"

# 1. backend reachable?
curl -s -m 5 "$BACKEND_URL/api/hosts" > /dev/null 2>&1 \
  || fail "backend unreachable ($BACKEND_URL)"

# 2. local sshd?
(timeout 3 bash -c "</dev/tcp/127.0.0.1/22" 2>/dev/null) \
  || skip "no sshd on 127.0.0.1:22, cannot run end-to-end test"

# 3. temp keypair + register pubkey in SSH_USER's authorized_keys
ssh-keygen -t ed25519 -f "$KEY" -N "" -C "e2e-smoke-test" -q \
  || fail "keypair generation failed"
mkdir -p "$USER_HOME/.ssh" && chmod 700 "$USER_HOME/.ssh"
PUBLINE="$(cat "$KEY.pub")"
grep -q "e2e-smoke-test" "$AUTH_KEYS" 2>/dev/null \
  || echo "$PUBLINE" >> "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"

# 4. add host (real keyscan)
HOST_RESP="$(python3 - "$BACKEND_URL" "$KEY" "$SSH_USER" <<'EOF'
import json, sys, urllib.request
base, key_path, ssh_user = sys.argv[1], sys.argv[2], sys.argv[3]
key = open(key_path).read()
req = urllib.request.Request(base + "/api/hosts",
    data=json.dumps({"name": "e2e-smoke", "address": "127.0.0.1",
                     "port": 22, "username": ssh_user, "private_key": key}).encode(),
    headers={"Content-Type": "application/json"})
try:
    print(json.load(urllib.request.urlopen(req, timeout=30))["id"])
except Exception as e:
    print("ERROR:" + str(e)); sys.exit(1)
EOF
)"
case "$HOST_RESP" in ERROR*) fail "add host failed: $HOST_RESP";; esac
HOST_ID="$HOST_RESP"
pass "host added (keyscan) id=$HOST_ID"

# 5. ad-hoc ping job -> poll -> verify
python3 - "$BACKEND_URL" "$HOST_ID" <<'EOF'
import json, sys, time, urllib.request
base, hid = sys.argv[1], sys.argv[2]
def post(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))
jid = post("/api/jobs", {"host_ids": [hid]})["job_id"]
for _ in range(60):
    job = json.load(urllib.request.urlopen(f"{base}/api/jobs/{jid}", timeout=30))
    if job["status"] != "running":
        break
    time.sleep(1)
assert job["status"] == "successful", f"job not successful: {job['status']}"
types = [e["type"] for e in job["events"]]
assert types == ["job_started", "task_start", "host_ok", "job_finished"], f"bad event sequence: {types}"
print(f"ping job {jid} ok")
EOF
[ $? -eq 0 ] && pass "ad-hoc ping end-to-end ok" || fail "ad-hoc ping job failed"

# 6. playbook + syntax check + template + launch -> verify snapshot and run
TPL_PB_IDS="$(python3 - "$BACKEND_URL" "$HOST_ID" <<'EOF'
import json, sys, time, urllib.request
base, hid = sys.argv[1], sys.argv[2]
def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))

pb_yaml = """- name: e2e template test
  hosts: all
  gather_facts: false
  tasks:
    - name: echo var
      ansible.builtin.shell: "echo {{ e2e_msg }}"
      register: out
    - name: show output
      ansible.builtin.debug:
        var: out.stdout
"""
pb_id = api("POST", "/api/playbooks", {"name": "e2e-playbook", "content": pb_yaml})["id"]
chk = api("POST", f"/api/playbooks/{pb_id}/syntax-check")
assert chk["ok"] is True, f"syntax check failed: {chk['output'][:300]}"

tpl_id = api("POST", "/api/templates", {
    "name": "e2e-template", "playbook_id": pb_id, "host_ids": [hid],
    "extra_vars": {"e2e_msg": "hello-e2e"}, "check_mode": False})["id"]

jid = api("POST", "/api/jobs", {"template_id": tpl_id})["job_id"]
for _ in range(90):
    job = api("GET", f"/api/jobs/{jid}")
    if job["status"] != "running":
        break
    time.sleep(1)
assert job["status"] == "successful", f"template job not successful: {job['status']}"
snap = job["snapshot"]
assert snap["kind"] == "template", f"bad snapshot kind: {snap['kind']}"
assert snap["template_name"] == "e2e-template"
assert snap["extra_vars"] == {"e2e_msg": "hello-e2e"}, f"bad snapshot vars: {snap['extra_vars']}"
assert any(e["type"] == "host_ok" for e in job["events"]), "no host_ok event"
print(f"template job {jid} ok, snapshot verified")
print(f"IDS:{tpl_id}:{pb_id}")
EOF
)"
[ $? -eq 0 ] || fail "template flow failed"
TPL_ID="$(echo "$TPL_PB_IDS" | grep '^IDS:' | cut -d: -f2)"
PB_ID="$(echo "$TPL_PB_IDS" | grep '^IDS:' | cut -d: -f3)"
pass "playbook + syntax check + template + snapshot launch ok"

echo "== ALL PASSED =="
