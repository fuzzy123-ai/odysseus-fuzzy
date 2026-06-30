"""Windows hardware probes for HW Fit.

This module keeps the PowerShell/WMI probe separate from the generic hardware
detector while preserving the existing dict shape consumed by HW Fit routes.
"""

from __future__ import annotations

import base64
import json
import shutil
from typing import Callable, Optional


RunCommand = Callable[[object], Optional[str]]
CanonicalArch = Callable[[object], str]


def _powershell_exe() -> str:
    """Pick the best local PowerShell executable without relying on PATH order."""
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def _powershell_encoded_for_ssh(script: str, run_command: RunCommand) -> Optional[str]:
    """Run a PowerShell script on a remote Windows host over SSH.

    Nested quotes in powershell -Command break when passed through Windows
    OpenSSH's cmd wrapper; -EncodedCommand avoids that.
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return run_command(f"powershell -NoProfile -EncodedCommand {encoded}")


def _windows_probe_script() -> str:
    return """
        $r = @{}
        $os = Get-CimInstance Win32_OperatingSystem
        $r.ram_gb = [math]::Round($os.TotalVisibleMemorySize / 1048576, 1)
        $r.avail_gb = [math]::Round($os.FreePhysicalMemory / 1048576, 1)
        $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
        $r.cpu_name = $cpu.Name
        $r.cpu_cores = (Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
        $r.arch = $cpu.AddressWidth
        $r.cpu_arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
        try {
            $nv = nvidia-smi --query-gpu=memory.total,name --format=csv,noheader,nounits 2>$null
            if ($LASTEXITCODE -eq 0 -and $nv) {
                $gpus = @()
                foreach ($line in $nv -split "`n") {
                    $p = $line -split ','
                    if ($p.Count -ge 2) { $gpus += [pscustomobject]@{name = $p[1].Trim(); vram_mb = [double]$p[0].Trim() } }
                }
                $r.gpu_name = $gpus[0].name
                $r.gpu_vram_gb = [math]::Round(($gpus | Measure-Object -Property vram_mb -Sum).Sum / 1024, 1)
                $r.gpu_count = $gpus.Count
                $r.gpu_backend = 'cuda'
            }
        }
        catch {}
        if (-not $r.gpu_name) {
            $wmiGpu = Get-CimInstance Win32_VideoController | Where-Object { $_.AdapterRAM -gt 0 } | Select-Object -First 1
            $GPUDriverKey = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0*"
            $GPUDeviceID = $wmiGpu.PNPDeviceID.Split('&')[0..1] -join '&'
            $VRAMfromRegistry = Get-ItemProperty -Path $GPUDriverKey |
            Where-Object { $_.MatchingDeviceId -like "${GPUDeviceID}*" } |
            Select-Object -ExpandProperty HardwareInformation.qwMemorySize -ErrorAction SilentlyContinue -First 1
            if ($wmiGpu) {
                $r.gpu_name = $wmiGpu.Name
                if ($VRAMfromRegistry -ge $wmiGpu.AdapterRAM) {
                    $r.gpu_vram_gb = [math]::Round($VRAMfromRegistry / 1073741824, 1)
                }
                else {
                    $r.gpu_vram_gb = [math]::Round($wmiGpu.AdapterRAM / 1073741824, 1)
                }
                $r.gpu_count = 1
                $r.gpu_backend = 'cpu_x86';
            }
        }
        $r | ConvertTo-Json -Compress
    """


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _shape_windows_result(data: dict, canonical_cpu_arch: CanonicalArch) -> dict:
    cpu_name = data.get("cpu_name") or "unknown"
    if isinstance(cpu_name, str):
        cpu_name = cpu_name.strip() or "unknown"

    result = {
        "total_ram_gb": data.get("ram_gb", 0),
        "available_ram_gb": data.get("avail_gb", 0),
        "cpu_cores": _as_int(data.get("cpu_cores"), 1),
        "cpu_name": cpu_name,
        "cpu_arch": canonical_cpu_arch(data.get("cpu_arch")),
        "has_gpu": bool(data.get("gpu_name")),
        "gpu_name": data.get("gpu_name"),
        "gpu_vram_gb": data.get("gpu_vram_gb"),
        "gpu_count": _as_int(data.get("gpu_count"), 0),
        "backend": data.get("gpu_backend", "cpu_x86"),
        "homogeneous": True,
        "gpu_error": None,
        "platform": "windows",
    }

    gpu_count = result["gpu_count"] or 0
    if result["has_gpu"] and gpu_count > 0:
        each_vram = round((result["gpu_vram_gb"] or 0) / gpu_count, 1)
        result["gpus"] = [
            {"index": index, "name": result["gpu_name"], "vram_gb": each_vram}
            for index in range(gpu_count)
        ]
        result["gpu_groups"] = [{
            "name": result["gpu_name"],
            "vram_each": each_vram,
            "count": gpu_count,
            "indices": list(range(gpu_count)),
            "vram_total": result["gpu_vram_gb"],
        }]
    return result


def detect_windows(
    run_command: RunCommand,
    *,
    remote_host: Optional[str],
    canonical_cpu_arch: CanonicalArch,
) -> Optional[dict]:
    """Detect Windows hardware via PowerShell/WMI."""
    script = _windows_probe_script()
    if remote_host:
        out = _powershell_encoded_for_ssh(script.strip(), run_command)
    else:
        out = run_command([_powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", script])
    if not out:
        return None

    try:
        data = json.loads(out)
        return _shape_windows_result(data, canonical_cpu_arch)
    except Exception:
        return None
