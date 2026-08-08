# Changelog

## 0.3.4 - 2026-08-08

- Added site-configured, runtime-bounded snapshot concurrency from `1` to `8`.
- Preserved the legacy snapshot API response contract.

## 0.3.3 - 2026-08-04

- Added the bounded, memory-only M5B NVR telemetry producer for Center ingest.

## 0.3.2 - 2026-08-03

- Hard-capped snapshot ffmpeg capture concurrency at one, regardless of the
  retained legacy `max_concurrency` option.
- Changed the default `max_concurrency` option to `1` while retaining its
  schema for existing add-on options compatibility.

## 0.3.1 - 2026-08-02

### Added

- Added background RTSP live-video probes and Dahua recording queries for the
  `/api/v1/channels` state endpoints.
- Added bounded operation deadlines, response-size limits, and redacted error
  reporting for NVR operations.

### Fixed

- Included all M2B runtime modules in the add-on image.
- Prevented RTCP packets from being counted as RTP video and mapped network
  errors to stable API error codes.
- Preserved the existing legacy snapshot endpoint response contract.

## 0.2.1 - 2026-07-18

### Changed

- Updated default `nvr_host` to `192.168.0.100` and mapped the addon port to
  `8122` on the host.
- Lowered default `max_concurrency` from `4` to `2`.

### Fixed

- PowerShell test script (`test/test_api.ps1`) now validates `CameraId` as an
  integer in the range `1`–`99` to prevent invalid channel values.

## 0.2.0 - 2026-07-01

### Added

- Timestamps in Uvicorn logs via `log_config.json` (both startup and access
  log lines now include `YYYY-MM-DD HH:MM:SS`).
- Container timezone set to `Asia/Taipei` (`tzdata` installed and `TZ`
  environment variable configured), so all logs and Python timestamps use
  Taiwan time.

### Changed

- Increased default `health_timeout_ms` from `8000` to `10000` for more
  reliable snapshots under concurrent load.

### Fixed

- Race condition when multiple snapshot requests were processed in the same
  millisecond: the ffmpeg output path was based only on
  `int(time.time()*1000)` and could collide, causing intermittent failures
  such as `ffmpeg exit code 255` or
  `Error opening output files: Invalid argument`.
  The temporary file name now includes the process id and a UUID
  (`/tmp/snap_{pid}_{uuid}.jpg`).

## 0.1.1

- Bump version.

## 0.1.0

- Initial release: single API returning stream health + JPEG snapshot from
  Dahua NVR RTSP.
