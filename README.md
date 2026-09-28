# ansible-gui

以 GUI 整合 Ansible 的遠端伺服器管理平台，核心場景是 **Kubernetes 叢集與 Worker Nodes 的維運**（OS patch、kubelet 升級、cordon/drain 編排）。

> 目前階段：📋 規劃中（planning）— 功能規劃與設計稿已完成，尚未開始寫 code。

## 為什麼做這個

管伺服器的人手上有兩套割裂的工具：

- **Ansible GUI**（AWX、Semaphore）只管 playbook 執行，看不到 K8s 節點狀態
- **K8s 管理 GUI**（Rancher、Portainer、Headlamp）只管叢集內資源，管不到 OS 層

中間缺了一塊：**「知道 K8s 節點狀態，又能用 Ansible 對 OS 層動手」的維運平面**。AWX 太重、Semaphore 太輕無 K8s 視角 — 這個專案填補中間的空隙。

## 文件

| 文件 | 說明 |
|---|---|
| [doc/planning.md](doc/planning.md) | 功能規劃 v0.1：專案定位、成功案例收集、模組規劃、技術架構、Roadmap |
| [doc/feature-list.md](doc/feature-list.md) | 詳細功能清單（P0/P1/P2 優先級） |
| [doc/dashboard-design.md](doc/dashboard-design.md) | 儀表板設計說明 |
| [doc/mockups/dashboard.html](doc/mockups/dashboard.html) | 儀表板設計稿（瀏覽器開啟） |
| [doc/mockups/dashboard.png](doc/mockups/dashboard.png) | 儀表板設計稿截圖 |

## 規劃中的核心功能

- 🖥️ 主機 Inventory（靜態＋從 K8s 自動同步 worker nodes）
- 📜 Playbook Git 同步 ＋ Job Template ＋ Ad-hoc command
- ☸️ 多叢集節點總覽（版本、Ready 狀態、資源使用率）
- 🛠️ **一鍵節點維護工作流**：cordon → drain → patch/升級 → 驗證 → uncordon（逐台、失敗即停）
- 📊 版本漂移偵測、即時任務日誌、審批、排程、RBAC

## Roadmap

- **Phase 1 — MVP**：inventory＋K8s 節點同步、Job Template 執行、一鍵節點維護工作流；驗收標準是在 homelab 對 3 台 worker 做一次全程 GUI 的 rolling OS patch
- **Phase 2 — 團隊可用**：排程、審批、RBAC、多叢集、漂移偵測、通知
- **Phase 3 — 強化**：審計鏈、容量趨勢、AWX/Semaphore 匯入工具

## 預定技術棧（待定）

前端 React＋TypeScript ／ 後端 Python（FastAPI）＋官方 `ansible-runner` ／ PostgreSQL ／ Redis ／ docker-compose 一鍵部署。
