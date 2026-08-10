param(
    # Example: .\test_api.ps1 192.168.1.10 1
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Ip,
    [Parameter(Mandatory=$true, Position=1)]
    [ValidateRange(1, 99)]
    [int]$CameraId
)

$ErrorActionPreference = "Stop"

# Fixed settings
$Port = 8122
$BaseUrl = "http://{0}:{1}" -f $Ip, $Port
$url = "{0}/api/camera/{1}" -f $BaseUrl.TrimEnd('/'), $CameraId

# Output directory = current directory
$OutDir = (Get-Location).Path
$resp = Join-Path $OutDir "resp.bin"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$jpg = Join-Path $OutDir ("cam{0}_{1}.jpg" -f $CameraId, $ts)
$snap = Join-Path $OutDir "snapshot.jpg"

Write-Host "[INFO] URL: $url"
Write-Host "[INFO] OUT_DIR: $OutDir"

$TotalSw = [System.Diagnostics.Stopwatch]::StartNew()

# 1) Download raw response (record HTTP code)
$DlSw = [System.Diagnostics.Stopwatch]::StartNew()
$HttpCode = (& curl.exe -sS -L -w "%{http_code}" -o $resp $url).Trim()
$DlSw.Stop()
$DlMs = [int][Math]::Round($DlSw.Elapsed.TotalMilliseconds)

$RespSize = if (Test-Path $resp) { (Get-Item $resp).Length } else { 0 }
Write-Host ("[INFO] HTTP: {0}, resp.bin: {1} bytes, download: {2} ms" -f $HttpCode, $RespSize, $DlMs)

if ($HttpCode -ne "200") {
    Write-Host "[ERROR] Non-200 response. First 300 bytes (best-effort):"
    if (Test-Path $resp) {
        $bytes = [System.IO.File]::ReadAllBytes($resp)
        $len = [Math]::Min(300, $bytes.Length)
        $sb = New-Object System.Text.StringBuilder
        for ($i = 0; $i -lt $len; $i++) {
            $b = $bytes[$i]
            if (($b -ge 32 -and $b -le 126) -or $b -eq 9) {
                [void]$sb.Append([char]$b)
            } else {
                [void]$sb.Append('.')
            }
        }
        Write-Host $sb.ToString()
    }
    exit 2
}

# 2) Parse / extract JPEG
$ParseSw = [System.Diagnostics.Stopwatch]::StartNew()
$PyCode = @'
import re, sys

resp = sys.argv[1]
out = sys.argv[2]

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
'@

$PyFile = Join-Path $env:TEMP ("test_api_parse_{0}.py" -f ([guid]::NewGuid().ToString("N")))
Set-Content -Path $PyFile -Value $PyCode -Encoding UTF8
try {
    & python.exe $PyFile $resp $snap
    $PyRc = $LASTEXITCODE
}
finally {
    if (Test-Path $PyFile) {
        Remove-Item -Force $PyFile
    }
}

$ParseSw.Stop()
$ParseMs = [int][Math]::Round($ParseSw.Elapsed.TotalMilliseconds)
Write-Host ("[INFO] parse: {0} ms" -f $ParseMs)

# 3) Rename extracted JPEG to timestamped filename (if created)
if ((Test-Path $snap) -and $PyRc -eq 0) {
    Move-Item -Force $snap $jpg
    Write-Host "[OK] Saved JPG => $jpg"
} else {
    Write-Host "[WARN] No JPG extracted. Raw response kept => $resp"
    exit 3
}

$TotalSw.Stop()
$TotalMs = [int][Math]::Round($TotalSw.Elapsed.TotalMilliseconds)
Write-Host ("[INFO] total: {0} ms" -f $TotalMs)
