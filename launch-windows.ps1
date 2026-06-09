#Requires -Version 5.1
<#
  Odysseus - native Windows launcher (no Docker).

  One command to: create a virtualenv, install dependencies, run first-time
  setup (prints an admin password on first run), and start the server.
  Safe to re-run - it skips whatever already exists.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -Port 7000 -BindHost 127.0.0.1

  Tip: bind 127.0.0.1 (default) for local-only use. Use 0.0.0.0 only when you
  intentionally want other devices on your LAN to reach it.
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1",
    [string]$ChromaHost = "127.0.0.1",
    [int]$ChromaPort = 8100
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

function Test-TcpPort($hostName, $portNumber) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($hostName, $portNumber, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne(1000, $false)
        if ($connected) { $client.EndConnect($async) }
        $client.Close()
        return $connected
    } catch {
        return $false
    }
}

function Test-VenvPython($pythonPath) {
    if (-not (Test-Path $pythonPath)) { return $false }
    try {
        & $pythonPath -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Move-BrokenVenvAside($venvPath) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backupPath = Join-Path $PSScriptRoot ("venv.broken-" + $stamp)
    try {
        Write-Host "Existing venv is broken - moving it to $backupPath"
        Move-Item -LiteralPath $venvPath -Destination $backupPath
        return $true
    } catch {
        Fail "The existing venv is broken, but it could not be moved aside. Close any running Odysseus/Chroma/Python processes, then rename or remove '$venvPath' manually and re-run this script. Details: $($_.Exception.Message)"
    }
}

function Test-ChromaReady($pythonPath, $hostName, $portNumber) {
    try {
        & $pythonPath -c "import chromadb; chromadb.HttpClient(host='$hostName', port=$portNumber).heartbeat()" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Find-GitBash {
    $cmd = Get-Command bash -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $roots = @()
    foreach ($name in @("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LocalAppData")) {
        $base = [Environment]::GetEnvironmentVariable($name)
        if ($base) { $roots += (Join-Path $base "Git") }
    }
    $roots += @("C:\Program Files\Git", "C:\Program Files (x86)\Git")

    foreach ($root in ($roots | Select-Object -Unique)) {
        foreach ($relative in @("bin\bash.exe", "usr\bin\bash.exe")) {
            $candidate = Join-Path $root $relative
            if (Test-Path $candidate) { return $candidate }
        }
    }
    return $null
}

# 1. Locate a Python interpreter (3.11+ required)
Write-Step "Checking for Python"
function Get-PythonVersionText($launcher, $launcherArgs) {
    try {
        return (& $launcher @launcherArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null).Trim()
    } catch {
        return $null
    }
}

$pyExe = $null
$pyArgs = @()
$pyVersion = $null

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    foreach ($v in @("-3.13", "-3.12", "-3.11")) {
        $ver = Get-PythonVersionText $pyLauncher.Source @($v)
        if ($ver) {
            $pyExe = $pyLauncher.Source
            $pyArgs = @($v)
            $pyVersion = $ver
            break
        }
    }
}

if (-not $pyExe) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $ver = Get-PythonVersionText $pythonCmd.Source @()
        if ($ver) {
            $versionParts = $ver.Split('.')
            $major = [int]$versionParts[0]
            $minor = [int]$versionParts[1]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                $pyExe = $pythonCmd.Source
                $pyVersion = $ver
            }
        }
    }
}

if (-not $pyExe) {
    Fail "Couldn't find Python 3.11+ for Windows setup. Install Python 3.11+ (or open the Python launcher with 'py -3.11') from https://www.python.org/downloads/, then re-run this script."
}
$pythonLabel = ("Using Python {0}: {1} {2}" -f $pyVersion, $pyExe, ($pyArgs -join ' ')).TrimEnd()
Write-Host $pythonLabel

