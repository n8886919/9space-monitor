# Manual Deployment

## 目的

本文件定義單一站點的手動部署，是 canonical deployment reference。部署任務已授權其中 bounded、可回復的 preflight、Supervisor update、integration upload、restart 與 smoke verification，不需在每個步驟重複請示。

本文件是部署說明，不是自動化腳本。

不使用：

- GitHub Actions deployment
- self-hosted runner
- 多站點 rollout
- 把 Snapshot add-on source 上傳到 HA local `/addons`
- 自動修改 Home Assistant `.storage`
- 自動 config-entry migration

## 安全邊界

- `8122` 是獨立舊正式服務，永久禁止操作。
- 不得對 `8122` 執行 test、restart、rebuild、reload、modify，亦不得把 `8122` 當成 integration URL。
- 本 repository 的 monorepo add-on 版本為 `0.3.8`，host port 是 `8222`，container port 是 `8000`。
- Snapshot add-on 只能由 HA 已設定的 Supervisor managed repository 安裝與更新；不得以 tar／scp 寫入 local `/addons`，也不得把 local source 當更新失敗時的替代路徑。
- Center 是獨立 container service，依 `center/README.md` 從 Git checkout 部署；它不是 HA add-on，不得安裝到 HA local `/addons`。
- Supervisor add-on identifier／slug 與 internal hostname 不可混用。
- 唯讀 version gate 只有在 Supervisor 已安裝 add-on 是 `0.3.8` 時才通過；不得因舊版 endpoint 可用就跳過必要的 repository update。
- 若任務已要求部署，add-on 的 managed update 或 integration 的 scoped upload、restart 與 verify 可依本文件連續完成；操作 `8122`、`.storage`、destructive rollback、schema／auth 或 public exposure 仍須另行明確確認。
- 不得直接編輯 `/config/.storage/core.config_entries` 或其他 `.storage` 檔案。
- 不得自動建立或接受帶有 `_2` 後綴的 replacement entities。
- 唯讀操作不備份。只有真正要修改的 component 才在 mutation 前建立 scoped rollback。
- `.storage`／config-entry backup 只在確定要變更 config entry 前建立；add-on update 使用 Supervisor 的 scoped partial backup，不建立 source copy。

## 變數

部署前由操作者明確設定，不要寫進 repository：

```bash
export HA_HOST="<site-host-or-ip>"
export HA_SSH_PORT="22"
export HA_USER="root"
export REMOTE="${HA_USER}@${HA_HOST}"

export INTEGRATION_DOMAIN="nvr_monitor"
export INTEGRATION_REMOTE_DIR="/config/custom_components/${INTEGRATION_DOMAIN}"

export ADDON_SLUG="<actual-supervisor-addon-slug>"
export ADDON_TARGET_VERSION="0.3.8"
export ADDON_HOSTNAME="afa94ae2-9space-snapshot-addon"
export ADDON_HOST_PORT="8222"
export ADDON_CONTAINER_PORT="8000"
export INTEGRATION_BASE_URL="http://${ADDON_HOSTNAME}:${ADDON_CONTAINER_PORT}"
```

變數用途必須明確區分：

- `ADDON_SLUG`：只供 `ha apps ...` 指令使用。
- `ADDON_SLUG`：必須以 Supervisor 實際輸出為準；目前觀察到的 identifier 是 `afa94ae2_9space_snapshot_addon`。
- `ADDON_TARGET_VERSION`：必須等於已發布至既有 Supervisor repository 的 `config.yaml` version；不能只存在未 push 的 local checkout。
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
  ha core info >/dev/null
'
```

確認 Supervisor 看到的 add-on slug 與目前版本，避免把 slug 與 hostname 混用：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" \
  "ha apps list | grep -i -A4 -B2 '9space\|snapshot'; ha apps info '$ADDON_SLUG'"
```

在 Home Assistant UI 的 `Settings > Apps > App store > ⋮ > Repositories` 唯讀確認這個 slug 來自既有的預期 Git repository。若 repository、slug 或輸出與預期不同，先停止並回報實際結果；不要新增／替換 repository，也不要自行猜測。

## 路徑選擇

