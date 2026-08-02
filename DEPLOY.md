# Manual Deployment

## 目的

現階段只部署一個站點，由使用者與 AI agent 透過 SSH 手動執行。

本文件是部署說明，不是自動化腳本。

不使用：

- `9space-ha-ops` GitHub Actions
- self-hosted runner
- 多站點 rollout
- 自動修改 Home Assistant `.storage`
- 自動 config-entry migration

`9space-ha-ops` 暫時保留作為歷史參考；新流程成功使用兩次後可 archive。

## M4 安全邊界

- `8122` 是獨立舊正式服務，永久禁止操作。
- 不得對 `8122` 執行 test、restart、rebuild、reload、modify，亦不得把 `8122` 當成 integration URL。
- 本 repository 的 monorepo add-on 目前已部署為 `0.3.1`，host port 是 `8222`，container port 是 `8000`。
- Supervisor add-on identifier／slug 與 internal hostname 不可混用。
- 若本輪只需完成 M4 integration 切換，預設走「快速路徑」：唯讀確認 add-on 正常後，跳過 add-on 上傳、rebuild 與 restart，只部署 integration。
- 任何 add-on 上傳、rebuild、restart、rollback 操作都必須再次取得使用者明確授權後才可執行。
- 不得直接編輯 `/config/.storage/core.config_entries` 或其他 `.storage` 檔案。
- 不得自動建立或接受帶有 `_2` 後綴的 replacement entities。

## 變數

部署前由操作者明確設定，不要寫進 repository：

```bash
export HA_HOST="<site-host-or-ip>"
export HA_SSH_PORT="2222"
export HA_USER="root"
export REMOTE="${HA_USER}@${HA_HOST}"

export ADDON_DIR_NAME="9space_snapshot_api"
export ADDON_REMOTE_DIR="/addons/${ADDON_DIR_NAME}"
export INTEGRATION_DOMAIN="nvr_monitor"
export INTEGRATION_REMOTE_DIR="/config/custom_components/${INTEGRATION_DOMAIN}"

export ADDON_SLUG="<actual-supervisor-addon-slug>"
export ADDON_HOSTNAME="afa94ae2-9space-snapshot-addon"
export ADDON_HOST_PORT="8222"
export ADDON_CONTAINER_PORT="8000"
export INTEGRATION_BASE_URL="http://${ADDON_HOSTNAME}:${ADDON_CONTAINER_PORT}"
```

變數用途必須明確區分：

- `ADDON_SLUG`：只供 `ha addons ...` 指令使用。
- `ADDON_SLUG`：必須以 Supervisor 實際輸出為準；目前觀察到的 identifier 是 `afa94ae2_9space_snapshot_addon`。
- `ADDON_HOSTNAME`：只供 Home Assistant Core 連到 add-on container。
- `ADDON_HOST_PORT`：只供 host-side smoke test。
- `ADDON_CONTAINER_PORT`：add-on container 內固定監聽 port，現值 `8000`。
- `INTEGRATION_BASE_URL`：integration 應填入 `http://${ADDON_HOSTNAME}:8000`。

不要把真實 IP、password、SSH private key 或 NVR credentials 寫入本文件、prompt 或 commit。

## 部署前檢查

本機：

```bash
git status --short
test -f 9space_snapshot_api/config.yaml
test -f custom_components/nvr_monitor/manifest.json
python3 -m compileall -q custom_components/nvr_monitor
```

遠端：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" '
  set -eu
  command -v ha
  test -d /config
  test -d /addons
  ha core info >/dev/null
'
```

確認 Supervisor 看到的 add-on slug，避免把 slug 與 hostname 混用：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" \
  "ha addons list | grep -i -A4 -B2 '9space\|snapshot'"
```

若輸出與預期不同，先停止並回報實際結果，再更新本次操作使用的 `ADDON_SLUG`。不要自行猜測。

## M4 共用備份（所有路徑都必須先執行）

無論是快速路徑、Generic Add-on Deployment、或後續 integration 部署，皆必須先完成備份並記錄 backup path。

