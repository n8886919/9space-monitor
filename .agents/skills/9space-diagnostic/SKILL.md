---
name: 9space-diagnostic
description: "Use when auditing Home Assistant NVR or DMSS video-loss diagnostics in read-only mode via SSH evidence collection, with no configuration changes."
---

9SPACE HA / NVR SSH DIAGNOSTIC SKILL — READ-ONLY AUDIT

PURPOSE

You are operating from VS Code Copilot with SSH access to the 9Space Home Assistant system.
Audit the currently installed Home Assistant entities and determine which ones are useful for diagnosing DMSS / Dahua NVR "Video Loss" or no-video incidents, which are redundant, and which are safe deletion candidates.

The immediate task is inspection and reporting only. Do not delete, disable, rename, migrate, edit, restart, deploy, or commit anything unless the user separately authorizes that exact action after reviewing your report.


NON-NEGOTIABLE SAFETY RULES

1. Treat live HA config entries, entity registry, live states, current custom-component source, and downloaded diagnostics as the sources of truth.
2. Do not reconstruct camera IPs or channel mappings from old YAML, arithmetic assumptions, filenames, or this document.
3. Never print or copy passwords, API tokens, SUPERVISOR_TOKEN, SSH keys, cookies, certificates, private diagnostics, or camera footage.
4. Never display the full contents of /config/.storage/core.config_entries; it may contain credentials. Query only non-secret metadata and key names with jq.
5. Do not modify /config/.storage directly.
6. Do not run broad or destructive commands. In particular: no rm under /config, no database edits, no recursive permission changes, no HA restart.
7. Do not publish diagnostics or the report to GitHub.
8. If a command or API route is unavailable, report the gap. Do not invent output or silently replace live evidence with assumptions.
9. An entity is never "safe to delete" merely because it is currently off, unavailable once, disabled, oddly named, or apparently duplicated.
10. Label findings as OBSERVED FACT, INFERENCE, or NEXT TEST.


SYSTEM BOUNDARIES

Production video path:
Tapo camera -> Wi-Fi / Omada AP -> site LAN -> Dahua NVR ONVIF/RTSP channel -> NVR display/DMSS and recording storage.

Home Assistant observes and diagnoses this path. It is not the production video proxy.

Current equipment context:
- Home Assistant: Raspberry Pi 4, 2 GB RAM.
- NVR family: Dahua DH-NVR4116HS-HDS3/I.
- Cameras: TP-Link Tapo C100/C110/C210/C320 variants.
- Primary HA integration: nine_space_camera_monitor, with one NVR parent config entry and camera Config Subentries.
- Read the current channel, IP, name, model, group, and enabled state from the live config entry/subentries. Channel number and IP address are independent identifiers.


EVIDENCE LAYERS — KEEP THESE SEPARATE

Layer 1: ICMP reachability and RTT/jitter/loss
- Proves that the IP path replied during sampling.
- Does not prove RTSP, NVR ingest, video, or recording.

Layer 2: Camera TCP and RTSP application response
- TCP 554/2020 open only proves a listening TCP service.
- A parsed RTSP DESCRIBE response, including RTSP 401, proves the camera RTSP application responded.
- It does not prove valid credentials, advancing frames, or NVR ingest.

Layer 3: NVR RTSP control plus advancing RTP timestamps
- DESCRIBE/SETUP/PLAY proves the NVR RTSP control path.
- At least two RTP packets with distinct timestamps prove that the NVR channel is emitting advancing video.
- This is the strongest direct HA signal for a current DMSS/NVR no-video incident.
- It does not prove a recording was stored.

Layer 4: NVR recording query, last recording, coverage, and gaps
- A successful media-file query proves that the NVR media index returned data.
- Query failure must be distinguished from confirmed no recording.
- Event/motion recording means low 24-hour coverage or gaps are not automatically evidence of video loss.

Layer 5: Dahua VideoMotion / VideoLoss / VideoBlind events
- VideoLoss reports source video loss but not the exact Wi-Fi cause.
- VideoMotion does not prove video was recorded.
- VideoBlind is not equivalent to a network failure.
- Do not trust these entities unless the Dahua event feed has a real, advancing event timestamp or other proof of reception.

Layer 6: Omada evidence
- Association, RSSI/SNR, retries, roaming, AP/radio, VLAN, and uplink evidence may identify the network cause.
- High RTT/loss alone is insufficient to blame Wi-Fi without AP/client evidence.

