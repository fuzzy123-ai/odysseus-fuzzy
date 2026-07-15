param(
    [ValidateSet("describe", "check", "health", "serve")]
    [string]$Action = "serve"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = if ($env:ODYSSEUS_PYTHON) {
    $env:ODYSSEUS_PYTHON
} else {
    Join-Path $RepoRoot "venv\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Odysseus Python runtime not found. Set ODYSSEUS_PYTHON explicitly."
}

Push-Location $RepoRoot
try {
    & $Python -m src.temporal_runtime.config $Action
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
