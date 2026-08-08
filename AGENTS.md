# Agent Instructions

本文件是本 repository 的最小入口與文件 router。不要預先載入完整文件樹或舊 session 歷史；以 code、tests、Git 與任務相關文件為準。

## 文件 routing

- 需要目前進度、下一步或 last-known 部署狀態：讀 `STATUS.md`。
- API contract 或 compatibility 工作：讀 `API.md`。
- SSH、部署、smoke test 或 rollback：讀 `DEPLOY.md`。
- Home Assistant／NVR 唯讀診斷：讀 `.agents/skills/9space-diagnostic/SKILL.md`。
- 其他普通 code、docs、tests 工作：直接檢查 `git status --short`、相關程式與測試；不強制讀 `README.md` 或上述專門文件。

`DESIGN_NOTES.md`、`ADDON_DEVELOPMENT_PLAN.md` 與舊 conversation 不屬於 active specification。文件與程式衝突時不要猜測；保留現有對外相容行為並回報證據。

## 架構與相容性不變量

- Add-on 是唯一可直接連接 Dahua NVR、保存 NVR host／ports／username／password、建立 RTSP URL、呼叫 Dahua CGI、執行 ffmpeg snapshot 的元件。
- Integration 只透過 local add-on API 取得 NVR／錄影狀態；不得取得 snapshot、建立 Snapshot camera entity 或重做 Home Assistant Ping integration。
- `GET /api/camera/{camera_id}` 的 path、status codes、multipart boundary、JSON／JPEG 結構與欄位保持相容，除非使用者明確批准 breaking change。
- Home Assistant entity identity 必須保留；不得用 `_2` replacement 規避衝突，不得自行重寫 unique IDs。
- Add-on／integration producer 只使用 24 小時 RAM ring 與 bounded memory queue，不把 telemetry history 寫入磁碟。
- Center telemetry SQLite 不得保存 JPEG。Center snapshot store 只能 bounded、atomic replace 地保存每個 site/channel 唯一一張最後成功 JPEG，且不得建立 history 或把 JPEG 寫入 export、log、fixture、Git。
- Center 只能經站點 add-on snapshot API 取圖，不能直接連 NVR。每站 channel count 與 entity IDs 由 mapping 明定，不可假設固定數量或命名。
- M5 Tailscale 內網不使用 per-site token；不得自行新增公開 auth flow 或公開暴露 Center。

## 永久安全邊界

- `8122` 是獨立舊正式服務；除非使用者明確且逐項指定，禁止 test、restart、rebuild、reload、modify 或以它替代本 repository 的驗證目標。
- 不直接編輯 Home Assistant `.storage`，不以 API 或檔案操作繞過使用者負責的 HA UI 步驟。
- 不把 credentials、真實站點 IP、private URL、完整 RTSP URL、JPEG／footage 或敏感 stderr/body 寫入 Git、tests、logs、diagnostics 或回報。
- 不改 repository 名稱、integration domain `nvr_monitor`、add-on slug `9space_snapshot_addon`、container port `8000` 或既有 host port `8122`。
- 不 commit、push、release 或部署，除非任務明確要求。

## 風險分級與自主性

單一 agent 是預設。Router、subagent、外部 worker 與獨立 review 都是收益明確時才使用的可選優化，不是每個 task 的 ceremony。

普通、可逆且 bounded 的工作直接完成：inspect → edit → targeted test → deploy if requested → verify → report。不要因一個 task 包含 fix、upload、restart 與 smoke test 就拆成多次批准；部署任務本身已授權 `DEPLOY.md` 中 scoped、可回復的 SSH／upload／restart／verify 操作。

只有下列高風險或 materially ambiguous 操作才先取得明確確認並加強 plan／backup／驗證：

- 操作 8122 或其他受保護正式服務；
- 編輯 `.storage`、credentials／auth、public exposure、firewall／Tailscale ACL；
- destructive data operation、persistent database migration／schema change；
- force push／history rewrite、breaking public API、unsafe entity identity change；
- destructive rollback，或無法判定正確目標／拓撲的遠端變更。

遇到 gate 失敗，保留現場並回報；不要猜 host、port、slug、credentials、schema 或 UI 結果。需要 HA UI 的步驟交由使用者操作。

## 實作與驗證

1. 先確認 working tree，不覆蓋使用者修改或處理不相關的未追蹤檔案。
2. 只讀任務相關程式、測試與專門文件，做最小修改，不跨到未要求的功能。
3. 行為修正先以最小 fake-based regression test 重現；不需要真實 NVR、公開站點資訊或影像 fixture。
4. 執行 targeted tests、syntax check 與 `git diff --check`；integration restart 前必須通過 `ha core check`。
5. 涉及 legacy API、安全、並發、database schema 或 HA lifecycle 時，加一次聚焦檢查。
6. 中文回報修改檔案、行為變化、實際 PASS／FAIL／TIMEOUT、未執行項目與剩餘風險；不要自行開始下一個不相關 milestone。
