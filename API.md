# Local API Contract

## 目的

此 API 是：

- Home Assistant integration 與同站點 add-on 的狀態邊界。
- M5 add-on／integration 透過 Tailscale push sanitized telemetry 到 Center 的相容基礎。
- 目前正在使用的舊 snapshot client 的相容介面。

M5 新增 Center telemetry ingest API；它與 legacy snapshot API、local integration API 分離，且不修改同事現有呼叫方式。

## 網路決策

目前：

- 舊 snapshot endpoint 仍可能透過公網 port forwarding 被既有 client 使用。
- 新 integration 在同站點呼叫 local add-on。
- 本階段不加入 API token，以免同時擴大 migration 範圍。
- API response 必須是 read-only，且不得包含 NVR credentials、完整 RTSP URL 或完整 ffmpeg stderr。

安全債務：

- 公網 port forwarding 不是最終架構。
- M5 add-on 與 integration 透過 Tailscale push telemetry 到 Center。
- 同事固定主機只呼叫 center。
- 同事完成切換後，移除 local API 的不必要公網 port forwarding。
- M5 為受控 Tailscale 內網，不新增 per-site token；Center 不得暴露至公網。

## Legacy API

### `GET /api/camera/{camera_id}`

此 endpoint 正在被使用，必須保持相容。

現有行為：

- 成功時回 `multipart/mixed`。
- Multipart 內含 JSON metadata 與 JPEG。
- 無 JPEG 時回 JSON。
- Busy 時可回 HTTP 503。
- 既有 JSON 欄位包含：
  - `camera_id`
  - `ok`
  - `latency_ms`
  - `detail`

目前不得：

- 改 path。
- 改為純 JPEG。
- 刪除或重新命名欄位。
- 強制 client 提供 token。
- 改變既有成功／失敗 status code。
- 停用 endpoint。

內部實作可共用新的 snapshot service，但必須有 compatibility test。

## Minimal local API

新 endpoint 只提供 integration 必需的資料；M5 Center telemetry 不從此 endpoint pull 資料。

### `GET /healthz`

用途：確認 add-on process 與 HTTP event loop 可回應。

Response：

```json
{
  "status": "ok"
}
```

規則：

- 不測試 NVR。
- NVR 離線時仍回 HTTP 200。
- 不回傳 site、channel、credentials 或 error detail。

### `GET /api/v1/channels`

用途：列出已設定的 NVR channels 與最新狀態。

Response：

```json
[
  {
    "channel_id": 1,
    "live_video": true,
    "live_checked_at": "2026-08-01T21:31:00+08:00",
    "snapshot_available": true,
    "recording_query_ok": true,
    "recording_recent": true,
    "last_recording": "2026-08-01T21:30:00+08:00",
    "recording_files_24h": 42,
    "recording_coverage_24h": 97.5,
    "checked_at": "2026-08-01T21:31:00+08:00",
    "error_code": null
  }
]
```

`recording_files_24h` 是最近一次成功錄影查詢中的有效片段數；
`recording_coverage_24h` 是有效片段在過去 24 小時覆蓋的時間比例。
兩者在錄影查詢失敗或尚無成功結果時為 `null`。`daily_online_rate` 與
`nvr_live_video_disconnect_count_24h` 不由 add-on API 提供；integration 使用
`live_video` 與 `live_checked_at` 在本機 bounded RAM 計算，並由 Home Assistant
Recorder 作為唯一持久化歷史。舊 add-on RAM history 不 migration。

### `GET /api/v1/channels/{channel_id}`

用途：取得單一 channel 的相同狀態。

未知 channel：

```json
{
  "error_code": "channel_not_found"
}
```

HTTP status：`404`

### `GET /api/v1/channels/{channel_id}/snapshot`

用途：供既有 client 取得最新 JPEG；Home Assistant integration 不呼叫此 endpoint，也不建立 Snapshot camera entity。M5F Center 只可由 scheduler 經此站點 add-on endpoint 取得 JPEG，並寫入自己的最後成功 snapshot store。

成功：

