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

`sites` 必須明確列出每個站點：

```yaml
sites:
  - site_id: example-site
    display_name: Example
    base_url: http://site-addon-hostname:8000
    channels: [1, 2]
    concurrency: 2
    timeout_seconds: 10
    refresh_seconds: 30
max_stale_seconds: 120
snapshot_store_limit_mb: 1024
```

真實 URL 與站點 mapping 只放 Supervisor options，不提交到 Git。

## API

- `GET /healthz`
- `POST /api/v1/telemetry`：沿用已部署 producer contract，驗證後只更新 RAM。
- `GET /api/v1/sites`：供 Hub component discovery 與目前狀態 polling。
- `GET /api/v1/sites/{site_id}/cameras/{camera_id}/snapshot`：只回傳仍在
  `max_stale_seconds` 內的 JPEG；沒有或過期時回 `503` JSON。
- `/api/v1/dashboard/summary` 與 `/`：過渡／debug UI，沒有 rolling history。

Hub add-on 預設不映射 host port；component 應使用 Supervisor 產生的 add-on
internal hostname 與 container port `8765`。
