# NVR Monitor

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
   **NVR Monitor**.
5. Add one camera subentry for each NVR channel that should be monitored.


## Scope

The integration exposes local network, camera service, NVR live-video,
recording, and Dahua event health. Site IP addresses, SSH configuration,
deployment runners, and customer diagnostics do not belong in this repository.

## Privacy

Credentials remain in the Home Assistant config entry. Diagnostics must redact
credentials and must not contain customer footage.
