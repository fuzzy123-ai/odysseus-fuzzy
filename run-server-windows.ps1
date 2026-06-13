#Requires -Version 5.1
<#
  Non-interactive Odysseus server runner for restart-windows.ps1.

  This script assumes launch-windows.ps1 has already created the venv and
  installed dependencies. It intentionally avoids prompts so it can run hidden.
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1",
    [string]$ChromaHost = "127.0.0.1",
    [int]$ChromaPort = 8100,
    [switch]$EnableBuiltinMcp,
    [switch]$DisableBuiltinMcp
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$tracePath = Join-Path $logDir "odysseus-runner.trace.log"
Add-Content -LiteralPath $tracePath -Value ("start {0} pid={1}" -f (Get-Date -Format o), $PID)
$transcriptPath = Join-Path $logDir "odysseus-runner.log"
try {
    Start-Transcript -Path $transcriptPath -Append | Out-Null
    Add-Content -LiteralPath $tracePath -Value ("transcript-started {0} pid={1}" -f (Get-Date -Format o), $PID)
} catch {
    Add-Content -LiteralPath $tracePath -Value ("transcript-failed {0} pid={1} error={2}" -f (Get-Date -Format o), $PID, $_.Exception.Message)
    # Transcript can be unavailable in constrained hosts; server startup should
    # not depend on it.
}

function Repair-PathEnvironmentKeys {
    $pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
    if (-not $pathValue) {
        $pathValue = [Environment]::GetEnvironmentVariable("PATH", "Process")
    }
    if (-not $pathValue) { return }

    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
}

$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    throw "Missing venv Python at $venvPy. Run launch-windows.ps1 once before restart-windows.ps1."
}

$env:CHROMADB_HOST = $ChromaHost
$env:CHROMADB_PORT = [string]$ChromaPort
if ($DisableBuiltinMcp) {
    $env:ODYSSEUS_DISABLE_MCP = "1"
} else {
    Remove-Item Env:ODYSSEUS_DISABLE_MCP -ErrorAction SilentlyContinue
}

Repair-PathEnvironmentKeys
Add-Content -LiteralPath $tracePath -Value ("uvicorn-start {0} pid={1}" -f (Get-Date -Format o), $PID)
& $venvPy -m uvicorn app:app --host $BindHost --port $Port
Add-Content -LiteralPath $tracePath -Value ("uvicorn-exit {0} pid={1} exit={2}" -f (Get-Date -Format o), $PID, $LASTEXITCODE)