- 唯讀 preflight、smoke 與 observation 不建立備份，也不建立 rollback artifact。
- Add-on 只走 Supervisor managed repository；更新時由 `ha apps update --backup` 建立 scoped partial backup，不複製 source。
- Integration source deployment 使用同 filesystem transaction 的 `integration_replaced` 作 rollback；完成驗證後清除 transaction。
- 只有實際要執行 UI Reconfigure 時，才備份一次 `core.config_entries`。
- `.storage` migration、destructive cleanup、schema／auth 變更不屬 routine deployment；另做精確 plan 與 task-specific backup。

## 唯讀 preflight／smoke／observation（預設）

Snapshot API smoke test 是 add-on API 與未來 Center/server 的 contract 驗證；integration 不建立 Snapshot camera entity，也不呼叫 Snapshot endpoint。

只有 Supervisor 已安裝版本明確為 `0.3.8`，且以下唯讀檢查都正常時，才可跳過 add-on repository update：

- `ha apps info "$ADDON_SLUG"` 顯示 version `0.3.8`
- `http://127.0.0.1:${ADDON_HOST_PORT}/healthz`
- `http://127.0.0.1:${ADDON_HOST_PORT}/api/camera/1`
- `http://127.0.0.1:${ADDON_HOST_PORT}/api/v1/channels`

版本 gate（唯讀）：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  ha apps info '$ADDON_SLUG' | grep -Eq 'version:[[:space:]]*$ADDON_TARGET_VERSION([[:space:]]|\$)'
"
```

任一版本檢查或 smoke test 失敗時，不得以 `8122` 作替代驗證。若任務只要求驗證／observation，回報實際結果後停止；若任務已要求部署且既有 repository、目標 version、slug 與 topology 均已確認，才進入下方 Add-on managed repository deployment。

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

1. 不更新 add-on，也不建立 add-on backup。
2. 若任務只要求 smoke／observation，到此停止。
3. 只有任務明確包含 integration source deployment 時，才進入 Integration 部署。

若任一項失敗：

1. 保留並回報實際錯誤。
2. 已要求部署且 repository 與目標明確時走下方 Add-on managed repository deployment；目標或 topology 不明時停止。

## Add-on managed repository deployment

已安裝版本落後，而任務已要求部署時使用以下步驟。目標版本必須先通過測試、增加 `9space_snapshot_api/config.yaml` version，並已 push 至 HA 現有 Git repository；本流程不負責 commit、push 或變更 HA repository 設定。

### 1. 刷新既有 repository 並確認 update

在 Home Assistant UI 開啟 `Settings > Apps > App store`，使用右上角選單的檢查更新／reload 動作。只刷新已存在且 preflight 已確認的 repository，不新增、刪除或替換 repository。

刷新後再次檢查：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  ha apps info '$ADDON_SLUG'
"
```

輸出或 UI 必須明確顯示目標 `ADDON_TARGET_VERSION` 可更新。若 repository 尚未取得目標版本、顯示其他 source，或 CLI／UI 無法確認，停止並回報；不得 fallback 到 HA local add-on source。

### 2. Supervisor update

以 Supervisor 提供的 update 交易更新至 repository 最新版，並在更新前建立 partial backup：

```bash
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  ha apps update --backup '$ADDON_SLUG'
  ha apps info '$ADDON_SLUG' | grep -Eq 'version:[[:space:]]*$ADDON_TARGET_VERSION([[:space:]]|\$)'
"
```

`ha apps update` 只更新至 repository 最新版，不能指定降版。若命令、build 或啟動失敗，保留 Supervisor 狀態與 partial backup，回報實際錯誤並停止；不要手動寫入 `/addons`、不要改用 local install，也不要自行執行額外 rebuild／restart 掩蓋失敗。

### 3. Add-on smoke test

Add-on managed repository update 完成後，只驗證 monorepo add-on host port，不得碰 `8122`：

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

本路徑不複製 add-on 或 integration source，也不碰 `.storage`。換入前才在 `/config/9space_deploy` 建立同 filesystem transaction；`integration_replaced` 就是本次唯一 source rollback。

### Integration layout 不變量（fail-closed）

Home Assistant 會檢查 `/config/custom_components` 的第一層目錄。該層中，`domain: "nvr_monitor"` 的 manifest **精確只能有一份**，而且路徑必須是：

```text
/config/custom_components/nvr_monitor/manifest.json
```

