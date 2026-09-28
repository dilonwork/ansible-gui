# 功能清單（Feature List）

> 優先級：**P0** = MVP 必備 · **P1** = Phase 2 團隊可用 · **P2** = Phase 3 強化
> 對應模組編號見 `planning.md` 第 3 節。

## M1 — 主機資產 / Inventory 管理

- [ ] P0 手動新增 / 編輯 / 刪除主機（hostname、IP、SSH port、群組）
- [ ] P0 主機群組管理（巢狀群組、host/group 變數，相容 Ansible inventory 語義）
- [ ] P0 SSH 可達性探測（在線/離線狀態）
- [ ] P0 從 K8s API 自動同步 worker nodes（依 label/taint/role 自動分群）
- [ ] P1 動態 inventory plugin（雲主機、NetBox）
- [ ] P1 主機 OS / kernel 版本盤點
- [ ] P2 主機標籤與自訂欄位、匯入/匯出（CSV/YAML）

## M2 — Playbook 管理

- [ ] P0 Git repo 同步（Project 概念：URL、分支/tag、異動自動拉取）
- [ ] P0 Playbook 瀏覽（repo 內 playbook 清單、語法檢查）
- [ ] P0 Galaxy roles / collections 安裝管理
- [ ] P0 變數管理 ＋ Ansible Vault 加密變數
- [ ] P0 內建官方工作流模板：OS 安全更新、kubelet 升級、rolling reboot、節點上線初始化
- [ ] P1 Playbook 版本 diff（本次執行 vs 上次）

## M3 — 任務執行引擎

- [ ] P0 Job Template（playbook ＋ inventory ＋ 憑證 ＋ 參數表單綁定）
- [ ] P0 Ad-hoc command（臨時對一批主機下指令）
- [ ] P0 並發控制（forks、serial 逐台、失敗即停 max_fail_percentage）
- [ ] P0 容器化 Execution Environment（執行隔離）
- [ ] P1 排程執行（cron-like，例行 patch window）
- [ ] P1 審批流程（drain/重啟/升級等高風險任務執行前核准）
- [ ] P1 Workflow（一鍵串連多個 Job Template，如本專案的節點維護流）
- [ ] P2 任務重試 / 跳過失敗節點繼續

## M4 — Kubernetes 叢集管理

- [ ] P0 多叢集接入（kubeconfig 匯入、連通性檢測）
- [ ] P0 叢集總覽：版本、節點 Ready 數、異常 Pod 數
- [ ] P0 節點列表：角色、K8s/kubelet/containerd 版本、Ready 狀態、CPU/記憶體使用率、labels/taints
- [ ] P0 受控操作：cordon / drain / uncordon（走審批）
- [ ] P1 工作負載唯讀視圖（Deployment/DaemonSet/Pod 狀態，參考 Headlamp）
- [ ] P2 節點事件時間線（Events 聚合）

## M5 — Worker Node 維運 ★ 差異化核心

- [ ] P0 **一鍵節點維護工作流**：cordon → drain → OS patch / kubelet 升級 → 健康驗證 → uncordon
- [ ] P0 逐台 serial 執行、任一失敗即停、可隨時暫停/恢復
- [ ] P0 維護前檢查清單：PDB 是否阻擋 drain、單副本 workload 警告、control plane 健康
- [ ] P1 **版本漂移偵測**：期望版本（inventory 宣告）vs 實際版本比對，產出待升級清單
- [ ] P1 kernel 升級後的 rolling reboot 編排
- [ ] P1 批次選取多節點排入維護佇列
- [ ] P2 維護歷史與成功率統計（每節點）

## M6 — 即時日誌與審計

- [ ] P0 WebSocket 即時串流任務輸出（ansible-runner events）
- [ ] P0 任務歷史：完整 log、執行者、耗時、變更摘要（changed/failed 統計）
- [ ] P1 hash-chained 審計鏈（不可竄改、支援離線驗證）
- [ ] P2 log 關鍵字搜尋、失敗任務快速定位

## M7 — 憑證與 RBAC

- [ ] P0 憑證管理：SSH key、帳號密碼、Vault 密碼、kubeconfig
- [ ] P1 RBAC：組織/團隊/角色（誰能對哪群主機跑哪個 template）
- [ ] P1 雲端憑證（後續接動態 inventory 用）
- [ ] P2 憑證輪換提醒、SSH key 指紋盤點

## M8 — 告警通知

- [ ] P1 任務成功/失敗通知（Webhook / Email）
- [ ] P1 節點 NotReady / 版本漂移通知
- [ ] P1 LINE Notify（台灣團隊常用）
- [ ] P2 Slack / Teams 範本訊息

## M9 — 儀表板

- [ ] P0 總覽儀表板（設計稿見 `doc/dashboard-design.md`）
  - KPI：受管主機、在線率、叢集數、待升級節點、進行中任務、近 7 天成功率
  - 叢集健康卡、Worker 節點表、進行中任務即時日誌、待辦維運、快速動作
- [ ] P1 自訂時間範圍、任務趨勢圖
- [ ] P2 節點容量趨勢（CPU/記憶體/磁碟歷史線）

## 非功能需求

- [ ] P0 單一 docker-compose 一鍵部署（含 Postgres、Redis）
- [ ] P0 操作審計（誰在何時對哪台機器做了什麼）
- [ ] P1 API 完整（前端只吃 REST API，方便之後寫 CLI / 接 CI）
- [ ] P1  secrets 落盤加密（Vault 密碼、SSH key at rest encryption）
- [ ] P2 高可用部署指引（多副本執行節點）
