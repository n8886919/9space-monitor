# Manual Deployment

## 目的

現階段只部署一個站點，由使用者與 AI agent 透過 SSH 手動執行。

不使用：

- `9space-ha-ops` GitHub Actions
- self-hosted runner
- 多站點 rollout
- 自動修改 Home Assistant `.storage`
- 自動 config-entry migration

`9space-ha-ops` 暫時保留作為歷史參考；新流程成功使用兩次後可 archive。

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
```

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

確認 add-on 目前實際 slug：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" \
  "ha addons list | grep -i -A4 -B2 '9space\|snapshot'"
```

不要假設 Supervisor slug 一定等於 directory name。後續命令以實際輸出的 slug 為準：

```bash
export ADDON_SLUG="<actual-supervisor-addon-slug>"
```

## 第一次 monorepo 遷移

第一次只移動程式，不同時修改 integration config。

### 1. 建立遠端備份

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  BACKUP=/config/9space_backups/$STAMP
  mkdir -p \"\$BACKUP\"

  if [ -d '$ADDON_REMOTE_DIR' ]; then
    cp -a '$ADDON_REMOTE_DIR' \"\$BACKUP/addon\"
  fi

  if [ -d '$INTEGRATION_REMOTE_DIR' ]; then
    cp -a '$INTEGRATION_REMOTE_DIR' \"\$BACKUP/integration\"
  fi

  cp -a /config/.storage/core.config_entries \
    \"\$BACKUP/core.config_entries\" 2>/dev/null || true

  echo \"BACKUP=\$BACKUP\"
"
```

記下輸出的 backup path。

### 2. 上傳 add-on source

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

### 3. Rebuild／restart add-on

先確認 slug，再執行：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  ha addons rebuild '$ADDON_SLUG'
  ha addons restart '$ADDON_SLUG'
"
```

若目前 HA CLI 不支援 `rebuild`，不要猜替代命令；回報實際 `ha addons --help` 輸出，再決定。

### 4. Smoke test add-on

從站點內部測試，避免 DNS／公網轉發干擾：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" '
  set -eu
  curl -fsS http://127.0.0.1:8122/healthz
'
```

Legacy endpoint：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" '
  set -eu
  CAMERA_ID=1
  curl -sS \
    -o /tmp/legacy_snapshot_response.bin \
    -w "%{http_code}\n" \
    "http://127.0.0.1:8122/api/camera/${CAMERA_ID}"
  test -s /tmp/legacy_snapshot_response.bin
  rm -f /tmp/legacy_snapshot_response.bin
'
```

確認同事原本使用的 URL 仍正常。不要在 log 輸出 JPEG body。

## Integration 部署

只在 add-on 新 API 與 integration client 都已在本機測試後執行。

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

### 3. 手動重新設定 integration

本階段不寫 config-entry migration。

在 Home Assistant UI：

1. 記錄目前 channel 名稱、group 與 entity 使用情況。
2. 刪除或停用舊 `nvr_monitor` config entry。
3. 重新加入 integration。
4. 輸入 local add-on base URL。
5. 重新建立必要 channel 設定。
6. 檢查 entity IDs 與 Dashboard。

不要由 AI 直接編輯 `/config/.storage/core.config_entries`。

## 實機驗證

至少確認：

```text
[ ] /healthz 正常
[ ] 舊 /api/camera/{camera_id} 正常
[ ] /api/v1/channels 正常
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

若 config entry 已手動刪除，不要直接覆蓋 `.storage`。使用 UI 重新加入，或由使用者明確批准後再從完整備份復原。

### Add-on rollback

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