因此 `/config/custom_components` 下禁止建立任何 integration 暫存、predeploy、failed、rollback 或 old source，特別是 `.nvr_monitor*`、`nvr_monitor.old*`、`nvr_monitor.bak*`。本次 transaction artifact 只能放在 `/config/9space_deploy/nvr-monitor.*`，完成驗證後清除；失敗時保留至 rollback 完成。

以下 gate 必須在換入前、以及每次 `ha core check`／`ha core restart` 前執行；任何不符都立刻停止，不能以臨時刪除或移動其他目錄繞過。清理不明 legacy sibling 是獨立且可能具破壞性的操作，須確認精確目標後再做。

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

若 gate 偵測到 `.nvr_monitor*`、`nvr_monitor.old*` 或 `nvr_monitor.bak*` 等同 domain sibling，routine deployment 必須 fail-closed，不得在部署途中移動或刪除。精確清理這類 artifact 是獨立的 destructive task，須先確認現況與目標。

### 1. 上傳 integration

Snapshot add-on `0.3.8` 需要 integration `0.2.6` 或更新版本，才能由
`live_checked_at` 維護 integration-owned 24 小時 RAM ring。Integration `0.2.7`
在啟動時透過 Recorder 的 read-only executor API 重建最近 24 小時 window；不建立
第二份磁碟 history，add-on 與 Center 仍不接收在線率或斷線次數。

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
EXPECTED_VERSION="0.2.7"
CUSTOM_COMPONENTS=/config/custom_components; CANONICAL="$CUSTOM_COMPONENTS/nvr_monitor"
mkdir -p /config/9space_deploy
ARTIFACT_DIR=$(mktemp -d /config/9space_deploy/nvr-monitor.XXXXXX)
# The operator has saved the single helper block verbatim as this local remote file.
source /tmp/nvr_monitor_layout_helper.sh
PREVIOUS_VERSION=$(jq -r '.version' "$CANONICAL/manifest.json")
begin_transaction
cleanup() { rm -f /tmp/nvr_monitor.tgz /tmp/nvr_monitor_layout_helper.sh; }; trap cleanup EXIT
tar -xzf /tmp/nvr_monitor.tgz --strip-components=1 -C "$STAGE"
swap_verified_stage
ROLLBACK_SOURCE="$REPLACED"
echo "DEPLOY_ARTIFACT_DIR=$ARTIFACT_DIR"
echo "ROLLBACK_SOURCE=$ROLLBACK_SOURCE"
echo "PREVIOUS_VERSION=$PREVIOUS_VERSION"
RESTART_MARKER=$(date '+%Y-%m-%d %H:%M:%S'); echo "nvr_monitor restart marker: $RESTART_MARKER"
verify_nvr_monitor_layout; ha core restart || { echo 'restart failed; run the new-transaction rollback below once' >&2; exit 1; }
REMOTE_SCRIPT
```

記下 `DEPLOY_ARTIFACT_DIR`、`ROLLBACK_SOURCE`、`PREVIOUS_VERSION` 與 restart marker。它們只屬於這次 integration mutation，不是長期 backup。

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

`core.config_entries` 只在確定要執行 UI Reconfigure 前建立一次；如果既有 config entry 不需變更，跳過本段備份與 UI 步驟。此檔只供緊急比對，不得編輯、不得直接覆寫回 `.storage`。

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  test -d /config/9space_backups
  test -f /config/.storage/core.config_entries
  CONFIG_ENTRY_BACKUP=\$(mktemp '/config/9space_backups/core.config_entries.$STAMP.XXXXXX')
  cp -a /config/.storage/core.config_entries \"\$CONFIG_ENTRY_BACKUP\"
  test -s \"\$CONFIG_ENTRY_BACKUP\"
  echo \"CONFIG_ENTRY_BACKUP=\$CONFIG_ENTRY_BACKUP\"
"
```

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
2. 目前 site mapping 定義的 channel 與 subentry 對應。
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
3. 請使用者決定涉及 config-entry identity 的下一步：
   A. 修正 internal add-on URL 後重試 Reconfigure。
   B. 刪除並重新建立 config entry。
   C. Rollback integration。

Agent 不得自行替使用者選擇會改變 config-entry／entity identity 的方案。

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

### 完成 integration transaction

只有 integration source、Core recovery、restart 後 log 與必要 UI 驗證全部通過，而且確定不需 rollback 後，才清除本次 transaction。Add-on 由 Supervisor managed repository 與其 partial backup 管理，不建立或保留 local source artifact。

