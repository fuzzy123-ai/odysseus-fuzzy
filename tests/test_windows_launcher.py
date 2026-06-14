import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "launch-windows.ps1"
RESTARTER = ROOT / "restart-windows.ps1"
RUNNER = ROOT / "run-server-windows.ps1"
WINDOWS_SCRIPTS = (LAUNCHER, RESTARTER, RUNNER)


def _launcher_text() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def _powershell_binary():
    return shutil.which("powershell") or shutil.which("powershell.exe") or shutil.which("pwsh")


def _assert_powershell_parses(script_name: str):
    powershell = _powershell_binary()
    if not powershell:
        pytest.skip("PowerShell is not available")

    command = (
        f"$src = Get-Content -Raw -LiteralPath '{script_name}';"
        "$errors = $null;"
        "[System.Management.Automation.Language.Parser]::ParseInput($src, [ref]$null, [ref]$errors) | Out-Null;"
        "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_windows_launcher_parses_as_powershell():
    _assert_powershell_parses("launch-windows.ps1")


def test_windows_scripts_force_utf8_python_stdio():
    for script in WINDOWS_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        assert '$env:PYTHONIOENCODING = "utf-8"' in text


def test_windows_launcher_repairs_duplicate_path_keys_before_start_process():
    text = _launcher_text()

    assert "function Repair-PathEnvironmentKeys" in text
    assert "[Environment]::GetEnvironmentVariable(\"Path\", \"Process\")" in text
    assert "[Environment]::SetEnvironmentVariable(\"PATH\", $null, \"Process\")" in text
    assert "[Environment]::SetEnvironmentVariable(\"Path\", $pathValue, \"Process\")" in text

    start_process_positions = [match.start() for match in re.finditer(r"^\s*\$\w+\s*=\s*Start-Process\b", text, re.MULTILINE)]
    assert start_process_positions, "launch-windows.ps1 must start ChromaDB via Start-Process"

    call_positions = [match.start() for match in re.finditer(r"^\s*Repair-PathEnvironmentKeys\s*$", text, re.MULTILINE)]
    assert call_positions, "Repair-PathEnvironmentKeys must be called before Start-Process"
    assert all(
        any(call < start for call in call_positions)
        for start in start_process_positions
    ), "Every Start-Process call must be preceded by Repair-PathEnvironmentKeys"


def test_windows_restarter_parses_as_powershell():
    _assert_powershell_parses("restart-windows.ps1")


def test_windows_server_runner_parses_as_powershell():
    _assert_powershell_parses("run-server-windows.ps1")


def test_windows_restarter_targets_only_this_checkout_uvicorn_and_dry_run():
    text = RESTARTER.read_text(encoding="utf-8")

    assert "function Get-OdysseusServerProcesses" in text
    assert "function Test-OdysseusHttpEndpoint" in text
    assert "Get-NetTCPConnection -LocalPort $Port -State Listen" in text
    assert "netstat -ano -p tcp" in text
    assert "LISTENING" not in text
    assert "ABH\u00d6REN" not in text
    assert "ABHOREN" not in text
    assert '$remoteAddress -notmatch "(:0|\\]:0)$"' in text
    assert "foreach ($pid " not in text
    assert "$server.ProcessId" not in text
    assert "$server.Id" in text
    assert 'Join-Path $PSScriptRoot "venv\\Scripts\\python.exe"' in text
    assert "Resolve-Path $process.Path" in text
    assert "Resolve-Path $venvPython" in text
    assert "Invoke-WebRequest -UseBasicParsing -TimeoutSec 2" in text
    assert "/api/health" in text
    assert '"status"\\s*:\\s*"healthy"' in text
    assert "[switch]$DryRun" in text
    assert "Would start: powershell.exe" in text
    assert "run-server-windows.ps1" in text
    assert '"-File", $runner' in text
    assert '"-File", $launcher' not in text
    assert "-WindowStyle Minimized" in text


def test_windows_restarter_repairs_path_before_start_process():
    text = RESTARTER.read_text(encoding="utf-8")

    start_process_positions = [match.start() for match in re.finditer(r"^\s*\$\w+\s*=\s*Start-Process\b", text, re.MULTILINE)]
    call_positions = [match.start() for match in re.finditer(r"^\s*Repair-PathEnvironmentKeys\s*$", text, re.MULTILINE)]

    assert start_process_positions, "restart-windows.ps1 must start run-server-windows.ps1 via Start-Process"
    assert call_positions, "Repair-PathEnvironmentKeys must be called before Start-Process"
    assert all(
        any(call < start for call in call_positions)
        for start in start_process_positions
    ), "Every Start-Process call must be preceded by Repair-PathEnvironmentKeys"


def test_windows_server_runner_is_non_interactive_uvicorn_wrapper():
    text = RUNNER.read_text(encoding="utf-8")

    assert "Read-Host" not in text
    assert "pip install" not in text
    assert "setup.py" not in text
    assert "venv\\Scripts\\python.exe" in text
    assert "-m uvicorn app:app --host $BindHost --port $Port" in text
    assert "uvicorn-child" not in text
    assert "Repair-PathEnvironmentKeys" in text
    assert "ODYSSEUS_DISABLE_MCP" in text
