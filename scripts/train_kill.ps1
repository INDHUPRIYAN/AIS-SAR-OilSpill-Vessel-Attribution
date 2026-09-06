# Hard-stop every training chain/trainer/dataloader-worker for this repo.
# Use only when the STOP file cannot work (e.g. trainer predates STOP support).
$victims = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(python|powershell|cmd)\.exe$' -and
    $_.CommandLine -match 'AIS-SAR-OilSpill' -and
    $_.CommandLine -match 'train_unet|train_chain|continue_training|multiprocessing\.spawn'
}
foreach ($v in $victims) {
    Write-Host ("killing {0,-6} {1}" -f $v.ProcessId, $v.CommandLine.Substring(0,[Math]::Min(80,$v.CommandLine.Length)))
    Stop-Process -Id $v.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 4
$left = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ml\.train_unet' })
Write-Host "remaining trainers: $($left.Count)"
