"""Safe sandbox job templates for common Odysseus agent tasks."""

from __future__ import annotations

from typing import Any

from src.agent_sandbox_contract import SandboxJobRequest, SandboxMount, SandboxResourceLimits


class SandboxJobTemplateError(ValueError):
    """Raised when a sandbox job template is unsupported."""


_TEMPLATES = {
    "python_pytest": {
        "profile_id": "python",
        "argv": ("python", "-m", "pytest"),
        "image": "localhost/odysseus_odysseus:latest",
        "mounts": ({"source": "tests", "target": "/workspace/repo/tests", "mode": "ro"},),
        "limits": {"timeout_seconds": 300, "memory_mb": 1024, "cpu_count": 1.0},
        "capabilities": ("python", "pytest", "read_repo", "artifact_reports"),
    },
    "node_check": {
        "profile_id": "node",
        "argv": ("node", "--check"),
        "image": "localhost/odysseus_odysseus:latest",
        "mounts": ({"source": "frontend", "target": "/workspace/repo/frontend", "mode": "ro"},),
        "limits": {"timeout_seconds": 120, "memory_mb": 512, "cpu_count": 0.5},
        "capabilities": ("node", "syntax_check", "read_repo"),
    },
    "browser_smoke": {
        "profile_id": "webdev_playwright",
        "argv": ("node", "scripts/browser-smoke.js"),
        "image": "localhost/odysseus_odysseus:latest",
        "mounts": ({"source": "data/reports/autonomous_coding_agent", "target": "/workspace/repo/reports", "mode": "rw"},),
        "limits": {"timeout_seconds": 300, "memory_mb": 1536, "cpu_count": 1.0},
        "capabilities": ("node", "playwright", "browser_gui", "screenshot_artifacts"),
        "artifact_policy": {
            "expected_artifacts": ("screenshot", "browser_console_summary"),
            "integrity_required": True,
            "raw_secret_scan_required": True,
        },
    },
    "document_convert": {
        "profile_id": "python",
        "argv": ("python", "scripts/document_convert_worker.py"),
        "image": "localhost/odysseus_odysseus:latest",
        "mounts": ({"source": "reports", "target": "/workspace/repo/reports", "mode": "rw"},),
        "limits": {"timeout_seconds": 300, "memory_mb": 1024, "cpu_count": 1.0},
        "capabilities": ("python", "document_convert", "artifact_reports"),
    },
    "static_analysis": {
        "profile_id": "python",
        "argv": ("python", "-m", "compileall"),
        "image": "localhost/odysseus_odysseus:latest",
        "mounts": ({"source": "src", "target": "/workspace/repo/src", "mode": "ro"},),
        "limits": {"timeout_seconds": 180, "memory_mb": 1024, "cpu_count": 1.0},
        "capabilities": ("python", "compile", "read_repo"),
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
        capabilities=tuple(spec["capabilities"]),
    )


def list_sandbox_job_templates() -> tuple[str, ...]:
    return tuple(sorted(_TEMPLATES))


def list_sandbox_job_template_specs() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "template_id": template_id,
            "profile_id": spec["profile_id"],
            "argv_prefix": tuple(spec["argv"]),
            "network_mode": "none",
            "secrets_attached": False,
            "capabilities": tuple(spec["capabilities"]),
            "artifact_policy": dict(spec.get("artifact_policy") or {}),
            "write_action_enabled": False,
        }
        for template_id, spec in sorted(_TEMPLATES.items())
    )
