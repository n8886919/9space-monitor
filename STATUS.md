# Status

Updated: 2026-08-10

## Current

- Branch: `main`.
- Functional release commits: `023170b`、`8229d86`、`8a7acac`、`19186d9` 與 `64e2340`，已 fast-forward push 至 `origin/main`；未 force push、未改寫歷史。
- Source/current test deployments: Snapshot add-on `0.3.11`; Hub add-on `0.3.2`; `nvr_monitor` integration `0.2.7`。
- 舊 Center source 已改名並重構為 `nine_space_monitor_hub/` Supervisor add-on，顯示名稱 `9Space Monitor Hub`／`9Space 監控中樞`。
- Hub 不使用 SQLite、events/export 或 rolling history；telemetry／snapshot attempt 只保存最新值於 RAM。每個 site/channel 只持久化一張 atomic-replace last-good JPEG。
- Hub 不再保存或要求 per-site options。Snapshot add-on 只新增一個 `hub_ip` hostname；HTTP scheme、Hub port/path 與站點 Snapshot port 固定。registration 不傳 URL，Hub 由 Tailscale peer 或 Hub MagicDNS suffix 加 `site_id` 自動推導站點 hostname。
- Snapshot add-on 的 Dahua RTSP／HTTP ports 固定為 `554`／`80`；`rtsp_port` 與 `nvr_http_port` 已從 config schema/runtime reads 移除。Supervisor 保留的舊 option keys 只為升級相容而忽略。
- 新 `nine_space_monitor_hub` component `0.1.0` 提供 camera、live／recording／snapshot binary sensors 與 current metric sensors；Home Assistant Recorder 是狀態歷史唯一持有者。
- Local `nvr_monitor` 仍保留最完整站點資訊與 local Ping；不取 Hub snapshot、不建立 Snapshot camera。
- Root tests `96/96` PASS；最新相關 Snapshot／Hub suites `62/62` PASS；compileall、shell syntax 與 `git diff --check` PASS。完整 suite 在既有 Snapshot lifecycle tests 於本 sandbox TIMEOUT；本機 Docker build 因無 daemon permission FAIL，兩站 Supervisor managed update/build PASS。

## Deployed

- 中央 Home Assistant 已安裝 `custom_components/nine_space_monitor_hub` source；本次 `ha core check` PASS，未編輯 `.storage`。
- daan-forest 已更新 Hub `0.3.2` 與 Snapshot add-on `0.3.11`；chengde 已更新 Snapshot add-on `0.3.11`。三者 state `started`、version gate PASS、去敏 log error count `0`，兩站 `8222/healthz` PASS。
- Snapshot add-on 以 bounded 直接 MagicDNS lookup 解決 container split-DNS 缺失；同機 Hub 使用 Supervisor internal hostname。Hub discovery 已自動註冊 `daan-forest` 與 `chengde` 共兩站。
- daan-forest snapshot attempts `3/3` success、available `3/3`。chengde 完整一輪 `0/14` success：`13 snapshot_timeout`、`1 snapshot_fetch_failed`。
- daan-forest SSH namespace 至 chengde `8222/healthz` TIMEOUT，Tailscale ping FAIL；工作站與 chengde local 的 8222 health PASS。失敗層定位為 daan-forest→chengde tailnet direction／ACL，而非 Snapshot API process。
- 舊 Penguin Center 的 Tailscale Serve 8765 已關閉，`9space-center-center-1` container 已移除；唯讀驗證為 `No serve config` 且無同名 container。
- 安全審核未批准永久刪除舊 Center image、data volume 與 private env；它們仍殘留但不提供服務。
- 承德 Snapshot add-on 已更新；既有 `nvr_monitor` deployment 未修改，8122 未操作。

## Next

1. 唯讀確認 Tailscale ACL／route，修通 daan-forest→chengde 的 8222 direction；不要改 Snapshot/NVR 或以 8122 替代。
2. 方向修通後等待一輪，確認 chengde `14/14` attempts 與 last-good snapshot。
3. 在中央 HA UI 新增 `9Space Monitor Hub` integration，填入 Supervisor internal hostname 與 container port `8765`；不得編輯 `.storage`。
4. 驗證 camera/current-state entities、Recorder history 與正式 Dashboard；不以 debug Web UI 作正式 UI 驗收。

## Blockers

- daan-forest→chengde tailnet direction／ACL 尚未修通；chengde Hub snapshot 與中央 entities/Recorder 驗收因此未完成。

## Temporary / last-known

- `8122` 是獨立舊正式服務，永久不在本任務操作範圍。
- 使用者於 35 分鐘時明確接受停止舊 observation；不得改寫成一小時 PASS。
- 不在此文件保存 host、private URL、credentials、backup path 或 JPEG／footage。
