# 9Space Hub

中央 Home Assistant app，中文名稱為「9Space 中樞」。它從各站
9Space Snapshot app 拉取截圖，並統計 Hub 自己觀測到的抓圖結果。

## 資料責任

- `/data/snapshots` 每個 site/channel 只保存一張 last-good JPEG，atomic replace，
  不建立圖片歷史。
- snapshot attempt、成功／失敗 counter 與連續失敗數只保存於 RAM；restart 後歸零。
- 不使用 SQLite，不保存逐筆紀錄或統計歷史。Hub component 將目前狀態映射成
  Home Assistant entities，歷史由 Recorder 保存。
- 不接受 credentials、Authorization、完整 URL、IP、raw NVR response、JPEG
  Home Assistant entity IDs，registration 也不接受 NVR telemetry 欄位。

## App options

Hub 本身只保留全域 snapshot freshness 與容量設定：

```yaml
max_stale_seconds: 120
snapshot_store_limit_mb: 1024
snapshot_refresh_seconds: 30
```

站點資料由各站 Snapshot app 以 snapshot-only heartbeat 自動註冊。Snapshot app options 中：

- `site_id`、`site_display_name`、`channel_count` 提供站點與 channel mapping。
- Dahua RTSP/HTTP ports 固定為 `554`／`80`，不再是 Supervisor options。
- `max_concurrency` 直接作為 Hub 拉圖 concurrency。
- Hub timeout 由 `health_timeout_ms` 換算並加 5 秒 capture margin，限制在 2–60 秒。
- `hub_ip` 只填中央 Hub 的 Tailscale IPv4 或完整 `*.ts.net` hostname。Snapshot
  app 固定使用 HTTP、port `8765` 與 `/api/v1/snapshot-sites/register`，不接受 scheme、port 或 path。
- Snapshot app 不依賴 container system DNS；遠端站點以 Tailscale resolver 取得
  Hub 與自身位址，連線時保留原始 Hub MagicDNS `Host`。與 Hub 同一台 HA 時，自動改走
  Supervisor internal hostname。Hub 優先使用實際 Tailscale peer；Supervisor NAT 隱藏
  peer 時使用 registration 中自動解析的站點位址。使用者不填站點 IP 或 URL。

舊版 Hub 已保存的 `sites` option，以及 Snapshot app 的舊
`center_telemetry_url`／`telemetry_*`／`hub_snapshot_*`／`rtsp_port`／`nvr_http_port` options，在升級時只為相容而忽略。
真實 hostname 只放 Supervisor options，不提交到 Git，也不出現在 discovery API。

## API

- `GET /healthz`
- `POST /api/v1/snapshot-sites/register`：只接收 site、channels 與抓圖排程參數。
- `GET /api/v1/sites`：供 Hub component discovery 與目前狀態 polling。
- `GET /api/v1/sites/{site_id}/cameras/{camera_id}/snapshot`：只回傳仍在
  `max_stale_seconds` 內的 JPEG；沒有或過期時回 `503` JSON。
- `/api/v1/dashboard/summary` 與 `/`：過渡／debug UI，沒有 rolling history。

Hub 映射 host port `8765` 接收各站註冊；component 應使用 Supervisor 產生的
app internal hostname 與 container port `8765`。
