# ONE COMMAND to train / watch / pause the full-corpus U-Net.
#
#   powershell -ExecutionPolicy Bypass -File scripts\train.ps1          # start (or attach) + live progress
#   powershell -ExecutionPolicy Bypass -File scripts\train.ps1 -Stop    # pause cleanly at the next epoch boundary
#
# Safe to run any number of times: if training is already running it just
# attaches and shows progress. Closing the window / Ctrl+C never stops
# training -- it runs detached with a keep-awake lock. Re-running after a
# crash or pause resumes from last.pt. Keep the laptop ON AC POWER.

param([switch] $Stop, [int] $Epochs = 0)

$repo   = Split-Path -Parent $PSScriptRoot
$outdir = Join-Path $repo "data\runs\training\unet-r34-fullcorpus"
$plog   = Join-Path $repo "data\raw\trujillo\train_pipeline.log"
$tlog   = Join-Path $repo "data\raw\trujillo\training.log"
$chain  = Join-Path $repo "scripts\train_chain.ps1"

function Running {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "ml\.train_unet|ml\.evaluate" } | Select-Object -First 1
}

$epochsFile = Join-Path $outdir "target_epochs.txt"
if ($Epochs -gt 0) {
    New-Item -ItemType Directory -Force -Path $outdir | Out-Null
    Set-Content -Path $epochsFile -Value $Epochs -Encoding ascii
    Write-Host "Target set to $Epochs epochs (applies when training starts or resumes)."
}
if ($Stop) {
    New-Item -ItemType File -Force -Path (Join-Path $outdir "STOP") | Out-Null
    Write-Host "STOP requested. Training finishes the current epoch (<=20 min), saves, and exits."
    Write-Host "Re-run scripts\train.ps1 later to resume from that epoch."
    exit 0
}

$proc = Running
if ($proc) {
    Write-Host "Training already running (python pid $($proc.ProcessId)) -- attaching to progress." -ForegroundColor Green
} else {
    New-Item -ItemType Directory -Force -Path $outdir | Out-Null
    Start-Process powershell -ArgumentList '-ExecutionPolicy','Bypass','-File',$chain `
        -WindowStyle Hidden -WorkingDirectory $repo | Out-Null
    Write-Host "Training launched (detached, keep-awake held). Resumes from last.pt if one exists." -ForegroundColor Green
    Start-Sleep -Seconds 8
}

Write-Host "Live progress (Ctrl+C or close window = stop WATCHING only, training continues)"
Write-Host "Pause cleanly at any time:  powershell -ExecutionPolicy Bypass -File scripts\train.ps1 -Stop`n"

# Progress comes from history.jsonl (one JSON record per epoch, written and
# closed by Python) -- the chain holds training.log locked while it runs.
$hist = Join-Path $outdir "history.jsonl"
$total = if (Test-Path $epochsFile) { [int](Get-Content $epochsFile -Raw).Trim() } else { 40 }
$seen = 0
$bestSoFar = -1.0
while ($true) {
    if (Test-Path $hist) {
        $recs = @(Get-Content $hist -ErrorAction SilentlyContinue | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
        if ($recs.Count -gt $seen) {
            foreach ($r in $recs[$seen..($recs.Count - 1)]) {
                if ($r.val_iou -gt $bestSoFar) { $bestSoFar = $r.val_iou; $flag = "  <- best" } else { $flag = "" }
                "epoch {0,3}/{1}  val IoU {2:N4}  P {3:N3}  R {4:N3}  loss {5:N4}  [{6:N0}s]{7}" -f `
                    $r.epoch, $total, $r.val_iou, $r.val_precision, $r.val_recall, $r.val_loss, $r.seconds, $flag | Write-Host
            }
            $seen = $recs.Count
            $avg = ($recs | Measure-Object -Property seconds -Average).Average
            $left = [math]::Max(0, $total - $seen)
            $eta = (Get-Date).AddSeconds($avg * $left)
            Write-Host ("        {0} epoch(s) left, ~{1:N1} h, ETA {2:HH:mm}" -f $left, ($avg * $left / 3600), $eta) -ForegroundColor DarkGray
        }
    }
    if (Test-Path $plog) {
        $tail = Get-Content $plog -Tail 1 -ErrorAction SilentlyContinue
        if ($tail -match "ALL DONE|FAILED|PAUSED") {
            Write-Host "`n$tail" -ForegroundColor Yellow
            if ($tail -match "ALL DONE") {
                $ev = Join-Path $outdir "eval_fullcorpus_holdout.json"
                if (Test-Path $ev) { Write-Host "`nHoldout evaluation (compare IoU to deployed 0.645):"; Get-Content $ev }
            }
            break
        }
    }
    if (-not (Running) -and $seen -gt 0) {
        Start-Sleep -Seconds 60
        if (-not (Running)) { Write-Host "`nTraining process is not running (crash/power loss?). Re-run this script to resume from last.pt." -ForegroundColor Red; break }
    }
    Start-Sleep -Seconds 30
}
