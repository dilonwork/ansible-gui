# M3 — 任務執行引擎

> 目標：把「playbook＋對象＋憑證＋參數」綁成可重複、可排程、可審批的執行單元；執行全程隔離、可觀測。

## 3.1 Job Template

**說明**：任務的基本單位，一次把執行所需的全部要素綁定，之後一鍵重跑。

**使用場景**：Dylan 建了一個「每週 OS 安全更新」template：綁 `os-security-update.yml`＋`patch-group`＋SSH 憑證＋forks=5；每週排程直接引用它，不用每次重填。

**功能細節**
- 欄位：名稱、Project＋playbook 選擇、inventory（群組多選＋可排除單台）、憑證、forks、verbose 等級、timeout
- 參數表單（survey）：從 playbook 解析到的變數自動產生初稿（見 M2.2），可手動增刪欄位、設必填/預設值/選項清單；執行時填表單即帶入 `extra_vars`
- 「試跑」按鈕：以 `--check` dry-run 模式先跑一遍，結果標示「試跑」不計入成功率統計
- 執行前快照：playbook commit hash、參數值、inventory 主機清單全部凍結寫入任務紀錄（可追溯）

**資料模型要點**：`job_templates(id, name, project_id, playbook_path, inventory_scope JSON, credential_id, forks, survey_schema JSON, require_approval, created_by)`

**驗收標準**：從按下「執行」到看到第一行 log < 15 秒；試跑任務不會對目標機器做任何變更。

## 3.2 Ad-hoc command

**說明**：不建 template 的臨時指令，回答「這批機器現在是什麼狀態」。

**使用場景**：懷疑某批 worker 的 kubelet 版本不一致，選 `k3s-workers` 跑 `kubelet --version`，30 秒內看到每台回傳彙總。

**功能細節**
- 選主機/群組 → 選 module：常用做成表單（ping、shell/command、copy、service、package），其餘走 raw 參數輸入
- 結果按主機彙總：ok / changed / failed 三群列表，失敗的顯示 stderr 摘要
- 高風險 module（shell 含 rm、reboot 等關鍵字）跳出二次確認
- Ad-hoc 執行同樣寫入任務歷史與審計（M6），不可繞過

**驗收標準**：14 台主機的 ping 在 30 秒內全部回傳並正確分群。

## 3.3 並發與批次策略

**說明**：控制「一次動幾台、失敗幾台就停」，是維運安全的核心開關。

**功能細節**
- `forks`（預設 5，可調）：同時對幾台建立 SSH 連線
- `serial`：批次大小（數字或百分比，如 `1`、`25%`）；M5 節點維護工作流強制覆寫為 `1`
- `max_fail_percentage`：失敗比例達門檻即中止整批（預設 0，即任一失敗就停，可放寬）
- UI 以白話呈現：「每批 1 台／任一台失敗即停止」，不要只丟 Ansible 術語

**驗收標準**：設 serial=1 跑 3 台，log 時間軸證明是逐台依序執行；第二台失敗時第三台未被觸及。

## 3.4 Execution Environment（容器化執行隔離）

**說明**：每個任務跑在乾淨的容器裡，ansible 版本與 collections 鎖定，換機器部署結果一致。

**使用場景**：Dylan 在 homelab 跑的 playbook，拿到另一台主機部署同樣 EE 映像，行為完全一致，不會遇到「我這邊 ansible 版本不一樣」的問題。

**功能細節**
- 系統預設 EE 映像：ansible-core＋kubernetes collection＋常用 collections，版本號固定
- 自訂 EE：UI 勾選 collections 清單 → 自動產生定義檔 → 建置映像 → 推送到內建 registry
- Job Template 可指定 EE 版本；任務歷史記錄實際使用的 EE digest
- 任務容器資源限制（CPU/記憶體）可設，避免大 forks 吃光主機

**技術要點**：執行層用官方 `ansible-runner` 函式庫在容器內驅動；事件流經 WebSocket 轉發（見 M6.1）。

**驗收標準**：同一 template 用 EE v1.2.0 跑兩次，collections 版本完全相同；自訂 EE 建置失敗時有明確的建置 log。

## 3.5 排程（P1）

**說明**：例行維運任務的時間觸發器，如每週日凌晨的 OS patch。

**使用場景**：「每週 OS 安全更新」排程：每週日 02:00 對 `patch-group` 跑 template，跑完推 LINE 通知結果。

**功能細節**
- cron 表達式＋圖形化產生器（分/時/週/月點選），時區可設（預設 America/Phoenix）
- 排程綁定 Job Template；執行時永遠用「最新」template 定義，但每次執行的實際參數寫入歷史
- 漏跑不補跑：服務停機期間錯過的排程只發通知、不自動補執行（維運任務補跑有風險，刻意設計）
- 排程可暫停/恢復；下次執行時間明確顯示

**驗收標準**：設一個 2 分鐘後的一次性排程，準時觸發；服務重啟模擬漏跑，只收到通知、沒有補執行。

## 3.6 審批流程（P1）

**說明**：高風險任務執行前多一道人眼確認。

**使用場景**：`kubelet-upgrade` template 標記需審批；Dylan 在外用手機收到 LINE 審批請求，點連結看參數摘要後核准，任務才開始跑。

**功能細節**
- Template 層級開關「需審批」；觸發後產生申請單（申請人、template、參數摘要、目標主機數、預估影響）
- 審批人由 RBAC `approver` 角色擔任；可核准/駁回（駁回必填理由）；申請人不可審批自己的單
- 審批請求經 M8 推播（LINE/Email）；申請單逾時（預設 4 小時，可設）自動失效
- 審批紀錄寫入審計鏈（M6.3），含核准人與時間戳

**驗收標準**：需審批的 template 按下執行後，任務狀態為「待審批」且無任何 SSH 連線產生；逾時後狀態變「已失效」。

## 3.7 Workflow（P1）

**說明**：把多個 Job Template 串成有分支的工作流；M5 一鍵節點維護就是內建 workflow。

**使用場景**：「上線新節點」workflow：node-init → 加入叢集 → 驗證，每步成功才往下走，任一步失敗走告警分支發通知。

**功能細節**
- 視覺化編排：節點＝Job Template，邊＝成功/失敗分支；支援平行分支（fork/join）
- 每個節點可覆寫參數（用上游節點輸出當變數，基本字串模板）
- Workflow 執行有總覽時間軸，每個子任務狀態獨立可見；整體狀態＝最嚴重的子任務狀態
- 內建 workflow：節點維護（M5）、新節點上線；使用者可複製修改

**驗收標準**：三節點 workflow 中間失敗，失敗分支被觸發且後續成功分支未執行；總覽頁能一眼看出卡在哪一步。

---

**本模組 Non-goals**：CI/CD pipeline（測試/建置/部署軟體）、跨系統的通用 workflow 引擎（如 n8n 那類）。
