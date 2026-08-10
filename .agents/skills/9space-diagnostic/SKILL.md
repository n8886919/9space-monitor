---
name: 9space-diagnostic
description: Use for read-only SSH audits of Home Assistant, nine_space_nvr_monitor, app, Hub, or DMSS/Dahua video-loss evidence; classify failed layers and entity usefulness without changing configuration, services, entities, or stored data.
---

# 9Space read-only video diagnostics

## Objective

Collect current, sanitized evidence for a reported DMSS／Dahua NVR no-video or recording problem. Separate the failed layer, assess useful entities, and identify cleanup candidates without making changes.

Treat the repository, live target, site mapping, versions, channel count, entity IDs, host, and observation window as task-specific. Never reuse old session values or infer a fixed 14-channel layout.

## Hard boundaries

- Remain read-only. Do not deploy, restart, reload, rebuild, rename, disable, delete, migrate, or edit anything.
- Never operate or probe protected legacy service `8122`. Use only the current user-supplied monorepo target; stop if host or port is ambiguous.
- Do not modify Home Assistant `.storage`. Query only the minimum non-secret metadata needed for the audit.
- Never print or save credentials, tokens, cookies, private keys, private URLs, full RTSP／CGI URLs, raw diagnostic payloads, JPEG bodies, or footage.
- Do not call Dahua CGI, construct NVR RTSP URLs, or run NVR ffmpeg from Home Assistant. Read the app’s sanitized state instead.
- Keep temporary sanitized output under `/tmp`; remove it when the audit finishes.
- Label every conclusion as `OBSERVED`, `INFERENCE`, or `BLOCKED`.
- Use at least two independent evidence layers before naming a root cause.

## Sources of truth

Prefer, in order:

1. Current observation timestamp and user-supplied target.
2. Live Home Assistant config-entry metadata, entity registry, and states.
3. Current installed `nine_space_nvr_monitor` source and app sanitized API responses.
4. Logs from the current observation window.
5. Current repository code, tests, `API.md`, and explicit site mapping.

Do not treat an old YAML file, filename, entity naming pattern, prior report, or disabled/unavailable state as current proof.

## Evidence layers

1. **HA Ping integration** — reachability and optional RTT／loss metadata. It does not prove video or recording.
2. **App process/API** — `/healthz` proves the service responds, not that the NVR works.
3. **NVR live-video state** — sanitized app channel state backed by RTSP control and advancing RTP evidence. It does not prove recording.
4. **Recording state** — recording-query success, last recording, coverage, gaps, and checked time. Query failure is not confirmed no recording.
5. **Producer/Hub metadata** — queue/drop, event freshness, snapshot-attempt success/error/latency. Never inspect snapshot bytes.
6. **Network/Omada evidence** — association, RSSI/SNR, retries, roaming, VLAN, AP, and uplink evidence. Ping alone is insufficient to blame Wi-Fi.

## Audit procedure

### 1. Establish scope and time

Confirm the supplied host, SSH port, expected app port, site alias, requested channels, and read-only scope. Record the observation window in Asia/Taipei.

Run only safe status commands relevant to the task, for example:

```bash
date -Iseconds
ha core info
ha supervisor info
ha apps list
```

Verify paths before reading them. Do not assume `/config`, `/addons`, or an app slug.

### 2. Inspect config entries without values

If `jq` is available, emit only non-secret metadata and data/options key names:

```bash
jq '
  .data.entries[]
  | select((.domain // "") | test("nine_space_nvr_monitor|ping|dahua"; "i"))
  | {
      entry_id,
      domain,
      title,
      source,
      disabled_by,
      state,
      data_keys: ((.data // {}) | keys),
      option_keys: ((.options // {}) | keys),
      subentry_count: ((.subentries // []) | length)
    }
' /config/.storage/core.config_entries
```

If the schema differs, adapt the query while preserving the no-values restriction. Never dump the file.

### 3. Inventory relevant entities

Read registry ownership, enabled state, and exact entity IDs for `nine_space_nvr_monitor`, Home Assistant Ping, and task-relevant diagnostics. Registry presence does not prove current availability.

