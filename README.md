# 9Space Monitor

`9space-monitor` 是此專案的唯一主要 repository，整合：

- Home Assistant custom integration：`custom_components/nvr_monitor`
- Home Assistant local add-on：`9space_snapshot_api`
- 未來 center 所需的 local API 邊界
- 單一站點的手動 SSH 部署文件

## 先讀順序

所有開發工具（GitHub Copilot、Codex、Claude）都使用相同入口：

1. `README.md`：目標、架構、目前里程碑與工作順序
2. `AGENTS.md`：修改規則、禁止事項與驗證要求
3. `API.md`：現有 API 相容性與 integration ↔ add-on 契約
4. `DEPLOY.md`：單一站點手動 SSH 部署與 rollback

`DESIGN_NOTES.md` 與 `ADDON_DEVELOPMENT_PLAN.md` 已由以上四份文件取代，不再是 active specification。

## 目前目標

只完成一個站點的整合：

1. 將 `9space-snapshot-addon` 內容移入本 repository。
2. 保持既有 snapshot API 相容，不能影響正在使用它的同事。
3. Add-on 成為唯一直接存取 Dahua NVR 的元件。
4. Integration 不再保存或使用 NVR host、port、username、password。
5. Integration 經 local add-on API 取得：
   - NVR channel 是否有實際影像
   - 最近錄影狀態
   - snapshot
6. 攝影機 Ping、在線率、RTT 等資料改用 Home Assistant 既有 Ping integration，不在本專案重做。
7. 由使用者與 AI agent 透過 SSH 手動部署到單一測試／正式站點。
8. 保留未來 center 使用 local API 的穩定邊界，但現在不開發 center。

## 架構邊界

```text
現在：

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
    └── local API
            │
            ▼
        Dahua NVR
```

### 未來 center TODO

```text
同事固定主機
      │
      ▼
    Center
      │
      │ Tailscale
      ▼
各站點 local add-on API
      │
      ▼
    Dahua NVR
```

必須保留的 TODO：

- [ ] Center 完成後，由 center 透過 Tailscale 呼叫各站點 local API。
- [ ] 同事的固定主機改成只呼叫 center，不再直接呼叫站點。
- [ ] 同事完成切換後，再決定舊 `/api/camera/{camera_id}` 的驗證、停用或移除方式。
- [ ] Center 上線後，移除不必要的 local API 公網 port forwarding。
- [ ] Center 的驗證、權限與快取規格另案設計，不提前塞進 local add-on。

## 現階段不做

- 多站點自動部署
- GitHub Actions 部署 pipeline
- 自動 config-entry migration
- 修改 Home Assistant `.storage`
- Center
- 客製 Lovelace card
- 錄影回放
- 新的 live-stream 技術
- Ping、24 小時在線率、RTT、jitter 或 packet-loss 實作
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

### M2B：搬移 NVR live probe 與 recording query（已完成，尚未部署）

- [x] 把 NVR RTSP live-video probe 搬到 add-on（`live_probe.py`，沿用 integration 原本的 DESCRIBE/SETUP/PLAY/RTP 判斷邏輯），預設每 300 秒執行一次。
- [x] 把 Dahua recording query 搬到 add-on（`recording_query.py`，沿用 integration 原本的 `mediaFileFind.cgi` 查詢與 24 小時判定邏輯，含 Asia/Taipei 本地時間假設），預設每 900 秒執行一次。
- [x] add-on 啟動後立即執行第一輪，不等待完整 interval。
- [x] `/api/v1/channels*` 改為只讀取 add-on 保存的最新背景結果（`channel_state.py` 的 in-memory store），不讓每次 GET 對 channel 同步執行探測。
- [x] 背景探測以 `max_concurrency` option 限制同時數量，單一 channel 失敗或逾時不影響其他 channel、也不中止整批。
- [x] Snapshot 維持 demand-driven 與現有 cache 行為（M2A 已共用同一套 cache/capture 路徑，M2B 不重做）。
- [x] Add-on 對外 response 不包含 credentials、RTSP URL、CGI request URL 或完整 ffmpeg/CGI stderr／body。
- [x] 使用 fake-based unit tests（`test_background_probes.py`），不要求真實 NVR、Docker 或 HAOS。
- [x] 背景 task 使用 FastAPI `startup`／`shutdown` event 管理；shutdown 會 cancel 並 await，不留孤兒 task。

完成後再進 M3。M2B 尚未部署到任何 add-on instance；部署與實機驗證待使用者核准後另行執行。

### M3：Integration 改用 add-on

- [x] 建立簡單 async local API client。
- [x] Integration config flow 改為設定 add-on base URL。
- [x] Integration 不再要求或保存 NVR credentials。
- [x] 移除 integration 內 NVR RTSP、Dahua HTTP recording client。
- [x] 移除自製 Ping coordinator 與 `icmplib` dependency。
- [x] 保留仍有必要的攝影機區域網路 service probe；不要在同一階段重寫。
- [x] Entities 名稱及 unique ID 儘量保持不變，降低 Dashboard 破壞。
- [x] Add-on unavailable 時，相關 entities 應變成 unavailable，不阻塞 HA event loop。

### M4：手動部署與實機 smoke test

依 `DEPLOY.md`：

- [ ] 備份現有 add-on、integration 與 config entry 資料。
- [ ] SSH 部署 add-on。
- [ ] 確認舊 API 仍可供同事使用。
- [ ] SSH 部署 integration。
- [ ] 手動重新設定 integration；不寫自動 migration。
- [ ] 確認 snapshot、live-video status、recording status。
- [ ] 確認 Home Assistant Ping integration 負責攝影機在線狀態。
- [ ] 記錄 rollback 路徑。

## 最小測試

自動測試只要求：

1. 舊 endpoint response 相容。
2. 新 channel endpoint 可用 fake NVR result 回 JSON。
3. 新 snapshot endpoint 回 `image/jpeg`。
4. Integration API client 可解析 channel response。
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
- Integration 能透過 local API 顯示既有核心狀態。
- Ping 使用 Home Assistant 現成 integration。
- 單一站點可由 AI agent 依文件手動部署及 rollback。
- 未來 center 呼叫 local API 的邊界已被保留。

不要求 center、多站點自動部署或正式 release pipeline。

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
