"""Read-only local discovery adapter for Universal Inbox dry runs.

The adapter scans a local inbox directory and returns file metadata only. It
does not mutate files, follow provider APIs, or serialize absolute host paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any


DISCOVERY_SCHEMA = "odysseus.universal_inbox.local_discovery_report.v1"
_TEMP_SUFFIXES = {
    ".crdownload",
    ".download",
    ".part",
    ".partial",
    ".swp",
    ".swo",
    ".tmp",
    ".temp",
}
_TEMP_NAMES = {
    "thumbs.db",
    "desktop.ini",
    ".ds_store",
}


class UniversalInboxDiscoveryError(ValueError):
    """Raised when a local discovery request is unsafe or invalid."""


@dataclass(frozen=True)
class UniversalInboxDiscoveryWarning:
    code: str
    relative_path: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"code": self.code, "relative_path": self.relative_path}
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class UniversalInboxDiscoveredFile:
    relative_path: str
    filename: str
    size: int
    mtime: str
    suffix: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "filename": self.filename,
            "size": self.size,
            "mtime": self.mtime,
            "suffix": self.suffix,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class UniversalInboxDiscoveryReport:
    items: tuple[UniversalInboxDiscoveredFile, ...]
    warnings: tuple[UniversalInboxDiscoveryWarning, ...] = ()
    schema: str = DISCOVERY_SCHEMA
    adapter: str = "local_read_only"

    @property
    def discovered_count(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "adapter": self.adapter,
            "discovered_count": self.discovered_count,
            "items": tuple(item.to_dict() for item in self.items),
            "warnings": tuple(warning.to_dict() for warning in self.warnings),
        }


def discover_universal_inbox_local(
    inbox_path: str | Path,
    *,
    max_file_size_bytes: int | None = None,
) -> UniversalInboxDiscoveryReport:
    """Scan a local inbox directory read-only and return metadata-only items."""

    root = Path(inbox_path)
    if not root.exists() or not root.is_dir():
        raise UniversalInboxDiscoveryError("inbox_path must be an existing directory")
    if max_file_size_bytes is not None and max_file_size_bytes < 0:
        raise UniversalInboxDiscoveryError("max_file_size_bytes must be non-negative")

    items: list[UniversalInboxDiscoveredFile] = []
    warnings: list[UniversalInboxDiscoveryWarning] = []
    for path in sorted(root.rglob("*"), key=lambda candidate: candidate.as_posix().lower()):
        relative_path = _relative_path(root, path)
        if _has_hidden_segment(path, root):
            if path.is_file():
                warnings.append(
                    UniversalInboxDiscoveryWarning("hidden_file_ignored", relative_path)
                )
            continue
        if path.is_symlink():
            warnings.append(UniversalInboxDiscoveryWarning("symlink_ignored", relative_path))
            continue
        if not path.is_file():
            continue
        if _is_temporary(path):
            warnings.append(UniversalInboxDiscoveryWarning("temporary_file_ignored", relative_path))
            continue

        try:
            before = path.stat()
        except OSError:
            warnings.append(UniversalInboxDiscoveryWarning("stat_failed", relative_path))
            continue
        if max_file_size_bytes is not None and before.st_size > max_file_size_bytes:
            warnings.append(
                UniversalInboxDiscoveryWarning(
                    "size_limit_exceeded",
                    relative_path,
                    f"size>{max_file_size_bytes}",
                )
            )
            continue

        try:
            digest = _sha256(path)
            after = path.stat()
        except OSError:
            warnings.append(UniversalInboxDiscoveryWarning("read_failed", relative_path))
            continue
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            warnings.append(UniversalInboxDiscoveryWarning("unstable_file_ignored", relative_path))
            continue

        items.append(
            UniversalInboxDiscoveredFile(
                relative_path=relative_path,
                filename=path.name,
                size=before.st_size,
                mtime=_utc_timestamp(before.st_mtime),
                suffix=path.suffix.lower(),
                sha256=digest,
            )
        )

    return UniversalInboxDiscoveryReport(items=tuple(items), warnings=tuple(warnings))


def scan_universal_inbox_local(
    inbox_path: str | Path,
    *,
    max_file_size_bytes: int | None = None,
) -> UniversalInboxDiscoveryReport:
    """Compatibility alias for callers that name the stage as a scan."""

    return discover_universal_inbox_local(
        inbox_path,
        max_file_size_bytes=max_file_size_bytes,
    )


def _relative_path(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise UniversalInboxDiscoveryError("discovered path must stay under inbox_path") from exc
    normalized = relative.as_posix()
    if normalized.startswith("../") or normalized == ".." or Path(normalized).is_absolute():
        raise UniversalInboxDiscoveryError("discovered path must be relative")
    return normalized


def _has_hidden_segment(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        return True
    if any(part.startswith(".") for part in relative_parts):
        return True
    try:
        return bool(getattr(path.stat(), "st_file_attributes", 0) & 0x2)
    except OSError:
        return False


def _is_temporary(path: Path) -> bool:
    name = path.name.lower()
    if name in _TEMP_NAMES:
        return True
    if name.startswith(("~$", ".~", "._")) or name.endswith(("~", ".bak")):
        return True
    return path.suffix.lower() in _TEMP_SUFFIXES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
