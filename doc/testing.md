# 測試方案

> 原則：每個開發任務完成時，相關測試必須全綠才算做完。測試不是寫完才補，是任務的驗收門檻。

## 測試分層

| 層級 | 工具 | 測什麼 | 何時跑 | 怎麼跑 |
|---|---|---|---|---|
| L1 API 測試 | pytest + FastAPI TestClient | REST CRUD、任務狀態機、WS 歷史補發、私鑰不外洩 | 每次改後端 | `cd backend && python -m pytest tests/ -q` |
| L2 前端建置 | tsc + vite build | 型別錯誤、建置失敗 | 每次改前端 | `cd frontend && npm run build` |
| L3 E2E smoke | `scripts/e2e_smoke.sh`（只用 python3 stdlib） | 真實 keyscan → 建主機 → 真實 SSH 跑 ansible ping → 驗證事件序列 → 清理 | 每次改「SSH/執行鏈路」後、本機驗證 | `BACKEND_URL=http://localhost:8000 ./scripts/e2e_smoke.sh` |
| L4 手動驗收 | 驗收清單（見下） | MVP 級場景：3 台 worker rolling patch 全程 GUI | 每個 milestone 結束 | 照 `doc/modules/` 驗收標準逐項打勾 |

說明：

- L1 用 monkeypatch 換掉 `keyscan` 與 `run_ping_job`，**不依賴 sshd**，所以能在 GitHub Actions 跑。
- 真實 SSH 路徑只在 L3 測（需要本機或測試環境有 sshd），避免 CI 脆弱。
- L2 目前是建置驗證；之後補 React 元件測試（Vitest）再升級為完整前端測試。

## CI（GitHub Actions）

`.github/workflows/ci.yml`：每次 push / PR 自動跑

- `backend` job：裝 `requirements.txt + requirements-dev.txt`，跑 pytest
- `frontend` job：`npm ci && npm run build`

CI 紅燈 = 不能算做完，修好才 push 下一個東西。

## 測試案例 vs 模組驗收標準

以 `doc/modules/M*.md` 的驗收標準為準。skeleton 階段已覆蓋：

| 案例 | 層級 | 狀態 |
|---|---|---|
| 新增主機：keyscan 成功才寫入 | L1（mock keyscan）、L3（真實 keyscan） | ✅ |
| 新增主機：keyscan 失敗回 400，不寫入 | L1 | ✅ |
| 主機列表不外洩私鑰 | L1 | ✅ |
| 刪除主機 | L1、L3（清理驗證） | ✅ |
| 建任務：未知主機回 400 | L1 | ✅ |
| ping 任務跑完狀態 successful，事件序列正確 | L1（假執行器）、L3（真實 ansible+SSH） | ✅ |
| 執行器回傳失敗 → 任務狀態 failed | L1 | ✅ |
| WS 晚連線：先收到歷史事件再收到 eof | L1 | ✅ |
| WS 連未知任務被拒絕 | L1 | ✅ |
| 前端建置通過（tsc 零錯誤） | L2、CI | ✅ |

之後每做一個 M 模組，把它的驗收標準逐條轉成測試案例加進這張表，沒加 = 沒做完。

## Definition of Done（開發任務完成標準）

1. 功能照 `doc/modules/` 規格實作
2. 對應測試案例寫好，**L1 全綠**（後端）/ **L2 通過**（前端）
3. 有動到 SSH/執行鏈路 → **L3 全綠**
4. commit + push，CI 全綠
5. 回報時附測試結果（幾項通過），不是只說「做完了」

## 監督機制

- 之後每個開發任務，Andrew 照上面的 DoD 執行：跑測試 → 全綠才 commit/push → 回報附測試結果。
- 測試案例與驗收標準的對應表（上表）持續更新，缺的會直接標出來，不會默默跳過。
- CI 在 GitHub 上把關；若 CI 紅了，優先修 CI 再做新功能。