Use at least two independent layers before naming a root cause. A single five-packet ping sample is weak evidence.


PRIOR SNAPSHOT — BASELINE ONLY, MUST BE REVERIFIED

The following was observed through HA on 2026-07-31. It is not current truth:

- nine_space_camera_monitor exposed about 336 entities: 14 channels x 24 entities.
- CH01-CH05 and CH07-CH14 had camera RTSP and NVR advancing RTP working.
- CH06 had RTSP, NVR video, and recording-query timeouts and was intentionally excluded from active investigation.
- Every VideoLoss entity was off, but last_event_timestamp was None.
- Every last_dahua_event entity was unknown. Therefore the Dahua event feed was not proven operational.
- Ping anomalies included .111 with about 138 ms average RTT, 343 ms jitter, and 20% loss; .101 and .114 showed 20% loss; .106 was offline. Recheck all values.
- About 44 legacy entities were unavailable and looked like cleanup candidates. They are listed later, but must still pass the current-source, reference, and replacement checks.


AUDIT PROCEDURE

Work in this order. Keep all temporary output under /tmp. Do not save secrets.

STEP 1 — IDENTIFY THE ACTUAL ENVIRONMENT

Run safe status commands:

  date -Iseconds
  uname -a
  ha core info
  ha supervisor info
  ha core logs | tail -n 200

Record the timezone and observation time. Correlate incident timestamps in Asia/Taipei.

Do not assume /config is the active path. Verify it first:

  test -d /config && printf '%s\n' '/config exists'
  test -f /config/.storage/core.entity_registry && printf '%s\n' 'entity registry exists'


STEP 2 — INSPECT ACTIVE CONFIG ENTRIES WITHOUT LEAKING DATA

