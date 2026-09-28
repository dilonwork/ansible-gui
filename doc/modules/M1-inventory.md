# M1 — 主機資產 / Inventory 管理

> 目標：所有被管機器的單一 truth 來源；K8s 節點自動同步進來後，維運視角與 Ansible 執行視角用同一份 inventory。

## 1.1 主機 CRUD

**說明**：主機是最小管理單位。每台主機記錄連線資訊與歸屬，作為所有任務的執行目標。

**使用場景**：Dylan 在 homelab 新增一台 worker，先填 IP 與 SSH 資訊，點「測試連線」確認 ansible ping 通，再歸到 `k3s-workers` 群組。

**功能細節**
- 欄位：顯示名稱、hostname/IP、SSH port（預設 22）、SSH 使用者、憑證（可選，留空則繼承群組/全域）、群組歸屬（多選）、標籤、備註
- 新增/編輯時提供「測試連線」：實際跑一次連通測試，失敗要分類報錯（連線逾時 / 認證失敗 / 主機不可達），不要只丟 raw traceback
- 刪除主機需二次確認；若主機屬於執行中的任務目標，阻擋刪除並提示
- 主機列表支援關鍵字搜尋、依群組/狀態篩選

**資料模型要點**：`hosts(id, name, address, port, ssh_user, credential_id, group_ids[], vars JSON, tags[], reachable, last_seen)`

**驗收標準**：新增主機後 10 秒內顯示可達性狀態；三種連線失敗各有明確中文錯誤訊息。

## 1.2 群組管理

**說明**：群組是任務的批量操作單位，支援巢狀，變數遵循 Ansible 優先級語義。

**使用場景**：`all → k8s-cluster → k3s-workers` 三層；K8s 版本期望值寫在 `k8s-cluster` 群組變數，所有 worker 繼承，單台特例寫在主機變數覆蓋。

**功能細節**
- 巢狀群組（parent/children），變數解析順序：all → 父群組 → 子群組 → 主機
- 主機頁提供「有效變數預覽」（effective vars），把繼承鏈攤平給人看，出問題好除錯
- K8s 同步產生的群組標示「自動同步」唯讀鎖定（見 1.4），避免手改被覆蓋

**驗收標準**：三層變數覆蓋結果與命令列 `ansible-inventory --host` 一致。

## 1.3 SSH 可達性探測

**說明**：背景心跳，儀表板「在線率」KPI 的資料來源。

**功能細節**
- 每 N 分鐘（可設，預設 5）對全部主機做 TCP 22＋SSH 握手探測，輕量不跑完整 ansible
- 狀態翻轉（在線↔離線）寫事件，供 M8 通知（P1）與儀表板使用
- 主機詳情顯示最近 24 小時在線率小趨勢

**驗收標準**：關閉一台測試機，5 分鐘內狀態變為離線；恢復後自動翻回在線。

## 1.4 K8s 節點自動同步（P0 核心）

**說明**：把 K8s 叢集的 nodes 自動映射成 inventory 主機/群組，是「K8s 視角＋Ansible 執行」合體的關鍵。

**使用場景**：叢集擴容加了 `k3s-worker-04`，60 秒內它自動出現在 inventory 的 worker 群組，版本漂移偵測（M5.4）立刻能看到它的版本。

**功能細節**
- 綁定叢集後每 60 秒（可設）從 K8s API 讀取 nodes
- 映射規則：node name → 主機名；連線 IP 優先用 annotation 宣告（如 `ops.ansible-gui/ssh-ip`），沒有則用 InternalIP，需手動補的標示「待補連線資訊」
- 自動分群：依 `node-role.kubernetes.io/*` 分 control-plane/worker；自訂 label 轉群組規則可設定（如 `gpu=true` → `gpu-nodes`）
- 節點離開叢集：標示「已離開叢集」但不直接刪除，需手動確認移除（防誤刪）
- 同步欄位唯讀鎖定，UI 明確標示來源叢集

**驗收標準**：叢集增刪 node，60 秒內 inventory 正確反映；手動改自動同步群組時被阻擋並提示原因。

## 1.5 動態 inventory（P1）

**說明**：接外部來源當 inventory，不再手動建主機。

**功能細節**
- 支援 Ansible 官方 dynamic inventory plugin（aws_ec2、azure_rm 起步）
- NetBox 連接器：以 NetBox 為 source of truth 定期同步
- 每個動態來源獨立設定同步排程＋手動立即觸發；同步結果有 diff 預覽（新增/刪除/變更）再套用

## 1.6 OS / kernel 盤點（P1）

**說明**：定期收集 facts，回答「這批機器 OS 版本分佈」與「誰有安全更新沒裝」。

**功能細節**
- 每天一次（可設）對群組跑 facts 收集：distro 版本、kernel、待更新套件數、安全更新數
- 儀表板「待 OS 更新」數字與 M5 維護佇列的資料來源
- 盤點結果保留歷史，可看單台機器的版本變化線

---

**本模組 Non-goals**：CMDB 財務/保固管理、自動化 OS 安裝（PXE/映像派送）。
