# M2 — Playbook 管理

> 目標：Git 是唯一的 playbook 來源；UI 只做「同步、瀏覽、檢查、發佈」，不做線上編輯器（編輯回 Git，這是刻意設計）。

## 2.1 Git Project 同步

**說明**：對應 AWX 的 Project 概念，一個 Project 就是一個 git repo。

**使用場景**：Dylan 把維運 playbook 推到 GitHub，系統自動同步，新的 `os-security-update.yml` 出現在 Playbook 列表可直接建 Job Template。

**功能細節**
- 欄位：名稱、git URL、分支/tag（預設 main）、憑證（private repo 用 deploy key，存 M7）、同步排程（預設每次執行前檢查＋定時）
- 同步時列出 repo 內的 playbook 檔案（`*.yml` 且含 `hosts:` 的啟發式判斷＋手動標記）
- repo 有新 commit 可選自動同步或只發通知；同步失敗（認證/網路）要明確報錯
- 每個 Project 記錄目前同步到的 commit hash，任務歷史關聯它（可追溯「這次執行用的是哪版 playbook」）

**驗收標準**：push 新 playbook 後 1 分鐘內出現在 UI；任務歷史能查到執行時的 commit hash。

## 2.2 Playbook 瀏覽與語法檢查

**說明**：執行前先看、先檢查，減少低級失敗。

**功能細節**
- 樹狀瀏覽 repo 檔案；點 playbook 看 YAML（唯讀＋語法高亮）
- 「語法檢查」按鈕跑 `ansible-playbook --syntax-check`，錯誤標出行號與訊息
- 靜態解析摘要：hosts、tasks 數量、引用的 roles、有無 `become: yes`（高風險標示）
- 建 Job Template 時自動帶入解析到的變數作為參數表單初稿（M3.1）

**驗收標準**：一個縮排錯誤的 playbook，檢查後 5 秒內指出行號。

## 2.3 Galaxy roles / collections 管理

**說明**：playbook 的依賴也要版本化，否則換環境就跑不起來。

**功能細節**
- 每個 Project 可附 `requirements.yml`，UI 可編輯（這是少數允許線上改的，因為它單純）
- 安裝/更新按鈕，版本鎖定；安裝結果寫入執行環境建置（關聯 M3.4）
- 顯示已安裝 collections 清單與版本（ansible-galaxy collection list）

## 2.4 變數與 Vault

**說明**：三層變數＋加密變數，UI 永不洩漏明文。

**功能細節**
- 變數層級：Project 預設 → 群組 → 主機（與 M1.2 的 effective vars 預覽打通）
- 兩種編輯模式：表單（key/value）與 YAML 原始模式
- Vault：vault 密碼存在 M7 憑證；加密值在 UI 顯示為 `!vault（已加密）`，任何列表/日誌都不出現明文
- 「新增加密變數」流程：在 UI 輸入明文 → 後端加密 → 只存密文（明文不落地、不進 log）

**驗收標準**：用關鍵字搜尋整個 UI（含日誌），找不到任何 vault 明文。

## 2.5 內建官方模板

**說明**：開箱即用的最佳實踐，降低第一次使用的門檻，也是 M5 工作流的預設 playbook。

**功能細節**
- 隨系統附帶：`os-security-update`、`kubelet-upgrade`、`rolling-reboot`、`node-init`（新節點初始化：建使用者、裝 containerd、關 swap 等）
- 每個模板附參數說明文件與合理預設值；使用者「複製為我的」後可修改，官方模板本身唯讀
- 模板版本跟著系統更新，更新時提示 diff

## 2.6 版本 diff（P1）

**說明**：回答「這次執行跟上次差在哪」。

**功能細節**
- 同一 playbook 兩個 commit 的 diff 檢視（沿用 git diff）
- 任務歷史頁直接顯示「本次執行 vs 上次成功執行」的 playbook diff 連結

---

**本模組 Non-goals**：線上 YAML 編輯器、playbook 市集/分享平台。
