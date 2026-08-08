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
- 本 repository 的 monorepo add-on 版本為 `0.3.3`，host port 是 `8222`，container port 是 `8000`。
- Supervisor add-on identifier／slug 與 internal hostname 不可混用。
- 快速路徑只適用於遠端 source 與已安裝 add-on 都已是 `0.3.3`；不得因舊 `0.3.2` endpoint 可用就跳過 add-on 部署。
- 任何 add-on 上傳、rebuild、restart、rollback 操作都必須再次取得使用者明確授權後才可執行。
- 不得直接編輯 `/config/.storage/core.config_entries` 或其他 `.storage` 檔案。
- 不得自動建立或接受帶有 `_2` 後綴的 replacement entities。

## 變數

部署前由操作者明確設定，不要寫進 repository：

```bash
export HA_HOST="<site-host-or-ip>"
export HA_SSH_PORT="22"
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

- `ADDON_SLUG`：只供 `ha apps ...` 指令使用。
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
  "ha apps list | grep -i -A4 -B2 '9space\|snapshot'"
```

若輸出與預期不同，先停止並回報實際結果，再更新本次操作使用的 `ADDON_SLUG`。不要自行猜測。

## M4 共用備份（所有路徑都必須先執行）

無論是快速路徑、Generic Add-on Deployment、或後續 integration 部署，皆必須先完成備份並記錄 backup path。

若 config-entry backup 任一步驟失敗，deployment gate 立即關閉，禁止進入 smoke test、integration upload、或任何 add-on operation。

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  test -d /config/9space_backups
  BACKUP=\$(mktemp -d "/config/9space_backups/$STAMP.XXXXXX")
  ARTIFACT_DIR=\"\$BACKUP/deployment_artifacts\"
  mkdir \"\$ARTIFACT_DIR\"

  if [ -d '$INTEGRATION_REMOTE_DIR' ]; then
    command -v jq >/dev/null
    jq -e '.domain == "nvr_monitor" and (.version | type == "string")' '$INTEGRATION_REMOTE_DIR/manifest.json' >/dev/null
    test ! -e \"\$ARTIFACT_DIR/integration_predeploy\"
    cp -a '$INTEGRATION_REMOTE_DIR' \"\$ARTIFACT_DIR/integration_predeploy\"
    test -f \"\$ARTIFACT_DIR/integration_predeploy/manifest.json\"
  fi

  if [ -d '$ADDON_REMOTE_DIR' ]; then
    cp -a '$ADDON_REMOTE_DIR' \"\$BACKUP/addon\"
  fi

  test -f /config/.storage/core.config_entries
  cp -a /config/.storage/core.config_entries \
    \"\$BACKUP/core.config_entries\"
  test -s \"\$BACKUP/core.config_entries\"

  echo \"BACKUP=\$BACKUP\"
  echo \"ARTIFACT_DIR=\$ARTIFACT_DIR\"
"
```

記下輸出的 `BACKUP` 與 `ARTIFACT_DIR`；後續 integration 指令以 `BACKUP_PATH` 表示這次輸出的 `BACKUP`。`/config/.storage/core.config_entries` 在 M4 只允許複製作備份，不得編輯、不得直接覆寫。

## M4 快速路徑（預設）

Snapshot API smoke test 是 add-on API 與未來 Center/server 的 contract 驗證；integration 不建立 Snapshot camera entity，也不呼叫 Snapshot endpoint。

只有遠端 source 和 Supervisor 已安裝版本都明確為 `0.3.3`，且以下唯讀檢查都正常時，才可跳過 add-on 上傳、rebuild 與 restart：

- `${ADDON_REMOTE_DIR}/config.yaml` 的 `version: "0.3.3"`
- `ha apps info "$ADDON_SLUG"` 顯示 version `0.3.3`
- `http://127.0.0.1:${ADDON_HOST_PORT}/healthz`
- `http://127.0.0.1:${ADDON_HOST_PORT}/api/camera/1`
- `http://127.0.0.1:${ADDON_HOST_PORT}/api/v1/channels`

版本 gate（唯讀）：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  grep -Fx 'version: \"0.3.3\"' '$ADDON_REMOTE_DIR/config.yaml'
  ha apps info '$ADDON_SLUG' | grep -Eq 'version:[[:space:]]*0\\.3\\.3([[:space:]]|\$)'
