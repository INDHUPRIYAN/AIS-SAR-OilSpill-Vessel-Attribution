# Keeps the Trujillo download alive across network drops, Zenodo throttling,
# and process death. Runs detached so it survives terminal/session exit.
#
# The downloader itself is resumable and single-instance-locked; this only
# restarts it when it exits before both parts are complete.
#
#   powershell -ExecutionPolicy Bypass -File scripts\download_supervisor.ps1
#   (or launch detached via Start-Process -WindowStyle Hidden)

$repo = Split-Path -Parent $PSScriptRoot
$py   = Join-Path $repo ".venv\Scripts\python.exe"
$main = Join-Path $repo "main_system"
$data = Join-Path $repo "data\raw\trujillo"
$log  = Join-Path $data "supervisor.log"
$lock = Join-Path $repo "data\raw\downloader.lock"

function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line -Encoding utf8
}

Log "supervisor started (pid $PID)"

while ($true) {
    $attempt = $attempt + 1
    $p1 = Test-Path (Join-Path $data "part1\01_Train_Val_Oil_Spill_images.7z")
    $p2 = @(Get-ChildItem (Join-Path $data "part2") -Filter *.7z -ErrorAction SilentlyContinue).Count -gt 0
    if ($p1 -and $p2) { Log "ALL PARTS COMPLETE - supervisor exiting"; break }

    # A lock left by a killed process would block every future attempt.
    if (Test-Path $lock) {
        $running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                   Where-Object { $_.CommandLine -match "ml\.download" }
        if (-not $running) { Remove-Item $lock -Force -EA SilentlyContinue; Log "cleared stale lock" }
    }

    $before = 0
    $part = Get-ChildItem (Join-Path $data "part*") -Filter *.part -EA SilentlyContinue |
            Select-Object -First 1
    if ($part) { $before = $part.Length }

    Log "attempt $attempt : launching downloader (at $([math]::Round($before/1GB,2)) GiB)"
    Push-Location $main
    & $py -u -m ml.download --dataset trujillo --part 1 --part 2 2>&1 |
        Tee-Object -FilePath (Join-Path $data "download.log") -Append | Out-Null
    Pop-Location

    $after = 0
    $part = Get-ChildItem (Join-Path $data "part*") -Filter *.part -EA SilentlyContinue |
            Select-Object -First 1
    if ($part) { $after = $part.Length }
    $gained = [math]::Round(($after - $before)/1MB, 1)
    Log "downloader exited (gained $gained MB)"

    Start-Sleep -Seconds 180
}
Log "supervisor finished"
