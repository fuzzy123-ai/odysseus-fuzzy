"""Repo-relative artifact integrity checks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
from typing import Any


class ArtifactIntegrityError(ValueError):
    """Raised when an artifact reference or file fails integrity checks."""


_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,220}")
_MIME_BY_SUFFIX = {
    ".log": "text/plain",
    ".txt": "text/plain",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


@dataclass(frozen=True, slots=True)
class ArtifactIntegrity:
    artifact_ref: str
    exists: bool
    size_bytes: int
    content_hash: str
    mime_hint: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_ref": self.artifact_ref,
            "exists": self.exists,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "mime_hint": self.mime_hint,
            "integrity_status": self.status,
        }


def safe_artifact_ref(value: Any, *, max_len: int = 220) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if (
        not text
        or len(text) > max_len
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:", text)
        or ".." in text.split("/")
        or not _REF_RE.fullmatch(text)
    ):
        raise ArtifactIntegrityError("artifact_ref is unsafe")
    parts = PurePosixPath(text).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ArtifactIntegrityError("artifact_ref is unsafe")
    return "/".join(parts)


def inspect_artifact(
    artifact_ref: Any,
    *,
    repo_root: Path | str | None = None,
    require_exists: bool = False,
    require_nonempty: bool = False,
    require_image: bool = False,
) -> ArtifactIntegrity:
    ref = safe_artifact_ref(artifact_ref)
    if repo_root is None:
        if require_exists or require_nonempty or require_image:
            raise ArtifactIntegrityError("repo_root is required for artifact integrity checks")
        return ArtifactIntegrity(
            artifact_ref=ref,
            exists=False,
            size_bytes=0,
            content_hash="",
            mime_hint=_mime_hint(ref),
            status="not_checked",
        )
    root = Path(repo_root).resolve()
    candidate = root / ref
    if candidate.is_symlink():
        raise ArtifactIntegrityError("artifact_ref must be a regular file")
    path = candidate.resolve()
    if root != path and root not in path.parents:
        raise ArtifactIntegrityError("artifact path escapes workspace")
    if not path.exists():
        if require_exists:
            raise ArtifactIntegrityError("artifact_ref does not exist")
        return ArtifactIntegrity(ref, False, 0, "", _mime_hint(ref), "missing")
    if path.is_symlink() or not path.is_file():
        raise ArtifactIntegrityError("artifact_ref must be a regular file")
    data = path.read_bytes()
    size = len(data)
    if require_nonempty and size <= 0:
        raise ArtifactIntegrityError("artifact_ref is empty")
    mime = _mime_hint(ref, data)
    if require_image and not mime.startswith("image/"):
        raise ArtifactIntegrityError("artifact_ref is not a supported image")
    return ArtifactIntegrity(
        artifact_ref=ref,
        exists=True,
        size_bytes=size,
        content_hash="sha256:" + hashlib.sha256(data).hexdigest(),
        mime_hint=mime,
        status="verified",
    )


def inspect_image_artifact(
    artifact_ref: Any,
    *,
    repo_root: Path | str | None = None,
) -> ArtifactIntegrity:
    return inspect_artifact(
        artifact_ref,
        repo_root=repo_root,
        require_exists=True,
        require_nonempty=True,
        require_image=True,
    )


def _mime_hint(ref: str, data: bytes | None = None) -> str:
    suffix = Path(ref).suffix.lower()
    if data is not None:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return "application/octet-stream"
    return _MIME_BY_SUFFIX.get(suffix, "application/octet-stream")
