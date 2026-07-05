"""Safe sandbox job templates for common Odysseus agent tasks."""

from __future__ import annotations

from typing import Any

from src.agent_sandbox_contract import SandboxJobRequest, SandboxMount, SandboxResourceLimits


class SandboxJobTemplateError(ValueError):
    """Raised when a sandbox job template is unsupported."""


_TEMPLATES = {
    "python_pytest": {
        "argv": ("python", "-m", "pytest"),
        "image": "localhost/odysseus_odysseus:latest",
        "mounts": ({"source": "tests", "target": "/workspace/repo/tests", "mode": "ro"},),
        "limits": {"timeout_seconds": 300, "memory_mb": 1024, "cpu_count": 1.0},
    },
    "node_check": {
        "argv": ("node", "--check"),
        "image": "localhost/odysseus_odysseus:latest",
        "mounts": ({"source": "frontend", "target": "/workspace/repo/frontend", "mode": "ro"},),
        "limits": {"timeout_seconds": 120, "memory_mb": 512, "cpu_count": 0.5},
    },
    "browser_smoke": {
        "argv": ("node", "scripts/browser-smoke.js"),
        "image": "localhost/odysseus_odysseus:latest",
        "mounts": ({"source": "data/reports/autonomous_coding_agent", "target": "/workspace/repo/reports", "mode": "rw"},),
        "limits": {"timeout_seconds": 300, "memory_mb": 1536, "cpu_count": 1.0},
    },
    "document_convert": {
        "argv": ("python", "scripts/document_convert_worker.py"),
        "image": "localhost/odysseus_odysseus:latest",
        "mounts": ({"source": "reports", "target": "/workspace/repo/reports", "mode": "rw"},),
        "limits": {"timeout_seconds": 300, "memory_mb": 1024, "cpu_count": 1.0},
    },
    "static_analysis": {
        "argv": ("python", "-m", "compileall"),
        "image": "localhost/odysseus_odysseus:latest",
        "mounts": ({"source": "src", "target": "/workspace/repo/src", "mode": "ro"},),
        "limits": {"timeout_seconds": 180, "memory_mb": 1024, "cpu_count": 1.0},
    },
}


def build_sandbox_job_from_template(template: str, *, job_id: Any, extra_args: tuple[Any, ...] = ()) -> SandboxJobRequest:
    key = str(template or "").strip().lower()
    if key not in _TEMPLATES:
        raise SandboxJobTemplateError("unsupported sandbox job template")
    spec = _TEMPLATES[key]
    argv = tuple(spec["argv"]) + tuple(str(arg) for arg in extra_args)
    return SandboxJobRequest.create(
        job_id=job_id,
        argv=argv,
        image=spec["image"],
        mounts=tuple(SandboxMount.create(**mount) for mount in spec["mounts"]),
        limits=SandboxResourceLimits.create(**spec["limits"]),
        network_mode="none",
        secrets_attached=False,
    )


def list_sandbox_job_templates() -> tuple[str, ...]:
    return tuple(sorted(_TEMPLATES))