```text
HTTP 200
Content-Type: image/jpeg
```

尚無 JPEG：

```json
{
  "error_code": "snapshot_unavailable"
}
```

HTTP status：`503`

未知 channel：HTTP `404`

## Channel 欄位

最小欄位：

| 欄位 | 型別 | 說明 |
| --- | --- | --- |
| `channel_id` | integer | Dahua NVR channel |
| `live_video` | boolean/null | NVR RTSP probe 是否收到有效 video data |
| `snapshot_available` | boolean | 是否有可回傳 JPEG |
| `recording_query_ok` | boolean | Dahua recording query 是否成功 |
| `recording_recent` | boolean/null | 是否有目前規則認定的近期錄影 |
| `last_recording` | string/null | ISO 8601 timestamp |
| `checked_at` | string/null | local service 最近一次更新時間 |
| `error_code` | string/null | 已去敏、可程式判斷的錯誤 |

M5 可在 `/api/v1` 增加不破壞既有 consumer 的 optional 診斷欄位與 24 小時 aggregates，例如 live online rate、disconnect count、recording count/coverage/gap、last recording age、first RTP 與 probe duration。具體 schema 由 M5A 以測試鎖定。

不在此 API 提供：

- Camera Ping
- RTT
- Jitter
- Packet loss
- raw Ping/RTT/Jitter/packet-loss probe
- Camera Wi-Fi signal
- NVR username/password
- RTSP URL
- 完整 ffmpeg stderr
- Snapshot body 以外的影像資料
- 錄影檔案下載或回放

## Error codes

目前只需要：

- `nvr_unreachable`
- `authentication_failed`
- `channel_not_found`
- `rtsp_timeout`
- `no_video`
- `snapshot_unavailable`
- `recording_query_failed`
- `service_busy`
- `internal_error`

完整內部錯誤可寫 add-on log，但必須去除：

- username
- password
- Authorization
- 完整 RTSP URL
- public IP
- JPEG body

## Integration 使用規則

- 使用 async HTTP client。
- 不在 Home Assistant event loop 執行 blocking request。
- Coordinator 可以一次讀 `/api/v1/channels`，不要每個 entity 各自 request。
- Integration 不呼叫 Snapshot endpoint，也不建立 Snapshot camera entity。
- Add-on 無法連線時，NVR 相關 entities 標記 unavailable。
- 不因 add-on unavailable 阻塞 Home Assistant startup。
- Integration 不 fallback 回直接連接 NVR。

Snapshot 保持由 add-on demand-driven capture 與 cache 提供。M5F 起 `max_concurrency`
為 site 可設定、runtime bounded 的同時 snapshot 上限，可依網路與 Pi 硬體設定；不得無上限併發。Center 只經站點 add-on snapshot API 取得圖片，獨立 store 每 site/channel 僅保存最後成功 JPEG，atomic replace、無 history，且 JPEG 不得進 telemetry SQLite、export、log、fixture 或 Git。

## 相容性政策

- Legacy API 在同事完成 center migration 前保持不變。
- `/api/v1` 可以增加 optional 欄位。
- 不刪除或重新命名既有 `/api/v1` 欄位，除非同步修改 integration 並記錄 breaking change。0.3.8 已同步把 live 24 小時 aggregates 移至 integration。
- local `/api/v1` 不加入 Center-specific path、site registry 或 multi-site schema；M5 Center 使用獨立 telemetry ingest contract。

## M5 Center telemetry contract

Center 接收兩類 sanitized batch，皆不含 JPEG、credentials、Authorization、完整 RTSP/CGI URL、raw CGI body 或 snapshot body：

1. Add-on NVR telemetry：channel 當下 live/recording 狀態、recording query aggregates、probe/query/snapshot metadata、allowlisted Dahua diagnostics；不含 live 24 小時 history、在線率或斷線次數。
2. Integration HA telemetry：allowlisted System Monitor、RPi Power、Fast.com entity states。

