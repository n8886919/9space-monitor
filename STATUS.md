# Status

Updated: 2026-08-09

## Current

- Branch: `agent/m5e-center-observability`
- Functional release commit: `97fd83e`.
- Local/deployed versions: add-on `0.3.8`; integration `0.2.7`.
- Live-history ownership correction 已發布並部署：add-on 不保存 24 小時 live history，integration 以 bounded RAM 計算在線率與斷線次數，Home Assistant Recorder 是唯一持久化歷史；舊 add-on RAM history 已直接捨棄，未 migration。
- Recorder bootstrap correction 已部署：integration 啟動時唯讀重建最近 24 小時 live window，在線率以已知狀態持續時間加權；HA reload 的 unavailable／unknown 不產生假斷線。
- 使用者已確認 dashboard 在線率顯示正常；本次 `0.2.7` deployment transaction 與其中的 `0.2.6` rollback 已清除。
- 有效片段數與 24 小時錄影覆蓋率仍由 Snapshot add-on 的最新 NVR query 提供；在線率與在線轉非在線斷線次數只留在 Home Assistant local，不送 Center。
- M5E v0.3.5 已部署至承德並完成使用者接受的 35 分鐘 observation。
- Ping (ICMP)／RTT／packet loss 保留在 Home Assistant：integration 不再送 `ha.ping`，Center 不再接受或顯示 Ping，dashboard renderer 產生 local current／rolling 1h／rolling 24h cards。
- Deployment contract 已改為 Snapshot add-on 僅走既有 Supervisor managed Git repository；禁止 HA local `/addons` source install。Center 維持獨立 Git checkout／container deployment，不是 HA add-on。
- Release commit `97fd83e` 已 push 至 task branch，並以 fast-forward 發布至 `origin/main`。

## Deployed

- Center 已部署 exact M5E code commit `5c5876b`；container health、UI、API、SQLite integrity 與 running source hash 均 PASS。
- 先前承德 add-on `0.3.5` rollout 的 options hash 保持不變，並完成使用者接受的 35 分鐘 observation。
- 承德 add-on 已由相同 Supervisor managed repository 更新至 `0.3.7`，並建立 scoped update backup；health、14 channels 與四個 aggregate 欄位 contract PASS，錄影與 live metrics 均為 14/14 ready。
- 承德 add-on 已由相同 managed repository 更新至 `0.3.8` 並建立 scoped backup；14/14 `live_checked_at` ready，add-on live-history fields 0，Center 最新 14 個 `nvr.live` events 含 live aggregates 0。
- 先前 `0.3.5` immediate smoke PASS：health、14 channels、legacy multipart contract、v1 snapshot status/content-type/size 與 Center ingest；驗證未讀取或輸出 JPEG body。
- 35 分鐘 observation：producer running、Center reachable、queue `0/100`、producer/scheduler drop `0`、live 與 recording `14/14`，DB 容量低且穩定。
- Snapshot attempt 大多為 `14/14` success；曾有單一 sample 短暫 `13/14`，立即恢復並持續 `14/14`，last-good 保留。
- Integration `0.2.5` 已通過 layout gate、`ha core check`、Core restart/recovery 與 restart 後 log 驗證；既有 1 個 entry、14 個 subentries、56 個 metrics entity IDs 均保留，disabled 0、replacement entity 0。
- Integration `0.2.6` 已通過 layout gate、`ha core check`、Core restart/recovery 與 logs；既有 1 個 entry、14 個 subentries、28 個在線率／斷線 entity IDs 保留，replacement entity 0。
- Integration `0.2.7` 已通過 layout gate、`ha core check`、Core restart/recovery、逐檔 source hash 與 logs；Recorder restore failure 0、14 個 exact live entity mappings、replacement entity 0，並完成 UI runtime 驗收。

## Next

1. 以 live entity registry 確認各 camera 的原生 Ping binary sensor、RTT average 與 packet-loss entity IDs；RTT／loss 預設停用時由使用者在 HA UI 啟用。
2. 由使用者在指定 HA dashboard view 的 UI code editor 套用 renderer 產生的 NVR + local Ping cards；不得以 API 或直接編輯 `.storage` 代替。

## Blockers

目前沒有 Recorder bootstrap 或 UI runtime blocker。

## Temporary / last-known

- `8122` 是獨立舊正式服務，不在 M5E 操作範圍。
- 使用者於 35 分鐘時明確接受停止 observation；未完成原先規劃的一小時，不得改寫成一小時 PASS。
- Integration `0.2.5` 本次 deployment transaction 已在 UI runtime 驗收後清除。
- 不在此文件保存 host、URL、credentials、backup path 或 JPEG／footage。
