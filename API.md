# Local API Contract

## 目的

此 API 是：

- Home Assistant integration 與同站點 add-on 的狀態邊界。
- 未來 center 透過 Tailscale 呼叫各站點 local service 的基礎。
- 目前正在使用的舊 snapshot client 的相容介面。

本階段不設計 center API，也不修改同事現有呼叫方式。

## 網路決策

目前：

- 舊 snapshot endpoint 仍可能透過公網 port forwarding 被既有 client 使用。
- 新 integration 在同站點呼叫 local add-on。
- 本階段不加入 API token，以免同時擴大 migration 範圍。
- API response 必須是 read-only，且不得包含 NVR credentials、完整 RTSP URL 或完整 ffmpeg stderr。

安全債務：

- 公網 port forwarding 不是最終架構。
- 未來 center 完成後，center 透過 Tailscale 呼叫 local API。
- 同事固定主機只呼叫 center。
- 同事完成切換後，移除 local API 的不必要公網 port forwarding。
- 到該階段再決定 local API authentication，不在目前實作。

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

新 endpoint 只提供 integration 與未來 center 必需的資料。

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

用途：供既有 client 與未來 Center/server 取得最新 JPEG；Home Assistant
integration 不呼叫此 endpoint，也不建立 Snapshot camera entity。

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

不在此 API 提供：

- Camera Ping
- RTT
- Jitter
- Packet loss
- 24 小時在線率
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
不得提高 Snapshot 併發。未來由 Center/server 呼叫此 endpoint 統一抓圖。

## 相容性政策

- Legacy API 在同事完成 center migration 前保持不變。
- `/api/v1` 可以增加 optional 欄位。
- 不刪除或重新命名既有 `/api/v1` 欄位，除非同步修改 integration 並記錄 breaking change。
- Center 尚未開發前，不加入 center-specific path、site registry 或 multi-site schema。

## Future TODO

- [ ] Center 透過 Tailscale 呼叫各站點 `/api/v1`。
- [ ] 同事固定主機改成只呼叫 center。
- [ ] Center 保存每個 channel 最新 snapshot、來源與 freshness。
- [ ] 同事完成切換後，停止直接呼叫 local legacy API。
- [ ] 移除 local API 的不必要公網 port forwarding。
- [ ] 評估 Tailscale ACL、service identity 或 API token。
- [ ] 決定 legacy endpoint 的 auth、deprecation 與移除日期。
