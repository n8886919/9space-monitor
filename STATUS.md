# Status

Updated: 2026-10-07

## 2026-10-07 Hub health worker isolation

- Hub source `0.3.8` 將 `/healthz` HTTP probe 移至專用工作池，production 最多 4 workers，與 snapshot／檔案工作分開。停止時取消 pending jobs 並關閉工作池；restart 建立新工作池。
- 60 秒探測間隔、2 秒 timeout、連續 3 次失敗判紅與一次成功恢復的判斷不變。
- 抓圖工作池飽和的 fake regression 與工作池 stop／restart lifecycle tests PASS；Hub `42/42`、syntax、diff check PASS。Root suite `102/104` PASS，仍為先前已確認的兩項 Notification Log FAIL。
- Source commit `8d62210` 已 push 至既有 `origin/main`；中央 HA 透過既有 managed repository UI、勾選更新前 scoped backup，完成 `0.3.7` → `0.3.8`。UI version／running／startup complete PASS，日誌未見 ERROR／traceback。GitHub CI hassfest PASS，validate 仍因相同兩項 Notification Log 測試 FAIL。
- 排隊造成的誤報機制已由 host test 驗證修正；實際站點的網路延遲／負載與持續觀察另行驗證。

## 2026-10-07 Hub toggle persistence

- Hub source `0.3.7` 將每個 site/channel 的啟用選擇保存於既有 bounded、atomic site registry；restart／更新後載入，heartbeat 不覆蓋仍存在的頻道選擇。
- 舊 registry 相容載入並預設啟用；移除的 channel 清除設定，新增 channel 預設啟用。保存失敗回去敏 `503` 且不改 RAM 或原檔案；health／attempt／counters 仍只在 RAM。
- Hub targeted tests `40/40`、compileall 與 `git diff --check` PASS，包含 restart、heartbeat、舊格式、寫入失敗與 concurrent choices。
- Root suite `100/102` PASS；Notification Log 的版本與 auth contract 共兩項 FAIL，在未修改的 `HEAD` 上亦可重現，不屬本次 Hub 變更。
- Source commit `9e5e0bb` 已 push 至既有 `origin/main`。GitHub CI：hassfest PASS，validate 因上述兩項既有 Notification Log 測試 FAIL。
- 中央 HA 已透過既有 managed repository UI 更新 Hub `0.3.6` → `0.3.7`；更新前已勾選 Supervisor scoped backup。UI version／running 與更新後 startup complete PASS，未見 ERROR／traceback。
- Restart 設定保留由 fake-based tests 驗證；本次實機未額外切換頻道或重啟作 persistence 驗收。以下其他部署資訊仍為先前 last-known。

## Current

- Branch: `main`.
- Functional release history 只以 fast-forward push 至 `origin/main`；未 force push、未改寫歷史。
- Source identities: `9Space Snapshot` app `0.3.14` (`9space_snapshot`), `9Space Hub` app `0.3.8` (`9space_hub`), `9Space Hub` integration `0.1.3` (`nine_space_hub`), `9Space NVR Monitor` integration `0.2.11` (`nine_space_nvr_monitor`)。Hub integration `0.1.3` 已部署中央 HA；NVR integration `0.2.11` 已部署兩站。
- 舊 Center source 已改名並重構為 `nine_space_hub/` Supervisor app，顯示名稱 `9Space Hub`／`9Space 中樞`。
- Hub 已移除 generic telemetry ingest、NVR live／recording current state、HA/Ping producer 與重複 entities；只負責 snapshot registration、跨站拉圖、last-good JPEG 與 RAM since-restart 成功率／成功失敗 counters。
- Hub 不再保存或要求 per-site options。Snapshot app 只新增一個 `hub_ip` hostname；HTTP scheme、Hub port/path 與站點 Snapshot port 固定。registration 不傳 URL，Hub 由 Tailscale peer 或 Hub MagicDNS suffix 加 `site_id` 自動推導站點 hostname。
- Snapshot app 的 Dahua RTSP／HTTP ports 固定為 `554`／`80`；`rtsp_port` 與 `nvr_http_port` 已從 config schema/runtime reads 移除。Supervisor 保留的舊 option keys 只為升級相容而忽略。
- `nine_space_nvr_monitor` runtime 固定使用 Supervisor internal Snapshot URL；新增／Reconfigure UI 不再要求 app base URL。舊 entry key 會被忽略，不改 entry、subentry 或 entity identity。
- 新 `nine_space_hub` component `0.1.3` 只提供 snapshot camera、截圖成功、圖片年齡／延遲、since-restart 成功率／counters，以及每站可連線／上次可連線 entities；已移除上次截圖嘗試。Home Assistant Recorder 是狀態歷史唯一持有者。
- Local `nine_space_nvr_monitor` 仍保留最完整站點資訊與 local Ping；不取 Hub snapshot、不建立 Snapshot camera。
- `nine_space_nvr_monitor` 已移除沒有 Dahua event producer 的 motion/video-loss/video-blind entities；保留 `camera_problem` 與 `diagnostic_status`。Snapshot API 新增錄影缺口數／總時長／最大缺口與 RTSP first-packet／probe timing，integration 沿用既有 debug unique IDs。
- `nine_space_nvr_monitor` 所有現行 entities 預設啟用；config-entry v2 migration 只把舊版由 integration 預設停用的 registry entries 一次性啟用，不覆蓋日後的使用者手動停用。
- 本次 root tests `89/89` PASS；Hub targeted tests `17/17`、compileall、JSON 與 `git diff --check` PASS。中央 HA Hub integration `0.1.3` source swap、`ha core check`、Core recovery、registry migration 與 restart 後 log check PASS；authenticated live-state API 因 HTTP 401 未驗證，待 UI 確認。

## Deployed

- 中央 Home Assistant 已部署 `custom_components/nine_space_hub` `0.1.3`；Hub app 本次更新至 `0.3.8` 並確認正常執行。先前升級 migration 由 config entry v1 升至 v2，精準移除 `119` 個舊 Hub NVR／錄影／last-attempt registry entries，建立 8 站／89 鏡頭共 `728` 個 snapshot-only／site entities，無 `_2` replacement。未直接編輯 `.storage`，先前部署前已保存 scoped source、entity-registry 與 config-entry rollback。
- daan-forest 目前 Snapshot app `0.3.13`；chengde 目前 Snapshot app `0.3.13`。兩者 state `started`；source 中的 Snapshot `0.3.14` 尚未部署。
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
