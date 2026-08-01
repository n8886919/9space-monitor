#!/usr/bin/with-contenv sh
set -e

# The add-on always listens on container port 8000 (mapped to host port 8222
# in config.yaml). This is intentionally NOT configurable via options: the
# `nvr_http_port` option configures the Dahua NVR's own HTTP/CGI port, it has
# nothing to do with where this add-on listens.
exec uvicorn main:app --app-dir /app --host 0.0.0.0 --port 8000 --log-config /app/log_config.json
