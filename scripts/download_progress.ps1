# Exact download progress, read from aria2's control file.
#
# Needed because neither obvious signal tells the truth mid-transfer:
#   - file length lies (16 connections write at scattered offsets, so the file
#     reaches full size within seconds while most of it is still holes)
#   - aria2's console line rounds to whole GiB and whole percent, so ~380 MB of
#     real progress can pass with the display frozen at "10GiB/37GiB(28%)"
#
# The .aria2 control file carries a bitfield of completed pieces, which is exact.
#
#   powershell -ExecutionPolicy Bypass -File scripts\download_progress.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\download_progress.ps1 -SampleSeconds 300

param(
    [int] $SampleSeconds = 0
)

$repo = Split-Path -Parent $PSScriptRoot
$data = Join-Path $repo "data\raw\trujillo"

function Get-Progress($ctl) {
    $b = [IO.File]::ReadAllBytes($ctl)
    # aria2 writes these fields big-endian.
    function BE($arr, $off, $len) {
        $v = [uint64]0
        for ($i = 0; $i -lt $len; $i++) { $v = ($v -shl 8) -bor $arr[$off + $i] }
        $v
    }
    $o = 2 + 4                              # version + extension
    $ihl = [int](BE $b $o 4); $o += 4 + $ihl   # infoHash (empty for plain HTTP)
    $piece = [int](BE $b $o 4); $o += 4
    $total = BE $b $o 8;       $o += 8 + 8     # totalLength + uploadLength
    $bfl = [int](BE $b $o 4);  $o += 4

    $bits = 0
    for ($i = 0; $i -lt $bfl; $i++) {
        $x = $b[$o + $i]
        while ($x) { $bits += ($x -band 1); $x = $x -shr 1 }
    }
    # The final piece is short, so cap the total rather than over-reporting.
    $done = [uint64]$bits * $piece
    if ($done -gt $total) { $done = $total }
    [pscustomobject]@{ Done = $done; Total = $total }
}

$ctls = Get-ChildItem $data -Recurse -Filter *.aria2 -ErrorAction SilentlyContinue
if (-not $ctls) {
    Write-Host "No .aria2 control files - nothing is downloading."
    Write-Host "Completed archives:"
    Get-ChildItem $data -Recurse -Filter *.7z -EA SilentlyContinue |
        ForEach-Object { "  {0}  {1:N2} GiB" -f $_.Name, ($_.Length / 1GB) }
    return
}

foreach ($c in $ctls) {
    $name = $c.Name -replace '\.part\.aria2$', ''
    $a = Get-Progress $c.FullName

    if ($SampleSeconds -gt 0) {
        Start-Sleep -Seconds $SampleSeconds
        $b = Get-Progress $c.FullName
        $rate = ($b.Done - $a.Done) / $SampleSeconds
        $left = $b.Total - $b.Done
        $eta = if ($rate -gt 0) { "{0:N1} h" -f ($left / $rate / 3600) } else { "stalled" }
        "{0}`n  {1:N2} / {2:N2} GiB ({3:N2}%)  rate {4:N0} KiB/s  ETA {5}" -f `
            $name, ($b.Done / 1GB), ($b.Total / 1GB), (100 * $b.Done / $b.Total), ($rate / 1KB), $eta
    }
    else {
        "{0}`n  {1:N2} / {2:N2} GiB ({3:N2}%)" -f `
            $name, ($a.Done / 1GB), ($a.Total / 1GB), (100 * $a.Done / $a.Total)
    }
}
