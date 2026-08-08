# Agent Instructions

本文件適用於 GitHub Copilot、Codex、Claude 與其他 coding agent。

## 文件優先順序

開始任何工作前依序讀取：

1. `README.md`
2. 本文件
3. 本次任務需要時才讀 `API.md`
4. 只有部署任務才讀 `DEPLOY.md`

不要再使用 `DESIGN_NOTES.md` 或 `ADDON_DEVELOPMENT_PLAN.md` 作為 active specification。

若文件與現有程式衝突：

1. 不要猜測。
2. 保留現有對外相容行為。
3. 回報實際程式證據與衝突。
4. 除非任務明確要求，不自行擴大範圍。

## 不可違反的架構規則

- Add-on 是唯一可直接連接 Dahua NVR 的元件。
- 只有 add-on 可保存 NVR host、ports、username、password。
- Integration 不得建立 NVR RTSP URL。
- Integration 不得直接呼叫 Dahua CGI API。
- Integration 不得執行 NVR ffmpeg snapshot、呼叫 add-on Snapshot endpoint 或建立 Snapshot camera entity。
- Integration 透過 local add-on API 取得 NVR 狀態與錄影狀態。
- 攝影機 Ping 使用 Home Assistant Ping integration，本專案不重做。
- 既有 `GET /api/camera/{camera_id}` 必須保持相容，直到使用者明確批准修改。
- M5 開發 Center telemetry server；Center 只處理診斷 metadata，不處理 JPEG。
- M5 Tailscale 內網不使用 per-site token；不要自行新增公開 auth flow。
- 不把 NVR credentials、真實站點 IP、snapshot 或完整 RTSP URL 寫入 git、tests、logs 或 diagnostics。

## 範圍限制

除非任務明確要求，不做以下工作：

- 不改 repository 名稱。
- 不改 integration domain `nvr_monitor`。
- 不改 add-on slug `9space_snapshot_addon`。
- 不改既有 host port 8122 或 container port 8000。
- 不刪除舊 snapshot endpoint。
- 不建立多站點自動部署。
- 不加入 GitHub Actions 部署流程。
- 不修改 Home Assistant `.storage`。
- 不建立自動 config-entry migration。
- 不製作 custom Lovelace card。
- 不新增 live-stream 技術。
- 不重寫所有 entities 或 unique IDs。
- 不加入大型 framework、database、message queue 或不必要 dependency。
- 不 commit、push、release 或 SSH 部署，除非使用者本次明確要求。

## 節省 token 與修改量

- 一次只完成 README 的一個 bounded M5 task。
- 先讀指定檔案，不掃描整個 repository 後再開始。
- 不為了「更乾淨」重構無關程式。
- 不產生本任務未要求的設計文件。
- 優先搬移及重用已存在、可測試的程式。
- 只建立支援目前行為所需的最小 abstraction。
- 測試使用 fake adapter/client，不要求真實 NVR。
- 不建立完整 CI matrix。
- 不在同一個變更中同時搬 repository、改 API、改 entities 與部署。

## 標準工作流程

1. 執行 `git status --short`。
2. 確認不會覆蓋使用者尚未提交的修改。
3. 讀 README 中本次 milestone。
4. 找出最少需要修改的檔案。
5. 先建立或更新最小測試。
6. 完成最小實作。
7. 執行與變更相關的測試及 syntax check。
8. 搜尋 NVR password／RTSP URL 是否仍出現在 integration source。
9. 回報：
   - 修改檔案
   - 行為變化
   - 實際執行的驗證
   - 未執行項目
   - 剩餘風險
10. 停止，不自動開始下一 milestone。

## Integration 邊界

Integration 可以負責：

- Config flow
- Home Assistant entities
- DataUpdateCoordinator
- Add-on API client
- unavailable／reload／unload
- translations
- diagnostics redaction
- 必要且既有的攝影機區域網路 service probe

Integration 不可以負責：

- NVR credential
- NVR RTSP DESCRIBE／SETUP／PLAY
- NVR RTP packet probe
- Dahua `mediaFileFind.cgi`
- NVR snapshot ffmpeg
- 自製 camera ping/history

## Add-on 邊界

Add-on 可以負責：

- NVR config
- RTSP URL builder
- ffmpeg snapshot
- snapshot cache
- NVR live-video probe
- Dahua recording query
- NVR error mapping
- Local HTTP API
- 以獨立、非阻塞 worker push NVR telemetry 到 Center

Add-on 不負責：

