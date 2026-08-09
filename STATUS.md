# Status

Updated: 2026-08-10

## Current

- Branch: `main`.
- Functional release commit: `023170b`，已 fast-forward push 至 `origin/main`；未 force push、未改寫歷史。
- Local/deployed site versions: Snapshot add-on `0.3.8`; `nvr_monitor` integration `0.2.7`。
- 舊 Center source 已改名並重構為 `nine_space_monitor_hub/` Supervisor add-on `0.1.0`，顯示名稱 `9Space Monitor Hub`／`9Space 監控中樞`。
- Hub 不使用 SQLite、events/export 或 rolling history；telemetry／snapshot attempt 只保存最新值於 RAM。每個 site/channel 只持久化一張 atomic-replace last-good JPEG。
- 新 `nine_space_monitor_hub` component `0.1.0` 提供 camera、live／recording／snapshot binary sensors 與 current metric sensors；Home Assistant Recorder 是狀態歷史唯一持有者。
- Local `nvr_monitor` 仍保留最完整站點資訊與 local Ping；不取 Hub snapshot、不建立 Snapshot camera。
- Root tests `90/90` PASS；Snapshot add-on test suite PASS；compileall、shell syntax 與 `git diff --check` PASS；Hub Docker build 與 runtime smoke PASS。

## Deployed

- 中央 Home Assistant 已安裝 `custom_components/nine_space_monitor_hub` source；`ha core check` PASS、Core restart PASS、restart 後 Hub component error count `0`。
- Component 尚未建立 config entry，因 Hub add-on 尚未由使用者在 UI 安裝；未編輯 `.storage`。
- 中央 HA 現有 App Store repositories 不包含本 monorepo；reload 後仍看不到 Hub。使用者需在 UI 加入 repository，安裝並設定 Hub options。
- 舊 Penguin Center 的 Tailscale Serve 8765 已關閉，`9space-center-center-1` container 已移除；唯讀驗證為 `No serve config` 且無同名 container。
- 安全審核未批准永久刪除舊 Center image、data volume 與 private env；它們仍殘留但不提供服務。
- 既有承德 Snapshot add-on／`nvr_monitor` deployments 未在本任務修改；8122 未操作。

## Next

1. 使用者在中央 HA App Store UI 加入 `https://github.com/n8886919/9space-monitor`，安裝 `9Space Monitor Hub`。
2. 在 Hub add-on UI options 明列各站 private base URL、site/display name、channels 與 bounded scheduler 參數；真實值不寫入 Git。
3. 安裝啟動後，以 `ha apps info <actual_slug>` 取得 Supervisor internal hostname；在 HA UI 新增 `9Space Monitor Hub` integration，填入 `http://<actual-hostname>:8765`。
4. 驗證 site/camera discovery、JPEG freshness、entities、Recorder history 與 Dashboard；不以 debug Web UI 作正式 UI 驗收。
5. 若仍要永久刪除舊 Center image、volume 與 private env，需逐項明確批准。

## Blockers

- Hub add-on 的 UI installation、private site options 與 component config entry 是使用者負責的 HA UI gate；目前無 live Hub entity／snapshot 驗證。

## Temporary / last-known

- `8122` 是獨立舊正式服務，永久不在本任務操作範圍。
- 使用者於 35 分鐘時明確接受停止舊 observation；不得改寫成一小時 PASS。
- 不在此文件保存 host、private URL、credentials、backup path 或 JPEG／footage。
