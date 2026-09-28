# Side Project：Drydock 遠端伺服器管理平台 — 功能規劃 v0.1

> 目標：以 GUI 整合 Ansible，對伺服器做遠端管理；核心場景是 **Kubernetes 叢集與 Worker Nodes 的維運**。
> 階段：先收集成功案例 → 功能規劃（本文件）→ 之後再進技術選型與 MVP 開發。
> 日期：2026-09-27

---

## 1. 專案定位

### 1.1 問題陳述

管伺服器的人手上有兩套割裂的工具：

- **Ansible GUI 工具**（AWX、Semaphore）只管「playbook 執行」，看不到 K8s 節點的狀態（版本、Ready、Pod 分佈），做 worker node 升級時還要人肉對照 `kubectl get nodes`。
- **K8s 管理 GUI**（Rancher、Portainer、Headlamp）只管叢集內的資源，**管不到 OS 層**：套件 patch、kubelet/containerd 升級、rolling reboot 都做不了。

中間缺了一塊：**「知道 K8s 節點狀態，又能用 Ansible 對 OS 層動手」的維運平面**。這就是本專案的切入點。

### 1.2 目標使用者

- 自己（homelab / 個人叢集維運，先驗證）
- 中小團隊的 infra / SRE：自建 kubeadm、k3s、RKE 叢集，需要定期做 OS patch 與 K8s 版本升級，但養不起 AWX 那套重量級方案

### 1.3 Non-goals（先不做）

- 不做成另一個 Rancher：工作負載的完整 CRUD、Helm、GitOps 不是重點
- 不做 CMDB 資產財務管理
- 第一版不碰 Windows / WinRM（先 Linux + SSH）

---

## 2. 成功案例收集

### 2.1 Ansible GUI 類

| 案例 | 定位 | 學到的借鏡點 |
|---|---|---|
| **AWX**（Ansible Automation Platform 上游） | 官方企業級方案：Web UI + REST API + 任務引擎，Job Template、Inventory、Credential、RBAC、Workflow | 功能最全的參考模型；**Execution Environments（容器化隔離執行）**值得學；但部署重（需 K8s + Postgres + Redis），近期社群對其 nightly 發布節奏的穩定性有疑慮 |
| **Semaphore UI** | 輕量開源 Ansible UI，Go 單一 binary + Vue 前端 | **「單 binary 好部署」是輕量路線的殺手鐧**；Task Template + 排程已夠小團隊用；缺點是擴展性與 RBAC 較弱 |
| **Rundeck** | Runbook 自動化平台，有 Ansible plugin | **「維運手冊化」思維**：把 drain→升級→驗證做成可重複執行的 runbook，並支援 self-service 與審批，適合包裝成「一鍵節點維護」 |
| **ansible-webui（O-X-L）** | Python Django + Svelte，用官方 `ansible-runner` 函式庫執行 | **技術實作參考**：直接用官方 ansible-runner 而非自己拼 subprocess，是最穩的整合方式 |
| **SwitchTender**（2026 新） | 單一 Go binary，可跑 Ansible/Terraform/Bash，hash-chained audit，可一鍵從 AWX/Semaphore 匯入 | 趨勢訊號：**輕量單 binary + 可驗證的審計鏈**；migration 工具是搶既有使用者的手段 |

### 2.2 Kubernetes 管理 GUI 類

| 案例 | 定位 | 學到的借鏡點 |
|---|---|---|
| **Rancher**（SUSE） | 多叢集 K8s 管理平台，agent 架構 | 多叢集接入架構可參考；但它不管 OS 層，正好是我們的互補位 |
| **Headlamp**（CNCF） | 輕快、plugin 化的 K8s dashboard | **UX 標竿**：資源瀏覽速度快、plugin 機制值得學；不做節點維運 |
| **Portainer** | Docker/K8s 通用 GUI，簡單好上手 | 證明「簡單」有市場；但 K8s 進階功能多在付費版（BE） |
| **KubeSphere** | 全功能分散式 OS 級 K8s 平台 | 反面教材：功能大而全 = 部署重、學習曲線陡；我們反其道而行 |

### 2.3 單機伺服器管理類

| 案例 | 定位 | 學到的借鏡點 |
|---|---|---|
| **Cockpit**（Red Hat） | 瀏覽器管單台 Linux（:9090），plugin 擴充儲存/網路/VM/容器，可納管多台 | **「每台機器的健康視角」**（服務、儲存、日誌、終端機）是我們節點詳情頁的參考；但它是單機視角、無編排 |

