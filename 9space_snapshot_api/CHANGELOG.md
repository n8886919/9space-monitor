# Changelog

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
