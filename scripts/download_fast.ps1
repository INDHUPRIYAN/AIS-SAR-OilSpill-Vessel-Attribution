# Fast, resumable Trujillo downloader (aria2c multi-connection).
#
# Replaces the single-stream requests download in ml/download.py, which tops out
# at one TCP stream to Zenodo. aria2c opens several and roughly doubles the rate.
#
# Keeps the ".part -> final name" convention that ml/download.py and
# ml/prepare_trujillo.py rely on: a file only gets its real .7z name after its
# md5 matches, so a half-finished archive can never be mistaken for a complete
# one. Holds the same data/raw/downloader.lock the python downloader takes, so
# the two can never append to one file at once.
#
#   powershell -ExecutionPolicy Bypass -File scripts\download_fast.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\download_fast.ps1 -Part 1

param(
    [int[]] $Part = @(1, 2),
    [int]   $Conns = 16
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$data = Join-Path $repo "data\raw\trujillo"
$lock = Join-Path $repo "data\raw\downloader.lock"
$log  = Join-Path $data "fast.log"

$records = @{ 1 = "8346860"; 2 = "8253899" }

function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line -Encoding utf8
    Write-Host $line
}

function Find-Aria {
    $c = Get-Command aria2c -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\aria2c.exe"
    if (Test-Path $p) { return $p }
    throw "aria2c not found. Install with: winget install --id aria2.aria2 -e"
}

# Zenodo is the source of truth for sizes and checksums; a hardcoded table goes
# stale silently when a record is revised. But the API refuses requests far more
# often than the file endpoints do, and a 27 GB resumable transfer must not be
# blocked by a metadata lookup -- so a successful manifest is cached and reused
# whenever the API is unreachable.
function Get-ZenodoFiles($record) {
    $cache = Join-Path $data "zenodo_$record.json"
    try {
        $json = curl.exe -s --max-time 120 "https://zenodo.org/api/records/$record"
        if (-not $json) { throw "empty response from Zenodo for record $record" }
        $files = @(($json | ConvertFrom-Json).files | ForEach-Object {
            [pscustomobject]@{
                Key  = $_.key
                Size = [int64]$_.size
                Md5  = ($_.checksum -replace '^md5:', '')
                Url  = $_.links.self
            }
        })
        if (-not $files) { throw "record $record listed no files" }
        $files | ConvertTo-Json -Depth 4 | Set-Content -Path $cache -Encoding utf8
        return $files
    }
    catch {
        if (Test-Path $cache) {
            Log "record $record : API unreachable ($($_.Exception.Message)); using cached manifest"
            return @(Get-Content $cache -Raw | ConvertFrom-Json)
        }
        throw
    }
}

# A stale lock from a killed process blocks every future run.
function Enter-Lock {
    if (Test-Path $lock) {
        $pid_ = (Get-Content $lock -Raw).Trim()
        $alive = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
        if ($alive) { throw "another downloader is running (pid $pid_); refusing to double-write" }
        Log "cleared stale lock (pid $pid_)"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $lock) | Out-Null
    Set-Content -Path $lock -Value $PID -Encoding utf8
}

function Exit-Lock {
    if (Test-Path $lock) {
        $owner = (Get-Content $lock -Raw).Trim()
        if ($owner -eq "$PID") { Remove-Item $lock -Force -EA SilentlyContinue }
    }
}

$aria = Find-Aria
Enter-Lock
Log "download_fast started (pid $PID, aria2 $Conns conns, parts $($Part -join ','))"

try {
    foreach ($p in $Part) {
        $record  = $records[$p]
        $destDir = Join-Path $data "part$p"
        New-Item -ItemType Directory -Force -Path $destDir | Out-Null

        $files = $null
        for ($t = 1; $t -le 10 -and -not $files; $t++) {
            try { $files = Get-ZenodoFiles $record }
            catch {
                $wait = [math]::Min(30 * $t, 300)
                Log "part $p : Zenodo API unreachable ($($_.Exception.Message)); retry in ${wait}s"
                Start-Sleep -Seconds $wait
            }
        }
        if (-not $files) { Log "part $p : giving up on Zenodo API"; continue }

        foreach ($f in $files) {
            $final = Join-Path $destDir $f.Key
            $partF = "$final.part"

            if (Test-Path $final) {
                Log "part $p : $($f.Key) already complete"
                continue
            }

            # No-progress backoff. The old supervisor spun 359 times gaining 0 MB
            # through a DNS outage; only stalled attempts count against the budget.
            #
            # Progress is judged by how long aria2 stayed up, NOT by file length:
            # with 16 connections aria2 writes at scattered offsets, so the file
            # jumps to its full size within seconds while most of it is still
            # holes. Length would read as "done" and then as "stalled forever".
            $stalled = 0
            while ($stalled -lt 12) {
                Log "part $p : $($f.Key) starting aria2 (attempt $($stalled + 1))"
                $ran = [Diagnostics.Stopwatch]::StartNew()

                # No --lowest-speed-limit: with 16 connections against a
                # congested Zenodo it fires spuriously and aria2 aborts the whole
                # transfer with exit 5 every ~30s. --timeout alone drops the dead
                # connections without killing the download.
                & $aria `
                    --continue=true `
                    --allow-overwrite=false `
                    --auto-file-renaming=false `
                    --max-connection-per-server=$Conns `
                    --split=$Conns `
                    --min-split-size=1M `
                    --max-tries=0 `
                    --retry-wait=10 `
                    --timeout=60 `
                    --connect-timeout=30 `
                    --file-allocation=none `
                    --console-log-level=warn `
                    --summary-interval=60 `
                    --checksum="md5=$($f.Md5)" `
                    --dir=$destDir `
                    --out="$($f.Key).part" `
                    $f.Url

                $code = $LASTEXITCODE
                $ran.Stop()
                $mins = [math]::Round($ran.Elapsed.TotalMinutes, 1)

                # aria2 exits 0 only after the whole file is present AND the
                # --checksum md5 matched, so this is the one safe promotion gate.
                if ($code -eq 0 -and -not (Test-Path "$partF.aria2")) {
                    Move-Item -Path $partF -Destination $final -Force
                    Log "part $p : $($f.Key) COMPLETE (md5 verified, ${mins}m)"
                    break
                }

                if ($ran.Elapsed.TotalSeconds -ge 120) {
                    $stalled = 0
                    Log "part $p : aria2 exited $code after ${mins}m of transfer; resuming"
                } else {
                    $stalled++
                    $wait = [math]::Min(30 * $stalled, 300)
                    Log "part $p : aria2 exited $code after ${mins}m (stall $stalled/12); waiting ${wait}s"
                    Start-Sleep -Seconds $wait
                }
            }

            if ($stalled -ge 12) { Log "part $p : $($f.Key) STALLED OUT; re-run to resume" }
        }

        Log "part $p : done. Tile and discard before the next part:"
        Log "         python -m ml.prepare_trujillo --part $p --discard"
    }
}
finally {
    Exit-Lock
    Log "download_fast finished"
}