若 config-entry backup 任一步驟失敗，deployment gate 立即關閉，禁止進入 smoke test、integration upload、或任何 add-on operation。

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  BACKUP=/config/9space_backups/$STAMP
  mkdir -p \"\$BACKUP\"

  if [ -d '$INTEGRATION_REMOTE_DIR' ]; then
    cp -a '$INTEGRATION_REMOTE_DIR' \"\$BACKUP/integration\"
  fi

  if [ -d '$ADDON_REMOTE_DIR' ]; then
    cp -a '$ADDON_REMOTE_DIR' \"\$BACKUP/addon\"
  fi

  test -f /config/.storage/core.config_entries
  cp -a /config/.storage/core.config_entries \
    \"\$BACKUP/core.config_entries\"
  test -s \"\$BACKUP/core.config_entries\"

  echo \"BACKUP=\$BACKUP\"
"
```

記下輸出的 backup path。`/config/.storage/core.config_entries` 在 M4 只允許複製作備份，不得編輯、不得直接覆寫。

## M4 快速路徑（預設）

目前 M4 預設不重新部署 add-on，因為 monorepo add-on `0.3.1` 已部署且 smoke test 通過。本輪主要部署 integration。

只有在以下三個唯讀檢查都正常時，才可跳過 add-on 上傳、rebuild 與 restart：

- `http://127.0.0.1:${ADDON_HOST_PORT}/healthz`
- `http://127.0.0.1:${ADDON_HOST_PORT}/api/camera/1`
- `http://127.0.0.1:${ADDON_HOST_PORT}/api/v1/channels`

host-side smoke test 範例：

```bash
test "$ADDON_HOST_PORT" = "8222"
test "$ADDON_HOST_PORT" != "8122"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  test '$ADDON_HOST_PORT' = '8222'
  test '$ADDON_HOST_PORT' != '8122'
  curl -fsS 'http://127.0.0.1:$ADDON_HOST_PORT/healthz'
"
```

Legacy endpoint smoke test：

```bash
CAMERA_ID=1
test "$ADDON_HOST_PORT" = "8222"
test "$ADDON_HOST_PORT" != "8122"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  test '$ADDON_HOST_PORT' = '8222'
  test '$ADDON_HOST_PORT' != '8122'
  code=\$(curl -sS -o /tmp/legacy_snapshot_response.bin -w '%{http_code}' \
    'http://127.0.0.1:$ADDON_HOST_PORT/api/camera/$CAMERA_ID')
  test \"\$code\" = "200"
  test -s /tmp/legacy_snapshot_response.bin
  rm -f /tmp/legacy_snapshot_response.bin
"
```

Channels endpoint smoke test：

```bash
test "$ADDON_HOST_PORT" = "8222"
test "$ADDON_HOST_PORT" != "8122"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  test '$ADDON_HOST_PORT' = '8222'
  test '$ADDON_HOST_PORT' != '8122'
  curl -fsS 'http://127.0.0.1:$ADDON_HOST_PORT/api/v1/channels'
"
```

若上述三項都正常：

1. 不上傳 add-on source。
2. 不執行 `ha addons rebuild`。
3. 不執行 `ha addons restart`。
4. 確認已完成「M4 共用備份」且已有 backup path 紀錄。
5. 直接進行 integration 部署。

若任一項失敗：

1. 停止自動操作並回報實際錯誤。
2. 由使用者決定是否授權走下方 `Generic Add-on Deployment`。

## Generic Add-on Deployment（目前預設跳過）

以下步驟只在使用者明確授權 add-on 操作時使用。

執行前先確認已完成「M4 共用備份」且已有 backup path 紀錄。

### 1. 上傳 add-on source

使用 tar，避免直接在遠端逐檔修改：

