<# SMSocket kernel stopper - PowerShell. Stops the detached process (runtime\SMSocket.pid). #>
[CmdletBinding()]
param([switch]$Force)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$PidFile = Join-Path $Root "runtime\SMSocket.pid"
if (-not (Test-Path $PidFile)) { Write-Host "[stop] no pid file - kernel not started by start.ps1"; exit 0 }
$ids = @((Get-Content $PidFile) | ForEach-Object { $_.Trim() } | Where-Object { $_ })
foreach ($id in $ids) {
    if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue)) { continue }
    $kids = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$id" -ErrorAction SilentlyContinue | ForEach-Object { $_.ProcessId })
    foreach ($k in $kids) { Stop-Process -Id $k -Force -ErrorAction SilentlyContinue }
    if ($Force) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    else { Stop-Process -Id $id -ErrorAction SilentlyContinue }
    Write-Host "[stop] kernel stopped (pid $id)"
}
Remove-Item $PidFile -Force