System Monitor 的 M5 allowlist 包含 disk／memory／load／temperature，以及
`processor_use_percent`、`last_boot`（含時區 ISO 8601）與 `uptime_seconds`（unit `s`）。
HA Ping (ICMP) entities、RTT 與 packet loss 僅留在 Home Assistant local，不屬於
Center telemetry contract。Local dashboard 可直接以原生 Ping entities 與 recorder
statistics 顯示目前狀態、rolling 1h 與 rolling 24h mean；不得把 Ping events、entity
IDs 或衍生 rollups 上送 Center。其餘 HA metrics 的 channel ID 為 `null`。

Producer 規則：

- 非阻塞、短 timeout；Center 失聯不得影響 NVR probe、integration coordinator 或 HA startup。
- producer 不保存 telemetry history；只保有 bounded memory queue。
- queue 滿或 server 不可達時允許丟棄資料，並以 sanitized counter metadata 回報。
- Center 保存七日；單站採 logical 保守水位，並以 2 GiB 實體檔 fail-closed guard 保護主機容量。具體預設值待 Center 實作完成後以測試核對。
- `site_id` 為穩定 ASCII identifier，display name 另存；承德原型為 `chengde`／「承德」。

## M5F Center snapshot contract（已實作）

M5F 不修改 local legacy `GET /api/camera/{camera_id}` 或 local `/api/v1/channels/{channel_id}/snapshot` contract；新增 Center endpoint：

### `GET /api/v1/sites/{site_id}/cameras/{camera_id}/snapshot`

- Center 有最後成功 JPEG，且其 age 小於或等於可設定 `max_stale_seconds`（預設 `120`）時：HTTP `200`、`Content-Type: image/jpeg`；即使最近一次 refresh attempt 失敗亦同。
- 沒有最後成功 JPEG，或最後成功 JPEG 過期時：HTTP `503` JSON（例如 `snapshot_unavailable` 或 `snapshot_stale`）；不得回 JPEG。
- 未知 site 或 camera：HTTP `404` JSON。
- 此 endpoint 僅供明確指定 site/camera 的 consumer；不提供 snapshot history、list/export 或 telemetry embedding。

Center UI 每 site 一個分頁；所有 camera 一律顯示最後成功圖片。最近 attempt 成功為綠框，失敗為紅框並顯示圖片 age；從未成功顯示 placeholder。前端以相對 `.../last-good-snapshot` route 取得 UI 顯示的最後成功圖片，該 route 不做 stale gate；明確指定 site/camera 的 consumer 應使用本節 `/snapshot` route，並由 `max_stale_seconds` 檢查可接受的新鮮度。兩者均不提供 snapshot history、list 或 export。

Center scheduler 使用每站手動 channel list；不得假設 channel count 或 `01`～`14` 命名。它以 bounded batch concurrency 執行，例如 13 channels、concurrency 4：`4/4/4/1`，完成一輪後等待 refresh interval。Add-on 的 `max_concurrency` 亦為 site 可設定、runtime bounded 的上限。

每次 snapshot attempt 的成功、timestamp、latency 與去敏 error code 可進 telemetry SQLite，供每 camera／site rolling 1h、24h、7d success rate 與 latency mean／population stddev；JPEG bytes 不得進 SQLite、telemetry export、log、fixture 或 Git。Center snapshot store 僅保存每 site/channel 一張 last-good JPEG，使用 atomic replace，不保存 history。

## Future TODO

- [x] Center Docker/SQLite metadata-only ingest/query/export API（M5A；不含 JPEG）。
- [x] 依每站 mapping 產生 dashboard YAML renderer（M5D）。
- [x] Ping (ICMP) 僅產生 Home Assistant local Lovelace／statistics cards，不進 Center。
- [x] M5F Center 最新 snapshot API、bounded store/scheduler、UI 與統計；未修改 legacy local endpoint。
- [ ] 同事完成切換後，停止直接呼叫 local legacy API。
- [ ] 移除 local API 的不必要公網 port forwarding。
- [ ] 評估 Tailscale ACL、service identity 或 API token。
- [ ] 決定 legacy endpoint 的 auth、deprecation 與移除日期。