```bash
tar -C . -czf /tmp/9space_snapshot_api.tgz 9space_snapshot_api

scp -P "$HA_SSH_PORT" \
  /tmp/9space_snapshot_api.tgz \
  "$REMOTE:/tmp/9space_snapshot_api.tgz"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  rm -rf '$ADDON_REMOTE_DIR.new'
  mkdir -p '$ADDON_REMOTE_DIR.new'
  tar -xzf /tmp/9space_snapshot_api.tgz \
    --strip-components=1 \
    -C '$ADDON_REMOTE_DIR.new'
  test -f '$ADDON_REMOTE_DIR.new/config.yaml'

  rm -rf '$ADDON_REMOTE_DIR.old'
  if [ -d '$ADDON_REMOTE_DIR' ]; then
    mv '$ADDON_REMOTE_DIR' '$ADDON_REMOTE_DIR.old'
  fi
  mv '$ADDON_REMOTE_DIR.new' '$ADDON_REMOTE_DIR'
  rm -f /tmp/9space_snapshot_api.tgz
  ha addons reload
"
```

### 2. Rebuild／restart add-on

先確認 `ADDON_SLUG`，再執行：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  ha addons rebuild '$ADDON_SLUG'
  ha addons restart '$ADDON_SLUG'
"
```

若目前 HA CLI 不支援 `rebuild`，不要猜替代命令；回報實際 `ha addons --help` 輸出，再決定。

### 3. Add-on smoke test

Generic add-on deployment 完成後，只驗證 monorepo add-on host port，不得碰 `8122`：

```bash
test "$ADDON_HOST_PORT" = "8222"
test "$ADDON_HOST_PORT" != "8122"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  test '$ADDON_HOST_PORT' = '8222'
  test '$ADDON_HOST_PORT' != '8122'
  curl -fsS 'http://127.0.0.1:$ADDON_HOST_PORT/healthz'
  curl -fsS 'http://127.0.0.1:$ADDON_HOST_PORT/api/v1/channels'
"
```

確認 legacy endpoint：

```bash
CAMERA_ID=1
test "$ADDON_HOST_PORT" = "8222"
test "$ADDON_HOST_PORT" != "8122"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  test '$ADDON_HOST_PORT' = '8222'
  test '$ADDON_HOST_PORT' != '8122'
  code=\$(curl -sS -o /tmp/legacy_snapshot_response.bin -w '%{http_code}' \
    'http://127.0.0.1:$ADDON_HOST_PORT/api/camera/$CAMERA_ID')
  test \"\$code\" = "200"
  test -s /tmp/legacy_snapshot_response.bin
  rm -f /tmp/legacy_snapshot_response.bin
"
```

## Integration 部署

只在 add-on 新 API 與 integration client 都已在本機測試後執行。

執行前先確認已完成「M4 共用備份」且已有 backup path 紀錄。

### 1. 上傳 integration

```bash
tar -C custom_components -czf /tmp/nvr_monitor.tgz nvr_monitor

scp -P "$HA_SSH_PORT" \
  /tmp/nvr_monitor.tgz \
  "$REMOTE:/tmp/nvr_monitor.tgz"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  rm -rf '$INTEGRATION_REMOTE_DIR.new'
  mkdir -p '$INTEGRATION_REMOTE_DIR.new'
  tar -xzf /tmp/nvr_monitor.tgz \
    --strip-components=1 \
    -C '$INTEGRATION_REMOTE_DIR.new'
  test -f '$INTEGRATION_REMOTE_DIR.new/manifest.json'

  rm -rf '$INTEGRATION_REMOTE_DIR.old'
  if [ -d '$INTEGRATION_REMOTE_DIR' ]; then
    mv '$INTEGRATION_REMOTE_DIR' '$INTEGRATION_REMOTE_DIR.old'
  fi
  mv '$INTEGRATION_REMOTE_DIR.new' '$INTEGRATION_REMOTE_DIR'
  rm -f /tmp/nvr_monitor.tgz

  ha core check
  ha core restart
"
```

### 2. 等待 Home Assistant

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" '
  set -eu
  ready=false
  for _ in $(seq 1 36); do
    if ha core stats >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 5
  done
  test "$ready" = true
'
```

### 3. UI Reconfigure 既有 integration

