# routes/shell_dependency_helpers.py
import os
import re
from collections import namedtuple
from pathlib import Path


_SSH_PORT_RE = re.compile(r"^\d{1,5}$")
_SAFE_VENV_RE = re.compile(r"^[A-Za-z0-9_./~-]+$")

DOCKER_IN_CONTAINER_HINT = (
    "Not available inside the Odysseus container by design. The image ships no "
    "docker CLI and no host socket is mounted. Run Docker-backed launches on a "
    "remote server, where docker is checked over SSH. Mounting /var/run/docker.sock "
    "into the container would grant it host-root access, so only do that if you "
    "accept that risk."
)

DockerRowStatus = namedtuple("DockerRowStatus", ["applicable", "install_hint"])
PackageUpdateStatus = namedtuple("PackageUpdateStatus", ["available", "note"])


def _ssh_base_argv(host: str, ssh_port: str | None) -> list[str]:
    """Build an ssh argv prefix for remote probes without local-shell parsing."""
    if not host or not str(host).strip() or str(host).lstrip().startswith("-"):
        raise ValueError("invalid ssh host")
    argv = ["ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no"]
    if ssh_port and str(ssh_port).strip() not in ("", "22"):
        port = str(ssh_port).strip()
        if not _SSH_PORT_RE.match(port) or not (1 <= int(port) <= 65535):
            raise ValueError("invalid ssh port")
        argv += ["-p", port]
    argv.append(str(host).strip())
    return argv


def _venv_activate_prefix(venv: str | None) -> str:
    """Return a remote activation prefix while preserving shell expansion of ~."""
    if not venv:
        return ""
    if not _SAFE_VENV_RE.match(venv):
        raise ValueError("invalid venv path")
    act = venv if venv.endswith("/bin/activate") else venv.rstrip("/") + "/bin/activate"
    return f". {act} && "


def _running_in_container(dockerenv_path="/.dockerenv", cgroup_path="/proc/1/cgroup"):
    if os.path.exists(dockerenv_path):
        return True
    try:
        with open(cgroup_path, "r", encoding="utf-8") as fh:
            contents = fh.read()
    except OSError:
        return False
    return any(token in contents for token in ("docker", "containerd", "kubepods"))


def _docker_row_status(*, on_remote, in_container, installed, default_hint):
    local_docker_unavailable = not on_remote and in_container and not installed
    if local_docker_unavailable:
        return DockerRowStatus(applicable=False, install_hint=DOCKER_IN_CONTAINER_HINT)
    return DockerRowStatus(applicable=True, install_hint=default_hint)


def _pip_dist_name(pkg: dict) -> str:
    """Distribution name for importlib.metadata lookups."""
    pip = (pkg.get("pip") or "").strip()
    if pip:
        base = re.split(r"[\[<>=!~;\s]", pip, maxsplit=1)[0].strip()
        if base:
            return base
    return (pkg.get("name") or "").replace("_", "-")


def _package_installed_from_probe(name: str, probe: dict) -> bool:
    """Return whether an optional dependency is usable by Cookbook."""
    binaries = probe.get("binaries") if isinstance(probe.get("binaries"), dict) else {}
    dists = probe.get("dists") if isinstance(probe.get("dists"), dict) else {}
    modules = probe.get("modules") if isinstance(probe.get("modules"), dict) else {}

    if name == "vllm":
        return bool(binaries.get("vllm"))
    if name == "llama_cpp":
        return bool(binaries.get("llama-server") or dists.get("llama-cpp-python"))
    if name == "sglang":
        return bool(dists.get("sglang") or modules.get("sglang", {}).get("real_module"))
    if name == "diffusers":
        return bool(
            (dists.get("diffusers") or modules.get("diffusers", {}).get("real_module"))
            and (dists.get("torch") or modules.get("torch", {}).get("real_module"))
        )
    if name == "hf_transfer":
        return bool(
            dists.get("hf-transfer")
            or modules.get("hf_transfer", {}).get("real_module")
        )
    return bool(dists.get(name) or modules.get(name, {}).get("real_module"))


def _package_status_note(name: str, probe: dict) -> str:
    binaries = probe.get("binaries") if isinstance(probe.get("binaries"), dict) else {}
    modules = probe.get("modules") if isinstance(probe.get("modules"), dict) else {}
    dists = probe.get("dists") if isinstance(probe.get("dists"), dict) else {}
    module = modules.get(name) if isinstance(modules.get(name), dict) else {}
    locations = module.get("locations") or []
    if name == "vllm":
        if binaries.get("vllm"):
            parts = [f"vLLM CLI: {binaries['vllm']}"]
            if dists.get("vllm"):
                parts.append(f"python package: vllm {dists['vllm']}")
            return "; ".join(parts)
        if module.get("found") and not dists.get("vllm"):
            loc = locations[0] if locations else module.get("origin") or "unknown path"
            return f"Python sees a vllm namespace at {loc}, but no vLLM CLI is on PATH."
        return "vLLM CLI not found on PATH."
    if name == "llama_cpp":
        parts = []
        if binaries.get("llama-server"):
            parts.append(f"native llama-server: {binaries['llama-server']}")
        if dists.get("llama-cpp-python"):
            parts.append(
                f"python package: llama-cpp-python {dists['llama-cpp-python']}"
            )
        return (
            "; ".join(parts)
            if parts
            else "No native llama-server or llama-cpp-python server package found."
        )
    if name == "diffusers":
        if _package_installed_from_probe(name, probe):
            return f"diffusers {dists.get('diffusers', 'available')} with torch {dists.get('torch', 'available')}"
        return "Diffusers serving needs both diffusers and torch."
    if name in dists:
        return f"{name} {dists[name]}"
    return ""


