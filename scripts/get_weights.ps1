# OceanTrace — fetch model weights (screen.onnx + segment.onnx) into the
# path the /detect service reads. Windows PowerShell 5.1 compatible.
#
#   powershell -ExecutionPolicy Bypass -File scripts\get_weights.ps1
#
# Weights are NEVER committed (.gitignore: *.onnx) and never baked into the
# Docker image; they are pulled from a GitHub Release. Base URL comes from the
# OILGUARD_WEIGHTS_URL environment variable, e.g.
#   https://github.com/INDHUPRIYAN/AIS-SAR-OilSpill-Vessel-Attribution/releases/download/weights-v1
#
# Exit codes: 0 = weights present and verified, 1 = missing/failed.

$ErrorActionPreference = "Stop"

# PS 5.1 defaults to TLS 1.0/1.1; GitHub requires TLS 1.2, and without this
# every download dies with "the connection was closed unexpectedly".
[Net.ServicePointManager]::SecurityProtocol = `
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$WeightsDir = Join-Path $RepoRoot "main_system\backend\services\detection\weights"

$DefaultUrl = "https://github.com/INDHUPRIYAN/AIS-SAR-OilSpill-Vessel-Attribution/releases/download/weights-v1"
$BaseUrl = $env:OILGUARD_WEIGHTS_URL
if ([string]::IsNullOrWhiteSpace($BaseUrl)) { $BaseUrl = $DefaultUrl }
$BaseUrl = $BaseUrl.TrimEnd("/")

# name -> expected sha256 (lowercase hex) and size in bytes.
# Computed from the artefacts exported by `python -m ml.export` /
# `python -m ml.train_yolo --export`. Update BOTH values when re-exporting.
$Weights = @(
    @{ Name = "screen.onnx";  Sha256 = "9a6ff8df0d2b4a8f8a287895c6780030c336c5c7f61f1d16d11cc89ceb9ac9fd"; Size = 10423456 },  # yolo11n, fingerprint-stamped 2026-09-01
    @{ Name = "segment.onnx"; Sha256 = "a4aac81f3ddd54ce05859c0fc4faada7b196288ed64a54293ab96488c5804a44"; Size = 97719644 }  # unet-r34-fullcorpus-e48
)

function Test-Weight($Path, $Sha256, $Size) {
    if (-not (Test-Path $Path)) { return $false }
    $item = Get-Item $Path
    if ($item.Length -ne $Size) { return $false }
    $hash = (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLower()
    return ($hash -eq $Sha256)
}

if (-not (Test-Path $WeightsDir)) {
    New-Item -ItemType Directory -Force -Path $WeightsDir | Out-Null
}

$failed = @()
foreach ($w in $Weights) {
    $target = Join-Path $WeightsDir $w.Name
    if (Test-Weight $target $w.Sha256 $w.Size) {
        Write-Host ("[get_weights] {0} already present, hash OK - skipping." -f $w.Name)
        continue
    }
    if (Test-Path $target) {
        Write-Host ("[get_weights] {0} exists but size/hash mismatch - re-downloading." -f $w.Name)
    }

    $url = "$BaseUrl/$($w.Name)"
    $tmp = "$target.download"
    Write-Host ("[get_weights] downloading {0} ..." -f $url)
    try {
        # Invoke-WebRequest progress rendering makes large downloads ~10x
        # slower on PS 5.1; silence it for the transfer only.
        $oldProgress = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        try {
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
        } finally {
            $ProgressPreference = $oldProgress
        }
        Move-Item -Force $tmp $target
    } catch {
        Write-Host ("[get_weights] ERROR downloading {0}: {1}" -f $w.Name, $_.Exception.Message)
        if (Test-Path $tmp) { Remove-Item -Force $tmp }
        $failed += $w.Name
        continue
    }

    if (Test-Weight $target $w.Sha256 $w.Size) {
        Write-Host ("[get_weights] {0} downloaded and sha256 verified." -f $w.Name)
    } else {
        Write-Host ("[get_weights] ERROR: {0} failed sha256/size verification - deleting." -f $w.Name)
        Remove-Item -Force $target
        $failed += $w.Name
    }
}

if ($failed.Count -eq 0) {
    Write-Host "[get_weights] all weights present and verified in $WeightsDir"
    exit 0
}

Write-Host ""
Write-Host "[get_weights] FAILED for: $($failed -join ', ')"
if ($BaseUrl -eq $DefaultUrl) {
    Write-Host @"

The GitHub Release 'weights-v1' probably does not exist yet. Someone with the
trained weights (they live only on the training laptop today, at
  main_system\backend\services\detection\weights\screen.onnx
  main_system\backend\services\detection\weights\segment.onnx
) must publish them once:

  cd <repo root>
  gh release create weights-v1 ``
      main_system/backend/services/detection/weights/screen.onnx ``
      main_system/backend/services/detection/weights/segment.onnx ``
      --title "Model weights v1" --notes "screen.onnx (YOLO11n DARTIS) + segment.onnx (U-Net Trujillo)"

Then re-run this script. To pull from a different host instead, set:
  `$env:OILGUARD_WEIGHTS_URL = "https://<host>/<path>"   (files served as <url>/screen.onnx etc.)
"@
}
Write-Host "[get_weights] NOTE: without weights the /detect service still runs, using the threshold_fallback engine."
exit 1