本階段不寫 config-entry migration。優先對既有 `nvr_monitor` entry 使用 Reconfigure，以保留 `entry_id`、`subentry_id`、entity identity 與 Dashboard 對應。

在 Home Assistant UI：

1. 找到既有 `nvr_monitor` config entry。
2. 執行 Reconfigure。
3. 輸入 `http://${ADDON_HOSTNAME}:8000`。
4. 完成驗證並儲存。
5. 確認既有 entry、subentries、entity identities 都保留。

不要要求使用者先刪除 entry。不要由 AI 直接編輯 `/config/.storage/core.config_entries`。

### 4. 若使用者選擇刪除／重建或停用後重建

刪除／重建或停用後重建不是預設路徑，但也不禁止。若使用者明確選擇這條路，文件必須先要求記錄：

1. 現有 config entry。
2. 14 個 channel 與 subentry 對應。
3. 現有 entity IDs。
4. Dashboard 使用情況。

刪除／重建前必須先警告：

- 重建後 `entry_id` 與 `subentry_id` 可能改變。
- 可能產生新的 entity identity。
- 可能破壞 Dashboard 對應。
- 不得自動建立或接受 `_2` entities。
- 不得直接修改 `.storage`。

## Reconfigure 失敗時的停止條件

若 Reconfigure 不存在、無法開啟或驗證失敗：

1. 停止自動操作。
2. 回報實際錯誤。
3. 由使用者決定下一步：
   A. 修正 internal add-on URL 後重試 Reconfigure。
   B. 刪除並重新建立 config entry。
   C. Rollback integration。

Engineer 不得自行替使用者選擇。

## 實機驗證

至少確認：

```text
[ ] /healthz 正常（monorepo add-on host port = 8222）
[ ] 舊 /api/camera/{camera_id} 正常（monorepo add-on host port = 8222）
[ ] /api/v1/channels 正常（monorepo add-on host port = 8222）
[ ] integration base URL = http://${ADDON_HOSTNAME}:8000
[ ] Reconfigure 後既有 entry、subentries、entity identities 保留
[ ] snapshot Content-Type 為 image/jpeg
[ ] integration 不再要求 NVR password
[ ] NVR channel live-video entities 正常
[ ] recording entities 正常
[ ] Ping entities 由 Home Assistant Ping integration 提供
[ ] Home Assistant log 沒有 nvr_monitor traceback
[ ] Add-on log 沒有 credentials 或完整 RTSP URL
```

查看 log：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" '
  ha core logs | grep -iE "nvr_monitor|traceback|error" | tail -n 100 || true
'
```

Add-on log 命令依實際 slug：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" \
  "ha addons logs '$ADDON_SLUG' | tail -n 100"
```

## Rollback

### Integration rollback

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  ha core stop
  rm -rf '$INTEGRATION_REMOTE_DIR'
  if [ -d '$INTEGRATION_REMOTE_DIR.old' ]; then
    mv '$INTEGRATION_REMOTE_DIR.old' '$INTEGRATION_REMOTE_DIR'
  fi
  ha core start
"
```

還原 integration source 後，必須重新啟動 Core。

若 entry data 已改成 `addon_base_url`：

1. 透過舊版 UI Reconfigure 恢復舊 NVR 設定。
2. 不直接覆寫或編輯 `.storage`。
3. 若缺少舊 credentials，停止並交由使用者處理。

### Add-on rollback

Add-on rollback 不是 M4 預設路徑，只有在使用者明確授權時才執行：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  rm -rf '$ADDON_REMOTE_DIR'
  if [ -d '$ADDON_REMOTE_DIR.old' ]; then
    mv '$ADDON_REMOTE_DIR.old' '$ADDON_REMOTE_DIR'
  fi
  ha addons reload
  ha addons rebuild '$ADDON_SLUG'
  ha addons restart '$ADDON_SLUG'
"
```

## 部署完成紀錄

每次手動部署只記錄：

```text
date:
git commit:
site alias:
add-on version:
integration version:
backup path:
smoke test:
rollback needed:
notes:
```

不要記錄真實 NVR credential 或 snapshot。
