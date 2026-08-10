# 9Space Monitor Hub

中央 Home Assistant add-on，中文名稱為「9Space 監控中樞」。它從各站
9Space Snapshot add-on 拉取截圖，並接收既有的去敏 telemetry push。

## 資料責任

- `/data/snapshots` 每個 site/channel 只保存一張 last-good JPEG，atomic replace，
  不建立圖片歷史。
- telemetry、snapshot attempt 與錯誤狀態只保存最新值於 RAM；restart 後重新
  由各站 producer／scheduler 填入。
- 不使用 SQLite，不保存狀態或統計歷史。Hub component 將目前狀態映射成
  Home Assistant entities，歷史由 Recorder 保存。
- 不接受 credentials、Authorization、完整 URL、IP、raw NVR response、JPEG
  telemetry 或 Home Assistant entity IDs。

## Add-on options

Hub 本身只保留全域 snapshot freshness 與容量設定：

```yaml
max_stale_seconds: 120
snapshot_store_limit_mb: 1024
snapshot_refresh_seconds: 30
```

站點資料由各站 Snapshot add-on 隨 telemetry 自動註冊。Snapshot add-on options 中：

- `site_id`、`site_display_name`、`channel_count` 提供站點與 channel mapping。
- `max_concurrency` 直接作為 Hub 拉圖 concurrency。
- Hub timeout 由 `health_timeout_ms` 換算並加 5 秒 capture margin，限制在 2–60 秒。
- `hub_ip` 只填中央 Hub 的 Tailscale IPv4 或完整 `*.ts.net` hostname。Snapshot
  add-on 固定使用 HTTP、port `8765` 與 `/api/v1/telemetry`，不接受 scheme、port 或 path。
- Hub 只從實際 TCP peer 推導 `http://<peer>:8222`，不接受 producer URL 或 proxy
  headers；peer 不是 Tailscale IPv4／IPv6 時拒絕自動註冊。

舊版 Hub 已保存的 `sites` option，以及 Snapshot add-on 的舊
`center_telemetry_url`／`hub_snapshot_*` options，在升級時只為相容而忽略。
真實 hostname 只放 Supervisor options，不提交到 Git，也不出現在 discovery API。

## API

- `GET /healthz`
- `POST /api/v1/telemetry`：沿用已部署 producer contract，驗證後只更新 RAM。
- `GET /api/v1/sites`：供 Hub component discovery 與目前狀態 polling。
- `GET /api/v1/sites/{site_id}/cameras/{camera_id}/snapshot`：只回傳仍在
  `max_stale_seconds` 內的 JPEG；沒有或過期時回 `503` JSON。
- `/api/v1/dashboard/summary` 與 `/`：過渡／debug UI，沒有 rolling history。

Hub 映射 host port `8765` 接收各站 telemetry；component 應使用 Supervisor 產生的
add-on internal hostname 與 container port `8765`。