### 2.4 Ansible 管 K8s Worker Node 的實務模式（社群驗證過的做法）

收集到的 homelab / 實務 playbook 幾乎都是同一套流程，可直接做成產品內建工作流：

1. `kubectl cordon` → 2. `kubectl drain --ignore-daemonsets --delete-emptydir-data` → 3. 升級 kubeadm / kubelet / containerd（或重跑 k3s installer）→ 4. `systemctl restart kubelet` → 5. 等待 Node Ready → 6. `kubectl uncordon`
- 關鍵參數：**逐台執行（`serial: 1`）、任一失敗即停（`max_fail_percentage: 0`）**、升級前先驗證 control plane 健康
- 版本相容規則：control plane ↔ kubelet 允許 ±1 minor version

### 2.5 結論：市場空隙

> **AWX（太重、無 K8s 視角）← 空隙 → Rancher（不管 OS 層）**
>
> 沒有一個輕量工具同時做到：「Ansible 任務編排 GUI」＋「K8s worker node 的 OS 層維運視角」。這就是本專案的差異化位置。

---

## 3. 功能規劃

### 模組總覽

```
┌──────────── 呈現層 ────────────┐
│ M9 儀表板                      │
├──────────── 核心功能 ──────────┤
│ M1 主機資產/Inventory          │
│ M2 Playbook 管理               │
│ M3 任務執行引擎（Job/排程/審批）│
│ M4 Kubernetes 叢集視圖         │
│ M5 Worker Node 維運 ★差異化核心 │
├──────────── 基礎能力 ──────────┤
│ M6 即時日誌與審計              │
│ M7 憑證與 RBAC                 │
│ M8 告警通知                    │
└───────────────────────────────┘
```

### M1 — 主機資產 / Inventory 管理

- 靜態 inventory：手動新增主機、群組、host/group 變數（相容 Ansible inventory 語義）
- K8s 節點自動同步：從叢集 API 讀取 nodes，依 label/taint/role 自動分群，節點增減自動反映到 inventory
- 動態 inventory（Phase 2）：支援 Ansible dynamic inventory plugin（雲主機、NetBox）
- 主機健康：SSH 可達性探測、OS / kernel 版本盤點

### M2 — Playbook 管理

- 以 Git repo 為 source of truth（對應 AWX 的 Project 概念）：分支/tag 切換、異動自動同步
- Galaxy roles / collections 管理
- 變數管理＋ Ansible Vault 加密變數
- 內建官方工作流模板：節點 OS patch、kubelet 升級、rolling reboot（見 M5）

### M3 — 任務執行引擎

- **Job Template**：playbook ＋ inventory ＋ 憑證 ＋ 參數表單（survey）綁成可重複執行的模板
- Ad-hoc command：臨時對一批主機下指令
- 排程執行（cron-like）：例行 patch window
- 並發與批次策略：forks、serial 逐台、失敗即停
- **審批流程**：高風險任務（drain、重啟、升級）執行前需核准（學 Rundeck）
- 執行環境隔離：容器化 execution environment，playbook 跑在乾淨環境（學 AWX）

### M4 — Kubernetes 叢集管理（唯讀為主＋受控操作）

- 多叢集接入（kubeconfig 匯入），叢集總覽：版本、節點數、異常 Pod
- 節點列表：角色、K8s 版本、kubelet/containerd 版本、Ready 狀態、資源使用率、labels/taints
- 受控操作（走審批＋ Ansible 或 K8s API）：cordon / drain / uncordon
- 工作負載唯讀視圖（UX 參考 Headlamp，不重做完整 CRUD）

### M5 — Worker Node 維運 ★（差異化核心）

- **一鍵節點維護工作流**：cordon → drain → OS patch / kubelet 升級 → 健康驗證 → uncordon，全程逐台 serial、失敗即停、可隨時暫停
- **版本漂移偵測**：比對「期望版本」（inventory 變數宣告）vs 節點實際版本，列出待升級清單
- OS 層：安全更新、kernel 升級後的 rolling reboot 編排
- K8s 元件：kubeadm / kubelet / kubectl / containerd 版本管理
- 維護前快照檢查清單：PDB 是否會擋 drain、單副本 workload 警告

### M6 — 即時日誌與審計

