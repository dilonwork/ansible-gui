# Backend skeleton

最小可跑的後端骨架，驗證整條鏈路：
瀏覽器 → REST → 背景執行緒跑 ansible-runner → SSH → 主機 → WebSocket 即時回傳。

## 本機跑（開發）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

開瀏覽器到 http://localhost:8000：新增主機（填 SSH 私鑰）→ 勾選 → 執行 ping → 看即時日誌。

## Docker 跑

```bash
docker compose up --build
```

## API

- `POST /api/hosts` {name, address, port, username, private_key} → 新增主機（會做 ssh-keyscan 探測）
- `GET /api/hosts` → 主機清單（不回傳私鑰）
- `DELETE /api/hosts/{id}`
- `POST /api/jobs` {host_ids} → 建立 ping 任務，回傳 job_id（背景執行）
- `GET /api/jobs/{job_id}` → 任務狀態＋事件歷史
- `WS /ws/jobs/{job_id}` → 即時事件串流（先補歷史再串流）

## 測試

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

L1 API 測試（不依賴 sshd，可進 CI）：見 `doc/testing.md` 的完整測試方案。
E2E smoke（需本機 sshd）：`./scripts/e2e_smoke.sh`（從 repo 根目錄跑）。

## 安全設計（已做）

- SSH 私鑰只在執行時寫成 600 暫存檔，跑完即刪
- host key 驗證：`StrictHostKeyChecking=yes` + keyscan 取得的 known_hosts，不盲信

## 之後要換掉的（skeleton 簡化）

- 記憶體 dict → PostgreSQL
- threading 背景執行 → Celery + Redis（長任務、重試、取消）
- 無驗證 → 登入＋RBAC
- 私鑰放記憶體 → Vault 加密落盤
