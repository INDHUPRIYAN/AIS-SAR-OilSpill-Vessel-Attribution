# OceanTrace — one-command demo (Windows-first; the demo laptop runs Windows).
# Windows PowerShell 5.1 compatible (no &&, no ternary).
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1            # local dev servers
#   powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1 -Docker    # docker compose stack
#
# Does, in order:
#   1. verify/fetch model weights (scripts\get_weights.ps1)
#   2. verify demo run artefacts exist under data\runs
#   3. start backend (uvicorn :8000) + frontend (vite :5173), or compose
#   4. print the URL to open

param(
    [switch]$Docker
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "=== OceanTrace demo ==="

# --- 1. weights -------------------------------------------------------------
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "get_weights.ps1")
if ($LASTEXITCODE -ne 0) {
    Write-Host "[run_demo] WARNING: weights unavailable; /detect will use the threshold_fallback engine. Demo continues."
}

# --- 2. demo run artefacts --------------------------------------------------
$RunsDir = Join-Path $RepoRoot "data\runs"
$runs = @()
if (Test-Path $RunsDir) {
    $runs = @(Get-ChildItem -Path $RunsDir -Directory | Sort-Object Name)
}
if ($runs.Count -gt 0) {
    Write-Host ("[run_demo] {0} run(s) found under data\runs:" -f $runs.Count)
    $runs | Select-Object -First 8 | ForEach-Object { Write-Host ("  - " + $_.Name) }
    if ($runs.Count -gt 8) { Write-Host ("  ... and {0} more" -f ($runs.Count - 8)) }
} else {
    Write-Host ""
    Write-Host "[run_demo] WARNING: data\runs is EMPTY - the dashboard will have nothing to show."
    Write-Host "Seed at least one run before demoing. Either:"
    Write-Host "  a) copy the data\runs\ folder from the demo laptop into this clone, or"
    Write-Host "  b) generate one from the committed mock scene:"
    Write-Host "       cd $RepoRoot\main_system"
    Write-Host "       ..\.venv\Scripts\python -m backend.services.pipeline.run ``"
    Write-Host "           --scene ..\contracts\mocks\scene_sigma0_db.tif ``"
    Write-Host "           --scene-meta ..\contracts\mocks\scene_meta.json --run-id inv-001"
    Write-Host "Continuing anyway (the API itself works without runs)."
    Write-Host ""
}

# --- 3. start services ------------------------------------------------------
if ($Docker) {
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $dockerCmd) {
        Write-Host "[run_demo] ERROR: -Docker requested but docker is not installed/on PATH."
        exit 1
    }
    Write-Host "[run_demo] starting docker compose stack (backend + frontend)..."
    Push-Location $RepoRoot
    try {
        docker compose up --build -d
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[run_demo] ERROR: docker compose up failed."
            exit 1
        }
    } finally {
        Pop-Location
    }
} else {
    # Prefer the repo venv; fall back to whatever python is on PATH.
    $Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $Py)) {
        Write-Host "[run_demo] NOTE: .venv not found; using 'python' from PATH."
        Write-Host "          (setup: python -m venv .venv; .venv\Scripts\python -m pip install -e .)"
        $Py = "python"
    }

    Write-Host "[run_demo] starting backend (uvicorn, port 8000) in a new window..."
    Start-Process -FilePath $Py `
        -ArgumentList "-m", "uvicorn", "backend.main:app", "--port", "8000" `
        -WorkingDirectory (Join-Path $RepoRoot "main_system")

    $FrontendDir = Join-Path $RepoRoot "main_system\frontend"
    if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        Write-Host "[run_demo] node_modules missing - running npm install (one-time)..."
        Push-Location $FrontendDir
        try {
            npm install
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[run_demo] ERROR: npm install failed. Is Node.js installed?"
                exit 1
            }
        } finally {
            Pop-Location
        }
    }
    Write-Host "[run_demo] starting frontend (vite, port 5173) in a new window..."
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c", "npm run dev" `
        -WorkingDirectory $FrontendDir
}

# --- 4. wait for the backend, then print the URL ----------------------------
Write-Host "[run_demo] waiting for backend http://127.0.0.1:8000/health ..."
$healthy = $false
foreach ($i in 1..45) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch {
        Start-Sleep -Seconds 2
    }
}

Write-Host ""
if ($healthy) {
    Write-Host "=== OceanTrace is up ==="
} else {
    Write-Host "=== Backend did not answer /health within 90 s - check its window/logs ==="
}
Write-Host "  Dashboard : http://localhost:5173"
Write-Host "  API       : http://localhost:8000  (docs at /docs, liveness at /health)"
if ($Docker) {
    Write-Host "  Stop with : docker compose down"
} else {
    Write-Host "  Stop with : close the two spawned windows (uvicorn + vite)"
}