- WebSocket/SSE 即時串流 ansible 輸出（ansible-runner event 機制）
- 歷史紀錄：每次執行的完整 log、變更 diff、執行者
- 審計鏈：hash-chained、不可竄改（學 SwitchTender，之後可做離線驗證）

### M7 — 憑證與 RBAC

- 憑證種類：SSH key、帳密、Vault 密碼、kubeconfig、雲端憑證
- RBAC：組織 / 團隊 / 角色，控到「誰能對哪群主機跑哪個 template」

### M8 — 告警通知

- 任務成功/失敗、節點異常、漂移偵測 → Webhook / Email / Slack / LINE（台灣團隊常用）

### M9 — 儀表板

- 任務成功率趨勢、節點健康分佈、待升級/待 patch 節點清單

---

## 4. 技術架構建議（初稿，待討論）

| 層 | 建議 | 理由 |
|---|---|---|
| 前端 | React + TypeScript | 你熟（WebTracker 同棧），手機版已驗證過可用同一套做 RWD |
| 後端 | Python (FastAPI) | 直接用官方 **ansible-runner** 函式庫整合執行層（O-X-L ansible-webui 已驗證此路線）；生態系對 Ansible 最友善 |
| 任務佇列 | RQ / Celery + Redis | Job 非同步執行、排程、可重試 |
| 資料庫 | PostgreSQL（開發期可用 SQLite 起步） | AWX/Semaphore 同級選擇 |
| 即時輸出 | WebSocket / SSE | 串流 ansible-runner events |
| 執行隔離 | 容器化 Execution Environments | 學 AWX，避免污染主機 Python 環境 |
| K8s 接入 | kubernetes Python client，多叢集 kubeconfig | 讀 node/pod 資訊、執行 cordon/drain |
| 部署 | docker-compose 一鍵起；長期朝單 binary / 單容器映像 | 學 Semaphore 輕量路線，降低採用門檻 |

替代方案（開放討論）：後端用 Go（學 Semaphore，部署更輕但要自己刻 Ansible 呼叫層，不如 Python 順手）。

---

## 5. Roadmap

### Phase 1 — MVP（驗證核心價值）
- M1：靜態 inventory ＋ K8s 節點自動同步
- M2：Git repo 同步 playbook
- M3：Job Template 執行 ＋ 即時日誌
- M4：單叢集節點列表（唯讀）
- M5：一鍵節點維護工作流（cordon→drain→patch→驗證→uncordon）
- M7：基礎憑證管理（SSH key / kubeconfig）

**MVP 驗收標準**：在 homelab 對 3 台 worker 做一次有人值守的 rolling OS patch，全程在 GUI 完成、零人肉 SSH。

### Phase 2 — 團隊可用
- 排程、審批流程、RBAC
- 多叢集管理
- 動態 inventory、版本漂移偵測
- 通知（LINE / Slack / Email）

### Phase 3 — 強化
- 審計鏈、儀表板、工作負載視圖
- Windows / WinRM（若有需求）
- AWX/Semaphore 匯入工具（搶既有使用者，學 SwitchTender）

---

## 6. 開放問題（待你拍板）

1. 專案名稱？（之後建 repo 用）
2. 後端語言：Python (FastAPI) vs Go？（我傾向 Python，ansible-runner 是決定性因素）
3. 第一驗證場景：家裡 homelab 的 k3s，還是直接對工作場景設計？
4. 要不要順手支援 Proxmox / VM 層？（Cockpit-machines 那類需求，homelab 很常見）

---

## 參考連結

- AWX vs Semaphore vs Rundeck 比較：https://roethof.net/posts/2025/07/ansible-automation-ecosystem-comparison/
- ansible-webui（Django + ansible-runner 實作參考）：https://github.com/o-x-l/ansible-webui/blob/HEAD/docs/source/getting_started/1_intro.rst
- SwitchTender（單 binary 新趨勢）：https://github.com/kordloom/switchtender
- K8s GUI 競品對照（kweblens 整理）：https://github.com/alexmond/kweblens/blob/HEAD/docs/competitive-review/competitor-analysis.md
- Rancher vs Headlamp 選型心得：https://medium.com/@kumar.vaibhav0501/rancher-vs-headlamp-choosing-the-right-kubernetes-dashboard-for-your-workflow-0662d460f061
- Ansible rolling 升級 k3s 實務：https://github.com/mainertoo/kubernetes-lab/blob/HEAD/ansible/README.md
- Cockpit（單機管理 GUI 參考）：https://wiki.ArchLinux.org/title/Cockpit
