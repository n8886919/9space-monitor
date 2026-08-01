#!/usr/bin/env bash
# Usage: ./test_api.sh <IP_OR_HOST> <CAMERA_ID>

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <IP_OR_HOST> <CAMERA_ID>"
  exit 1
fi

HOST="$1"
CAMERA_ID="$2"

PORT=8122
BASE_URL="http://${HOST}:${PORT}"
URL="${BASE_URL%/}/api/camera/${CAMERA_ID}"

OUT_DIR="$(pwd)"
RESP="${OUT_DIR}/resp.bin"
TS="$(date +%Y%m%d_%H%M%S)"
JPG="${OUT_DIR}/cam${CAMERA_ID}_${TS}.jpg"
SNAP="${OUT_DIR}/snapshot.jpg"

echo "[INFO] URL: $URL"
echo "[INFO] OUT_DIR: $OUT_DIR"

t0_ns="$(date +%s%N)"

# ---- Download (record HTTP code) ----
t_dl0_ns="$(date +%s%N)"
http_code="$(
  curl -sS -L \
    -w "%{http_code}" \
    -o "$RESP" \
    "$URL"
)"
t_dl1_ns="$(date +%s%N)"
dl_ms="$(( (t_dl1_ns - t_dl0_ns) / 1000000 ))"

resp_size="$(stat -c %s "$RESP" 2>/dev/null || echo 0)"
echo "[INFO] HTTP: $http_code, resp.bin: ${resp_size} bytes, download: ${dl_ms} ms"

if [[ "$http_code" != "200" ]]; then
  echo "[ERROR] Non-200 response. First 300 bytes (best-effort):"
  head -c 300 "$RESP" | sed 's/[^[:print:]\t ]/./g'
  exit 2
fi

# ---- Parse / extract JPEG ----
t_p0_ns="$(date +%s%N)"
python3 - <<PY
import os, re, sys
resp = r"$RESP"
out  = r"$SNAP"

data = open(resp, "rb").read()

# Case 1: raw JPEG
if data.startswith(b"\xff\xd8\xff"):
    open(out, "wb").write(data)
    print("[INFO] RAW_JPEG", len(data))
    sys.exit(0)

# Try to find boundary from headers (fallback to BOUNDARY)
m = re.search(br"boundary=([A-Za-z0-9'()+_,./:=?-]+)", data)
boundary = m.group(1).strip(b'"') if m else b"BOUNDARY"

for p in data.split(b"--" + boundary):
    if b"Content-Type: image/jpeg" in p:
        i = p.find(b"\r\n\r\n")
        if i < 0:
            continue
        body = p[i+4:]
        if body.endswith(b"\r\n"):
            body = body[:-2]
        open(out, "wb").write(body)
        print("[INFO] MULTIPART_JPEG", len(body), "boundary=", boundary.decode("utf-8","ignore"))
        sys.exit(0)

m2 = re.search(br"\{.*\}", data, re.S)
msg = m2.group(0).decode("utf-8","ignore") if m2 else "NO_JPEG_FOUND"
print("[ERROR]", msg)
sys.exit(3)
PY
py_rc=$?
t_p1_ns="$(date +%s%N)"
parse_ms="$(( (t_p1_ns - t_p0_ns) / 1000000 ))"
echo "[INFO] parse: ${parse_ms} ms"

# ---- Rename / report ----
if [[ -f "$SNAP" && $py_rc -eq 0 ]]; then
  mv -f "$SNAP" "$JPG"
  echo "[OK] Saved JPG => $JPG"
else
  echo "[WARN] No JPG extracted. Raw response kept => $RESP"
  exit 3
fi

t1_ns="$(date +%s%N)"
total_ms="$(( (t1_ns - t0_ns) / 1000000 ))"
echo "[INFO] total: ${total_ms} ms"
