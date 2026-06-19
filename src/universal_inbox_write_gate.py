"""Scoped write-capability probe for Universal Inbox live-readiness.

This gate is only for explicit scratch/staging roots. It proves that local
write, rename, move, and cleanup mechanics work without touching Inbox files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import uuid


WRITE_GATE_SCHEMA = "odysseus.universal_inbox.write_capability_probe.v1"
_SENTINEL_TEXT = "universal-inbox-write-capability-sentinel\n"
_ALLOWED_ROOT_HINTS = ("scratch", "staging", "stage", "test", "tmp", "temp", "probe")


class UniversalInboxWriteGateError(ValueError):
    """Raised when a write-capability probe request is unsafe."""


@dataclass(frozen=True)
class UniversalInboxWriteCapabilityProbe:
    status: str
    reasons: tuple[str, ...]
    probe_root_label: str
    write_ok: bool
    rename_ok: bool
    move_ok: bool
    cleanup_ok: bool
    probe_writes_performed: bool
    live_writes_performed: bool = False
    inbox_files_touched: bool = False
    absolute_paths_visible: bool = False
    schema: str = WRITE_GATE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "reasons": self.reasons,
            "probe_root_label": self.probe_root_label,
            "write_ok": self.write_ok,
            "rename_ok": self.rename_ok,
            "move_ok": self.move_ok,
            "cleanup_ok": self.cleanup_ok,
            "probe_writes_performed": self.probe_writes_performed,
            "live_writes_performed": self.live_writes_performed,
            "inbox_files_touched": self.inbox_files_touched,
            "absolute_paths_visible": self.absolute_paths_visible,
        }


def run_universal_inbox_write_capability_probe(
    probe_root: str | Path,
    *,
    allow_probe_writes: bool = False,
    probe_root_label: str = "operator_scratch_probe",
) -> UniversalInboxWriteCapabilityProbe:
    """Probe write/rename/move/cleanup in an explicit scratch or staging root."""

    label = _safe_label(probe_root_label)
    root = Path(probe_root)
    if not allow_probe_writes:
        return _blocked(label, "probe_writes_not_enabled")
    if not _looks_like_probe_root(root, label):
        return _blocked(label, "probe_root_not_marked_scratch_or_staging")
    if not root.exists() or not root.is_dir():
        return _blocked(label, "probe_root_missing")

    probe_dir = root / f"uix-write-gate-{uuid.uuid4().hex[:12]}"
    target_dir = probe_dir / "target"
    original = probe_dir / "sentinel.txt"
    renamed = probe_dir / "sentinel-renamed.txt"
    moved = target_dir / "sentinel-renamed.txt"
    write_ok = False
    rename_ok = False
    move_ok = False
    cleanup_ok = False
    reasons: list[str] = []

    try:
        target_dir.mkdir(parents=True, exist_ok=False)
        original.write_text(_SENTINEL_TEXT, encoding="utf-8")
        write_ok = original.exists()
        original.rename(renamed)
        rename_ok = renamed.exists() and not original.exists()
        renamed.replace(moved)
        move_ok = moved.exists() and not renamed.exists()
        if moved.read_text(encoding="utf-8") != _SENTINEL_TEXT:
            reasons.append("sentinel_content_mismatch")
    except OSError:
        reasons.append("probe_operation_failed")
    finally:
        cleanup_ok = _cleanup_probe_dir(probe_dir)

    if not write_ok:
        reasons.append("write_failed")
    if not rename_ok:
        reasons.append("rename_failed")
    if not move_ok:
        reasons.append("move_failed")
    if not cleanup_ok:
        reasons.append("cleanup_failed")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return UniversalInboxWriteCapabilityProbe(
        status="go" if not unique_reasons else "no_go",
        reasons=unique_reasons,
        probe_root_label=label,
        write_ok=write_ok,
        rename_ok=rename_ok,
        move_ok=move_ok,
        cleanup_ok=cleanup_ok,
        probe_writes_performed=True,
    )


def _blocked(label: str, reason: str) -> UniversalInboxWriteCapabilityProbe:
    return UniversalInboxWriteCapabilityProbe(
        status="no_go",
        reasons=(reason,),
        probe_root_label=label,
        write_ok=False,
        rename_ok=False,
        move_ok=False,
        cleanup_ok=False,
        probe_writes_performed=False,
    )


def _cleanup_probe_dir(probe_dir: Path) -> bool:
    if not probe_dir.exists():
        return True
    try:
        for child in sorted(probe_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        probe_dir.rmdir()
    except OSError:
        return False
    return not probe_dir.exists()


def _looks_like_probe_root(root: Path, label: str) -> bool:
    text = f"{root.name} {label}".lower()
    return any(hint in text for hint in _ALLOWED_ROOT_HINTS)


def _safe_label(value: str) -> str:
    label = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not label or not all(ch.isalnum() or ch == "_" for ch in label):
        raise UniversalInboxWriteGateError("probe_root_label must be a safe label")
    return label[:80]
