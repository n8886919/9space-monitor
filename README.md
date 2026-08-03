# 9Space Monitor

`9space-monitor` 是此專案的唯一主要 repository，整合：

- Home Assistant custom integration：`custom_components/nvr_monitor`
- Home Assistant local add-on：`9space_snapshot_api`
- M5 Center telemetry server 與 dashboard template
- 單一站點的手動 SSH 部署文件

## 先讀順序

所有開發工具（GitHub Copilot、Codex、Claude）都使用相同入口：

1. `README.md`：目標、架構、目前里程碑與工作順序
2. `AGENTS.md`：修改規則、禁止事項與驗證要求
3. `API.md`：現有 API 相容性與 integration ↔ add-on 契約
4. `DEPLOY.md`：單一站點手動 SSH 部署
5. `docs/SESSION_HANDOFF.md`：下一個 session 的目前狀態、決策與工作順序

`DESIGN_NOTES.md` 與 `ADDON_DEVELOPMENT_PLAN.md` 已由以上四份文件取代，不再是 active specification。

## 目前目標

目前先以承德作為原型，之後擴展至約十個站點：

1. 將 `9space-snapshot-addon` 內容移入本 repository。
2. 保持既有 snapshot API 相容，不能影響正在使用它的同事。
3. Add-on 成為唯一直接存取 Dahua NVR 的元件。
4. Integration 不再保存或使用 NVR host、port、username、password。
5. Integration 經 local add-on API 取得：
   - NVR channel 是否有實際影像
   - 最近錄影狀態
   - 不取得 snapshot；M5 Center 不處理影像，未來若需要統一取圖須另行批准
6. 攝影機 Ping 與硬體資訊沿用 Home Assistant 既有 integrations；`nvr_monitor` 只讀取白名單 entity 並送往 Center。
7. Add-on 以非阻塞 batch push 將 NVR telemetry 傳給 Center；不在 add-on 儲存 telemetry history。
8. Center 以 Docker/SQLite 保存最近七天診斷資料，供多站點 dashboard 使用；以 logical 保守水位搭配 2 GiB 實體檔 fail-closed guard 保護主機容量，預設細節待 Center 實作完成後核對。
9. 由使用者與 AI agent 透過 SSH 手動部署到測試站點；8122 舊正式 instance 永久排除。

## 架構邊界

```text
目前：

Home Assistant
├── nvr_monitor integration
│   ├── 建立 entities、config flow、diagnostics
│   ├── 可保留必要的攝影機區域網路 service probe
│   ├── 不直接連接 NVR
│   └── 不保存 NVR credentials
│
└── 9space_snapshot_api add-on
    ├── 唯一保存 NVR credentials
    ├── NVR RTSP live-video probe
    ├── Dahua recording query
    ├── snapshot 與 cache
    ├── local API
    └── async NVR telemetry push（M5）
            │
            ▼
        Dahua NVR

Center（M5）
├── Docker service（port 8765）
├── SQLite：最近七天、單站 logical 保守水位、全域 2 GiB 實體檔 fail-closed guard
├── 接收 add-on NVR telemetry 與 integration HA telemetry
└── 不保存、傳輸或匯出 JPEG
```

### M5 Center topology

```text
各站點 add-on ──push──┐
各站點 integration ──push──┼── Tailscale ──> Center（Docker/SQLite）
                           │
                           └── 只保留診斷 metadata，不含影像
```

必須保留的 TODO：

- [ ] M5A 建立 Center Docker/SQLite skeleton、七日 retention 與容量限制。
- [ ] M5B Add-on push NVR telemetry；producer 不寫 history 磁碟。
- [ ] M5C Integration push allowlisted HA Ping/System Monitor/RPi Power/Fast.com telemetry。
- [ ] M5D 依各站手動 mapping 產生 dashboard YAML（NVR、Ping、診斷三張表）。
- [ ] 未來才決定 Center 取圖與同事 legacy client migration；M5 不傳送 JPEG。

## M5 明確不做

- 多站點自動部署
- GitHub Actions 部署 pipeline
- 自動 config-entry migration
- 修改 Home Assistant `.storage`
- 客製 Lovelace card
- 錄影回放
- 新的 live-stream 技術
- 自製 Ping、RTT、jitter 或 packet-loss probe（只讀 Home Assistant 既有 entity）
- 大型效能測試矩陣
- 複雜的 release／checksum／environment approval 系統
- Repository 改名或 add-on slug 變更

## Repository 結構

