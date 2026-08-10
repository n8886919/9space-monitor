#!/usr/bin/with-contenv sh
set -eu

exec uvicorn nine_space_monitor_hub.app:app \
  --app-dir /app \
  --host 0.0.0.0 \
  --port 8765 \
  --no-proxy-headers
