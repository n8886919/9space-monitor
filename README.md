# 9Space Monitor

`9space-monitor` 是單人維護的 Home Assistant／Dahua NVR 監控 repository。它把站點內的 NVR 狀態與截圖送到中央 9Space Hub，同時保留既有 snapshot API 相容性。

## Architecture

```text
Home Assistant
├── nine_space_nvr_monitor integration
│   ├── config flow、entities、coordinator、diagnostics
│   ├── 讀取 local app API
│   └── push allowlisted HA telemetry
│
└── nine_space_snapshot app
    ├── 唯一保存 NVR credentials 並直接連接 NVR
    ├── live-video probe、recording query、snapshot/cache
    ├── legacy 與 local HTTP API
    └── push NVR telemetry
            │
            └── Tailscale ──> 9Space Hub app

中央 Home Assistant
├── 9Space Hub app
│   ├── current telemetry 只留 RAM
│   ├── debug Web UI
│   └── bounded last-good snapshot store（每 site/channel 最多一張）
└── nine_space_hub component
    ├── camera／binary_sensor／sensor entities
    └── 狀態歷史交由 Home Assistant Recorder 保存
```

App 是唯一直接存取 Dahua NVR 的元件。Integration 不保存 NVR credentials、不建立 RTSP URL、不呼叫 CGI／snapshot endpoint，也不建立 Snapshot camera entity。Camera Ping 使用 Home Assistant 原生 integration。

舊 `GET /api/camera/{camera_id}` 仍是受保護的 compatibility contract。`8122` 屬於獨立舊正式服務，本 repository 的開發 app 預設使用 host port `8222`，兩者不可混用。

## Components

- `nine_space_snapshot/`：由 Home Assistant Supervisor managed repository 安裝／更新的 app、NVR adapters、snapshot 與 telemetry producer；不使用 HA local `/addons` source install。
- `custom_components/nine_space_nvr_monitor/`：Home Assistant custom integration 與 allowlisted HA telemetry producer。
- `nine_space_hub/`：Supervisor app、RAM current-state、snapshot scheduler/store 與 debug Web UI。
- `custom_components/nine_space_hub/`：中央 Home Assistant component，將 Hub 當下狀態映射成 Recorder-managed entities。
- `dashboard/`：以每站 private mapping 產生 local Lovelace 與非 Ping HA telemetry mapping；Ping (ICMP) 與其 statistics 留在 Home Assistant local，sample 只含合成資料。
- `tests/`、`nine_space_snapshot/test/`：API、safety、storage、lifecycle 與 compatibility tests。

## Development start

先確認 working tree，再只跑與修改相關的測試：

```bash
git status --short
python3 -m unittest tests.test_repository
python3 -m unittest discover -s nine_space_snapshot/test -v
python3 -m compileall -q custom_components/nine_space_nvr_monitor custom_components/nine_space_hub nine_space_hub dashboard
git diff --check
```

Hub app 設定見 `nine_space_hub/README.md`。Dashboard renderer 範例：

```bash
python3 -m dashboard.render dashboard/chengde.sample.json --format lovelace
python3 -m dashboard.render dashboard/chengde.sample.json --format lovelace-view
python3 -m dashboard.render dashboard/chengde.sample.json --format telemetry
```

真實 site mapping 使用 ignored 的 `dashboard/*.private.json`；不得提交真實 entity IDs、IP、credentials 或影像。

## Documentation routing

- `AGENTS.md`：agent 入口、永久架構／安全規則與風險分級。
- `STATUS.md`：短期 current／deployed／next／blockers／last-known 狀態。
- `API.md`：legacy、local、Hub telemetry 與 snapshot API contract。
- `DEPLOY.md`：單站手動 SSH 部署、checks、smoke test 與 rollback reference。
- `.agents/skills/9space-diagnostic/SKILL.md`：Home Assistant／NVR 唯讀診斷。
- `nine_space_hub/README.md`：Hub app options、資料責任與 API。

普通工作不必預讀所有文件；由 `AGENTS.md` 依任務 lazy-load 專門文件。歷史由 Git 保存，不以 session handoff 或 milestone diary 作為 source of truth。