```text
9space-monitor/
├── 9space_snapshot_api/
│   ├── config.yaml
│   ├── Dockerfile
│   ├── run.sh
│   ├── requirements.txt
│   ├── src/
│   └── tests/
├── custom_components/
│   └── nvr_monitor/
├── tests/
├── scripts/
├── AGENTS.md
├── API.md
├── DEPLOY.md
└── README.md
```

實際移入 add-on 時，保留 Home Assistant add-on repository 所需的第一層 add-on 目錄。不要同時重新命名 slug、port 或 options。

## Current milestone

> **Port／來源說明（2026-08 起）**：`8122` 是同事目前使用的正式 instance，來源是**獨立的舊 repo**（不是這個 monorepo），本專案禁止修改或停止它。`8222` 是**這個 monorepo**（`9space_snapshot_api/`）建置出來的開發 instance，M2 系列開發與測試都只針對 `8222`。兩者的 add-on slug 目前都叫 `9space_snapshot_addon`，但分屬不同 repository，Supervisor 會依 repository 分別加上前綴以區分。本 repository 只保留一份 add-on source（`9space_snapshot_api/`），不再有 `_v2` 資料夾或 `_v2` slug。

### M1：合併 repository，不改行為（已完成）

- [x] 將 `9space-snapshot-addon/9space_snapshot_api` 複製到本 repository 根目錄。
- [x] 保留 add-on slug `9space_snapshot_addon`。
- [x] 保留 container port `8000`。
- [x] 保留舊 `GET /api/camera/{camera_id}` response。
- [x] 確認 add-on 可 build、start，舊 endpoint 可取得 snapshot。
- [x] 不修改 integration 行為。

### M2A：Add-on API 骨架（合併進 canonical `9space_snapshot_api`，開發 port 8222）

開發與測試只使用這個 monorepo 的 `9space_snapshot_api`（開發 instance，預設 host port `8222`）。同事正在使用的正式 instance（獨立舊 repo，port `8122`）不受本 repository 影響，不修改、不停止。

- [x] `9space_snapshot_api` 新增 `/healthz`、`/api/v1/channels`、`/api/v1/channels/{channel_id}`、`/api/v1/channels/{channel_id}/snapshot`。
- [x] 新增 options：`nvr_http_port`（Dahua NVR 自己的 HTTP/CGI port，預設 `80`，與 add-on 監聽 port 無關）、`channel_count`（預設 `14`）。add-on 本身固定監聽 container port `8000`（開發 instance 預設 host port `8222`），不可由任何 option 改變。
- [x] 舊 `GET /api/camera/{camera_id}` 的 path、status code、JSON、multipart 與 JPEG response 完全不變（含 semaphore busy 時的 HTTP 503），並以測試鎖定。
- [x] `/api/v1/channels/{channel_id}*` 對任何小於 1 或大於 `channel_count` 的 channel（含 0、負數）統一回 HTTP 404 `{"error_code":"channel_not_found"}`。
- [x] 不搬 NVR RTSP live-video probe（`live_video` 目前固定回 `null`）。
- [x] 不搬 Dahua recording query（`recording_query_ok`／`recording_recent`／`last_recording` 目前固定回 `false`／`null`）。
- [x] 不修改 integration。
- [x] 不部署，只在本機以 fake-based unit tests 驗證。

完成後再進 M2B。

### M2B：搬移 NVR live probe 與 recording query（已完成）

- [x] 把 NVR RTSP live-video probe 搬到 add-on（`live_probe.py`，沿用 integration 原本的 DESCRIBE/SETUP/PLAY/RTP 判斷邏輯），預設每 300 秒執行一次。
- [x] 把 Dahua recording query 搬到 add-on（`recording_query.py`，沿用 integration 原本的 `mediaFileFind.cgi` 查詢與 24 小時判定邏輯，含 Asia/Taipei 本地時間假設），預設每 900 秒執行一次。
- [x] add-on 啟動後立即執行第一輪，不等待完整 interval。
- [x] `/api/v1/channels*` 改為只讀取 add-on 保存的最新背景結果（`channel_state.py` 的 in-memory store），不讓每次 GET 對 channel 同步執行探測。
- [x] 背景探測共用固定單一 semaphore（併發 1）；單一 channel 失敗或逾時不影響其他 channel、也不中止整批。
- [x] Snapshot 維持 demand-driven 與現有 cache 行為（M2A 已共用同一套 cache/capture 路徑，M2B 不重做）。
- [x] `max_concurrency` 僅為既有 Snapshot options 相容而保留；Snapshot ffmpeg runtime hard cap 固定為 1，不能提高抓圖併發。
- [x] Add-on 對外 response 不包含 credentials、RTSP URL、CGI request URL 或完整 ffmpeg/CGI stderr／body。
- [x] 使用 fake-based unit tests（`test_background_probes.py`），不要求真實 NVR、Docker 或 HAOS。
- [x] 背景 task 使用 FastAPI `startup`／`shutdown` event 管理；shutdown 會 cancel 並 await，不留孤兒 task。

