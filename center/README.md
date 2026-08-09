# 9Space Center telemetry MVP

這是只保存診斷 metadata 的 Center。它不接受、保存或匯出 JPEG、base64
影像、credentials、Authorization、完整 URL 或 raw payload。

## 安全邊界

- 本版沒有 per-site token，只能放在受控 Tailscale tailnet，不可公開到 Internet。
- Compose 預設只綁 `127.0.0.1:8765`。要從 tailnet 存取時，在未提交的
  `.env` 內把 `CENTER_BIND_ADDRESS` 設成該主機的 Tailscale address，並配置
  Tailscale ACL；不要把真實 IP 寫進 repository。
- Container 使用 non-root user、read-only root filesystem、drop all capabilities，
  不使用 privileged mode。
- SQLite named volume 固定掛載於 `/data`。

## 啟動

在 repository root 執行：

```text
cp center/.env.example center/.env
docker compose --env-file center/.env -f center/compose.yaml up -d --build
```

映像未綁架構，`python:3.13-slim` 可在 Docker 支援的 x86_64 與 arm64 主機各自
原生 build。服務監聽 container port `8765`，restart policy 為
`unless-stopped`，Docker JSON log rotation 為 10 MB × 3。

## Ingest contract

`POST /api/v1/telemetry`，`Content-Type: application/json`：

```json
{
  "site_id": "sample-site",
  "display_name": "範例站點",
  "source": "addon",
  "events": [
    {
      "event_id": "9a3fc128b2d49f2900f268cbb837bca48405543c671f2e3a978bf40f66cd1a6b",
      "timestamp": "2026-08-03T12:00:00Z",
      "kind": "nvr.live",
      "channel_id": 1,
      "metrics": {
        "live_video": true,
        "probe_duration_ms": 45.2,
        "error_code": null
      }
    }
  ]
}
```

- `site_id` 是通用 ASCII slug；中文名稱只放 `display_name`。
- `source` 只能是 `addon` 或 `integration`。
- `kind` 固定為：`nvr.live`、`nvr.recording`、`nvr.probe`、`nvr.snapshot`、
  `ha.system`、`ha.rpi_power`、`ha.fastdotcom`、`producer.health`。
- Ping (ICMP)、RTT 與 packet loss 留在 Home Assistant local，不由 Center ingest、
  query、export 或 UI 處理。
- `event_id` 只能是 64 位小寫 hex SHA-256。Producer 應對 canonical safe metadata
  （例如 source/site/kind/channel/timestamp/sequence）計算 hash；hash input 不得包含
  raw payload、credentials、URL、IP、entity ID 或影像內容。
- 一批最多 500 events，request body 最多 512 KiB，metrics 最多 32 個。
- `(source, site_id, event_id)` 唯一，producer 重送不會重複保存。
- Metrics 逐欄位限制型別、值域、enum 或短代碼；nested data 與任意自由文字一律拒絕。
- Center 不接受原始 Home Assistant `entity_id`。Producer 必須先依 site mapping
  轉成安全的 `kind`、`channel_id` 與 allowlisted metrics；避免 IP 型 entity ID 落盤。
- `display_name` 是唯一允許中文的站點 metadata，但仍拒絕 IP、URL、Authorization
  與 password/token/secret/credential 字樣；`event_id` 也套用相同敏感資料檢查。

## Query/export

- `GET /healthz`
- `GET /api/v1/sites`
- `GET /api/v1/sites/{site_id}/events?after_cursor=0&limit=1000`
- `GET /api/v1/sites/{site_id}/latest`
- `GET /api/v1/sites/{site_id}/export.json?after_cursor=0&limit=1000`

Export 與 query 共用相同 sanitized rows，最多 1000 筆一頁；用
`next_cursor` 取得下一頁。沒有影像 export。

## Retention與容量

- timestamp 超過七天的 event 不寫入，已保存資料每小時及每次 ingest/query 清理。
- Logical 安全水位為單站 192 MiB、全域 1536 MiB，刻意保留 SQLite page/index/WAL
  空間。單站超限時只移除該站最舊 rows，絕不為本站寫入刪除其他站資料；全域
  logical 超限則整批 HTTP 507 fail-closed。
- DB、WAL、SHM 合計另有 2 GiB filesystem hard guard，寫入前保留 128 MiB reserve
  並估算 growth，寫入後、commit 前再檢查實體用量。超限會 rollback 並截斷未提交
  WAL，不做部分寫入、不改其他站資料。
- Logical quota 跨 SQLite 版本可重現；physical guard 才是 database/WAL/SHM 的最終
  上限。Docker volume/filesystem 仍應保留作業系統層監控與額外可用空間。
