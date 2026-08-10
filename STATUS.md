# Status

Updated: 2026-08-10

## Current

- Branch: `main`.
- Functional release history 只以 fast-forward push 至 `origin/main`；未 force push、未改寫歷史。
- Source/current test deployments: Snapshot add-on `0.3.12`; Hub add-on `0.3.2`; `nvr_monitor` integration `0.2.9`。
- 舊 Center source 已改名並重構為 `nine_space_monitor_hub/` Supervisor add-on，顯示名稱 `9Space Monitor Hub`／`9Space 監控中樞`。
- Hub 不使用 SQLite、events/export 或 rolling history；telemetry／snapshot attempt 只保存最新值於 RAM。每個 site/channel 只持久化一張 atomic-replace last-good JPEG。
- Hub 不再保存或要求 per-site options。Snapshot add-on 只新增一個 `hub_ip` hostname；HTTP scheme、Hub port/path 與站點 Snapshot port 固定。registration 不傳 URL，Hub 由 Tailscale peer 或 Hub MagicDNS suffix 加 `site_id` 自動推導站點 hostname。
- Snapshot add-on 的 Dahua RTSP／HTTP ports 固定為 `554`／`80`；`rtsp_port` 與 `nvr_http_port` 已從 config schema/runtime reads 移除。Supervisor 保留的舊 option keys 只為升級相容而忽略。
- `nvr_monitor` runtime 固定使用 Supervisor internal Snapshot URL；新增／Reconfigure UI 不再要求 add-on base URL。舊 entry key 會被忽略，不改 entry、subentry 或 entity identity。
- 新 `nine_space_monitor_hub` component `0.1.0` 提供 camera、live／recording／snapshot binary sensors 與 current metric sensors；Home Assistant Recorder 是狀態歷史唯一持有者。
- Local `nvr_monitor` 仍保留最完整站點資訊與 local Ping；不取 Hub snapshot、不建立 Snapshot camera。
- `nvr_monitor` 已移除沒有 Dahua event producer 的 motion/video-loss/video-blind entities；保留 `camera_problem` 與 `diagnostic_status`。Snapshot API 新增錄影缺口數／總時長／最大缺口與 RTSP first-packet／probe timing，integration 沿用既有 debug unique IDs。
- Root tests `99/99`、`169` subtests PASS；新增 RTSP timing 精準測試 `7/7` PASS；compileall、JSON、shell syntax 與 `git diff --check` PASS。Snapshot FastAPI lifecycle 全套在本機既知 TestClient/AnyIO shutdown 卡住，未宣告整套 PASS；兩站 Supervisor managed build 與實機 API 補驗 PASS。

## Deployed

- 中央 Home Assistant 已安裝 `custom_components/nine_space_monitor_hub` source；本次 `ha core check` PASS，未編輯 `.storage`。
- daan-forest 已更新 Hub `0.3.2` 與 Snapshot add-on `0.3.12`；chengde 已更新 Snapshot add-on `0.3.12`。三者 state `started`、version gate PASS、去敏 log error count `0`，兩站 `8222/healthz` PASS。
- Snapshot add-on 以 bounded 直接 MagicDNS lookup 解決 container split-DNS 缺失；同機 Hub 使用 Supervisor internal hostname。Hub discovery 已自動註冊 `daan-forest` 與 `chengde` 共兩站。
- daan-forest Tailscale add-on 已由使用者關閉 userspace networking；daan-forest→chengde `8222/healthz` PASS，ACL allow-all 無需修改。
- daan-forest snapshot attempts `3/3` success。chengde `13/14` success；Camera 09 為 local `rtsp_timeout`／`recording_query_failed`／`snapshot_unavailable`，其餘跨站 snapshot path 正常。
- Snapshot `0.3.12` metadata contract 兩站欄位完整；RTSP probe timing daan-forest `3/3`、chengde `14/14` 有值。錄影缺口 chengde `12/14` 有值；其餘反映當下錄影查詢失敗／NVR unreachable，不是欄位缺失。
- `nvr_monitor` `0.2.9` 已原子部署至 daan-forest 與 chengde；兩站 `ha core check`、Core recovery、唯一 canonical layout 與版本 gate PASS，restart 後去敏 `nvr_monitor` error count `0`，transaction 已清除。
- chengde registry 已建立 gap count／total／largest 各 `14` 個；既有 first-packet／probe-duration unique IDs 各 `14` 個已接回 add-on data，沿用原 enabled/disabled 狀態。舊 event entities 留為 orphan，未編輯 `.storage`。
- 舊 Penguin Center 的 Tailscale Serve 8765 已關閉，`9space-center-center-1` container 已移除；唯讀驗證為 `No serve config` 且無同名 container。
- 安全審核未批准永久刪除舊 Center image、data volume 與 private env；它們仍殘留但不提供服務。
- 8122 未操作；未編輯 `.storage`。

## Next

1. 使用者在 HA UI 刪除不再由 integration 提供的 motion event、last motion／Dahua event、motion、video loss 與 video blind orphan entities；不得建立 `_2` replacement。
2. 修復 chengde Camera 09 的 local NVR／RTSP 問題，並盤點目前第二路錄影查詢失敗。
3. 驗證 Hub camera/current-state entities、Recorder history 與正式 Dashboard；不以 debug Web UI 作正式 UI 驗收。
4. 製作非工程人員只看截圖的正式 Dashboard，以及工程用站點表格／current statistics view。

## Blockers

- chengde Camera 09 local RTSP 仍失敗，當下另有一路錄影查詢失敗；正式 Dashboard 與 Recorder UI 尚未驗收。

## Temporary / last-known

- `8122` 是獨立舊正式服務，永久不在本任務操作範圍。
- 使用者於 35 分鐘時明確接受停止舊 observation；不得改寫成一小時 PASS。
- 不在此文件保存 host、private URL、credentials、backup path 或 JPEG／footage。