"
```

任一版本檢查或 smoke test 失敗時，快速路徑立即關閉；停止並要求使用者另行授權 add-on 上傳、rebuild 與 restart。不得以 `8122` 作替代驗證。

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

v1 Snapshot JPEG smoke test（不輸出 JPEG body）：

```bash
CAMERA_ID=1
test "$ADDON_HOST_PORT" = "8222"
test "$ADDON_HOST_PORT" != "8122"

ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  test '$ADDON_HOST_PORT' = '8222'
  test '$ADDON_HOST_PORT' != '8122'
  tmp_dir=\$(mktemp -d /tmp/9space-v1-snapshot.XXXXXX)
  trap 'rm -rf "\$tmp_dir"' EXIT
  code=\$(curl -sS -D "\$tmp_dir/headers" -o "\$tmp_dir/snapshot.jpg" \
    -w '%{http_code}' \
    'http://127.0.0.1:$ADDON_HOST_PORT/api/v1/channels/$CAMERA_ID/snapshot')
  test \"\$code\" = '200'
  grep -qi '^content-type:[[:space:]]*image/jpeg' "\$tmp_dir/headers"
  test -s "\$tmp_dir/snapshot.jpg"
"
```

若以上版本 gate 與全部 smoke tests 都正常：

1. 不上傳 add-on source。
2. 不執行 `ha apps rebuild`。
3. 不執行 `ha apps restart`。
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
  ha apps reload
"
```

### 2. Rebuild／restart add-on

先確認 `ADDON_SLUG`，再執行：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  ha apps rebuild '$ADDON_SLUG'
  ha apps restart '$ADDON_SLUG'