M2B 已隨後在 monorepo add-on 完成實機驗證；8122 獨立舊正式 instance 未受影響。

### M3：Integration 改用 add-on

- [x] 建立簡單 async local API client。
- [x] Integration config flow 改為設定 add-on base URL。
- [x] Integration 不再要求或保存 NVR credentials。
- [x] 移除 integration 內 NVR RTSP、Dahua HTTP recording client。
- [x] 移除自製 Ping coordinator 與 `icmplib` dependency。
- [x] 保留仍有必要的攝影機區域網路 service probe；不要在同一階段重寫。
- [x] Entities 名稱及 unique ID 儘量保持不變，降低 Dashboard 破壞。
- [x] Add-on unavailable 時，相關 entities 應變成 unavailable，不阻塞 HA event loop。

### M4：手動部署與實機 smoke test（已完成）

依 `DEPLOY.md`：

- [x] Monorepo add-on 與 integration 已部署並實機驗證。
- [x] Integration 不建立 Snapshot camera entity；舊殘留 entity 已由使用者移除。
- [x] Home Assistant Ping integration 負責攝影機在線狀態。

### M5：多站 telemetry 與 Center（進行中）

- [ ] 約十站；每站 channel count 與 entity IDs 均由 site mapping 手動定義，不假設 `01`～`14`。
- [ ] Add-on push NVR telemetry：live/RTSP、recording query、24 小時統計與診斷 metadata。
- [ ] Integration push allowlisted HA telemetry：Ping、System Monitor、RPi Power、Fast.com。
- [ ] 兩種 producer 均只用 24 小時 RAM ring 與 bounded memory queue；Center 失聯時可丟棄資料，不寫 telemetry history 到磁碟。
- [ ] Center Docker/SQLite：七日 retention、單站 logical 保守水位、2 GiB 實體檔 fail-closed guard，預設 port `8765`；具體預設值待實作完成後核對。
- [ ] 永久不保存、傳輸、匯出或在 log 中輸出 JPEG。
- [ ] Dashboard renderer 依 site mapping 產生可貼入 HA UI 的 YAML，分為 NVR/recording、Ping/network、diagnostics。
- [ ] 承德原型使用 `site_id: chengde`、顯示名稱「承德」；Tailscale 內網運作，M5 不新增 per-site token。

## 最小測試

自動測試只要求：

1. 舊 endpoint response 相容。
2. 新 channel endpoint 可用 fake NVR result 回 JSON。
3. 新 snapshot endpoint 回 `image/jpeg`。
4. Integration API client 可解析 channel response（不解析或呼叫 Snapshot）。
5. Integration source/config flow 不再使用 NVR password。
6. Integration manifest 不再依賴 `icmplib`。

實機只要求：

- Add-on 啟動。
- 舊 snapshot endpoint 正常。
- 新 local endpoint 正常。
- 14 個 channel 狀態可讀。
- Integration entities 正常。
- NVR 暫時無法連線時，HA 不被阻塞。
- 回復 NVR 後不需重裝元件。

## 完成定義

本階段完成代表：

- Add-on 與 integration 已位於 `9space-monitor`。
- Add-on 是唯一直接存取 NVR 的程式。
- Integration 不保存 NVR credentials。
- 舊 snapshot API 不變。
- Integration 能透過 local API 顯示既有核心狀態，但不建立 Snapshot camera entities。
- M5 Center 僅處理 telemetry；任何未來統一取圖須另案設計與批准。
- Ping 使用 Home Assistant 現成 integration。
- 單一站點可由 AI agent 依文件手動部署；M5 一般 patch 不強制 rollback。
- Center telemetry push 與 legacy local API 的邊界已被保留。

不要求多站點自動部署或正式 release pipeline。M5 的 Center 是診斷資料服務，不處理影像。

## 給 AI agent 的啟動指令

不要只說「讀 README 並完成全部」。

建議依序給三次任務：

```text
讀 README.md 與 AGENTS.md，只完成 M1。不要修改行為、不要部署。完成後回報 diff 與驗證結果。
```

```text
讀 README.md、AGENTS.md、API.md，只完成 M2。保留舊 API，不碰 integration、不部署。
```

```text
讀 README.md、AGENTS.md、API.md，只完成 M3。不要 SSH 部署；完成後回報測試、剩餘風險與 M4 手動步驟。
```

一次要求完成 M1～M3，較容易產生大範圍重構、漏掉相容性或消耗過多 Copilot Max 額度。