If jq is available, show only non-secret metadata and the names of data/options keys:

  jq '
    .data.entries[]
    | select((.domain // "") | test("nine_space_camera_monitor|ping|dahua"; "i"))
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

If the schema differs, adapt the jq query while preserving the restriction: never print values from data/options.


STEP 3 — INVENTORY RELEVANT ENTITY REGISTRY RECORDS

Extract exact entity IDs and registry ownership:

  jq -r '
    .data.entities[]
    | select(
        (((.platform // "") | test("nine_space_camera_monitor|ping|dahua|history_stats"; "i")))
        or (((.entity_id // "") | test("camera|nvr|dahua|rtsp|recording|video_loss|video_blind"; "i")))
      )
    | [
        (.entity_id // ""),
        (.platform // ""),
        (.config_entry_id // ""),
        (.disabled_by // ""),
        (.hidden_by // ""),
        (.original_name // "")
      ]
    | @tsv
  ' /config/.storage/core.entity_registry | sort

Also count entities by platform and enabled/disabled state:

  jq -r '
    .data.entities[]
    | [(.platform // ""), (.disabled_by // "enabled")]
    | @tsv
  ' /config/.storage/core.entity_registry | sort | uniq -c | sort -nr

Do not infer current runtime state from the registry. The registry says that an entity is registered, not that it is available or useful.


STEP 4 — OBTAIN LIVE STATES SAFELY

Preferred: use an authenticated HA API mechanism that is already available on the SSH host. Never ask the user to paste a token into chat or source code.

If SUPERVISOR_TOKEN already exists in the environment, you may test the Supervisor Core proxy only if shell tracing is off. Never echo the token:

  set +x
  if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    http_code="$(curl -sS -o /tmp/9space_ha_states.json -w '%{http_code}' \
      -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
      http://supervisor/core/api/states)"
    printf 'HA states HTTP status: %s\n' "$http_code"
  else
    printf '%s\n' 'SUPERVISOR_TOKEN is not available; live API state audit is blocked.'
  fi

Only parse /tmp/9space_ha_states.json when the HTTP status is 200 and the file is a JSON array. Otherwise do not treat it as live evidence.

Extract only necessary fields:

  jq -r '
    .[]
    | select(
        (((.entity_id // "") | test("camera|nvr|dahua|rtsp|recording|video_loss|video_blind|192_168_0"; "i")))
      )
    | [
        (.entity_id // ""),
        (.state // ""),
        (.last_changed // ""),
        (.last_updated // ""),
        (.attributes.friendly_name // "")
      ]
    | @tsv
  ' /tmp/9space_ha_states.json | sort

Do not dump all attributes. Inspect individual attributes only when needed for a specific decision, such as history_observed hours, packet loss, last recording, checked_at, recording_error, or last Dahua event timestamp.

If live API access is unavailable, explicitly mark live state/current availability and duration claims as BLOCKED. Do not substitute registry data.


STEP 5 — INSPECT THE CURRENT INTEGRATION SOURCE

First locate the actual component:

  find /config/custom_components -maxdepth 2 -type f \
    \( -name manifest.json -o -name sensor.py -o -name binary_sensor.py -o -name diagnostics.py -o -name coordinator.py \) \
    -print 2>/dev/null

Inspect only the relevant nine_space_camera_monitor files. Determine:

- Which entity descriptions exist now.
- Which coordinator or service produces each entity.
- Which entities rely on Ping, camera RTSP, NVR RTP, recording CGI/API, or Dahua events.
- Whether old YAML/command_line probes are still loaded.
- Whether event entities have a real event source.
- Whether 24-hour entities expose less than 24 hours of observations.

Do not change the source.


STEP 6 — SEARCH CONFIG REFERENCES BEFORE CALLING ANYTHING DELETABLE

For each deletion candidate, search references in current YAML, stored Lovelace dashboards, automations, scripts, scenes, templates, and packages. Prefer rg; use grep only if rg is unavailable.

Exclude databases, WAL/SHM files, logs, backups, media, recordings, and camera footage. Do not print matching secret values; report only the candidate entity ID and filenames/count of references.

Relevant locations usually include:

  /config/configuration.yaml
  /config/automations.yaml
  /config/scripts.yaml
  /config/scenes.yaml
  /config/packages
  /config/.storage/lovelace*
  /config/.storage/core.config_entries

Also establish whether the owning config entry and source platform still exist.


STEP 7 — CHECK LOG EVIDENCE

Filter recent HA Core logs for the current integration, Dahua, RTSP/RTP, recording, timeout, and unavailable errors. Do not publish unredacted logs:

  ha core logs \
    | grep -Ei 'nine_space_camera_monitor|dahua|rtsp|rtp|record|video.?loss|timeout|unavailable|exception|traceback' \
    | tail -n 400

Redact credentials, tokens, public endpoints, and private diagnostic payloads from the report.


ENTITY VALUE CLASSIFICATION

HIGH VALUE FOR DMSS / NVR NO-VIDEO DEBUGGING

1. NVR live-video / advancing-RTP binary sensors.
   These directly test whether the NVR channel emits advancing video.

2. Camera RTSP-responding binary sensors.
   These separate a camera-side RTSP/service failure from an NVR-side ingest failure.

3. Network-reachable sensors plus RTT/jitter/loss.
   These identify reachability and network degradation, but not video by themselves.

4. Recording-query-problem sensors.
   These separate an API/query failure from a real lack of recordings.

5. Last-recording and time-since-last-recording sensors.
   These test the recording/storage layer after live-video status is known.

6. Exact checked_at, last_success, error, or failure-reason fields, if the current integration exposes them.
   A stale green state without a recent successful check is not reliable.

CONDITIONALLY USEFUL

1. VideoLoss/VideoBlind/last Dahua event entities.
   Keep as diagnostic evidence only if the event stream is proven to receive events and timestamps advance. An off state with last_event_timestamp=None proves nothing.

2. Recording coverage/gap/count entities.
   Useful for storage/rule analysis, but not direct proof of no-video when recording is event-based.

3. Aggregate problem sensors.
   Useful for alerting, but retain the underlying per-layer entities for root-cause diagnosis.

4. ONVIF/TCP-port sensors.
   Useful as intermediate service checks, but weaker than RTSP application and advancing-RTP checks.

POTENTIALLY REDUNDANT

- Old command_line probes that are fully replaced by the custom integration.
- Duplicate Ping/history_stats entities whose only consumers have migrated to the integration's rolling 24-hour entities.
- Old camera entities that never provided advancing-video evidence.
- Dahua entities that have no operational event source and no consumers.


SAFE-DELETION DECISION RULE

An entity may be reported as a STRONG DELETION CANDIDATE only when all applicable conditions are observed:

1. Its owning integration/config entry/platform is removed, obsolete, or intentionally replaced.
2. Its runtime state is unavailable/stale for a meaningful period, or it cannot be produced by any active source.
3. No dashboard, automation, script, scene, template, package, or alert references it.
4. A verified replacement exists when the function is still required.
5. Removing it will not erase the only remaining historical baseline needed for a migration.
6. It is not merely disabled by the user for a deliberate reason.

Otherwise classify it as:

- KEEP
- KEEP UNTIL MIGRATION COMPLETES
- CONDITIONAL / NEEDS EVENT-FEED TEST
- WEAK DELETION CANDIDATE
- BLOCKED — INSUFFICIENT LIVE EVIDENCE

Never delete anything during this audit.


LEGACY CANDIDATES FROM THE PRIOR SNAPSHOT — VERIFY INDIVIDUALLY

The previous browser audit observed these as unavailable or obsolete-looking:

- sensor.camera_service_probe
- binary_sensor.camera_service_probe_problem
- sensor.nvr_recording_probe
- binary_sensor.nvr_ch01_recording_recent through binary_sensor.nvr_ch14_recording_recent
- binary_sensor.nvr_ch01_live_video through binary_sensor.nvr_ch14_live_video
- binary_sensor.nvr_recording_problem
- binary_sensor.nvr_recording_api_problem
- binary_sensor.nvr_live_video_problem
- camera.ch07_mainstream
- camera.ch07_minorstream
- automation.camera_diagnostics_log_*
- sensor.last_dahua_camera_event
- automation.camera_ping_group_a_2
- Camera 115/116 legacy history_stats entities

Do not automatically include all of them in the final deletion list. Verify current state, source ownership, references, and replacement first.

Previously, Camera 101-114 legacy online_today/offline_periods_today history_stats were intentionally retained because the new rolling 24-hour integration metrics had only about 2.85 hours of observations. They can become candidates only after:

- the replacement metrics have at least a full 24-hour observation window;
- dashboards/automations use the replacement entities;
- exact entity references are absent;
- the user accepts losing the old entities/history.


INCIDENT INTERPRETATION MATRIX

- Ping down:
  Suspect power, Wi-Fi association, VLAN/LAN path, or address change. Check Omada/client evidence.

- Ping up + camera RTSP down:
  Suspect camera RTSP service hang, session exhaustion, credentials/application failure, or partial network failure.

- Camera RTSP up + NVR advancing RTP down:
  Suspect NVR channel credentials/configuration, NVR ingest/session exhaustion, or NVR-side stream failure. This is highly relevant to DMSS/NVR no-video.

- NVR advancing RTP up + DMSS no video:
  The current HA probe does not reproduce the failure. Investigate DMSS account/channel permissions, NVR remote-access/P2P path, client decode/cache, stream selection, or transient timing. Do not blame camera Wi-Fi without other evidence.

- NVR advancing RTP up + recording stale:
  Suspect schedule/rule, disk/index/write path, or recording query behavior.

- Motion event present + recording absent:
  Suspect event-to-recording rule or storage path. Motion alone does not prove stored video.

- High loss/jitter but video layers work:
  Network is degraded, but correlate Omada evidence before changing RF settings or TX power.


REQUIRED FINAL REPORT FORMAT

Keep the report concise and use exact entity IDs. Do not report only wildcard patterns.

1. OBSERVATION WINDOW
   - Start/end in Asia/Taipei
   - HA version
   - Whether live API states were available

2. CURRENT FAILED LAYER
   - Affected channels
   - Confidence: high / medium / low
   - Decisive evidence
   - Competing explanations

3. KEEP — HIGH DIAGNOSTIC VALUE
   For each entity: exact ID, source layer, current state, latest check time, why it matters.

4. KEEP CONDITIONALLY
   For each entity: exact ID, missing proof, smallest test needed.

5. STRONG DELETION CANDIDATES
   For each entity: exact ID, current state/duration, obsolete owner/source, reference search result, verified replacement.

6. WEAK OR BLOCKED CANDIDATES
   State exactly what evidence is missing. Do not recommend deletion yet.

7. DMSS / NVR VIDEO-LOSS DIAGNOSIS
   Explain the layer combination for each affected channel. Do not equate Ping, port-open, motion, or recording with live video.

8. SMALLEST NEXT ACTION
   Give the least invasive test that distinguishes the top competing explanations.

9. CHANGES MADE
   Must say: None. Read-only audit.


STOP CONDITIONS

Stop and ask the user before:

- deleting/disabling/renaming an entity;
- editing YAML, .storage, or custom-component source;
- restarting HA, NVR, cameras, APs, or network equipment;
- changing credentials, channel settings, schedules, RF settings, port forwarding, DuckDNS, NGINX, or Tailscale;
- downloading or viewing camera footage;
- publishing, committing, or uploading diagnostics.

The goal is an evidence-backed audit, not cleanup by guesswork.