# Session handoff

更新：2026-08-08。此文件供新的 PM／Engineer session 直接接管；公開 repository 不記錄真實站點 IP、credentials、影像或 `.storage`。

## 已完成狀態

- Canonical `origin/main`：`c1aa4a3`（M5F merge）。本地 branch `agent/m5e-center-observability` 的 M5E code commit 為 `5c5876b`；v0.3.5 尚未 push、尚未部署。
- 保護所有使用者未追蹤檔案：不得讀取、修改、搬移或加入 Git。
- 較早 deployment-prep 歷史證據：PR #3、備份 `/config/9space_backups/20260808T093731Z.EHpMep`、`ha core check` 與 Core restart 曾 PASS；這些不是 v0.3.5 的部署證據。8122 未操作。
- 已驗證 live metadata：承德 14 live、14 recording、1 health，drop=0；Center snapshot mapping 為 channel 1..14，channel 14 attempt success。不得記錄 URL、IP 或 JPEG。
- M5F Center prototype 已運作；v0.3.5 的完整一小時 observation 尚未開始。不得在未重新驗證前宣稱精確 deployed add-on 或 integration version。
- integration 不再建立 Snapshot camera entity；既有 Reconfigure 與 entity identity 歷史證據可保留，但未重新驗證不作目前部署宣稱。
- `8122` 是獨立舊正式 instance：永久禁止操作。只可變更 monorepo 的新 add-on/integration。

## 使用者決策

- M5 是多站 telemetry 與 Center，約十站；每站頻道數、entity IDs 與 Ping entity 均不同，必須手動 site mapping。
- 承德：`site_id: chengde`，顯示名稱「承德」。Center port 為 `8765`。
- Center 位於 Tailscale 內網；M5 不使用 per-site token。不得寫入任何真實 IP／token／credentials。
- Add-on push NVR telemetry；integration push allowlisted HA telemetry，不由 Center pull 8222。
- producer 無 telemetry/history disk persistence：24h RAM ring + bounded memory queue；Center 失聯直接丟資料可接受。
- Center Docker + SQLite 保存最近七天；單站採 logical 保守水位，並以 2 GiB 實體檔 fail-closed guard 保護主機容量。具體預設值待 Center 實作完成後以測試核對；不可宣稱 SQLite 物理檔必然等於 logical quota。
- M5F 已完成：Center 可保存及傳輸每 site/channel 唯一一張最後成功 JPEG，存於獨立 bounded snapshot store，atomic replace、無 history；JPEG 不得進 telemetry SQLite、export、log、fixture 或 Git。Center 只可透過站點 add-on snapshot API 取圖，不能直接連 NVR。
- dashboard 放 repository template，按 site mapping render 為 UI-ready YAML；拆 NVR/recording、Ping/network、diagnostics 三表。可依站點安裝 `card-mod`。
- HA telemetry 白名單：Home Assistant Ping、System Monitor、Raspberry Pi Power、Fast.com。Ping probe 不由本專案重做。
- 快速開發：新 add-on/integration 可中斷 1–2 小時；一般 task 不強制 rollback/獨立 review。仍必須保護 8122、舊 add-on、`.storage`、HA Core；integration restart 前 `ha core check` 不可略過。
- 所有後續開發都由 PM 開 subagent 執行。一般 task 用低成本模型；legacy/security/concurrency/database schema/HA lifecycle 才做一次輕量 review。
- 使用者明確要求不使用 Sonnet；後續 subagent 優先選擇當下可用的低成本模型，僅在資料庫、安全或並發任務提高 reasoning。
- 使用者的個人 side project 可採精簡開發、驗證與部署流程；核心要求是機密資訊不得進 Git，不必加入繁瑣企業級流程。
- 每個使用者批准的工作必須由 subagent 執行。Router v3 每個 task route once；native Luna 不可用。僅在明確 enable 且 bounded 時才使用 OpenRouter external worker，不要每個 task 強制探測。Plus 不可被假定等於 API 或 OpenRouter capability。
- 使用者表示這可能是本 session 最後一次傳話；將仍有用的決策與偏好保留於本文件，但避免另建膨脹設計文件。

## M5 工作順序

1. M5A：Center Docker skeleton、SQLite schema、ingest API、7-day retention、logical 保守水位與 2 GiB 實體檔 fail-closed guard、sanitized export；不部署。
2. M5B：Add-on NVR telemetry model + 24h RAM ring + bounded async push queue；沒有 disk history，沒有 JPEG。
3. M5C：Integration allowlisted HA entity exporter；不得阻塞 HA startup/coordinator。
4. M5D：dashboard site mapping 格式與 renderer；先做承德 sample mapping，不放真實 host/IP。
5. M5E：使用者明確批准後，在承德部署並觀察至少一小時，實測容量；再擴展其他站。

## 下一個 bounded task：M5E v0.3.5 deploy + 1h final observation

- 使用者明確批准後，部署本地 `5c5876b` 的 v0.3.5；不碰 8122、`.storage` 或未追蹤檔案。
- 完成至少一小時 observation，記錄 producer、queue/drop、容量、live/recording metadata 與 Center snapshot attempt 的去敏結果；不得記錄 URL、IP 或 JPEG。
- 重新驗證當次部署版本、`ha core check`、必要 restart 與 user-owned HA UI/entity 狀態；不要將較早 deployment-prep 證據當作本次結果。

## 指標與資料

NVR：`live_video`、error code、RTSP control/RTP 診斷、24h online rate、24h disconnect count、recording count/coverage/gaps、last recording/age、query duration/page count、snapshot metadata（僅成功／耗時／bytes，沒有影像）。

HA：Ping reachable/RTT/jitter/loss、disk/memory/CPU/load/temperature、RPi under-voltage、Fast.com speed。非即時硬體資料以小時彙整；producer 可較高頻取樣但 Center 僅保存彙整值。

容量初估（不含影像）：單站典型 20–45 MB/週，高量 70–150 MB/週；十站典型 200–450 MB/週。這是 logical payload 估算，不是 SQLite 實體檔案承諾；需靠 2 GiB 實體檔 fail-closed guard 防止異常資料耗盡空間。

## 承德 HA 唯讀盤點

- Ping、Fast.com、Raspberry Pi Power 及既有 NVR entity registry 已配置。
- System Monitor 已安裝，但 73 個 entities 目前均為 `disabled_by=integration`；因 Home Assistant API 回 `401`，未能以 live state 確認。
- M5C 前由使用者僅在 HA UI 啟用指定的 System Monitor entities；不得修改 `.storage` 或以 API 繞過 UI。具體清單由 M5C 實作接近完成時再提出。

## ChromeOS Center

- ChromeOS Linux disk 已擴至可用約 22 GB，可進行 Docker/Center 開發；Docker 尚未安裝，未經批准不得安裝。
- 長期建議移至 Raspberry Pi 5 + SSD/NVMe，不使用 microSD 保存資料庫。
- ChromeOS 僅適合作為原型；螢幕關閉不等於可進入睡眠，使用者需接電源並設定保持喚醒。Crostini 重開後需重新確保 service 啟動。

## 新 session 啟動檢查

1. `git status --short`；保護所有使用者未追蹤檔案，絕不讀取或處理它們。
2. Router v3 route once；native Luna 不可用，OpenRouter external worker 僅在明確 enable 且 bounded 時使用。
3. 確認 branch/HEAD，閱讀 `README.md`、`AGENTS.md`、本文件；API 任務再讀 `API.md`。
4. 只取一個 bounded task，開 Engineer subagent；不要自行跨 milestone。
5. 任何 SSH、Docker install、部署、HA UI 都需使用者當次明確批准。
