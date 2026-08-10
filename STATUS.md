# Status

Updated: 2026-08-10

## Current

- Branch: `main`.
- Functional release commits: `023170b`、`8229d86`、`8a7acac` 與 `19186d9`，已 fast-forward push 至 `origin/main`；未 force push、未改寫歷史。
- Source/current central test deployment: Snapshot add-on `0.3.10`; Hub add-on `0.3.1`; `nvr_monitor` integration `0.2.7`。
- 舊 Center source 已改名並重構為 `nine_space_monitor_hub/` Supervisor add-on，顯示名稱 `9Space Monitor Hub`／`9Space 監控中樞`。
- Hub 不使用 SQLite、events/export 或 rolling history；telemetry／snapshot attempt 只保存最新值於 RAM。每個 site/channel 只持久化一張 atomic-replace last-good JPEG。
- Hub 不再保存或要求 per-site options。Snapshot add-on 只新增一個 `hub_ip` hostname；HTTP scheme、Hub port/path 與站點 Snapshot port 固定。registration 不傳 URL，Hub 由 Tailscale peer 或 Hub MagicDNS suffix 加 `site_id` 自動推導站點 hostname。
- 新 `nine_space_monitor_hub` component `0.1.0` 提供 camera、live／recording／snapshot binary sensors 與 current metric sensors；Home Assistant Recorder 是狀態歷史唯一持有者。
- Local `nvr_monitor` 仍保留最完整站點資訊與 local Ping；不取 Hub snapshot、不建立 Snapshot camera。
- Root tests `95/95` PASS；最新相關 Snapshot／Hub suites `58/58` PASS；compileall、shell syntax 與 `git diff --check` PASS。完整 suite 在既有 Snapshot lifecycle tests 於本 sandbox TIMEOUT；本機 Docker build 因無 daemon permission FAIL，Supervisor managed update/build PASS。

## Deployed

- 中央 Home Assistant 已安裝 `custom_components/nine_space_monitor_hub` source；本次 `ha core check` PASS，未編輯 `.storage`。
- 中央 HA 已由既有 managed repository 更新 Hub `0.3.1` 與 Snapshot add-on `0.3.10`；兩者 state `started`、version gate PASS、去敏 log error count `0`。Hub tailnet `8765/healthz` 與 Snapshot `8222/healthz` PASS。
- 實際 topology smoke 證明 Supervisor NAT 隱藏 TCP peer；0.3.1 MagicDNS fallback registration HTTP `200` PASS。測試註冊已由 Hub restart 清除，restart 後 discovery `0 sites`。
- Hub discovery 目前 `0 sites`：尚無站點 Snapshot add-on 送出 `snapshot_registration`，因此 component config entry 與 camera/entity runtime 尚未驗收。
- 舊 Penguin Center 的 Tailscale Serve 8765 已關閉，`9space-center-center-1` container 已移除；唯讀驗證為 `No serve config` 且無同名 container。
- 安全審核未批准永久刪除舊 Center image、data volume 與 private env；它們仍殘留但不提供服務。
- 既有承德 Snapshot add-on／`nvr_monitor` deployments 未在本任務修改；8122 未操作。

## Next

1. 各站 Snapshot add-on 更新至 `0.3.10`，在 UI 只填中央 Hub 的完整 Tailscale hostname 至 `hub_ip`；確認 `site_id` 等於該站 Tailscale machine name。既有 display/channel/concurrency/timeout 繼續沿用。
2. 第一批註冊成功後，確認 Hub discovery 不再是 `0 sites` 且能取得 last-good snapshot。
3. 在中央 HA UI 新增 `9Space Monitor Hub` integration，填入 Supervisor internal hostname 與 container port `8765`；不得編輯 `.storage`。
4. 驗證 camera/current-state entities、Recorder history 與正式 Dashboard；不以 debug Web UI 作正式 UI 驗收。

## Blockers

- 每站 `hub_ip` 與 component config entry 是使用者負責的 HA UI gate；目前無 live Hub entity／snapshot 驗證。

## Temporary / last-known

- `8122` 是獨立舊正式服務，永久不在本任務操作範圍。
- 使用者於 35 分鐘時明確接受停止舊 observation；不得改寫成一小時 PASS。
- 不在此文件保存 host、private URL、credentials、backup path 或 JPEG／footage。
