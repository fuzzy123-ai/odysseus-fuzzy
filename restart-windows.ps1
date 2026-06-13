#Requires -Version 5.1
<#
  Restart Odysseus on native Windows.

  This helper stops the existing Odysseus uvicorn process for this checkout
  and starts run-server-windows.ps1 in a new hidden PowerShell process so
  automation can apply code changes without asking for a manual restart every
  time. Run launch-windows.ps1 once first for setup/dependencies.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\restart-windows.ps1
    powershell -ExecutionPolicy Bypass -File .\restart-windows.ps1 -Port 7000 -BindHost 127.0.0.1
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1",
    [string]$ChromaHost = "127.0.0.1",
    [int]$ChromaPort = 8100,
    [switch]$EnableBuiltinMcp,
    [switch]$DisableBuiltinMcp,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }

function Repair-PathEnvironmentKeys {
    $pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
    if (-not $pathValue) {
        $pathValue = [Environment]::GetEnvironmentVariable("PATH", "Process")
    }
    if (-not $pathValue) { return }

    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
}

function Test-OdysseusHttpEndpoint {
    try {
        $url = "http://{0}:{1}/api/health" -f $BindHost, $Port
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $url
        return ($response.StatusCode -eq 200 -and $response.Content -match '"status"\s*:\s*"healthy"')
    } catch {
        return $false
    }
}

function Get-OdysseusServerProcesses {
    $venvPython = (Join-Path $PSScriptRoot "venv\Scripts\python.exe")
    $pids = @()
    try {
        $connections = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
        foreach ($conn in $connections) {
            if ($conn.OwningProcess) {
                $pids += [int]$conn.OwningProcess
            }
        }
    } catch {
        $rows = @(netstat -ano -p tcp 2>$null | Select-String -Pattern (":{0}\s+" -f [Regex]::Escape([string]$Port)))
        foreach ($row in $rows) {
            $line = $row.Line.Trim()
            $parts = $line -split "\s+"
            if ($parts.Count -lt 5) { continue }
            $localAddress = $parts[1]
            $remoteAddress = $parts[2]
            if ($localAddress -notmatch (":{0}$" -f [Regex]::Escape([string]$Port))) { continue }
            if ($remoteAddress -notmatch "(:0|\]:0)$") { continue }
            if ([int]$parts[-1] -ne 0) {
                $pids += [int]$parts[-1]
            }
        }
    }

    foreach ($listenerPid in ($pids | Select-Object -Unique)) {
        $process = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        try {
            if ($process.Path -and ((Resolve-Path $process.Path).Path -ieq (Resolve-Path $venvPython).Path)) {
                $process
                continue
            }
            if (Test-OdysseusHttpEndpoint) {
                $process
            }
        } catch {
            if (Test-OdysseusHttpEndpoint) {
                $process
            }
        }
    }
}

function Wait-ProcessExit($processId, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        $p = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if (-not $p) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

Write-Step "Stopping current Odysseus server processes"
$servers = @(Get-OdysseusServerProcesses)
if (-not $servers.Count) {
    Write-Host "No Odysseus uvicorn process found for this checkout."
} else {
    foreach ($server in $servers) {
        Write-Host ("Stopping PID {0}" -f $server.Id)
        if (-not $DryRun) {
            Stop-Process -Id $server.Id -ErrorAction SilentlyContinue
            if (-not (Wait-ProcessExit $server.Id 8)) {
                Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

Write-Step "Starting Odysseus server"
$runner = Join-Path $PSScriptRoot "run-server-windows.ps1"
$args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $runner,
    "-Port", [string]$Port,
    "-BindHost", $BindHost,
    "-ChromaHost", $ChromaHost,
    "-ChromaPort", [string]$ChromaPort
)
if ($DisableBuiltinMcp) {
    $args += "-DisableBuiltinMcp"
}
if ($EnableBuiltinMcp) {
    $args += "-EnableBuiltinMcp"
}

if ($DryRun) {
    Write-Host ("Would start: powershell.exe {0}" -f ($args -join " "))
    exit 0
}

Repair-PathEnvironmentKeys
$process = Start-Process -FilePath "powershell.exe" `
    -ArgumentList $args `
    -WorkingDirectory $PSScriptRoot `
    -WindowStyle Minimized `
    -PassThru

Write-Host ("Odysseus server runner started as PID {0}." -f $process.Id)
