# M4 — Kubernetes 叢集管理

> 目標：多叢集的唯讀可觀測＋受控的節點操作（cordon/drain/uncordon）；工作負載只看不改，改機器的事交給 Ansible。

## 4.1 叢集接入

**說明**：把 kubeconfig 交給系統，後續所有 K8s 視角都從這裡來。

**使用場景**：Dylan 貼上 homelab k3s 的 kubeconfig，系統驗證連通後，叢集出現在頂欄切換器，節點開始同步進 inventory（M1.4）。

**功能細節**
- 匯入方式：貼上 YAML / 上傳檔案；解析後顯示叢集名稱、server 位址、憑證到期日預檢
- 連通性檢測：實際打一次 API（列出 nodes），失敗分類報錯（憑證過期 / 網路不通 / RBAC 權限不足）
- kubeconfig 以憑證形式存 M7（加密落盤，UI 不回傳明文）；支援多叢集並存，各自獨立憑證
- 憑證即將過期（30 天內）發提醒（經 M8）

**驗收標準**：貼上過期憑證的 kubeconfig，明確提示「憑證已過期」而非泛用連線錯誤；刪除叢集後其同步產生的 inventory 標示一併清理。

## 4.2 叢集總覽

**說明**：一屏回答「這個叢集現在健康嗎」。

**功能細節**
- 卡片欄位：K8s 版本、節點 Ready x/y、異常 Pod 數（CrashLoopBackOff、Pending、ImagePullBackOff 分類計數）、API 延遲
- 異常 Pod 按 namespace 分組取 top 5，點入跳 4.5 工作負載視圖（P1 前先給 kubectl 指令提示）
- 更新頻率 30 秒；P0 用 polling，P1 切 watch＋WebSocket 推送
- 多叢集時可並排對比（儀表板設計稿的雙卡片即此規格）

**驗收標準**：手動 cordon 一台 node，30 秒內總覽 Ready 數正確變化；API 斷線時卡片顯示「資料過期」而非靜默展示舊數字。

## 4.3 節點列表

**說明**：Worker 節點的作戰地圖；儀表板第一屏的核心表格即此規格的精簡版。

**使用場景**：升級前 Dylan 篩出「版本低於 v1.31.2 的 worker」，全選加入維護佇列（M5.6）。

**功能細節**
- 欄位：主機名、角色（control-plane/worker，來自 label）、K8s 版本、kubelet 版本、containerd/CRI 版本、Ready 狀態、CPU/記憶體使用率、OS 映像、運行時間
- 篩選：角色、狀態（Ready/NotReady/漂移）、版本、群組；關鍵字搜尋主機名
- 點主機名 → 節點詳情抽屜：完整 labels/taints、conditions 時間線、已分配資源（allocatable vs requests）、最近 Events、SSH 連線資訊快捷入口
- 版本漂移 badge：實際版本低於群組期望版本（M5.4）時琥珀色標示，該列操作按鈕變「升級」

**驗收標準**：14 節點列表載入 < 2 秒；篩選條件可存成常用視圖（如「待升級 worker」）。

## 4.4 cordon / drain / uncordon

**說明**：節點維護的標準前置動作，做成受控操作而非裸 kubectl。

**使用場景**：rke2-worker-04 要換硬碟，Dylan 在節點列表點「drain」，系統先警告有 2 個 Pod 受 PDB 保護會卡住，確認後執行並即時顯示驅逐進度。

**功能細節**
- drain 前預檢並顯示：將被驅逐的 Pod 數、是否會被 PDB 阻擋（列出阻擋的 PDB）、DaemonSet 會被忽略的提示
- 參數：`--ignore-daemonsets`、`--delete-emptydir-data` 預設開啟並明確標示；grace period 可調；timeout 預設 300 秒
- 高風險操作走審批（M3.6，若 template/操作被標記）；所有操作寫入審計（M6.3）
- drain 進度即時顯示（已驅逐 x/y Pod），卡住時可取消；uncordon 一鍵恢復調度

**驗收標準**：drain 一台有 PDB 保護的測試節點，預檢正確警告且執行時尊重 PDB（不強殺）；操作全程可在審計查到「誰、何時、對哪台」。

## 4.5 工作負載唯讀視圖（P1）

**說明**：出問題時快速定位「哪個 workload 在鬧」，但不提供修改（修改走 GitOps/CI，那是別的工具的事）。

**功能細節**
- 列表：Deployment / StatefulSet / DaemonSet / Pod，欄位含狀態、就緒副本 x/y、重啟次數（重啟次數高亮排序，方便抓 CrashLoop）
- 點 Pod 看最近 200 行 logs、多容器可切換；點 Deployment 看 events
- namespace 篩選＋關鍵字搜尋；預設隱藏 kube-system（可展開）以降低噪音

**驗收標準**：一個 CrashLoopBackOff 的 Pod，能在 3 次點擊內看到它的 log。

## 4.6 節點事件時間線（P2）

**說明**：把散在各處的 Events 按節點聚合，方便回溯「這台機器昨天發生什麼事」。

**功能細節**
- 按節點聚合近 7 天 Events：驅逐、OOMKilled、磁碟壓力、kubelet 重啟等，依嚴重度著色
- 與 M6 任務歷史交叉引用：同一時間段在該節點跑過的任務自動標註在時間線上（「這次 NotReady 前 10 分鐘跑過 OS patch」）

---

**本模組 Non-goals**：工作負載的建立/修改/刪除、Helm 管理、GitOps、叢集建立（kubeadm/k3s 安裝向導）。
