# Status

Updated: 2026-08-09

## Current

- Branch: `agent/m5e-center-observability`
- HEAD: `902eaa9`
- Code versions: add-on `0.3.5`; integration `0.2.3`.
- M5 Center telemetry、producer、dashboard 與 last-good snapshot 功能已在 code 中；目前待完成 M5E deployment observation。

## Deployed

- Add-on／integration 的遠端 deployed versions 本次未驗證；任何先前記錄都只能視為 last known。
- M5F Center prototype 曾運作（last known，未於本次重新驗證）。

## Next

1. 部署 M5E add-on v0.3.5 與相關 integration code 到指定測試站點。
2. 完成至少一小時 final observation，記錄去敏的 producer、queue/drop、容量、live/recording 與 snapshot-attempt metadata。
3. 依當次 live evidence 更新 deployed versions 與 observation 結果。

## Blockers

None. M5E v0.3.5 deploy + 1h observation 尚未執行屬於 Next，不是 blocker。

## Temporary / last-known

- `8122` 是獨立舊正式服務，不在 M5E 操作範圍。
- 遠端版本、服務健康與 HA entity/UI 狀態必須在部署當次重新驗證。
- 不在此文件保存 host、URL、credentials、backup path 或 JPEG／footage。
