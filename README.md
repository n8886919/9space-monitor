# 9Space Monitor

`9space-monitor` 是此專案的唯一主要 repository，整合：

- Home Assistant custom integration：`custom_components/nvr_monitor`
- Home Assistant local add-on：`9space_snapshot_api`
- 未來 center 所需的 local API 邊界
- 單一站點的手動 SSH 部署文件

## 先讀順序

所有開發工具（GitHub Copilot、Codex、Claude）都使用相同入口：

1. `README.md`：目標、架構、目前里程碑與工作順序
2. `AGENTS.md`：修改規則、禁止事項與驗證要求
3. `API.md`：現有 API 相容性與 integration ↔ add-on 契約
4. `DEPLOY.md`：單一站點手動 SSH 部署與 rollback

`DESIGN_NOTES.md` 與 `ADDON_DEVELOPMENT_PLAN.md` 已由以上四份文件取代，不再是 active specification。

## 目前目標

只完成一個站點的整合：

1. 將 `9space-snapshot-addon` 內容移入本 repository。
2. 保持既有 snapshot API 相容，不能影響正在使用它的同事。
3. Add-on 成為唯一直接存取 Dahua NVR 的元件。
4. Integration 不再保存或使用 NVR host、port、username、password。
5. Integration 經 local add-on API 取得：
   - NVR channel 是否有實際影像
   - 最近錄影狀態
   - snapshot
6. 攝影機 Ping、在線率、RTT 等資料改用 Home Assistant 既有 Ping integration，不在本專案重做。
7. 由使用者與 AI agent 透過 SSH 手動部署到單一測試／正式站點。
8. 保留未來 center 使用 local API 的穩定邊界，但現在不開發 center。

## 架構邊界

```text
現在：

Home Assistant
├── nvr_monitor integration
│   ├── 建立 entities、config flow、diagnostics
│   ├── 可保留必要的攝影機區域網路 service probe
│   ├── 不直接連接 NVR
│   └── 不保存 NVR credentials
│
└── 9space_snapshot_api add-on
    ├── 唯一保存 NVR credentials
    ├── NVR RTSP live-video probe
    ├── Dahua recording query
    ├── snapshot 與 cache
    └── local API
            │
            ▼
        Dahua NVR
```

### 未來 center TODO

```text
同事固定主機
      │
      ▼
    Center
      │
      │ Tailscale
      ▼
各站點 local add-on API
      │
      ▼
    Dahua NVR
```

必須保留的 TODO：

- [ ] Center 完成後，由 center 透過 Tailscale 呼叫各站點 local API。
- [ ] 同事的固定主機改成只呼叫 center，不再直接呼叫站點。
- [ ] 同事完成切換後，再決定舊 `/api/camera/{camera_id}` 的驗證、停用或移除方式。
- [ ] Center 上線後，移除不必要的 local API 公網 port forwarding。
- [ ] Center 的驗證、權限與快取規格另案設計，不提前塞進 local add-on。

## 現階段不做

- 多站點自動部署
- GitHub Actions 部署 pipeline
- 自動 config-entry migration
- 修改 Home Assistant `.storage`
- Center
- 客製 Lovelace card
- 錄影回放
- 新的 live-stream 技術
- Ping、24 小時在線率、RTT、jitter 或 packet-loss 實作
- 大型效能測試矩陣
- 複雜的 release／checksum／environment approval 系統
- Repository 改名或 add-on slug 變更

## Repository 結構

```text
9space-monitor/
├── 9space_snapshot_api/
│   ├── config.yaml
│   ├── Dockerfile
│   ├── run.sh
│   ├── requirements.txt
│   ├── src/
│   └── tests/
├── custom_components/
│   └── nvr_monitor/
├── tests/
├── scripts/
├── AGENTS.md
├── API.md
├── DEPLOY.md
└── README.md
```

實際移入 add-on 時，保留 Home Assistant add-on repository 所需的第一層 add-on 目錄。不要同時重新命名 slug、port 或 options。

## Current milestone

### M1：合併 repository，不改行為

