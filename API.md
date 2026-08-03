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
    "snapshot_available": true,
    "recording_query_ok": true,
    "recording_recent": true,
    "last_recording": "2026-08-01T21:30:00+08:00",
    "checked_at": "2026-08-01T21:31:00+08:00",
    "error_code": null
  }
]
```

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

用途：供既有 client 取得最新 JPEG；Home Assistant integration 不呼叫此 endpoint，也不建立 Snapshot camera entity。M5 Center 不呼叫、傳輸或保存此 endpoint 的 JPEG。

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

Snapshot 保持由 add-on demand-driven capture 與 cache 提供。Snapshot ffmpeg
實際同時抓圖固定為 1；`max_concurrency` option 僅為既有設定相容而保留，
不得提高 Snapshot 併發。M5 Center 絕不呼叫、傳輸或保存此 endpoint 的 JPEG；未來若需統一取圖必須另案批准。

## 相容性政策

- Legacy API 在同事完成 center migration 前保持不變。
- `/api/v1` 可以增加 optional 欄位。
- 不刪除或重新命名既有 `/api/v1` 欄位，除非同步修改 integration 並記錄 breaking change。
- local `/api/v1` 不加入 Center-specific path、site registry 或 multi-site schema；M5 Center 使用獨立 telemetry ingest contract。

## M5 Center telemetry contract

Center 接收兩類 sanitized batch，皆不含 JPEG、credentials、Authorization、完整 RTSP/CGI URL、raw CGI body 或 snapshot body：

1. Add-on NVR telemetry：channel live/recording 狀態、24 小時 aggregates、probe/query/snapshot metadata、allowlisted Dahua diagnostics。
2. Integration HA telemetry：allowlisted Ping、System Monitor、RPi Power、Fast.com entity states。

System Monitor 的 M5 allowlist 包含 disk／memory／load／temperature，以及
`processor_use_percent`、`last_boot`（含時區 ISO 8601）與 `uptime_seconds`（unit `s`）。
Ping mapping 必須明確指定 Center-safe channel ID；其餘 HA metrics 的 channel ID 為 `null`。

Producer 規則：

- 非阻塞、短 timeout；Center 失聯不得影響 NVR probe、integration coordinator 或 HA startup。
- producer 不保存 telemetry history 至磁碟；只保有 24 小時 RAM ring 與 bounded memory queue。
- queue 滿或 server 不可達時允許丟棄資料，並以 sanitized counter metadata 回報。
- Center 保存七日；單站採 logical 保守水位，並以 2 GiB 實體檔 fail-closed guard 保護主機容量。具體預設值待 Center 實作完成後以測試核對。
- `site_id` 為穩定 ASCII identifier，display name 另存；承德原型為 `chengde`／「承德」。

## Future TODO

- [ ] Center Docker/SQLite ingest/query/export API（M5；不含 JPEG）。
- [ ] 依每站 mapping 產生 dashboard YAML。
- [ ] 若未來要讓同事固定主機改呼叫 Center，須另開 legacy snapshot migration；M5 telemetry Center 不代理 JPEG。
- [ ] 若未來需要 Center snapshot 功能，須另開設計與安全審查；M5 永久不傳輸或保存 JPEG。
- [ ] 同事完成切換後，停止直接呼叫 local legacy API。
- [ ] 移除 local API 的不必要公網 port forwarding。
- [ ] 評估 Tailscale ACL、service identity 或 API token。
- [ ] 決定 legacy endpoint 的 auth、deprecation 與移除日期。