"
```

若目前 HA CLI 不支援 `rebuild`，不要猜替代命令；回報實際 `ha apps --help` 輸出，再決定。

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

### Integration layout 不變量（fail-closed）

Home Assistant 會檢查 `/config/custom_components` 的第一層目錄。該層中，`domain: "nvr_monitor"` 的 manifest **精確只能有一份**，而且路徑必須是：

```text
/config/custom_components/nvr_monitor/manifest.json
```

因此 `/config/custom_components` 下禁止建立任何 integration 暫存、predeploy、failed、rollback 或 old source，特別是 `.nvr_monitor*`、`nvr_monitor.old*`、`nvr_monitor.bak*`。所有這些 artifacts 只能在 `/tmp` 或這次備份的 `/config/9space_backups/<timestamp>/deployment_artifacts`。

以下 gate 必須在換入前、以及每次 `ha core check`／`ha core restart` 前執行；任何不符都立刻停止，不能以刪除或移動未預先核准的目錄繞過。

下列是唯一的 production helper 定義。每次 remote integration 操作都要將它完整傳入同一個 `bash -s` session；不可複製出不同版本。它以 `jq` 驗證 JSON，缺少 `jq`、manifest 壞掉、version 不符、或 device 不同都會 fail-closed。

```bash
# DEPLOY_LAYOUT_HELPER_BEGIN
require_jq() { command -v jq >/dev/null || { echo 'jq is required' >&2; return 1; }; }
manifest_ok() { test -f "$1/manifest.json" && test -f "$1/__init__.py" && jq -e --arg v "$2" '.domain == "nvr_monitor" and .version == $v' "$1/manifest.json" >/dev/null; }
verify_nvr_monitor_layout() {
  local d m count=0 expected="$CUSTOM_COMPONENTS/nvr_monitor/manifest.json"
  require_jq || return; for d in "$CUSTOM_COMPONENTS"/* "$CUSTOM_COMPONENTS"/.[!.]* "$CUSTOM_COMPONENTS"/..?*; do
    test -d "$d" || continue; m="$d/manifest.json"; test -f "$m" || continue
    jq -e 'type == "object" and (.domain | type == "string")' "$m" >/dev/null || return 1
    if jq -e '.domain == "nvr_monitor"' "$m" >/dev/null; then count=$((count + 1)); test "$m" = "$expected" || return 1; fi
  done; test "$count" -eq 1
}
same_filesystem() { test "$(stat -c %d "$1")" = "$(stat -c %d "$2")"; }
begin_transaction() {
  TXN_DIR=$(mktemp -d "$ARTIFACT_DIR/transaction.XXXXXX") || return 1
  STAGE="$TXN_DIR/stage"; REPLACED="$TXN_DIR/integration_replaced"; FAILED="$TXN_DIR/integration_failed"
  test ! -e "$REPLACED" && test ! -e "$FAILED" && mkdir "$STAGE"
}
restore_canonical() {
  test -d "$REPLACED" || return 1
  if test -d "$CANONICAL"; then mv "$CANONICAL" "$FAILED" || return 1; fi
  mv "$REPLACED" "$CANONICAL" && verify_nvr_monitor_layout && ha core check
}
swap_verified_stage() {
  manifest_ok "$STAGE" "$EXPECTED_VERSION" && verify_nvr_monitor_layout && same_filesystem "$STAGE" "$CUSTOM_COMPONENTS" || return 1
  test -n "${TXN_DIR:-}" && test ! -e "$REPLACED" || return 1
  # Two renames have a short no-canonical window; Core is not reloaded in it.
  mv "$CANONICAL" "$REPLACED" || return 1
  if ! mv "$STAGE" "$CANONICAL" || ! verify_nvr_monitor_layout || ! ha core check; then restore_canonical; return 1; fi
}
filter_logs_after_marker() {
  local marker="$1" output="$2"
  ha core logs | awk -v marker="$marker" '
    {
      # Remove ANSI control sequences before testing timestamp boundaries.
      line=$0
      gsub(/\033\[[0-9;]*[[:alpha:]]/, "", line)
    }
    line ~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9][[:space:]][0-9][0-9]:[0-9][0-9]:[0-9][0-9]/ {
      enabled=(substr(line, 1, 19) >= marker); if (enabled) saw_at_or_after=1
    }
    enabled { print line }
    END { if (!saw_at_or_after) exit 64 }
  ' >"$output"
}
# DEPLOY_LAYOUT_HELPER_END
```

本次現場曾觀察到下列 legacy sibling backup；它們不可在這一輪移動或刪除，但 gate 會因其 manifest 而 fail-closed，須先由使用者另行授權處理：

```text
nvr_monitor.bak_20260731_230508
nvr_monitor.bak_20260801_150229
nvr_monitor.old
nvr_monitor.old.20260802T154701Z
```

### 1. 上傳 integration

```bash
tar -C custom_components -czf /tmp/nvr_monitor.tgz nvr_monitor
awk '/DEPLOY_LAYOUT_HELPER_BEGIN/{keep=1} keep{print} /DEPLOY_LAYOUT_HELPER_END/{exit}' DEPLOY.md \
  > /tmp/nvr_monitor_layout_helper.sh
bash -n /tmp/nvr_monitor_layout_helper.sh

scp -P "$HA_SSH_PORT" \
  /tmp/nvr_monitor.tgz \
  "$REMOTE:/tmp/nvr_monitor.tgz"
scp -P "$HA_SSH_PORT" /tmp/nvr_monitor_layout_helper.sh \
  "$REMOTE:/tmp/nvr_monitor_layout_helper.sh"

ssh -p "$HA_SSH_PORT" "$REMOTE" 'bash -s' <<'REMOTE_SCRIPT'
set -euo pipefail
BACKUP_PATH="<recorded-backup-path>"; EXPECTED_VERSION="0.2.3"
CUSTOM_COMPONENTS=/config/custom_components; CANONICAL="$CUSTOM_COMPONENTS/nvr_monitor"
ARTIFACT_DIR="$BACKUP_PATH/deployment_artifacts"; REPLACED="$ARTIFACT_DIR/integration_replaced"
test -d "$ARTIFACT_DIR"
# The operator has saved the single helper block verbatim as this local remote file.
source /tmp/nvr_monitor_layout_helper.sh
begin_transaction
cleanup() { rm -f /tmp/nvr_monitor.tgz /tmp/nvr_monitor_layout_helper.sh; }; trap cleanup EXIT
tar -xzf /tmp/nvr_monitor.tgz --strip-components=1 -C "$STAGE"
swap_verified_stage
RESTART_MARKER=$(date '+%Y-%m-%d %H:%M:%S'); echo "nvr_monitor restart marker: $RESTART_MARKER"
verify_nvr_monitor_layout; ha core restart || { echo 'restart failed; run the new-transaction rollback below once' >&2; exit 1; }
REMOTE_SCRIPT
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

本階段不寫 config-entry migration。優先對既有 `nvr_monitor` entry 使用 Reconfigure，以保留仍由 integration 提供的 `entry_id`、`subentry_id`、entity identity 與 Dashboard 對應；刻意退役的 `camera.*_snapshot` 不在此保留範圍。

在 Home Assistant UI：

1. 找到既有 `nvr_monitor` config entry。
2. 執行 Reconfigure。
3. 輸入 `http://${ADDON_HOSTNAME}:8000`。
4. 完成驗證並儲存。
5. 確認既有 entry、subentries 與仍由 integration 提供的 entity identities 都保留。
6. 部署後由使用者在 HA UI 移除不再由 integration 提供的 orphan `camera.*_snapshot` entities；不得編輯 `.storage`，也不得建立或接受 `_2` replacement entities。

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
[ ] Reconfigure 後既有 entry、subentries 與非 Snapshot entity identities 保留
[ ] 使用者已在 HA UI 移除 orphan `camera.*_snapshot` entities；未修改 `.storage`，未產生 `_2`
[ ] add-on Snapshot endpoint Content-Type 為 image/jpeg（未來 Center/server contract）
[ ] integration 不再要求 NVR password
[ ] NVR channel live-video entities 正常
[ ] recording entities 正常
[ ] Ping entities 由 Home Assistant Ping integration 提供
[ ] Home Assistant log 沒有 nvr_monitor traceback
[ ] Add-on log 沒有 credentials 或完整 RTSP URL
```

查看 log：

```bash
awk '/DEPLOY_LAYOUT_HELPER_BEGIN/{keep=1} keep{print} /DEPLOY_LAYOUT_HELPER_END/{exit}' DEPLOY.md > /tmp/nvr_monitor_layout_helper.sh
bash -n /tmp/nvr_monitor_layout_helper.sh
scp -P "$HA_SSH_PORT" /tmp/nvr_monitor_layout_helper.sh "$REMOTE:/tmp/nvr_monitor_layout_helper.sh"
ssh -p "$HA_SSH_PORT" "$REMOTE" 'bash -s' <<'REMOTE_SCRIPT'
  set -euo pipefail
  # Set this to the marker printed immediately before this deployment restart.
  RESTART_MARKER="<YYYY-MM-DD HH:MM:SS>"
  source /tmp/nvr_monitor_layout_helper.sh
  log_file=$(mktemp /tmp/nvr-monitor-log.XXXXXX)
  trap 'rm -f "$log_file" /tmp/nvr_monitor_layout_helper.sh' EXIT
  filter_logs_after_marker "$RESTART_MARKER" "$log_file"
  grep -iE "nvr_monitor|traceback|error" "$log_file" || true
REMOTE_SCRIPT
```

只可判讀 marker 之後的 log；不得把 restart 前的歷史 traceback 當成本次部署錯誤。若 Core log 格式不能以此 marker 做時間界線，停止並回報，改由操作者提供可驗證的本次 restart 後 log 範圍。

Add-on log 命令依實際 slug：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" \
  "ha apps logs '$ADDON_SLUG' | tail -n 100"
```

## Rollback

### Integration rollback

```bash
awk '/DEPLOY_LAYOUT_HELPER_BEGIN/{keep=1} keep{print} /DEPLOY_LAYOUT_HELPER_END/{exit}' DEPLOY.md > /tmp/nvr_monitor_layout_helper.sh
bash -n /tmp/nvr_monitor_layout_helper.sh
scp -P "$HA_SSH_PORT" /tmp/nvr_monitor_layout_helper.sh "$REMOTE:/tmp/nvr_monitor_layout_helper.sh"
ssh -p "$HA_SSH_PORT" "$REMOTE" 'bash -s' <<'REMOTE_SCRIPT'
set -euo pipefail
BACKUP_PATH="<recorded-backup-path>"; EXPECTED_VERSION="0.2.2"
CUSTOM_COMPONENTS=/config/custom_components; CANONICAL="$CUSTOM_COMPONENTS/nvr_monitor"
ARTIFACT_DIR="$BACKUP_PATH/deployment_artifacts"
rollback_source="$ARTIFACT_DIR/integration_predeploy"; test -d "$rollback_source"
source /tmp/nvr_monitor_layout_helper.sh
begin_transaction
cleanup() { rm -f /tmp/nvr_monitor_layout_helper.sh; }; trap cleanup EXIT
cp -a "$rollback_source/." "$STAGE"; swap_verified_stage
verify_nvr_monitor_layout; ha core restart
REMOTE_SCRIPT
```

Rollback 使用相同的唯一 helper；還原 integration source 後，必須先通過 `ha core check` 才能重新啟動 Core，且 failed source 與 rollback source 都保留在 `deployment_artifacts`，不留在 `custom_components`。

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
  ha apps reload
  ha apps rebuild '$ADDON_SLUG'
  ha apps restart '$ADDON_SLUG'
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
