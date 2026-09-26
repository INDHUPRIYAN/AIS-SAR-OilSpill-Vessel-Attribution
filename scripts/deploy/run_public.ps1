# OceanTrace public host on this machine: the deploy image + Tailscale Funnel.
#
#   powershell -ExecutionPolicy Bypass -File scripts\deploy\run_public.ps1
#
# Serves the same single-container image CI smoke-tests, on a permanent HTTPS
# link  https://<machine>.<tailnet>.ts.net  that answers while this machine is
# on and online. Idempotent: re-run it after a reboot, a code change (-Rebuild)
# or to recover. Docker's restart policy brings the app back on its own once
# Docker Desktop is running; Funnel's config survives reboots.
#
# Data: first run copies the staged bundle (.deploy\bundle, from
# scripts\deploy\data_bundle.py build) into the Docker volume "oceantrace-data".
# It is a real copy -- the bundle is hard-linked to data\, and the public app
# writes (new runs, uploads, the registry), so it must never touch the originals.
param(
    [string]$Image = "oceantrace:local",
    [int]$Port = 7860,
    [switch]$Rebuild,
    [switch]$ReseedData
)

# "Continue", not "Stop": Windows PowerShell 5.1 turns any stderr line from a
# native command (docker's progress, "No such container" from a cleanup rm)
# into a terminating error. Every native call's $LASTEXITCODE is checked instead.
$ErrorActionPreference = "Continue"
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$Bundle = Join-Path $Repo ".deploy\bundle"
$EnvFile = Join-Path $Repo ".deploy\public.env"
$Volume = "oceantrace-data"
$Name = "oceantrace"
$Tailscale = "C:\Program Files\Tailscale\tailscale.exe"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Step "Docker"
docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep 3
        docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
    }
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop did not start" }
}

Step "Image $Image"
docker image inspect $Image 2>$null | Out-Null
if ($Rebuild -or $LASTEXITCODE -ne 0) {
    docker build -f "$Repo\docker\app.Dockerfile" -t $Image $Repo
    if ($LASTEXITCODE -ne 0) { throw "image build failed" }
}

Step "Settings ($EnvFile)"
# Only provider credentials come from .env: its HOST/PORT/DATA_ROOT describe the
# dev server and would point the container at the wrong port and paths.
$providerKeys = @("CDSE_USERNAME", "CDSE_PASSWORD", "CDSE_CLIENT_ID", "CDSE_CLIENT_SECRET",
                  "EARTHDATA_USER", "EARTHDATA_PASS", "CMEMS_USERNAME", "CMEMS_PASSWORD",
                  "CDSAPI_URL", "CDSAPI_KEY", "AISSTREAM_API_KEY")
$lines = @()
if (Test-Path "$Repo\.env") {
    foreach ($l in Get-Content "$Repo\.env") {
        if ($l -match '^\s*([A-Z0-9_]+)\s*=(.*)$' -and $providerKeys -contains $Matches[1]) {
            $value = $Matches[2].Trim().Trim([char]34, [char]39)   # strip " and '
            $lines += "$($Matches[1])=$value"
        }
    }
}
# Session + vault secrets: generated once, kept across restarts so sign-ins and
# stored keys survive. .deploy\ is gitignored.
$secrets = @{}
if (Test-Path $EnvFile) {
    foreach ($l in Get-Content $EnvFile) {
        if ($l -match '^(SECRET_KEY|JWT_SECRET)=(.+)$') { $secrets[$Matches[1]] = $Matches[2] }
    }
}
foreach ($k in "SECRET_KEY", "JWT_SECRET") {
    if (-not $secrets[$k]) {
        $bytes = New-Object byte[] 32
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $secrets[$k] = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    }
    $lines += "$k=$($secrets[$k])"
}
$lines += "OT_PUBLIC_EVALUATOR=true"      # judges open the link with no account
$lines += "PORT=$Port"
New-Item -ItemType Directory -Force (Split-Path $EnvFile) | Out-Null
Set-Content -Path $EnvFile -Value $lines -Encoding ascii
Write-Host "  $($lines.Count) settings ($(@($lines | Where-Object { $_ -match '^(CDSE|EARTHDATA|CMEMS|CDSAPI|AISSTREAM)' }).Count) provider keys)"

Step "Data volume $Volume"
$hasDb = docker run --rm --entrypoint sh -v "${Volume}:/app/data" $Image -c "test -f /app/data/oceantrace.db && echo yes"
if ($ReseedData -or $hasDb -ne "yes") {
    if (-not (Test-Path "$Bundle\oceantrace.db")) {
        throw "no bundle at $Bundle -- run: .venv\Scripts\python.exe scripts\deploy\data_bundle.py build"
    }
    Write-Host "  copying the bundle into the volume (a few minutes, ~9.5 GB)..."
    docker rm -f $Name 2>$null | Out-Null
    docker run --rm --entrypoint sh -v "${Bundle}:/src:ro" -v "${Volume}:/app/data" $Image `
        -c "rm -rf /app/data/* /app/data/.bundle-revision; cp -a /src/. /app/data/ && echo copied"
    if ($LASTEXITCODE -ne 0) { throw "seeding the data volume failed" }
} else {
    Write-Host "  already seeded (use -ReseedData to replace it with the bundle)"
}

Step "Container $Name"
docker rm -f $Name 2>$null | Out-Null
# Bound to localhost only: the public way in is Funnel, never the LAN.
docker run -d --name $Name --restart unless-stopped `
    -p "127.0.0.1:${Port}:${Port}" --env-file $EnvFile -v "${Volume}:/app/data" $Image | Out-Null
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:$Port/readyz" -TimeoutSec 3 | Out-Null; $ready = $true; break }
    catch { Start-Sleep 2 }
}
if (-not $ready) { docker logs --tail 40 $Name; throw "app did not become ready" }
Write-Host "  ready on http://127.0.0.1:$Port"

Step "Tailscale Funnel"
if (-not (Test-Path $Tailscale)) { throw "Tailscale not installed: winget install Tailscale.Tailscale" }
& $Tailscale funnel --bg $Port
$dns = (& $Tailscale status --json | ConvertFrom-Json).Self.DNSName.TrimEnd(".")
Write-Host "`nOceanTrace is public at:  https://$dns" -ForegroundColor Green
