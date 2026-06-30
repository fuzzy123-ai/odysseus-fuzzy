"""Tail route registrations for Cookbook routes.

Large backend-only Cookbook endpoints live here so ``routes.cookbook_routes``
can stay as the download/serve setup facade while preserving the same public
API paths.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.platform_compat import IS_WINDOWS, kill_process_tree, pid_alive
from routes._validators import validate_remote_host, validate_ssh_port
from routes.cookbook_helpers import _SESSION_ID_RE, _parse_serve_phase
from routes.cookbook_output import (
    HF_CACHE_COMPLETE_PROBE,
    HF_CACHE_INCOMPLETE_PROBE,
    classify_dead_download,
    error_aware_output_tail,
)
from routes.shell_routes import TMUX_LOG_DIR
from src.auth_helpers import require_user

logger = logging.getLogger(__name__)


def register_cookbook_tail_routes(
    router: APIRouter,
    *,
    cookbook_state_path: Path,
    state_for_client,
    state_for_storage,
    diagnose_serve_output,
    require_admin_func,
) -> None:
    _cookbook_state_path = Path(cookbook_state_path)
    _state_for_client = state_for_client
    _state_for_storage = state_for_storage
    _diagnose_serve_output = diagnose_serve_output
    require_admin = require_admin_func

    # GPU availability probe

    async def _run_nvidia_smi(query: str, host: str | None, ssh_port: str | None, timeout: int = 8):
        """Run nvidia-smi locally or over SSH. Returns (stdout, error_or_None)."""
        if host:
            pf = f"-p {ssh_port} " if ssh_port and ssh_port != "22" else ""
            cmd = f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {pf}{host} '{query}'"
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(query),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return None, "nvidia-smi timed out"
        if proc.returncode != 0:
            err = (stderr.decode("utf-8", errors="replace") or "").strip()[:200]
            return None, err or "nvidia-smi failed"
        return stdout.decode("utf-8", errors="replace"), None

    async def _run_gpu_shell(cmd_text: str, host: str | None, ssh_port: str | None, timeout: int = 8):
        """Run a small GPU probe shell command locally or over SSH."""
        if host:
            pf = f"-p {ssh_port} " if ssh_port and ssh_port != "22" else ""
            quoted_cmd = shlex.quote(cmd_text)
            remote_cmd = (
                f"if command -v sh >/dev/null 2>&1; then sh -lc {quoted_cmd}; "
                f"elif command -v bash >/dev/null 2>&1; then bash -lc {quoted_cmd}; "
                f"elif command -v zsh >/dev/null 2>&1; then zsh -lc {quoted_cmd}; "
                "else echo 'No POSIX shell found for GPU probe' >&2; exit 127; fi"
            )
            cmd = f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {pf}{host} {shlex.quote(remote_cmd)}"
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                cmd_text, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return None, "GPU probe timed out"
        if proc.returncode != 0:
            err = (stderr.decode("utf-8", errors="replace") or "").strip()[:200]
            return None, err or f"GPU probe failed ({proc.returncode})"
        return stdout.decode("utf-8", errors="replace"), None

    async def _gpu_read_file(path: str, host: str | None, ssh_port: str | None) -> str | None:
        out, err = await _run_gpu_shell(f"cat {shlex.quote(path)} 2>/dev/null", host, ssh_port, timeout=4)
        if err is not None or out is None:
            return None
        return out.strip()

    async def _probe_gpu_device_processes(host: str | None, ssh_port: str | None) -> list[dict]:
        pid_cmd = (
            "{ command -v lsof >/dev/null 2>&1 && "
            "lsof -w -t /dev/kfd /dev/dri/renderD* 2>/dev/null || true; "
            "command -v fuser >/dev/null 2>&1 && "
            "fuser /dev/kfd /dev/dri/renderD* 2>/dev/null || true; } "
            "| tr ' ' '\\n' | sed '/^[0-9][0-9]*$/!d' | sort -n -u"
        )
        out, err = await _run_gpu_shell(pid_cmd, host, ssh_port, timeout=5)
        if err is not None or not out:
            return []
        processes = []
        seen = set()
        for raw in out.splitlines():
            try:
                pid = int(raw.strip())
            except ValueError:
                continue
            if pid in seen:
                continue
            seen.add(pid)
            name_out, _ = await _run_gpu_shell(f"ps -p {pid} -o comm= 2>/dev/null", host, ssh_port, timeout=3)
            name = (name_out or "").strip().splitlines()[0] if (name_out or "").strip() else "process"
            processes.append({"pid": pid, "name": name[:80], "used_mb": 0})
        return processes

    async def _probe_amd_sysfs(host: str | None, ssh_port: str | None) -> list[dict]:
        out, err = await _run_gpu_shell("ls -1 /sys/class/drm 2>/dev/null", host, ssh_port, timeout=4)
        if err is not None or not out:
            return []
        gpus = []
        for entry in out.split():
            if not entry.startswith("card") or "-" in entry:
                continue
            base = f"/sys/class/drm/{entry}/device"
            vendor = await _gpu_read_file(f"{base}/vendor", host, ssh_port)
            if vendor != "0x1002":
                continue
            vram_raw = await _gpu_read_file(f"{base}/mem_info_vram_total", host, ssh_port)
            vis_raw = await _gpu_read_file(f"{base}/mem_info_vis_vram_total", host, ssh_port)
            gtt_raw = await _gpu_read_file(f"{base}/mem_info_gtt_total", host, ssh_port)
            vram_bytes = int(vram_raw) if vram_raw and vram_raw.isdigit() else 0
            vis_bytes = int(vis_raw) if vis_raw and vis_raw.isdigit() else 0
            gtt_bytes = int(gtt_raw) if gtt_raw and gtt_raw.isdigit() else 0
            total_bytes = max(vram_bytes, vis_bytes)
            used_attr = "mem_info_vis_vram_used" if vis_bytes and vis_bytes >= vram_bytes else "mem_info_vram_used"
            unified = bool(vis_bytes and vis_bytes >= vram_bytes)
            if total_bytes <= 0:
                total_bytes = gtt_bytes
                used_attr = "mem_info_gtt_used"
                unified = True
            if total_bytes <= 0:
                continue
            used_raw = await _gpu_read_file(f"{base}/{used_attr}", host, ssh_port)
            used_bytes = int(used_raw) if used_raw and used_raw.isdigit() else 0
            name = await _gpu_read_file(f"{base}/product_name", host, ssh_port)
            if not name:
                device = await _gpu_read_file(f"{base}/device", host, ssh_port)
                name = f"AMD GPU {device or entry}"
            total_mb = max(0, int(total_bytes / (1024 * 1024)))
            used_mb = max(0, min(total_mb, int(used_bytes / (1024 * 1024))))
            free_mb = max(0, total_mb - used_mb)
            # GTT = the system-RAM pool the GPU pages into when VRAM is full.
            # On a discrete card a large gtt_used means the model spilled past
            # VRAM into RAM over PCIe — much slower. Surface it so the UI can
            # warn "spilling to RAM" instead of the user wondering why it's slow.
            gtt_used_raw = await _gpu_read_file(f"{base}/mem_info_gtt_used", host, ssh_port)
            gtt_used_mb = max(0, int(int(gtt_used_raw) / (1024 * 1024))) if (gtt_used_raw and gtt_used_raw.isdigit()) else 0
            gpus.append({
                "index": len(gpus), "name": name, "uuid": entry,
                "free_mb": free_mb, "total_mb": total_mb, "used_mb": used_mb,
                "gtt_used_mb": gtt_used_mb,
                "util_pct": 0, "busy": bool(total_mb and (free_mb / total_mb) < 0.85),
                "processes": [], "backend": "rocm", "source": "amd-sysfs",
                "unified_memory": unified,
            })
        if gpus:
            processes = await _probe_gpu_device_processes(host, ssh_port)
            if processes:
                gpus[0]["processes"] = processes
                gpus[0]["busy"] = True
        return gpus

    @router.get("/api/cookbook/gpus")
    async def list_gpus(request: Request, host: str | None = None, ssh_port: str | None = None):
        """Probe GPU memory/process state locally or via SSH.

        Probe order:
            1. NVIDIA via nvidia-smi
            2. AMD/ROCm and unified-memory APUs via /sys/class/drm
            3. Generic GPU device holders via /dev/kfd and /dev/dri/renderD*

        Returned shape:
            { "ok": True, "gpus": [
                {"index": 0, "name": "...", "free_mb": int, "total_mb": int,
                 "used_mb": int, "util_pct": int, "busy": bool,
                 "uuid": "GPU-...",
                 "processes": [{"pid": int, "name": str, "used_mb": int}, ...]
                }, ...
            ]}
        `busy` is True when free_mb/total_mb < 0.5.
        """
        require_admin(request)
        host = validate_remote_host(host)
        ssh_port = validate_ssh_port(ssh_port)
        gpu_query = "nvidia-smi --query-gpu=index,name,memory.free,memory.total,memory.used,utilization.gpu,uuid --format=csv,noheader,nounits"
        nvidia_error = None
        try:
            gpu_out, err = await _run_nvidia_smi(gpu_query, host, ssh_port)
            if err is not None:
                nvidia_error = err
                gpu_out = ""
        except FileNotFoundError:
            nvidia_error = "nvidia-smi not found"
            gpu_out = ""
        except Exception as e:
            nvidia_error = str(e)[:200]
            gpu_out = ""

        gpus = []
        uuid_to_idx: dict[str, int] = {}
        for line in (gpu_out or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                continue
            try:
                idx = int(parts[0])
                name = parts[1]
                free_mb = int(float(parts[2]))
                total_mb = int(float(parts[3]))
                used_mb = int(float(parts[4]))
                util_pct = int(float(parts[5]))
                gpu_uuid = parts[6]
            except (ValueError, IndexError):
                continue
            busy = total_mb > 0 and (free_mb / total_mb) < 0.5
            uuid_to_idx[gpu_uuid] = idx
            gpus.append({
                "index": idx, "name": name, "uuid": gpu_uuid,
                "free_mb": free_mb, "total_mb": total_mb,
                "used_mb": used_mb, "util_pct": util_pct,
                "busy": busy, "processes": [],
            })

        # Best-effort process listing — skip silently if it fails
        proc_query = "nvidia-smi --query-compute-apps=pid,gpu_uuid,process_name,used_memory --format=csv,noheader,nounits"
        try:
            proc_out, proc_err = await _run_nvidia_smi(proc_query, host, ssh_port, timeout=5)
            if proc_err is None and proc_out:
                gpus_by_idx = {g["index"]: g for g in gpus}
                for line in proc_out.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 4:
                        continue
                    try:
                        pid = int(parts[0])
                        pname = parts[2]
                        pmem = int(float(parts[3]))
                    except (ValueError, IndexError):
                        continue
                    idx = uuid_to_idx.get(parts[1])
                    if idx is None or idx not in gpus_by_idx:
                        continue
                    gpus_by_idx[idx]["processes"].append({
                        "pid": pid, "name": pname, "used_mb": pmem,
                    })
        except Exception:
            pass

        if gpus:
            return {"ok": True, "gpus": gpus, "backend": "cuda", "source": "nvidia-smi"}

        # Local Apple Silicon / Metal fallback. macOS has no nvidia-smi and no
        # Linux /sys/class/drm tree, but services.hwfit.hardware already knows
        # how to size the shared unified-memory GPU budget. Keep this route in
        # sync so Cookbook's GPU picker doesn't show "nvidia-smi not found" on
        # native Mac launches.
        if not host and sys.platform == "darwin":
            try:
                from services.hwfit.hardware import detect_system
                info = detect_system(fresh=True)
                backend = str(info.get("backend") or "").lower()
                if backend in {"metal", "mps", "apple"} and info.get("gpu_count", 0) > 0:
                    total_mb = int(float(info.get("gpu_vram_gb") or info.get("total_ram_gb") or 0) * 1024)
                    free_mb = int(float(info.get("available_ram_gb") or 0) * 1024)
                    if total_mb and (free_mb <= 0 or free_mb > total_mb):
                        free_mb = total_mb
                    used_mb = max(0, total_mb - max(0, free_mb))
                    return {
                        "ok": True,
                        "gpus": [{
                            "index": 0,
                            "name": info.get("gpu_name") or info.get("cpu_name") or "Apple Silicon GPU",
                            "uuid": "apple-metal-0",
                            "free_mb": max(0, free_mb),
                            "total_mb": max(0, total_mb),
                            "used_mb": used_mb,
                            "util_pct": 0,
                            "busy": bool(total_mb and (free_mb / total_mb) < 0.5),
                            "processes": [],
                            "backend": "metal",
                            "source": "apple-metal",
                            "unified_memory": True,
                        }],
                        "backend": "metal",
                        "source": "apple-metal",
                        "fallback_from": "nvidia-smi",
                        "nvidia_error": nvidia_error,
                    }
            except Exception as e:
                logger.warning("Apple Metal GPU fallback failed: %s", e)

        amd_gpus = await _probe_amd_sysfs(host, ssh_port)
        if amd_gpus:
            return {
                "ok": True,
                "gpus": amd_gpus,
                "backend": "rocm",
                "source": "amd-sysfs",
                "fallback_from": "nvidia-smi",
                "nvidia_error": nvidia_error,
            }

        processes = await _probe_gpu_device_processes(host, ssh_port)
        if processes:
            return {
                "ok": True,
                "gpus": [{
                    "index": 0, "name": "GPU device holders", "uuid": "dev-dri",
                    "free_mb": 0, "total_mb": 0, "used_mb": 0, "util_pct": 0,
                    "busy": True, "processes": processes,
                    "backend": "generic", "source": "gpu-devices",
                }],
                "backend": "generic",
                "source": "gpu-devices",
                "fallback_from": "nvidia-smi",
                "nvidia_error": nvidia_error,
            }

        return {"ok": False, "error": nvidia_error or "No GPU memory probe available", "gpus": []}

    class KillPidRequest(BaseModel):
        pid: int
        host: str | None = None
        ssh_port: str | None = None
        signal: str = "TERM"  # TERM (graceful) or KILL (force)

    @router.post("/api/cookbook/kill-pid")
    async def kill_pid(request: Request, req: KillPidRequest):
        """Kill a PID that's holding GPU memory.

        Admin-gated. Validates PID is positive int, signal is TERM/KILL, and
        forbids low PIDs (<100) to avoid accidentally signalling init/system
        daemons. Uses `kill -<sig> <pid>` locally or over SSH.
        """
        require_admin(request)
        if req.pid < 100:
            raise HTTPException(400, f"Refusing to signal PID {req.pid} (<100, likely system process)")
        sig = (req.signal or "TERM").upper()
        if sig not in ("TERM", "KILL", "INT"):
            raise HTTPException(400, "signal must be TERM, KILL, or INT")
        host = validate_remote_host(req.host)
        req.ssh_port = validate_ssh_port(req.ssh_port)
        kill_cmd = f"kill -{sig} {req.pid}"
        try:
            if host:
                pf = f"-p {req.ssh_port} " if req.ssh_port and req.ssh_port != "22" else ""
                cmd = f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {pf}{host} '{kill_cmd}'"
                proc = await asyncio.create_subprocess_shell(
                    cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
            elif IS_WINDOWS:
                # No `kill` binary / POSIX signals on Windows. taskkill /F /T tears
                # down the PID and its children. There's no graceful-vs-force
                # distinction, so TERM/KILL/INT all map to the same forced kill.
                # NB: never use os.kill(pid, 0) to probe here — on Windows that
                # routes to TerminateProcess and would kill the process.
                if not pid_alive(req.pid):
                    return {"ok": False, "error": f"PID {req.pid} is not running"}
                await asyncio.to_thread(kill_process_tree, req.pid)
                return {"ok": True, "pid": req.pid, "signal": sig}
            else:
                proc = await asyncio.create_subprocess_exec(
                    "kill", f"-{sig}", str(req.pid),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode != 0:
                err = (stderr.decode("utf-8", errors="replace") or "").strip()[:200]
                return {"ok": False, "error": err or f"kill returned {proc.returncode}"}
            return {"ok": True, "pid": req.pid, "signal": sig}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "kill command timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    # ── Cookbook state persistence (cross-device sync) ──

    @router.get("/api/cookbook/state")
    async def get_cookbook_state(request: Request):
        """Load saved cookbook state (tasks, servers, presets, settings)."""
        require_admin(request)
        if _cookbook_state_path.exists():
            try:
                return _state_for_client(json.loads(_cookbook_state_path.read_text(encoding="utf-8")))
            except Exception:
                return {}
        return {}

    @router.post("/api/cookbook/state")
    async def save_cookbook_state(request: Request):
        """Save cookbook state for cross-device sync.

        Admin-gated because cookbook state is read back into shell-quoting
        contexts when polling tmux session status (see status handler).

        Merge guard: the UI debounces a `_syncToServer` POST every few
        seconds with whatever localStorage has. The agent's tool layer
        writes server-side tasks (e.g. `download_model` registering a
        task). Without a merge, every UI sync wipes the agent's recent
        additions. We preserve any on-disk task that the incoming body
        omits but was added in the last RACE_WINDOW seconds — that's a
        race, not an intentional delete.
        """
        require_admin(request)
        RACE_WINDOW_MS = 60_000
        try:
            from core.atomic_io import atomic_write_json
            data = await request.json()
            if not isinstance(data, dict):
                data = {}
            try:
                if _cookbook_state_path.exists():
                    on_disk = json.loads(_cookbook_state_path.read_text(encoding="utf-8"))
                else:
                    on_disk = {}
            except Exception:
                on_disk = {}
            # Anti-wipe guard for env servers. The UI debounces a
            # sync of whatever is in memory; if it fires before the state has
            # hydrated from GET /state (a load-time race) or during a render
            # glitch, `env.servers` would be empty and silently overwrite the
            # saved servers on disk. Never let an empty/absent incoming
            # env.servers clobber a populated on-disk one — preserve the disk
            # values while still accepting the rest of the incoming env.
            disk_env = on_disk.get("env") if isinstance(on_disk, dict) and isinstance(on_disk.get("env"), dict) else None
            if disk_env:
                inc_env = data.get("env") if isinstance(data.get("env"), dict) else None
                if inc_env is None:
                    data["env"] = disk_env
                    logger.warning("cookbook state POST: incoming body had no env; preserved on-disk env (anti-wipe guard)")
                elif disk_env.get("servers") and not inc_env.get("servers"):
                    inc_env["servers"] = disk_env["servers"]
                    logger.warning("cookbook state POST: incoming env.servers empty; preserved on-disk servers (anti-wipe guard)")

            disk_tasks = on_disk.get("tasks") or [] if isinstance(on_disk, dict) else []
            incoming_tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
            # Anti-poisoning guard: a stale browser tab can keep POSTing a
            # download task as status='done' from before the strict-finish
            # fix landed, undoing any server-side correction. For each
            # incoming "done" download, override to "running" if the last
            # shard pattern says N<total AND no DOWNLOAD_OK/DOWNLOAD_FAILED/
            # /snapshots/ sentinel is in the output.
            import re as _re_dl
            for _it in incoming_tasks:
                if (not isinstance(_it, dict)) or _it.get("type") != "download" or _it.get("status") != "done":
                    continue
                _out = _it.get("output") or ""
                if ("DOWNLOAD_OK" in _out) or ("DOWNLOAD_FAILED" in _out) or ("/snapshots/" in _out):
                    continue
                _shards = _re_dl.findall(r"model-(\d+)-of-(\d+)\.safetensors", _out)
                if _shards:
                    _n, _tot = _shards[-1]
                    if int(_n) < int(_tot):
                        logger.info(f"cookbook state POST: rejecting stale done for {_it.get('sessionId')} "
                                    f"(last shard {_n}/{_tot}, no DOWNLOAD_OK)")
                        _it["status"] = "running"
                else:
                    _completed = _out.count("Download complete")
                    _starts = _out.count("Downloading '")
                    if _starts > _completed:
                        logger.info(f"cookbook state POST: rejecting stale done for {_it.get('sessionId')} "
                                    f"({_completed}/{_starts} files complete, no DOWNLOAD_OK)")
                        _it["status"] = "running"
            incoming_ids = {t.get("sessionId") for t in incoming_tasks if isinstance(t, dict) and t.get("sessionId")}
            import time as _t
            now_ms = int(_t.time() * 1000)
            preserved = []
            for t in disk_tasks:
                if not isinstance(t, dict):
                    continue
                sid = t.get("sessionId")
                if not sid or sid in incoming_ids:
                    continue  # client's version wins
                ts = t.get("ts") or 0
                if isinstance(ts, (int, float)) and (now_ms - ts) <= RACE_WINDOW_MS:
                    preserved.append(t)
            if preserved:
                logger.info(f"cookbook state POST: preserving {len(preserved)} recent task(s) "
                            f"not in incoming body (race guard): "
                            f"{[t.get('sessionId') for t in preserved]}")
                data["tasks"] = incoming_tasks + preserved
            atomic_write_json(str(_cookbook_state_path), _state_for_storage(data, on_disk), indent=2)
            return {"ok": True, "preserved": len(preserved)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @router.get("/api/cookbook/hf-latest")
    async def hf_latest(vram_gb: float = 0, limit: int = 10, pipeline: str = "text-generation", owner: str = Depends(require_user)):
        """Fetch latest HuggingFace models, filtered by what fits in available VRAM.

        vram_gb: total available VRAM in GB. 0 = no filter (return everything).
        limit:   how many models to return (default 10).
        pipeline: HF pipeline_tag filter (text-generation, text-to-image, etc.).
        """
        import re
        import httpx

        # Fetch a larger pool so we have enough to filter from (we drop ~80%)
        pool_size = max(limit * 15, 100)
        url = (
            "https://huggingface.co/api/models"
            f"?sort=trendingScore&direction=-1&limit={pool_size}&filter={pipeline}"
        )
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return {"models": [], "error": f"HF API HTTP {resp.status_code}"}
                raw = resp.json()
        except Exception as e:
            return {"models": [], "error": str(e)}

        # Estimate VRAM from the model id. Looks for patterns like "7B", "70B", "1.5B" etc.
        # Returns approx VRAM in GB at fp16 (params*2). Caller adjusts for quant.
        def _est_vram_fp16(repo_id: str) -> float | None:
            m = re.search(r'[-_/](\d+(?:\.\d+)?)\s*[Bb](?![a-zA-Z])', repo_id)
            if not m:
                return None
            params_b = float(m.group(1))
            return params_b * 2.0  # fp16 baseline

        # Detect quantization from repo_id / tags. Returns a multiplier on fp16 size.
        def _quant_factor(repo_id: str, tags: list) -> float:
            text = (repo_id + " " + " ".join(tags or [])).lower()
            if "fp4" in text or "nf4" in text or "int4" in text or "4bit" in text or "q4" in text or "awq" in text or "gptq" in text:
                return 0.25
            if "int8" in text or "8bit" in text or "q8" in text or "fp8" in text:
                return 0.5
            if "bf16" in text or "fp16" in text:
                return 1.0
            return 1.0  # default fp16

        # Exclude adapters, LoRAs, datasets, GGUF-only repos, and other non-runnable artifacts
        EXCLUDE_TAG_SUBSTRINGS = (
            "lora", "adapter", "peft", "qlora",
            "dataset", "embeddings",
            "merge", "control-lora",
            "diffusion-lora", "stable-diffusion-lora",
            "text-classification", "token-classification",
            "feature-extraction", "sentence-similarity",
        )
        EXCLUDE_NAME_SUBSTRINGS = (
            "lora", "adapter", "peft", "qlora",
            "embedding", "embed-",
            "dataset",
        )

        def _is_excluded(repo_id: str, tags: list) -> bool:
            text = repo_id.lower()
            for s in EXCLUDE_NAME_SUBSTRINGS:
                if s in text:
                    return True
            tag_text = " ".join(t.lower() for t in (tags or []))
            for s in EXCLUDE_TAG_SUBSTRINGS:
                if s in tag_text:
                    return True
            return False

        out = []
        for entry in raw:
            repo_id = entry.get("modelId") or entry.get("id") or ""
            if not repo_id:
                continue
            tags = entry.get("tags") or []
            pipeline_tag = entry.get("pipeline_tag") or ""

            # Hard filter: only the requested pipeline (HF's filter param is loose)
            if pipeline and pipeline_tag and pipeline_tag != pipeline:
                continue
            # Skip adapters, LoRAs, datasets, etc.
            if _is_excluded(repo_id, tags):
                continue

            est_fp16 = _est_vram_fp16(repo_id)
            quant_mult = _quant_factor(repo_id, tags)
            est_vram = (est_fp16 * quant_mult) if est_fp16 else None
            # Add 30% headroom for KV cache, activations, etc.
            needed_vram = (est_vram * 1.3) if est_vram else None

            if vram_gb > 0 and needed_vram is not None and needed_vram > vram_gb:
                continue
            # Unknown-size models (e.g. MiniMax-M2.7, DeepSeek-V4-Flash) have no
            # "NB" in the repo id, so the regex above can't extract their
            # param count. Previously we dropped them entirely, which made
            # brand-new flagship releases silently vanish from this list even
            # on rigs with hundreds of GB of VRAM. Adapters/LoRAs are already
            # filtered by _is_excluded(), so what falls through here is
            # overwhelmingly full models — keep them, just without a size
            # badge (the frontend handles needed_vram_gb=null gracefully).

            out.append({
                "repo_id": repo_id,
                "downloads": entry.get("downloads", 0),
                "likes": entry.get("likes", 0),
                "createdAt": entry.get("createdAt", ""),
                "tags": tags[:5],  # trim
                "pipeline_tag": pipeline_tag,
                "est_vram_gb": round(est_vram, 1) if est_vram else None,
                "needed_vram_gb": round(needed_vram, 1) if needed_vram else None,
            })
            if len(out) >= limit:
                break

        return {"models": out}

    # Rate-limit for the orphan-tmux adoption sweep. 60s interval so SSH
    # work is genuinely sparse even on an actively-polled cookbook page.
    _last_orphan_sweep_ts = [0.0]
    _ORPHAN_SWEEP_MIN_INTERVAL_S = 60.0
    # Concurrency guard so two requests racing don't both spawn a sweep.
    _orphan_sweep_inflight = [False]

    def _maybe_sweep_orphans(tasks: list, state: dict) -> None:
        """Scan each configured cookbook server for `serve-*` tmux sessions
        the cookbook doesn't know about and adopt them into state.tasks.

        Heavy SSH work runs in a background thread via asyncio.to_thread so
        it never blocks the request that triggered it. Was previously
        disabled because the sync implementation pegged uvicorn CPU during
        active cookbook polling — re-enabled now with the work pushed off
        the event loop and a slower (60s) cadence.
        """
        import time as _time
        now = _time.monotonic()
        if _orphan_sweep_inflight[0]:
            return
        if now - _last_orphan_sweep_ts[0] < _ORPHAN_SWEEP_MIN_INTERVAL_S:
            return
        _last_orphan_sweep_ts[0] = now
        _orphan_sweep_inflight[0] = True
        # Snapshot inputs so the worker doesn't race with state mutations.
        try:
            tasks_snap = list(tasks or [])
        except Exception:
            tasks_snap = []
        state_snap = state if isinstance(state, dict) else {}

        # Caller is _cookbook_tasks_status_sync (sync context, no event
        # loop). Use a plain background thread — no asyncio needed.
        import threading
        def _run_sweep() -> None:
            try:
                _sync_sweep_orphans(tasks_snap, state_snap)
            except Exception as _e:
                logger.warning(f"orphan sweep thread failed: {_e!r}")
            finally:
                _orphan_sweep_inflight[0] = False
        try:
            threading.Thread(target=_run_sweep, daemon=True, name="orphan-sweep").start()
        except Exception as _e:
            logger.warning(f"orphan sweep thread spawn failed: {_e!r}")
            _orphan_sweep_inflight[0] = False
        return

    def _sync_sweep_orphans(tasks: list, state: dict) -> None:
        """The actual sync sweep — never call this on the event loop."""
        import subprocess
        env = state.get("env") if isinstance(state, dict) else {}
        servers = env.get("servers") if isinstance(env, dict) else []
        logger.info(f"orphan sweep starting: {len(servers) if isinstance(servers, list) else 0} server(s), known_sids={len([t for t in tasks if isinstance(t, dict) and t.get('sessionId')])}")
        if not isinstance(servers, list):
            return

        known_sids = {
            t.get("sessionId") for t in tasks
            if isinstance(t, dict) and t.get("sessionId")
        }

        adopted_any = False
        for srv in servers:
            if not isinstance(srv, dict):
                continue
            host = (srv.get("host") or "").strip()
            if not host:
                continue  # local-only entry; the /proc scan handles it
            try:
                host = validate_remote_host(host)
            except HTTPException:
                continue
            sport = str(srv.get("port") or "").strip()
            ssh_base = ["ssh", "-o", "ConnectTimeout=4", "-o", "StrictHostKeyChecking=no"]
            if sport and sport != "22":
                try:
                    sport = validate_ssh_port(sport)
                except HTTPException:
                    continue
                if sport != "22":
                    ssh_base.extend(["-p", sport])

            try:
                ls = subprocess.run(
                    ssh_base + [host, "tmux ls 2>/dev/null"],
                    timeout=6, capture_output=True, text=True,
                )
            except Exception:
                continue
            for line in (ls.stdout or "").splitlines():
                sid = line.split(":", 1)[0].strip()
                if not sid or not _SESSION_ID_RE.match(sid):
                    continue
                if sid in known_sids:
                    continue
                # Adopt any session whose pane is currently running a
                # known model-server process (checked below). The earlier
                # prefix gate (serve-/cookbook-) dropped legitimate
                # serves whenever tmux fell back to numeric IDs, leaving
                # them invisible in the Cookbook UI — so the user could
                # neither see nor stop them.
                # Skip zombie / idle-shell sessions. A tmux session left
                # over from a crashed vllm just shows a bash prompt —
                # adopting it would pollute the UI with "running" tasks
                # that aren't actually serving anything. pane_current_command
                # is the foreground process in the pane right now; only
                # real model serves leave a python/vllm/etc. process there.
                try:
                    pc = subprocess.run(
                        ssh_base + [host, "tmux", "list-panes", "-t", sid,
                                    "-F", "#{pane_current_command}"],
                        timeout=4, capture_output=True, text=True,
                    )
                    cur = (pc.stdout or "").strip().splitlines()
                except Exception:
                    cur = []
                LIVE_PROCS = {"python", "python3", "vllm", "llama-server",
                              "llama_cpp_main", "sglang", "lmdeploy",
                              "ollama", "node", "uvicorn"}
                if not any(c in LIVE_PROCS for c in cur):
                    continue
                # Try to recover a plausible repo_id + port from the
                # pane buffer. Cheap heuristic — if we can't, register
                # with placeholder fields; the UI still shows it.
                try:
                    cap = subprocess.run(
                        ssh_base + [host, "tmux", "capture-pane", "-t", sid, "-p", "-S", "-300"],
                        timeout=6, capture_output=True, text=True,
                    )
                    pane = cap.stdout or ""
                except Exception:
                    pane = ""
                import re as _re_orphan
                # vLLM banner: "model   /path/...". Falls back to the
                # raw vllm-serve command if the banner already scrolled.
                m_model = _re_orphan.search(r"model\s+(\S+)", pane)
                model = m_model.group(1) if m_model else ""
                if not model:
                    m_serve = _re_orphan.search(r"vllm\s+serve\s+(\S+)", pane)
                    model = m_serve.group(1) if m_serve else f"adopted:{sid}"
                m_port = _re_orphan.search(r"--port\s+(\d+)", pane)
                port = int(m_port.group(1)) if m_port else 0

                import time as _t2
                tasks.append({
                    "id": sid,
                    "sessionId": sid,
                    "name": model.split("/")[-1] if "/" in model else model,
                    "type": "serve",
                    "status": "running",
                    "output": f"Auto-adopted from orphan tmux session on {host}. "
                              "Open the task to see live output.",
                    "ts": int(_t2.time() * 1000),
                    "payload": {
                        "repo_id": model,
                        "remote_host": host,
                        "_cmd": "(orphan tmux session — original launch cmd unknown)",
                        "port": port,
                    },
                    "remoteHost": host,
                    "sshPort": sport,
                    "platform": "linux",
                    "_serveReady": False,
                    "_endpointAdded": False,
                    "_adoptedExternally": True,
                })
                known_sids.add(sid)
                adopted_any = True
                logger.info(f"auto-adopted orphan tmux session {sid!r} on {host}")

        if adopted_any:
            try:
                from core.atomic_io import atomic_write_json
                state["tasks"] = tasks
                atomic_write_json(_cookbook_state_path, state)
            except Exception as e:
                logger.warning(f"orphan sweep: state write failed: {e}")

    # In-memory cache for the Ollama library scrape. ollama.com is a public
    # site, but it doesn't expose a stable JSON listing — we fetch the HTML
    # search page and regex out the model cards. Cached for 1 h so a busy
    # cookbook view doesn't hammer the site on every render.
    _ollama_library_cache: dict = {"models": [], "fetched_at": 0.0, "error": None}

    _OLLAMA_FALLBACK_LIBRARY = [
        {"name": "qwen2.5", "description": "Qwen2.5 series — strong general/coding model from Alibaba.", "sizes": ["0.5b", "1.5b", "3b", "7b", "14b", "32b", "72b"]},
        {"name": "qwen2.5-coder", "description": "Code-specialized Qwen2.5 family.", "sizes": ["0.5b", "1.5b", "3b", "7b", "14b", "32b"]},
        {"name": "qwen3", "description": "Qwen3 — newer Alibaba family with hybrid reasoning.", "sizes": ["0.6b", "1.7b", "4b", "8b", "14b", "32b"]},
        {"name": "llama3.2", "description": "Meta Llama 3.2 instruct (and tiny / vision variants).", "sizes": ["1b", "3b", "11b", "90b"]},
        {"name": "llama3.1", "description": "Meta Llama 3.1 instruct.", "sizes": ["8b", "70b", "405b"]},
        {"name": "llama3.3", "description": "Meta Llama 3.3 70B instruct.", "sizes": ["70b"]},
        {"name": "gemma3", "description": "Google Gemma 3 — multimodal capable open-weights.", "sizes": ["1b", "4b", "12b", "27b"]},
        {"name": "gemma2", "description": "Google Gemma 2 instruct.", "sizes": ["2b", "9b", "27b"]},
        {"name": "mistral", "description": "Mistral 7B instruct — small, fast generalist.", "sizes": ["7b"]},
        {"name": "mistral-nemo", "description": "Mistral NeMo 12B instruct.", "sizes": ["12b"]},
        {"name": "mistral-small", "description": "Mistral Small 22B / 24B instruct.", "sizes": ["22b", "24b"]},
        {"name": "mixtral", "description": "Mistral MoE 8x7B / 8x22B.", "sizes": ["8x7b", "8x22b"]},
        {"name": "phi3", "description": "Microsoft Phi-3 small / medium.", "sizes": ["mini", "medium"]},
        {"name": "phi4", "description": "Microsoft Phi-4 14B.", "sizes": ["14b"]},
        {"name": "deepseek-r1", "description": "DeepSeek R1 reasoning model (distilled variants).", "sizes": ["1.5b", "7b", "8b", "14b", "32b", "70b"]},
        {"name": "deepseek-v3", "description": "DeepSeek V3 MoE 671B (huge — needs serious VRAM).", "sizes": ["671b"]},
        {"name": "codellama", "description": "Meta Code Llama instruct family.", "sizes": ["7b", "13b", "34b", "70b"]},
        {"name": "starcoder2", "description": "BigCode StarCoder2 — code completion.", "sizes": ["3b", "7b", "15b"]},
        {"name": "deepseek-coder-v2", "description": "DeepSeek Coder V2 — code MoE.", "sizes": ["16b", "236b"]},
        {"name": "nomic-embed-text", "description": "Embedding model — text vector encoder.", "sizes": ["latest"]},
        {"name": "mxbai-embed-large", "description": "Embedding model — Mixedbread large.", "sizes": ["latest"]},
        {"name": "llava", "description": "LLaVA multimodal vision-language model.", "sizes": ["7b", "13b", "34b"]},
        {"name": "minicpm-v", "description": "MiniCPM-V multimodal.", "sizes": ["8b"]},
        {"name": "command-r", "description": "Cohere Command R — RAG-oriented.", "sizes": ["35b"]},
        {"name": "command-r-plus", "description": "Cohere Command R+ — larger RAG model.", "sizes": ["104b"]},
        {"name": "qwq", "description": "Qwen QwQ reasoning preview.", "sizes": ["32b"]},
        {"name": "smollm2", "description": "HuggingFaceTB SmolLM2 — tiny capable models.", "sizes": ["135m", "360m", "1.7b"]},
        {"name": "granite3.1-dense", "description": "IBM Granite 3.1 dense instruct.", "sizes": ["2b", "8b"]},
        {"name": "nemotron", "description": "NVIDIA Nemotron 70B.", "sizes": ["70b"]},
        {"name": "olmo2", "description": "AI2 OLMo 2 open-weights.", "sizes": ["7b", "13b"]},
    ]

    @router.get("/api/cookbook/ollama/library")
    async def ollama_library(refresh: int = 0, request: Request = None, owner: str = Depends(require_user)):
        """List popular Ollama library models for the Browse picker.

        Tries a 1-hour-cached fetch of ollama.com/library, falls back to a
        curated hard-coded list so the picker always renders something."""
        import time as _time
        import httpx as _httpx
        TTL = 3600.0
        now = _time.time()
        if refresh or (now - _ollama_library_cache["fetched_at"]) > TTL or not _ollama_library_cache["models"]:
            models: list[dict] = []
            err = None
            try:
                async with _httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                    resp = await client.get(
                        "https://ollama.com/search?sort=popular",
                        headers={"User-Agent": "odysseus-cookbook/1.0"},
                    )
                if resp.status_code == 200:
                    html = resp.text
                    # ollama.com renders each model card as a single anchor:
                    #   <a href="/library/<name>" class="group w-full"> … </a>
                    # The description + sizes live inside that anchor. Pull
                    # the whole block then extract pieces individually.
                    block_re = re.compile(
                        r'<a[^>]*href="/library/([A-Za-z0-9._-]+)"[^>]*>(.*?)</a>',
                        re.DOTALL,
                    )
                    desc_re = re.compile(r'<p[^>]*>([^<]{4,400})</p>', re.DOTALL)
                    # Size tags on ollama.com cards look like "0.5b", "14b",
                    # "8x7b", "27b". Pulled from short <span>-wrapped chips.
                    size_re = re.compile(r'>\s*(\d+(?:\.\d+)?(?:x\d+)?[bBmM])\s*<')
                    seen: set[str] = set()
                    for bm in block_re.finditer(html):
                        name = bm.group(1).strip()
                        if name in seen:
                            continue
                        seen.add(name)
                        body = bm.group(2)
                        dm = desc_re.search(body)
                        desc = (dm.group(1).strip() if dm else "").replace("\n", " ")
                        sizes_raw = size_re.findall(body)
                        # Dedup sizes preserving order
                        sizes: list[str] = []
                        for s in sizes_raw:
                            s_low = s.lower()
                            if s_low not in sizes:
                                sizes.append(s_low)
                        models.append({"name": name, "description": desc, "sizes": sizes})
                        if len(models) >= 80:
                            break
                else:
                    err = f"HTTP {resp.status_code}"
            except Exception as e:
                err = str(e)[:160]
            # Merge curated fallback so classics (qwen2.5, llama3, deepseek-r1,
            # …) stay reachable even when ollama.com's front page is dominated
            # by brand-new releases the user might not be looking for.
            live_names = {m["name"] for m in models}
            for fb in _OLLAMA_FALLBACK_LIBRARY:
                if fb["name"] not in live_names:
                    models.append(fb)
            if not models:
                models = list(_OLLAMA_FALLBACK_LIBRARY)
                if err is None:
                    err = "parsed 0 results — using fallback list"
            _ollama_library_cache["models"] = models
            _ollama_library_cache["fetched_at"] = now
            _ollama_library_cache["error"] = err
        return {
            "models": _ollama_library_cache["models"],
            "fetched_at": _ollama_library_cache["fetched_at"],
            "error": _ollama_library_cache["error"],
        }

    # ── vLLM recipe scraper ─────────────────────────────────────────────
    # Fetches the official YAML recipe for a model from vllm-project/recipes
    # and normalizes it into a small JSON the frontend can consume. Cached
    # per-repo so the GitHub raw endpoint isn't hammered.
    _vllm_recipe_cache: dict[str, tuple[float, dict | None]] = {}
    # Manifest of all <org>/<model> ids that have a recipe in the upstream
    # repo. Cheap to fetch (one Git Tree API call), so we cache the whole
    # set for ~12h. Per-row "does this model have a recipe?" lookups hit
    # this set instead of doing 912 individual recipe fetches.
    _vllm_recipe_manifest: dict = {"fetched_at": 0.0, "models": set(), "error": ""}

    @router.get("/api/cookbook/vllm-recipe-manifest")
    async def vllm_recipe_manifest(refresh: int = 0):
        """Return the set of <org>/<model> ids known to have a vLLM recipe.
        One GitHub Tree API call, 12h cache. The frontend uses this to badge
        rows in the model list before the user expands them."""
        import time as _time
        import httpx as _httpx
        TTL = 12 * 3600.0
        now = _time.time()
        if (
            refresh
            or (now - _vllm_recipe_manifest["fetched_at"]) > TTL
            or not _vllm_recipe_manifest["models"]
        ):
            url = (
                "https://api.github.com/repos/vllm-project/recipes/"
                "git/trees/main?recursive=1"
            )
            def _fetch_sync() -> tuple[int, dict | None, str]:
                try:
                    headers = {"Accept": "application/vnd.github+json"}
                    with _httpx.Client(timeout=10.0, follow_redirects=True) as client:
                        r = client.get(url, headers=headers)
                        if r.status_code != 200:
                            return r.status_code, None, r.text[:200]
                        return 200, r.json(), ""
                except Exception as e:
                    return 0, None, f"fetch error: {e}"
            status, data, err = await asyncio.to_thread(_fetch_sync)
            if status == 200 and isinstance(data, dict):
                models: set[str] = set()
                for entry in data.get("tree") or []:
                    path = (entry or {}).get("path") or ""
                    if not path.startswith("models/") or not path.endswith(".yaml"):
                        continue
                    # path = "models/<org>/<model>.yaml" → "<org>/<model>"
                    body = path[len("models/"):-len(".yaml")]
                    if "/" in body:
                        models.add(body)
                _vllm_recipe_manifest["models"] = models
                _vllm_recipe_manifest["fetched_at"] = now
                _vllm_recipe_manifest["error"] = ""
            else:
                _vllm_recipe_manifest["error"] = (
                    f"HTTP {status}: {err}" if status else err
                )
                # Don't clobber a stale-but-usable list on transient failures.
                if not _vllm_recipe_manifest["models"]:
                    return {
                        "models": [],
                        "count": 0,
                        "error": _vllm_recipe_manifest["error"],
                    }
        return {
            "models": sorted(_vllm_recipe_manifest["models"]),
            "count": len(_vllm_recipe_manifest["models"]),
            "fetched_at": _vllm_recipe_manifest["fetched_at"],
            "error": _vllm_recipe_manifest["error"],
        }

    @router.get("/api/cookbook/vllm-recipe")
    async def vllm_recipe(repo: str, refresh: int = 0):
        """Return the vLLM official recipe for a HuggingFace repo, if one
        exists at vllm-project/recipes. `repo` is the full HF id like
        'MiniMaxAI/MiniMax-M2'. Cached 6h."""
        import time as _time
        import httpx as _httpx
        import yaml as _yaml

        TTL = 6 * 3600.0
        now = _time.time()
        repo = (repo or "").strip().strip("/")
        if "/" not in repo:
            return {"exists": False, "error": "repo must be <org>/<model>"}

        cached = _vllm_recipe_cache.get(repo)
        if cached and not refresh and (now - cached[0]) < TTL:
            return cached[1] or {"exists": False, "cached": True}

        url = (
            f"https://raw.githubusercontent.com/vllm-project/recipes/"
            f"main/models/{repo}.yaml"
        )

        def _fetch_sync() -> tuple[int, str]:
            try:
                with _httpx.Client(timeout=8.0, follow_redirects=True) as client:
                    r = client.get(url)
                    return r.status_code, r.text
            except Exception as e:
                return 0, f"fetch error: {e}"

        status, text = await asyncio.to_thread(_fetch_sync)
        if status == 404:
            _vllm_recipe_cache[repo] = (now, {"exists": False})
            return {"exists": False}
        if status != 200:
            return {"exists": False, "error": f"HTTP {status}", "transient": True}

        try:
            doc = _yaml.safe_load(text) or {}
        except Exception as e:
            return {"exists": False, "error": f"yaml parse: {e}"}

        meta = doc.get("meta") or {}
        model = doc.get("model") or {}
        features = doc.get("features") or {}
        deps = doc.get("dependencies") or []
        variants = doc.get("variants") or {}
        hw_overrides = doc.get("hardware_overrides") or {}
        strat_overrides = doc.get("strategy_overrides") or {}

        # Tool-call + reasoning parsers, as flat arg arrays, so the frontend
        # can drop them straight into the launch command.
        tool_calling = features.get("tool_calling") or {}
        reasoning = features.get("reasoning") or {}

        normalized = {
            "exists": True,
            "source_url": url,
            "title": meta.get("title") or "",
            "provider": meta.get("provider") or "",
            "description": meta.get("description") or "",
            "date_updated": str(meta.get("date_updated") or ""),
            "hardware_support": meta.get("hardware") or {},
            "model_id": model.get("model_id") or repo,
            "min_vllm_version": model.get("min_vllm_version") or "",
            "architecture": model.get("architecture") or "",
            "parameter_count": model.get("parameter_count") or "",
            "active_parameters": model.get("active_parameters") or "",
            "context_length": model.get("context_length") or 0,
            "base_args": list(model.get("base_args") or []),
            "base_env": dict(model.get("base_env") or {}),
            "tool_calling": {
                "description": tool_calling.get("description") or "",
                "args": list(tool_calling.get("args") or []),
            } if tool_calling else None,
            "reasoning": {
                "description": reasoning.get("description") or "",
                "args": list(reasoning.get("args") or []),
            } if reasoning else None,
            "dependencies": [
                {
                    "note": (d.get("note") or "").strip(),
                    "command": (d.get("command") or "").strip(),
                    "optional": bool(d.get("optional", False)),
                }
                for d in deps if isinstance(d, dict)
            ],
            "variants": {
                k: {
                    "model_id": v.get("model_id") or model.get("model_id") or repo,
                    "precision": v.get("precision") or "",
                    "vram_minimum_gb": v.get("vram_minimum_gb") or 0,
                    "description": v.get("description") or "",
                    "extra_args": list(v.get("extra_args") or []),
                    "extra_env": dict(v.get("extra_env") or {}),
                }
                for k, v in variants.items() if isinstance(v, dict)
            },
            "hardware_overrides": {
                hw: {
                    "extra_args": list((ov or {}).get("extra_args") or []),
                    "extra_env": dict((ov or {}).get("extra_env") or {}),
                }
                for hw, ov in hw_overrides.items() if isinstance(ov, dict)
            },
            "strategy_overrides": {
                strat: dict(ov or {})
                for strat, ov in strat_overrides.items() if isinstance(ov, dict)
            },
            "compatible_strategies": list(doc.get("compatible_strategies") or []),
        }
        _vllm_recipe_cache[repo] = (now, normalized)
        return normalized

    @router.get("/api/cookbook/tasks/status")
    async def cookbook_tasks_status(request: Request):
        """Check status of all active cookbook tmux sessions.

        Critical: every subprocess.run inside this handler is a sync blocking
        call that — when this was a plain async def — froze the entire server
        event loop. Now the whole body runs in a worker thread via
        asyncio.to_thread so other requests stay responsive."""
        require_admin(request)
        return await asyncio.to_thread(_cookbook_tasks_status_sync)

    def _cookbook_tasks_status_sync():
        import subprocess

        def _download_cache_complete(repo_id: str, remote_host: str = "", ssh_port: str = "", cache_root: str = "") -> bool:
            """Best-effort check for a completed HF cache entry.

            tmux output can stop at a stale progress line if the pane/session
            disappears before Cookbook captures the final DOWNLOAD_OK marker.
            In that case, trust the cache shape: a snapshot directory with files
            and no *.incomplete blobs means HuggingFace finished materializing the
            model. cache_root is the task's custom download dir — the runner
            pointed HF_HOME there, so the cache lives under <cache_root>/hub,
            not wherever this probe's environment says.
            """
            if not repo_id or "/" not in repo_id:
                return False
            cmd = ["python3", "-c", HF_CACHE_COMPLETE_PROBE, repo_id, cache_root or ""]
            try:
                if remote_host:
                    ssh_base = ["ssh"]
                    if ssh_port and ssh_port != "22":
                        ssh_base.extend(["-p", str(ssh_port)])
                    shell_cmd = " ".join(shlex.quote(x) for x in cmd)
                    proc = subprocess.run(ssh_base + [remote_host, shell_cmd], timeout=12, capture_output=True)
                else:
                    proc = subprocess.run(cmd, timeout=12, capture_output=True)
                return proc.returncode == 0
            except Exception:
                return False

        def _download_cache_incomplete(repo_id: str, remote_host: str = "", ssh_port: str = "", cache_root: str = "") -> bool:
            """Best-effort check for resumable HF partial blobs.

            A lost SSH/tmux session can leave a real download still incomplete.
            Treat any *.incomplete blob as stronger evidence than stale
            "100%" lines in the captured pane output.
            """
            if not repo_id or "/" not in repo_id:
                return False
            cmd = ["python3", "-c", HF_CACHE_INCOMPLETE_PROBE, repo_id, cache_root or ""]
            try:
                if remote_host:
                    ssh_base = ["ssh"]
                    if ssh_port and ssh_port != "22":
                        ssh_base.extend(["-p", str(ssh_port)])
                    shell_cmd = " ".join(shlex.quote(x) for x in cmd)
                    proc = subprocess.run(ssh_base + [remote_host, shell_cmd], timeout=12, capture_output=True)
                else:
                    proc = subprocess.run(cmd, timeout=12, capture_output=True)
                return proc.returncode == 0
            except Exception:
                return False

        # Load saved tasks from cookbook state
        tasks = []
        state = {}
        if _cookbook_state_path.exists():
            try:
                state = json.loads(_cookbook_state_path.read_text(encoding="utf-8"))
                saved_tasks = state.get("tasks", [])
                if isinstance(saved_tasks, list):
                    tasks = saved_tasks
                elif isinstance(saved_tasks, dict):
                    tasks = list(saved_tasks.values())
            except Exception:
                pass

        # Orphan-tmux auto-adoption sweep. When the agent (or anyone)
        # SSH-launches a `serve-*` tmux session — usually because
        # serve_model rejected `source ... && vllm ...` or because of a
        # manual relaunch via tmux send-keys — that session is invisible
        # to the cookbook UI even though it's a live model server. The
        # sweep finds those orphans on each configured remote host and
        # writes them into state.tasks with _adoptedExternally=True, so
        # they show up in the UI on the next poll without anyone having
        # to remember to call adopt_served_model. Rate-limited via the
        # module-level _last_orphan_sweep so we don't SSH every 3s.
        try:
            _maybe_sweep_orphans(tasks, state)
        except Exception as _sweep_e:
            logger.warning(f"orphan sweep failed (non-fatal): {_sweep_e!r}")

        results = []
        for task in tasks:
            session_id = task.get("sessionId", "")
            if not session_id:
                continue
            remote = task.get("remoteHost", "")
            task_type = task.get("type", "download")  # "download" or "serve"
            # Field name varies depending on whether the task was added
            # via the download flow (`repoId`), the serve flow (`modelId`),
            # or the UI-side serve preset (which uses `name` + `payload.repo_id`).
            _payload = task.get("payload") or {}
            model = (
                task.get("modelId")
                or task.get("repoId")
                or task.get("name")
                or _payload.get("repo_id")
                or _payload.get("modelId")
                or ""
            )
            task_platform = task.get("platform", "")

            # Check if session is alive + capture output
            _tport = task.get("sshPort", "")
            # Defense-in-depth: cookbook state is admin-writable but the values
            # land in shell-interpolated commands below. Reject anything that
            # isn't a benign session-id / hostname / port.
            if not _SESSION_ID_RE.match(session_id):
                logger.warning(f"Skipping task with unsafe session_id: {session_id!r}")
                continue
            if remote:
                try:
                    remote = validate_remote_host(remote)
                except HTTPException:
                    logger.warning(f"Skipping task with unsafe remoteHost: {remote!r}")
                    continue
            if _tport:
                try:
                    _tport = validate_ssh_port(str(_tport))
                except HTTPException:
                    logger.warning(f"Skipping task with unsafe sshPort: {_tport!r}")
                    continue
            if task_platform == "windows" and remote:
                # Windows: check PID file + Get-Process, read log tail
                sd = "$env:TEMP\\odysseus-sessions"
                ssh_base = ["ssh"]
                if _tport and _tport != "22":
                    ssh_base.extend(["-p", str(_tport)])
                check_cmd = ssh_base + [
                    remote,
                    "powershell",
                    "-Command",
                    f"$pid = Get-Content \"{sd}\\{session_id}.pid\" -ErrorAction SilentlyContinue; "
                    "if ($pid) {{ Get-Process -Id $pid -ErrorAction SilentlyContinue | Out-Null; if ($?) {{ exit 0 }} else {{ exit 1 }} }} else {{ exit 1 }}"
                ]
                capture_cmd = ssh_base + [
                    remote,
                    "powershell",
                    "-Command",
                    f"Get-Content \"{sd}\\{session_id}.log\" -Tail 10 -ErrorAction SilentlyContinue",
                ]
            elif remote:
                ssh_base = ["ssh"]
                if _tport and _tport != "22":
                    ssh_base.extend(["-p", str(_tport)])
                check_cmd = ssh_base + [remote, "tmux", "has-session", "-t", session_id]
                # Capture 500 lines (was 50) so a Python traceback survives
                # the post-crash neofetch banner + bash prompt that otherwise
                # fills the visible tail. Without this, output_tail ends up
                # as just "Locale: C / Ubuntu_Odysseus ❯" and the agent
                # can't diagnose the actual error.
                capture_cmd = ssh_base + [remote, "tmux", "capture-pane", "-t", session_id, "-p", "-S", "-500"]
            elif IS_WINDOWS:
                # LOCAL Windows task: launched as a detached process (no tmux).
                # Liveness comes from the <session>.pid file, output from the
                # <session>.log file the wrapper redirects into. No subprocess.
                check_cmd = None
                capture_cmd = None
            else:
                check_cmd = ["tmux", "has-session", "-t", session_id]
                capture_cmd = ["tmux", "capture-pane", "-t", session_id, "-p", "-S", "-500"]

            local_win_task = (not remote) and IS_WINDOWS

            progress_text = ""
            full_snapshot = ""

            if local_win_task:
                # File-based liveness + output for the detached-process model.
                pid_path = TMUX_LOG_DIR / f"{session_id}.pid"
                log_path = TMUX_LOG_DIR / f"{session_id}.log"
                task_pid = None
                try:
                    task_pid = int(pid_path.read_text(encoding="utf-8").strip())
                except Exception:
                    task_pid = None
                is_alive = pid_alive(task_pid)
                try:
                    if log_path.exists():
                        full_snapshot = log_path.read_text(
                            encoding="utf-8", errors="replace"
                        ).strip()[-12000:]
                        lines = [l.strip() for l in full_snapshot.split('\n') if l.strip()]
                        downloading_lines = [l for l in lines if l.startswith("Downloading")]
                        if downloading_lines:
                            progress_text = downloading_lines[-1]
                        elif lines:
                            progress_text = lines[-1]
                except Exception:
                    pass
            else:
                # Skip the live SSH check entirely for tasks already in a
                # terminal state — they won't change, and 10s timeouts
                # stacked per task were the dominant cost of this whole
                # status endpoint (3+ minute stalls with ~8 accumulated
                # stopped tasks). The agent's `list_served_models` call
                # was blocking the chat stream every time.
                _task_status = (task.get("status") or "").lower()
                if _task_status in {"stopped", "done", "completed",
                                    "crashed", "error", "failed",
                                    "ended", "killed"}:
                    is_alive = False
                    # Keep the persisted output_tail for the UI — it's
                    # what the agent uses to diagnose past failures.
                    full_snapshot = (task.get("output") or "")[-12000:]
                else:
                    try:
                        alive = subprocess.run(check_cmd, timeout=4, capture_output=True)
                        is_alive = alive.returncode == 0
                    except Exception:
                        is_alive = False

                    # Capture last lines for progress. Prefer the "Downloading" line
                    # (real aggregate bytes) over "Fetching N files" (whole-file count that
                    # lags with hf_transfer). Falls back to the true last line otherwise.
                    if is_alive:
                        try:
                            cap = subprocess.run(capture_cmd, timeout=4, capture_output=True, text=True)
                            if cap.returncode == 0:
                                full_snapshot = cap.stdout.strip()
                                lines = [l.strip() for l in full_snapshot.split('\n') if l.strip()]
                                downloading_lines = [l for l in lines if l.startswith("Downloading")]
                                if downloading_lines:
                                    progress_text = downloading_lines[-1]
                                elif lines:
                                    progress_text = lines[-1]
                        except Exception:
                            pass

            # Determine status. For the local-Windows detached model the log file
            # persists after the process exits, so a finished download still has a
            # snapshot to classify (DOWNLOAD_OK / exit marker) — evaluate it even
            # when the PID is gone instead of blindly reporting "stopped".
            download_zero_files = False
            exit_code = None
            status = "unknown"
            download_has_ok = task_type == "download" and "DOWNLOAD_OK" in full_snapshot
            download_has_failed = task_type == "download" and "DOWNLOAD_FAILED" in full_snapshot
            download_has_incomplete_evidence = (
                task_type == "download"
                and (
                    ".incomplete" in full_snapshot
                    or bool(re.search(r'model-\d+-of-\d+\.[A-Za-z0-9_.-]+:\s+(?:[0-9]|[1-8][0-9])%', full_snapshot))
                    or _download_cache_incomplete(_payload.get("repo_id") or model, remote, str(_tport or ""), _payload.get("local_dir") or "")
                )
            )
            if is_alive or (local_win_task and full_snapshot):
                lower = full_snapshot.lower()
                exit_match = re.search(r"=== process exited with code\s+(-?\d+)", full_snapshot, re.I)
                has_exit = exit_match is not None
                exit_code = int(exit_match.group(1)) if exit_match else None
                has_error = "error" in lower or "failed" in lower or "traceback" in lower
                if has_exit and task_type == "serve":
                    # Serve tasks that exit are always errors — they should run indefinitely
                    status = "error"
                elif has_exit and task_type == "download":
                    # Dependency installs are tracked as download tasks but only
                    # emit the generic runner exit marker, not HF download markers.
                    if download_has_incomplete_evidence and not download_has_ok:
                        status = "running" if is_alive else "stopped"
                    else:
                        status = "completed" if exit_code == 0 else "error"
                elif has_exit and "unrecognized arguments" in lower:
                    status = "error"
                elif has_error and not ("application startup complete" in lower):
                    status = "error"
                elif task_type == "download" and download_has_ok:
                    if re.search(r"Fetching\s+0\s+files", full_snapshot, re.IGNORECASE):
                        status = "error"
                        download_zero_files = True
                    else:
                        status = "completed"
                elif task_type == "download" and download_has_failed:
                    status = "error"
                elif task_type == "download" and download_has_incomplete_evidence:
                    status = "running" if is_alive else "stopped"
                elif "application startup complete" in lower:
                    status = "ready"
                elif not is_alive:
                    # local-Windows: process gone, log has no success/ready marker.
                    status = "stopped"
                else:
                    status = "running"
            else:
                # Session is dead — check if it completed or crashed. The
                # runner markers in the retained output are conclusive
                # (DOWNLOAD_OK only prints after exit 0), so check them before
                # the cache probe, which can't see ollama pulls at all.
                marker = classify_dead_download(full_snapshot) if task_type == "download" else None
                if marker is not None:
                    status, download_zero_files = marker
                    if status == "completed" and not progress_text:
                        progress_text = "Download complete"
                elif (
                    task_type == "download"
                    and not download_has_incomplete_evidence
                    and _download_cache_complete(_payload.get("repo_id") or model, remote, str(_tport or ""), _payload.get("local_dir") or "")
                ):
                    status = "completed"
                    if not progress_text:
                        progress_text = "Download complete"
                    if not full_snapshot:
                        full_snapshot = "DOWNLOAD_OK"
                else:
                    status = "stopped"

            # Parse structured phase info — single source of truth for the UI
            phase_info = _parse_serve_phase(full_snapshot, task_type) if (task_type == "serve" and full_snapshot) else {}
            if phase_info.get("status") == "ready":
                status = "ready"
            serve_phase = phase_info.get("phase", "")
            diagnosis = _diagnose_serve_output(full_snapshot) if task_type == "serve" and full_snapshot else None
            if diagnosis and status in {"running", "unknown", "stopped"} and phase_info.get("status") != "ready":
                status = "error"
            if download_zero_files:
                diagnosis = {"message": "No matching files were downloaded. The model repo or filename/quant pattern may be wrong (for example a ':Q4_K_M' tag that does not exist in the repo). Check the repo and the include/quant pattern."}
            output_tail = error_aware_output_tail(full_snapshot, status)

            results.append({
                "session_id": session_id,
                "type": task_type,
                "model": model.split("/")[-1] if "/" in model else model,
                "status": status,
                "progress": serve_phase if task_type == "serve" else progress_text[:120],
                "phase": serve_phase,
                "diagnosis": diagnosis,
                "output_tail": output_tail,
                "exit_code": exit_code,
                "cmd": _payload.get("_cmd") or "",
                "tps": phase_info.get("tps"),
                "reqs": phase_info.get("reqs"),
                "pct": phase_info.get("pct"),
                "remote": remote or "local",
            })

        return {"tasks": results}
