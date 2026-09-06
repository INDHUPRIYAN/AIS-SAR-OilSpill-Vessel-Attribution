# PRODUCTION chain, resume point: cache already tiled+verified -> train -> evaluate.
# (Tiling finished 08-30 20:01; the machine went down before training started.)
$repo = "C:\Users\Indhu Priyan\Documents\GitHub\AIS-SAR-OilSpill-Vessel-Attribution"
$py   = "$repo\.venv\Scripts\python.exe"
$log  = "$repo\data\raw\trujillo\train_pipeline.log"

function Log($m) {
    Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format "MM-dd HH:mm:ss"), $m) -Encoding utf8
}
function Run-Py($module, $moduleArgs, $label) {
    Log "$label starting"
    Push-Location "$repo\main_system"
    # cmd redirection opens the log with shared read, so a watcher can tail it
    # while training runs (PowerShell Out-File locks it exclusively).
    $argline = ($moduleArgs | ForEach-Object { if ($_ -match ' ') { '"' + $_ + '"' } else { $_ } }) -join ' '
    & cmd /c "`"$py`" -u -m $module $argline > `"$repo\data\raw\trujillo\$label.log`" 2>&1"
    $code = $LASTEXITCODE
    Pop-Location
    Log "$label finished (exit $code)"
    return $code
}

# Keep-awake: the 08-31 run died when the laptop slept on battery and then
# crashed. ES_CONTINUOUS|ES_SYSTEM_REQUIRED blocks idle sleep for the life of
# this process (display may still turn off). It cannot stop a dead battery --
# the machine must stay on AC.
Add-Type -Namespace KeepAwake -Name Native -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
[void][KeepAwake.Native]::SetThreadExecutionState([uint32]0x80000001)

Log "=== PRODUCTION training (resume after tiling) started (pid $PID) ==="
Log "keep-awake lock held (ES_SYSTEM_REQUIRED); machine must stay on AC"
Log "tiling finished earlier: 93646 tiles (15115 oil / 78531 neg), part=1+2+3carve, fp 01e24b0fb0e8"

$outdir = "$repo\data\runs\training\unet-r34-fullcorpus"
$epochsFile = Join-Path $outdir "target_epochs.txt"
$epochs = if (Test-Path $epochsFile) { (Get-Content $epochsFile -Raw).Trim() } else { "40" }
Log "target epochs: $epochs"
$resume = @()
if (Test-Path "$outdir\last.pt") { $resume = @("--resume", "$outdir\last.pt"); Log "resuming from last.pt" }
$code = Run-Py "ml.train_unet" (@("--out", $outdir, "--epochs", $epochs) + $resume) "training"
if ($code -eq 10) {
    Log "PAUSED cleanly by STOP file (all epochs so far checkpointed). Re-run this script to resume."; exit 0
}
if ($code -ne 0) {
    Log "TRAINING FAILED -- see training.log"; exit 3
}

$new = "$outdir\best.pt"
if (Test-Path $new) {
    Run-Py "ml.evaluate" @("--checkpoint", $new, "--sweep",
                           "--out", "$outdir\eval_fullcorpus_holdout.json") "eval_new" | Out-Null
} else {
    Log "no best.pt found after training -- check training.log"
}
Log "ALL DONE -- gate: compare eval_new vs deployed IoU 0.645; export is interactive"
