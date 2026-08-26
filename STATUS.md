# Status

Updated: 2026-08-10

## Current

- Branch: `main`.
- Functional release history 只以 fast-forward push 至 `origin/main`；未 force push、未改寫歷史。
- Source identities: `9Space Snapshot` app `0.3.14` (`9space_snapshot`), `9Space Hub` app `0.3.4` (`9space_hub`), `9Space Hub` integration `0.1.1` (`nine_space_hub`), `9Space NVR Monitor` integration `0.2.11` (`nine_space_nvr_monitor`)。本次 Snapshot／Hub source 尚未部署；NVR integration `0.2.11` 已部署兩站。
- 舊 Center source 已改名並重構為 `nine_space_hub/` Supervisor app，顯示名稱 `9Space Hub`／`9Space 中樞`。
- Hub 已移除 generic telemetry ingest、NVR live／recording current state、HA/Ping producer 與重複 entities；只負責 snapshot registration、跨站拉圖、last-good JPEG 與 RAM since-restart 成功率／成功失敗 counters。
- Hub 不再保存或要求 per-site options。Snapshot app 只新增一個 `hub_ip` hostname；HTTP scheme、Hub port/path 與站點 Snapshot port 固定。registration 不傳 URL，Hub 由 Tailscale peer 或 Hub MagicDNS suffix 加 `site_id` 自動推導站點 hostname。
- Snapshot app 的 Dahua RTSP／HTTP ports 固定為 `554`／`80`；`rtsp_port` 與 `nvr_http_port` 已從 config schema/runtime reads 移除。Supervisor 保留的舊 option keys 只為升級相容而忽略。
- `nine_space_nvr_monitor` runtime 固定使用 Supervisor internal Snapshot URL；新增／Reconfigure UI 不再要求 app base URL。舊 entry key 會被忽略，不改 entry、subentry 或 entity identity。
- 新 `nine_space_hub` component `0.1.1` 只提供 snapshot camera、最近嘗試、圖片年齡／延遲與 since-restart 成功率／counters；Home Assistant Recorder 是狀態歷史唯一持有者。
- Local `nine_space_nvr_monitor` 仍保留最完整站點資訊與 local Ping；不取 Hub snapshot、不建立 Snapshot camera。
- `nine_space_nvr_monitor` 已移除沒有 Dahua event producer 的 motion/video-loss/video-blind entities；保留 `camera_problem` 與 `diagnostic_status`。Snapshot API 新增錄影缺口數／總時長／最大缺口與 RTSP first-packet／probe timing，integration 沿用既有 debug unique IDs。
- `nine_space_nvr_monitor` 所有現行 entities 預設啟用；config-entry v2 migration 只把舊版由 integration 預設停用的 registry entries 一次性啟用，不覆蓋日後的使用者手動停用。
- 本次 root tests `73/73`、Snapshot focused tests `17/17` PASS；compileall、JSON 與 `git diff --check` PASS。Snapshot FastAPI lifecycle 全套有三個既知 TestClient/AnyIO shutdown `TIMEOUT`，未宣告整套 PASS。NVR integration 兩站實機部署／Core recovery PASS；Snapshot／Hub 本次未部署。

## Deployed

- 中央 Home Assistant 已 additive 安裝新 `custom_components/nine_space_hub` 與 `custom_components/nine_space_nvr_monitor` source；舊 `nine_space_monitor_hub`／`nvr_monitor` source 與 config entries 仍保留運作。`ha core check`、Core recovery PASS，新 domain restart 後去敏 error count `0`，未編輯 `.storage`。
- daan-forest 目前 Hub `0.3.2`、Snapshot app `0.3.13`；chengde 目前 Snapshot app `0.3.13`。三者 state `started`；source 中的 `0.3.4`／`0.3.14` 尚未部署。
- Snapshot app 以 bounded 直接 MagicDNS lookup 解決 container split-DNS 缺失；同機 Hub 使用 Supervisor internal hostname。Hub discovery 已自動註冊 `daan-forest` 與 `chengde` 共兩站。
- daan-forest Tailscale app 已由使用者關閉 userspace networking；daan-forest→chengde `8222/healthz` PASS，ACL allow-all 無需修改。
- daan-forest snapshot attempts `3/3` success。chengde `13/14` success；Camera 09 為 local `rtsp_timeout`／`recording_query_failed`／`snapshot_unavailable`，其餘跨站 snapshot path 正常。
- Snapshot `0.3.12` metadata contract 兩站欄位完整；RTSP probe timing daan-forest `3/3`、chengde `14/14` 有值。錄影缺口 chengde `12/14` 有值；其餘反映當下錄影查詢失敗／NVR unreachable，不是欄位缺失。
- 兩站已安裝新 domain `nine_space_nvr_monitor` `0.2.11` source，`ha core check`、Core recovery PASS，restart 後去敏 error count `0`。chengde 目前沒有 NVR config entry，可直接由 UI 新增新版；daan-forest 仍有舊 `nvr_monitor` entry，須先由 UI 刪除舊 entry 再新增新版以避免 identity 衝突。
- chengde registry 已建立 gap count／total／largest 各 `14` 個；camera RTSP response、first-packet、probe-duration、ONVIF port、RTSP port 各 `14/14` 啟用，整個舊 `nvr_monitor` registry 的 integration-disabled／user-disabled 均為 `0`。舊 event entities 留為 orphan，未直接編輯 `.storage`。
- 舊 Penguin Center 的 Tailscale Serve 8765 已關閉，`9space-center-center-1` container 已移除；唯讀驗證為 `No serve config` 且無同名 container。
- 安全審核未批准永久刪除舊 Center image、data volume 與 private env；它們仍殘留但不提供服務。
- 8122 未操作；未編輯 `.storage`。

## Next

1. 由 HA UI 在 chengde 新增 `9Space NVR Monitor`；daan-forest 先刪除舊 `NVR Monitor` entry，再新增新版。Hub／Snapshot `0.3.4`／`0.3.14` 發布後再部署並驗證 registration／snapshot entities。
2. 修復 chengde Camera 09 的 local NVR／RTSP 問題，並盤點目前第二路錄影查詢失敗。
3. 驗證 Hub camera/current-state entities、Recorder history 與正式 Dashboard；不以 debug Web UI 作正式 UI 驗收。
4. 製作非工程人員只看截圖的正式 Dashboard，以及工程用站點表格／current statistics view。

## Blockers

- chengde Camera 09 local RTSP 仍失敗，當下另有一路錄影查詢失敗；正式 Dashboard 與 Recorder UI 尚未驗收。

## Temporary / last-known

- `8122` 是獨立舊正式服務，永久不在本任務操作範圍。
- 使用者於 35 分鐘時明確接受停止舊 observation；不得改寫成一小時 PASS。
- 不在此文件保存 host、private URL、credentials、backup path 或 JPEG／footage。
