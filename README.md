# Nine Space NVR Monitor

Home Assistant custom integration for monitoring cameras connected to a Dahua
NVR. One NVR config entry acts as a hub, while each monitored camera is stored
as a config subentry.

## Requirements

- Home Assistant 2026.2 or newer
- A Dahua NVR reachable from Home Assistant
- Camera and NVR credentials configured through the Home Assistant UI

## Install

1. Download `nvr_monitor.zip` from the required GitHub Release.
2. Extract it into the Home Assistant configuration directory. The resulting
   path must be:

   ```text
   /config/custom_components/nvr_monitor/manifest.json
   ```

3. Restart Home Assistant.
4. Open **Settings → Devices & services → Add integration** and select
   **Nine Space NVR Monitor**.
5. Add one camera subentry for each NVR channel that should be monitored.

Managed Nine Space sites should install a pinned release through the separate
`9space-ha-ops` repository instead of copying files manually.

## Upgrading from Nine Space Camera Monitor

`nvr_monitor` is a new Home Assistant domain. Home Assistant treats
it as a different integration from `nine_space_camera_monitor`.

Do not remove the old integration before the new integration is installed,
configured, and verified. The old rolling history is not migrated because its
storage keys belong to the old domain.

## Scope

The integration exposes local network, camera service, NVR live-video,
recording, and Dahua event health. Site IP addresses, SSH configuration,
deployment runners, and customer diagnostics do not belong in this repository.

## Privacy

Credentials remain in the Home Assistant config entry. Diagnostics must redact
credentials and must not contain customer footage.