- Home Assistant entities
- Dashboard
- Ping camera IP
- Center polling
- Multi-site deployment
- User-facing permissions
- JPEG telemetry、JPEG upload、JPEG export 或 JPEG history

## M5 Center telemetry 邊界

- Center 使用 Docker/SQLite，只保存最近七天資料；單站採 logical 保守水位，並以 2 GiB 實體檔 fail-closed guard 保護主機容量。具體預設值以 Center 實作完成後的測試為準，不得宣稱 SQLite 物理檔必然等於 logical quota。
- Add-on 與 integration producer 不得將 telemetry/history 寫入磁碟；只可使用 24 小時 RAM ring 與 bounded memory queue。Center 不可達時允許丟棄資料。
- 永久不得保存、傳輸、匯出、fixture 化或記錄 JPEG；只允許 snapshot 成功與耗時等 metadata。
- Add-on 只 push NVR telemetry；integration 只 push allowlisted Home Assistant Ping、System Monitor、RPi Power、Fast.com entities。
- 每站 channel count 與 entity IDs 必須由 site mapping 手動定義；不得假設 14 channels 或 `01`～`14` 命名。
- Dashboard 維護為 repository template，拆為 NVR/recording、Ping/network、diagnostics；不得提交真實站點 IP、credentials、影像或 `.storage`。

## 相容性規則

舊 endpoint：

```text
GET /api/camera/{camera_id}
```

在目前 milestone：

- Path 不變。
- 成功及失敗 status code 不變。
- Multipart boundary 及 JSON/JPEG 結構不主動修改。
- JSON 欄位不刪除或重新命名。
- 同事不需要修改 client。

新程式可以讓舊 endpoint 內部共用 service，但外部 response 必須由測試鎖定。

## 停止條件

遇到以下任一情況，停止修改並回報：

- 需要真實 NVR password 才能繼續。
- 需要修改正在使用的舊 API response。
- 需要修改 integration entity unique ID。
- 發現未提交修改會被覆蓋。
- Home Assistant add-on schema 無法支援設計。
- 任務需要直接修改 `.storage`。
- 任務開始跨到下一 milestone。
- 無法執行指定測試。
- 需要公開站點 IP 或影像才能建立 fixture。

## M5 快速開發與部署

- 使用者已接受新 add-on/integration 暫時中斷 1–2 小時；一般 M5 task 不強制 rollback 設計或獨立 code review。
- 只要維持 8122、舊 add-on、`.storage` 與 Home Assistant Core 的安全邊界，即可採 bounded task、相關測試、patch version 的快速修復流程。
- 任務涉及 legacy API、安全、並發、資料庫 schema 或 Home Assistant lifecycle 時，仍需一次輕量唯讀檢查。
- Integration restart 前仍必須執行 `ha core check`；不得為快速流程略過此檢查。
- 每個由使用者批准的工作都應由 subagent 執行；PM 不代替 Engineer 實作。

## 長期操作規則

以下規則長期有效，適用於所有 milestone，不只本次任務：

- Home Assistant entity 若與舊 entity 衝突，不得建立名稱或 unique ID 後綴 `_2`；M3（或任何之後改動 entity 的任務）應保留／覆蓋既有 entity identity。若無法安全完成，停止交由使用者處理，不自行選擇一個折衷方案。
- 任何需要 Home Assistant 網頁 UI 才能完成的操作，必須停止，列出最少操作步驟交給使用者，不嘗試用其他方式繞過。
- 遇到規格不清、可能影響現有正在使用的服務（例如 8122 正式 instance），或較適合由使用者操作（例如需要真實 credentials、需要網頁 UI）的事項，停止並回報，不得因使用者暫時沒有回覆而自行猜測後繼續擴大範圍。
- SSH 可使用 `ssh -p 22 root@<site-host>`（實際 host 由操作者提供，不寫入本文件或任何 repository 檔案）；除非任務明確核准部署，否則 SSH 只能用於唯讀檢查（例如 `ha apps info`、`ha apps list`、`curl` 讀取 endpoint、`tail` log），不得執行 restart、update、rebuild 或修改遠端檔案。
- 回報一律使用中文。
- 不得把真實 credential（NVR password、真實站點 IP 等）寫入 AGENTS.md 或任何 repository 檔案；這類資訊只允許暫時存在於當次對話或終端機輸出中。

## 完成回報格式

```text
完成：
- ...

修改：
- path/to/file: ...

驗證：
- command: PASS/FAIL

未執行：
- ...

風險：
- ...
```
