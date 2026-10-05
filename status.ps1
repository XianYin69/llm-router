<# SMSocket kernel status - PowerShell. Shows pid / liveness / urls / log tail. #>
[CmdletBinding()]
param([int]$Tail = 15)
$Root = $PSScriptRoot
$Runtime = Join-Path $Root "runtime"
$PidFile = Join-Path $Runtime "SMSocket.pid"
$LogFile = Join-Path $Runtime "SMSocket.log"
$port = 8000
$cfg = Join-Path $Root "config.yaml"
if (Test-Path $cfg) {
    $m = Select-String -Path $cfg -Pattern 'listen:\s*\S+?:(\d+)' | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}
$key = "-"
$keyFile = Join-Path $Root "smsocket.key"
if (Test-Path $keyFile) { $key = (Get-Content $keyFile -First 1).Trim() }
$state = "down"
if (Test-Path $PidFile) {
    $id = (Get-Content $PidFile -First 1).Trim()
    if ($id -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) { $state = "up (pid $id)" }
}
Write-Host "[status] SMSocket kernel: $state"
Write-Host ("  web  http://127.0.0.1:{0}/" -f $port)
Write-Host ("  api  http://127.0.0.1:{0}/v1" -f $port)
Write-Host ("  key  {0}" -f $key)
if (Test-Path $LogFile) {
    Write-Host "  last log lines:"
    Get-Content $LogFile -Tail $Tail | ForEach-Object { Write-Host "    $_" }
}