```bash
DEPLOY_ARTIFACT_DIR="<recorded-deploy-artifact-dir>"
ssh -p "$HA_SSH_PORT" "$REMOTE" "
  set -eu
  case '$DEPLOY_ARTIFACT_DIR' in
    /config/9space_deploy/nvr-monitor.*) ;;
    *) echo 'invalid deploy artifact path' >&2; exit 1 ;;
  esac
  test -d '$DEPLOY_ARTIFACT_DIR'
  rm -rf -- '$DEPLOY_ARTIFACT_DIR'
"
```

## Rollback

### Integration rollback

`swap_verified_stage` 或 `ha core check` 失敗時會在同一 transaction 自動恢復。只有換入成功後才發現問題時，才使用先前記錄的 `ROLLBACK_SOURCE` 與 `PREVIOUS_VERSION`：

```bash
awk '/DEPLOY_LAYOUT_HELPER_BEGIN/{keep=1} keep{print} /DEPLOY_LAYOUT_HELPER_END/{exit}' DEPLOY.md > /tmp/nvr_monitor_layout_helper.sh
bash -n /tmp/nvr_monitor_layout_helper.sh
scp -P "$HA_SSH_PORT" /tmp/nvr_monitor_layout_helper.sh "$REMOTE:/tmp/nvr_monitor_layout_helper.sh"
ssh -p "$HA_SSH_PORT" "$REMOTE" 'bash -s' <<'REMOTE_SCRIPT'
set -euo pipefail
ROLLBACK_SOURCE="<recorded-rollback-source>"
EXPECTED_VERSION="<recorded-previous-version>"
CUSTOM_COMPONENTS=/config/custom_components; CANONICAL="$CUSTOM_COMPONENTS/nvr_monitor"
case "$ROLLBACK_SOURCE" in
  /config/9space_deploy/nvr-monitor.*/transaction.*/integration_replaced) ;;
  *) echo 'invalid rollback source' >&2; exit 1 ;;
esac
test -d "$ROLLBACK_SOURCE"
mkdir -p /config/9space_deploy
ARTIFACT_DIR=$(mktemp -d /config/9space_deploy/nvr-monitor.XXXXXX)
source /tmp/nvr_monitor_layout_helper.sh
begin_transaction
cleanup() { rm -f /tmp/nvr_monitor_layout_helper.sh; }; trap cleanup EXIT
cp -a "$ROLLBACK_SOURCE/." "$STAGE"; swap_verified_stage
verify_nvr_monitor_layout; ha core restart
echo "ROLLBACK_ARTIFACT_DIR=$ARTIFACT_DIR"
REMOTE_SCRIPT
```

Rollback 使用相同 helper；還原 integration source 後必須先通過 `ha core check` 才能重新啟動 Core。驗證完成後，以前述相同 path gate 清除原 deployment 與 rollback transaction，不在 `custom_components` 留 artifact。

若 entry data 已改成 `addon_base_url`：

1. 透過舊版 UI Reconfigure 恢復舊 NVR 設定。
2. 不直接覆寫或編輯 `.storage`。
3. 若缺少舊 credentials，停止並交由使用者處理。

### Add-on rollback

HA CLI 的 `ha apps update` 不能指定降版，因此 routine rollback 不可把舊 source 放回 `/addons`，也不可改用 local install。標準方式是 **forward rollback**：

1. 在 Git repository revert 問題變更。
2. 將 `config.yaml` version 提升為高於問題版本的新版本，完成相同測試後 push。
3. 在 HA App store 刷新既有 repository，確認新修復版本可用。
4. 再依本文件執行 `ha apps update --backup` 與 smoke tests。

若要還原 update 建立的 Supervisor partial backup，屬可能回復其他 scoped add-on 狀態的 destructive restore；必須先確認精確 backup 與影響範圍並取得使用者明確批准。不得自行 restore，也不得以 force push、history rewrite 或 local source swap 取代 forward rollback。

## 部署完成紀錄

每次手動部署只記錄：

```text
date:
git commit:
site alias:
add-on version:
add-on repository: existing managed repository confirmed / not confirmed
add-on update backup: created / not created
integration version:
integration transaction: removed / retained for rollback / none
config-entry backup: path / not created
smoke test:
rollback needed:
notes:
```

不要記錄真實 NVR credential 或 snapshot。