def _package_pip_update_status(
    pkg: dict, probe: dict | None = None
) -> PackageUpdateStatus:
    """Return whether the Dependencies UI should offer a generic pip update."""
    if pkg.get("name") == "APFEL":
        return PackageUpdateStatus(
            False,
            "",  # Note is empty because IT DOES allow for updates outside of PIP.
        )

    if pkg.get("kind") == "system" or not pkg.get("pip"):
        return PackageUpdateStatus(
            False, "Update this system dependency outside Odysseus."
        )

    name = pkg.get("name")
    binaries = (
        probe.get("binaries")
        if isinstance(probe, dict) and isinstance(probe.get("binaries"), dict)
        else {}
    )
    dists = (
        probe.get("dists")
        if isinstance(probe, dict) and isinstance(probe.get("dists"), dict)
        else {}
    )

    if name == "llama_cpp" and binaries.get("llama-server"):
        return PackageUpdateStatus(
            False,
            "Using native llama-server on PATH; update it with its package manager or source checkout.",
        )
    if name == "vllm" and binaries.get("vllm") and not dists.get("vllm"):
        return PackageUpdateStatus(
            False,
            "Using a vLLM CLI on PATH without Python package metadata; update it outside Odysseus.",
        )

    return PackageUpdateStatus(
        True, "Update uses pip in the selected Python environment."
    )


def _prepend_user_install_bins_to_path() -> None:
    """Make pip --user console scripts visible to dependency probes."""
    try:
        import site

        candidates = [os.path.join(site.USER_BASE, "bin")]
    except Exception:
        candidates = []
    home = os.environ.get("HOME") or str(Path.home())
    candidates.append(os.path.join(home, ".local", "bin"))

    parts = (
        os.environ.get("PATH", "").split(os.pathsep) if os.environ.get("PATH") else []
    )
    changed = False
    for path in reversed([p for p in candidates if p]):
        if path not in parts:
            parts.insert(0, path)
            changed = True
    if changed:
        os.environ["PATH"] = os.pathsep.join(parts)


def _package_probe_script(names: list[str]) -> str:
    names_lit = ",".join(repr(n) for n in names)
    return f"""
import importlib.util
import importlib.metadata as md
import json
import os
import shutil
import site

names=[{names_lit}]
dist_names={{
    'vllm':['vllm'],
    'llama_cpp':['llama-cpp-python'],
    'sglang':['sglang'],
    'diffusers':['diffusers','torch'],
    'hf_transfer':['hf-transfer','hf_transfer'],
}}
bin_names={{
    'vllm':['vllm'],
    'llama_cpp':['llama-server'],
}}

def add_user_install_bins_to_path():
    candidates = []
    try:
        candidates.append(os.path.join(site.USER_BASE, 'bin'))
    except Exception:
        pass
    candidates.append(os.path.expanduser('~/.local/bin'))
    parts = os.environ.get('PATH', '').split(os.pathsep) if os.environ.get('PATH') else []
    changed = False
    for path in reversed([p for p in candidates if p]):
        if path not in parts:
            parts.insert(0, path)
            changed = True
    if changed:
        os.environ['PATH'] = os.pathsep.join(parts)

add_user_install_bins_to_path()

def mod_status(n):
    spec = importlib.util.find_spec(n)
    loader = getattr(spec, 'loader', None) if spec else None
    return {{
        'found': bool(spec),
        'origin': getattr(spec, 'origin', None) if spec else None,
        'loader': type(loader).__name__ if loader else None,
        'locations': list(getattr(spec, 'submodule_search_locations', []) or []),
        'real_module': bool(spec and loader),
    }}

def dist_status(ds):
    out = {{}}
    for d in ds:
        try:
            out[d] = md.version(d)
        except Exception:
            pass
    return out

def probe(n):
    mods = {{n: mod_status(n)}}
    if n == 'diffusers':
        mods['torch'] = mod_status('torch')
    dists = dist_status(dist_names.get(n, [n]))
    bins = {{b: shutil.which(b) for b in bin_names.get(n, [])}}
    return {{'modules': mods, 'dists': dists, 'binaries': bins}}

print(json.dumps({{n: probe(n) for n in names}}))
"""