Obtain live states through an already available authenticated mechanism. Never ask the user to paste a token and never echo an existing token. Save only the minimum selected fields:

- entity ID
- state／available
- last changed／updated
- task-relevant sanitized attributes such as checked time, error code, recording age, RTT, or packet loss

If live states are unavailable, mark availability and duration claims `BLOCKED`; do not substitute registry data.

### 4. Read sanitized app and Hub evidence

Confirm the current port is the authorized monorepo target and is not `8122`. Query only endpoints needed by the incident:

- `/healthz`
- `/api/v1/channels`
- `/api/v1/channels/{channel_id}`
- Hub metadata/query endpoints when Hub freshness or producer health is relevant

Do not request or view snapshot/JPEG bodies. For snapshot diagnosis, use attempt metadata only.

Distinguish workstation host-port reachability, SSH-session localhost, Home Assistant app hostname, and Hub/Tailscale routes. Failure in one namespace is not proof that another is unhealthy.

### 5. Inspect current source and logs

Inspect only the installed component files needed to map entities to producers. Confirm whether each signal comes from HA Ping, the integration, the app, or Hub; do not infer from its name.

Filter logs to the current observation window and relevant component. Summarize sanitized errors rather than reproducing complete log lines. Do not treat pre-restart history as evidence for a current run.

### 6. Check references before cleanup recommendations

For each candidate entity, search current dashboards, automations, scripts, scenes, templates, packages, and current source ownership. Prefer `rg`; exclude databases, logs, backups, media, and recordings.

Report only entity ID, referencing filenames/counts, current producer, and verified replacement. Do not print surrounding secret-bearing content.

## Entity classification

Classify each exact entity as one of:

- `KEEP — HIGH VALUE`: directly distinguishes a current evidence layer and has recent checks.
- `KEEP CONDITIONALLY`: useful only if a missing producer, event feed, or freshness condition is proven.
- `STRONG DELETION CANDIDATE`: obsolete owner/source, meaningful stale/unavailable evidence, zero references, and a verified replacement when needed.
- `WEAK CANDIDATE`: appears redundant but lacks duration, references, ownership, or replacement proof.
- `BLOCKED`: live evidence is unavailable or contradictory.

Never call an entity deletable merely because it is off, unavailable once, disabled, oddly named, or duplicated in appearance.

## Interpretation guide

- Ping down: investigate power, association, VLAN/LAN path, or addressing; require network evidence.
- Ping up + app live-video down: investigate the NVR channel/control/RTP path; do not infer a camera Wi-Fi cause without independent evidence.
- Live-video up + recording stale: investigate recording rules, storage/index path, or query behavior.
- Live-video up + DMSS no video: the app did not reproduce the failure; investigate DMSS permissions, P2P/remote path, decoding/cache, stream selection, or timing.
- Producer/Hub stale while local channel state is current: investigate telemetry queue, route, or Hub freshness separately from NVR health.
- Snapshot attempt failure with an existing last-good image: report attempt failure and image age separately; do not inspect the image.

## Required report

Report concisely in Chinese:

1. Observation window, target alias, versions, and evidence availability.
2. Current failed layer, affected mapped channels, confidence, decisive evidence, and competing explanations.
3. `KEEP — HIGH VALUE` entities with exact IDs and freshness.
4. Conditional, strong, weak, and blocked cleanup candidates with reference-search evidence.
5. Smallest next read-only test that separates the top explanations.
6. Commands/checks summarized as `PASS`／`FAIL`／`TIMEOUT`／`BLOCKED` without secrets or JPEG bodies.
7. `CHANGES MADE: None`.

## Stop conditions

Stop and request direction before any action that would:

- change an entity, config entry, YAML, `.storage`, source, credential, channel, schedule, network, ACL, or public exposure;
- restart/reload/rebuild HA, app, NVR, camera, AP, or network equipment;
- touch `8122`, download/view footage, or publish/upload diagnostics;
- require guessing a host, port, slug, channel mapping, entity identity, or live state.