- [ ] 將 `9space-snapshot-addon/9space_snapshot_api` 複製到本 repository 根目錄。
- [ ] 保留 add-on slug `9space_snapshot_addon`。
- [ ] 保留 container port `8000` 與目前 host port `8122`。
- [ ] 保留舊 `GET /api/camera/{camera_id}` response。
- [ ] 確認 add-on 可 build、start，舊 endpoint 可取得 snapshot。
- [ ] 不修改 integration 行為。

完成後再進 M2。

### M2：建立最小 local API 邊界

- [ ] Add-on 提供 `API.md` 所列的最小 endpoint。
- [ ] 舊 endpoint 仍使用原本 response 格式。
- [ ] 把 NVR RTSP live-video probe 搬到 add-on。
- [ ] 把 Dahua recording query 搬到 add-on。
- [ ] Add-on 對外 response 不包含 credentials、RTSP URL 或完整 ffmpeg stderr。
- [ ] 使用少量 fake-based unit tests，不要求真實 NVR。

完成後再進 M3。

### M3：Integration 改用 add-on

- [ ] 建立簡單 async local API client。
- [ ] Integration config flow 改為設定 add-on base URL。
- [ ] Integration 不再要求或保存 NVR credentials。
- [ ] 移除 integration 內 NVR RTSP、Dahua HTTP recording client。
- [ ] 移除自製 Ping coordinator 與 `icmplib` dependency。
- [ ] 保留仍有必要的攝影機區域網路 service probe；不要在同一階段重寫。
- [ ] Entities 名稱及 unique ID 儘量保持不變，降低 Dashboard 破壞。
- [ ] Add-on unavailable 時，相關 entities 應變成 unavailable，不阻塞 HA event loop。

### M4：手動部署與實機 smoke test

依 `DEPLOY.md`：

- [ ] 備份現有 add-on、integration 與 config entry 資料。
- [ ] SSH 部署 add-on。
- [ ] 確認舊 API 仍可供同事使用。
- [ ] SSH 部署 integration。
- [ ] 手動重新設定 integration；不寫自動 migration。
- [ ] 確認 snapshot、live-video status、recording status。
- [ ] 確認 Home Assistant Ping integration 負責攝影機在線狀態。
- [ ] 記錄 rollback 路徑。

## 最小測試

自動測試只要求：

1. 舊 endpoint response 相容。
2. 新 channel endpoint 可用 fake NVR result 回 JSON。
3. 新 snapshot endpoint 回 `image/jpeg`。
4. Integration API client 可解析 channel response。
5. Integration source/config flow 不再使用 NVR password。
6. Integration manifest 不再依賴 `icmplib`。

實機只要求：

- Add-on 啟動。
- 舊 snapshot endpoint 正常。
- 新 local endpoint 正常。
- 14 個 channel 狀態可讀。
- Integration entities 正常。
- NVR 暫時無法連線時，HA 不被阻塞。
- 回復 NVR 後不需重裝元件。

## 完成定義

本階段完成代表：

- Add-on 與 integration 已位於 `9space-monitor`。
- Add-on 是唯一直接存取 NVR 的程式。
- Integration 不保存 NVR credentials。
- 舊 snapshot API 不變。
- Integration 能透過 local API 顯示既有核心狀態。
- Ping 使用 Home Assistant 現成 integration。
- 單一站點可由 AI agent 依文件手動部署及 rollback。
- 未來 center 呼叫 local API 的邊界已被保留。

不要求 center、多站點自動部署或正式 release pipeline。

## 給 AI agent 的啟動指令

不要只說「讀 README 並完成全部」。

建議依序給三次任務：

```text
讀 README.md 與 AGENTS.md，只完成 M1。不要修改行為、不要部署。完成後回報 diff 與驗證結果。
```

```text
讀 README.md、AGENTS.md、API.md，只完成 M2。保留舊 API，不碰 integration、不部署。
```

```text
讀 README.md、AGENTS.md、API.md，只完成 M3。不要 SSH 部署；完成後回報測試、剩餘風險與 M4 手動步驟。
```

一次要求完成 M1～M3，較容易產生大範圍重構、漏掉相容性或消耗過多 Copilot Max 額度。
