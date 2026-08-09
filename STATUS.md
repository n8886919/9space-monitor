# Status

Updated: 2026-08-09

## Current

- Branch: `agent/m5e-center-observability`
- Functional release commit: `b3d3741`.
- Local/deployed versions: add-on `0.3.7`; integration `0.2.5`.
- Recording/live aggregate correction 已發布並部署至承德：有效片段數、24 小時錄影覆蓋率、日在線率與在線轉非在線斷線次數均由 Snapshot add-on 提供，history 僅保留 bounded RAM，不送 Center。
- M5E v0.3.5 已部署至承德並完成使用者接受的 35 分鐘 observation。
- Ping (ICMP)／RTT／packet loss 保留在 Home Assistant：integration 不再送 `ha.ping`，Center 不再接受或顯示 Ping，dashboard renderer 產生 local current／rolling 1h／rolling 24h cards。
- Deployment contract 已改為 Snapshot add-on 僅走既有 Supervisor managed Git repository；禁止 HA local `/addons` source install。Center 維持獨立 Git checkout／container deployment，不是 HA add-on。
- Release commit `b3d3741` 已 push 至 task branch，並以 fast-forward 發布至 `origin/main`。

## Deployed

- Center 已部署 exact M5E code commit `5c5876b`；container health、UI、API、SQLite integrity 與 running source hash 均 PASS。
- 先前承德 add-on `0.3.5` rollout 的 options hash 保持不變，並完成使用者接受的 35 分鐘 observation。
- 承德 add-on 已由相同 Supervisor managed repository 更新至 `0.3.7`，並建立 scoped update backup；health、14 channels 與四個 aggregate 欄位 contract PASS，錄影與 live metrics 均為 14/14 ready。
- 先前 `0.3.5` immediate smoke PASS：health、14 channels、legacy multipart contract、v1 snapshot status/content-type/size 與 Center ingest；驗證未讀取或輸出 JPEG body。
- 35 分鐘 observation：producer running、Center reachable、queue `0/100`、producer/scheduler drop `0`、live 與 recording `14/14`，DB 容量低且穩定。
- Snapshot attempt 大多為 `14/14` success；曾有單一 sample 短暫 `13/14`，立即恢復並持續 `14/14`，last-good 保留。
- Integration `0.2.5` 已通過 layout gate、`ha core check`、Core restart/recovery 與 restart 後 log 驗證；既有 1 個 entry、14 個 subentries、56 個 metrics entity IDs 均保留，disabled 0、replacement entity 0。

## Next

1. 以 live entity registry 確認各 camera 的原生 Ping binary sensor、RTT average 與 packet-loss entity IDs；RTT／loss 預設停用時由使用者在 HA UI 啟用。
2. 由使用者在指定 HA dashboard view 的 UI code editor 套用 renderer 產生的 NVR + local Ping cards；不得以 API 或直接編輯 `.storage` 代替。
3. Recording/live aggregate correction 已完成 UI runtime 驗收，本次 integration transaction 已清除。Center 不需變更。
4. 既有 Center `ha.ping` rows 可依七日 retention 自然淘汰；若要求立即刪除，需另做精確 destructive data approval。

## Blockers

目前沒有 recording/live aggregate correction blocker；使用者已確認片段數、覆蓋率與斷線次數顯示正常。

## Temporary / last-known

- `8122` 是獨立舊正式服務，不在 M5E 操作範圍。
- 使用者於 35 分鐘時明確接受停止 observation；未完成原先規劃的一小時，不得改寫成一小時 PASS。
- Integration `0.2.5` 本次 deployment transaction 已在 UI runtime 驗收後清除。
- 不在此文件保存 host、URL、credentials、backup path 或 JPEG／footage。
