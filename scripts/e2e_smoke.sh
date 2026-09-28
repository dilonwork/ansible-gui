#!/bin/bash
# E2E smoke test：對一個已啟動的後端做端到端驗證。
#
# 測什麼：新增主機（含真實 ssh-keyscan）→ 建 ping 任務 → 輪詢到結束 →
#         驗證 status=successful 且事件序列正確 → 清理測試資料。
# 只用 python3 stdlib，沒有額外依賴。
#
# 前置需求：
#   1. 後端跑在 $BACKEND_URL（預設 http://localhost:8000）
#   2. 本機有 sshd 在 127.0.0.1:22（測試目標；沒有會 SKIP）
#   3. 允許腳本把臨時 pubkey 加入 $SSH_USER 的 authorized_keys（結束後移除）
#
# 用法：BACKEND_URL=http://localhost:8000 ./scripts/e2e_smoke.sh
set -u

BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
SSH_USER="${SSH_USER:-root}"   # 測試目標的 SSH 使用者
TMPDIR_WORK="$(mktemp -d)"
KEY="$TMPDIR_WORK/id_e2e"
PUBLINE=""
# 用 getent 解析該使用者的家目錄（不要用 $HOME：執行環境的 HOME 未必對應 SSH_USER）
USER_HOME="$(getent passwd "$SSH_USER" | cut -d: -f6)"
[ -n "$USER_HOME" ] || { echo "FAIL: 找不到使用者 $SSH_USER"; exit 1; }
AUTH_KEYS="$USER_HOME/.ssh/authorized_keys"

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; exit 1; }
skip() { echo "SKIP: $1"; exit 0; }

cleanup() {
  # 移除測試主機與臨時 pubkey
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

# 1. 後端可達？
curl -s -m 5 "$BACKEND_URL/api/hosts" > /dev/null 2>&1 \
  || fail "後端連不上（$BACKEND_URL）"

# 2. 本機 sshd？
(timeout 3 bash -c "</dev/tcp/127.0.0.1/22" 2>/dev/null) \
  || skip "本機 127.0.0.1:22 沒有 sshd，無法做端到端測試"

# 3. 臨時 keypair + 註冊 pubkey（寫入 SSH_USER 的 authorized_keys）
ssh-keygen -t ed25519 -f "$KEY" -N "" -C "e2e-smoke-test" -q \
  || fail "keypair 產生失敗"
mkdir -p "$USER_HOME/.ssh" && chmod 700 "$USER_HOME/.ssh"
PUBLINE="$(cat "$KEY.pub")"
grep -q "e2e-smoke-test" "$AUTH_KEYS" 2>/dev/null \
  || echo "$PUBLINE" >> "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"

# 4. 新增主機（含真實 keyscan）
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
case "$HOST_RESP" in ERROR*) fail "新增主機失敗：$HOST_RESP";; esac
HOST_ID="$HOST_RESP"
pass "新增主機（含 keyscan）id=$HOST_ID"

# 5. 建任務 → 輪詢 → 驗證
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
assert job["status"] == "successful", f"任務未成功：{job['status']}"
types = [e["type"] for e in job["events"]]
assert types == ["job_started", "task_start", "host_ok", "job_finished"], f"事件序列異常：{types}"
print(f"任務 {jid} 成功，事件序列正確")
EOF
[ $? -eq 0 ] && pass "ping 任務端到端成功" || fail "ping 任務失敗"

echo "== 全部通過 =="
