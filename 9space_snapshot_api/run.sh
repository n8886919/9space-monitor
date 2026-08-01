#!/usr/bin/with-contenv sh
set -e

exec uvicorn main:app --app-dir /app --host 0.0.0.0 --port 8000 --log-config /app/log_config.json