# 2. Create the virtualenv if missing
$venvDir = Join-Path $PSScriptRoot "venv"
$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Step "Creating virtual environment (venv)"
    & $pyExe @pyArgs -m venv venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "Failed to create the virtual environment." }
} else {
    if (-not (Test-VenvPython $venvPy)) {
        Move-BrokenVenvAside $venvDir | Out-Null
        Write-Step "Creating fresh virtual environment (venv)"
        & $pyExe @pyArgs -m venv venv
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "Failed to recreate the virtual environment." }
        if (-not (Test-VenvPython $venvPy)) { Fail "The recreated venv still cannot run Python. Check the Python installation selected above: $pythonLabel" }
        Write-Host "Fresh venv created."
    } else {
        Write-Host "venv already exists and is usable - skipping creation."
    }
}

# 3. Install / update dependencies
Write-Step "Installing dependencies (first run can take a few minutes)"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed. Scroll up for the pip error." }

# 4. First-time setup (creates data dirs, DB, .env, admin user)
Write-Step "Running first-time setup"
& $venvPy setup.py
if ($LASTEXITCODE -ne 0) { Fail "setup.py failed." }

# 5. Start or reuse ChromaDB
$env:CHROMADB_HOST = $ChromaHost
$env:CHROMADB_PORT = [string]$ChromaPort
$chromaExe = Join-Path $PSScriptRoot "venv\Scripts\chroma.exe"
$chromaData = Join-Path $PSScriptRoot "data\chroma"
$chromaOutLog = Join-Path $PSScriptRoot "logs\chromadb.out.log"
$chromaErrLog = Join-Path $PSScriptRoot "logs\chromadb.err.log"

if (-not (Test-Path $chromaExe)) {
    Fail "ChromaDB CLI was not installed. Ensure requirements.txt installs the full 'chromadb' package, then re-run this script."
}

if (-not (Test-ChromaReady $venvPy $ChromaHost $ChromaPort)) {
    if (Test-TcpPort $ChromaHost $ChromaPort) {
        Fail "Something is listening on $ChromaHost`:$ChromaPort, but it is not a healthy ChromaDB server. Stop that process or choose another -ChromaPort."
    }

    Write-Step ("Starting ChromaDB at http://{0}:{1}" -f $ChromaHost, $ChromaPort)
    New-Item -ItemType Directory -Force -Path $chromaData | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path $chromaOutLog -Parent) | Out-Null
    $chromaProcess = Start-Process -FilePath $chromaExe `
        -ArgumentList @("run", "--path", $chromaData, "--host", $ChromaHost, "--port", [string]$ChromaPort) `
        -WorkingDirectory $PSScriptRoot `
        -RedirectStandardOutput $chromaOutLog `
        -RedirectStandardError $chromaErrLog `
        -WindowStyle Hidden `
        -PassThru

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if ($chromaProcess.HasExited) {
            Fail "ChromaDB exited during startup. See $chromaOutLog and $chromaErrLog for details."
        }
        if (Test-ChromaReady $venvPy $ChromaHost $ChromaPort) {
            Write-Host "ChromaDB is ready."
            break
        }
        Start-Sleep -Milliseconds 750
    }

    if (-not (Test-ChromaReady $venvPy $ChromaHost $ChromaPort)) {
        Fail "ChromaDB did not become ready within 30 seconds. See $chromaOutLog and $chromaErrLog for details."
    }
} else {
    Write-Host "ChromaDB already running at $ChromaHost`:$ChromaPort - reusing it."
}

# 6. Friendly note about Git Bash (full Cookbook / agent-shell parity)
if (-not (Find-GitBash)) {
    Write-Host ""
    Write-Host "NOTE: Git Bash (bash.exe) was not found on PATH." -ForegroundColor Yellow
    Write-Host "      The core app works without it. For full Cookbook background" -ForegroundColor Yellow
    Write-Host "      downloads and the agent shell tool, install Git for Windows:" -ForegroundColor Yellow
    Write-Host "      https://git-scm.com/download/win" -ForegroundColor Yellow
}

# 7. Start the server (use `python -m uvicorn` - bare `uvicorn` may not be on PATH)
Write-Step ("Starting Odysseus at http://{0}:{1}" -f $BindHost, $Port)
Write-Host "Press Ctrl+C to stop."
Write-Host ""
& $venvPy -m uvicorn app:app --host $BindHost --port $Port
