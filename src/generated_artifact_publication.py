"""Owner-scoped publication of files created by an agent.

Generated files are copied into the existing upload store.  Chat metadata only
receives the public upload identifier and bounded descriptive fields; source
paths and storage paths never leave this module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.upload_handler import secure_filename


GENERATED_ARTIFACT_SCHEMA = "odysseus.generated_artifact.v1"

_ALLOWED_EXTENSIONS = frozenset(
    {
        ".html",
        ".png",
        ".py",
    }
)

_upload_handler: Any | None = None


class GeneratedArtifactPublicationError(ValueError):
    """Raised when a generated file cannot be published safely."""


def configure_generated_artifact_publication(upload_handler: Any) -> None:
    """Bind the application's existing UploadHandler instance."""

    global _upload_handler
    _upload_handler = upload_handler


def get_generated_artifact_upload_handler() -> Any:
    if _upload_handler is None:
        raise GeneratedArtifactPublicationError("generated artifact publication is not configured")
    return _upload_handler


def publish_generated_artifact(
    source_path: str | os.PathLike[str],
    *,
    owner: str,
    allowed_root: str | os.PathLike[str],
    display_name: str | None = None,
    upload_handler: Any | None = None,
) -> dict[str, Any]:
    """Copy one regular file into the owner-protected upload store.

    ``allowed_root`` is the workspace that authorized the agent's file access.
    Both the lexical path and its resolved target must stay under that root;
    symlinked path components are rejected so publication cannot become a path
    exfiltration primitive.
    """

    normalized_owner = str(owner or "").strip()
    if not normalized_owner:
        raise GeneratedArtifactPublicationError("an authenticated owner is required")

    source, root = _resolve_source(source_path, allowed_root)
    safe_name = _safe_display_name(display_name or source.name, source.suffix)

    handler = upload_handler or get_generated_artifact_upload_handler()
    try:
        stored = handler.register_generated_artifact(
            str(source),
            owner=normalized_owner,
            allowed_root=str(root),
            display_name=safe_name,
        )
    except GeneratedArtifactPublicationError:
        raise
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        message = str(detail or exc or "publication failed")
        raise GeneratedArtifactPublicationError(message) from exc

    return project_generated_attachment(stored)


def _resolve_source(
    source_path: str | os.PathLike[str],
    allowed_root: str | os.PathLike[str],
) -> tuple[Path, Path]:
    raw_source = Path(os.fspath(source_path)).expanduser()
    raw_root = Path(os.fspath(allowed_root)).expanduser()
    try:
        lexical_root = Path(os.path.abspath(raw_root))
        lexical_source = Path(os.path.abspath(raw_source))
        root = lexical_root.resolve(strict=True)
        source = lexical_source.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GeneratedArtifactPublicationError("generated artifact path does not exist") from exc

    if not root.is_dir():
        raise GeneratedArtifactPublicationError("allowed_root must be a directory")
    if root != source and root not in source.parents:
        raise GeneratedArtifactPublicationError("generated artifact is outside the active workspace")
    if lexical_root != lexical_source and lexical_root not in lexical_source.parents:
        raise GeneratedArtifactPublicationError("generated artifact path escapes the active workspace")
    if not source.is_file():
        raise GeneratedArtifactPublicationError("generated artifact must be a regular file")

    try:
        relative = lexical_source.relative_to(lexical_root)
    except ValueError as exc:
        raise GeneratedArtifactPublicationError("generated artifact path escapes the active workspace") from exc
    cursor = lexical_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise GeneratedArtifactPublicationError("generated artifact paths must not contain symlinks")

    extension = source.suffix.lower()
    if extension not in _ALLOWED_EXTENSIONS:
        raise GeneratedArtifactPublicationError("generated artifact type is not publishable")
    return source, root


def _safe_display_name(value: str, source_suffix: str) -> str:
    safe_name = secure_filename(str(value or ""))
    if not safe_name:
        raise GeneratedArtifactPublicationError("generated artifact name is invalid")
    display_suffix = Path(safe_name).suffix.lower()
    if display_suffix != source_suffix.lower():
        raise GeneratedArtifactPublicationError("display name must keep the generated file extension")
    if display_suffix not in _ALLOWED_EXTENSIONS:
        raise GeneratedArtifactPublicationError("generated artifact type is not publishable")
    return safe_name


def project_generated_attachment(stored: dict[str, Any]) -> dict[str, Any]:
    """Return the only metadata shape allowed into chat history/the browser."""

    attachment = {
        "schema": GENERATED_ARTIFACT_SCHEMA,
        "id": str(stored.get("id") or ""),
        "name": str(stored.get("name") or stored.get("original_name") or "artifact"),
        "mime": str(stored.get("mime") or "application/octet-stream"),
        "size": int(stored.get("size") or 0),
        "hash": str(stored.get("hash") or ""),
        "kind": "generated_artifact",
        "download_ready": True,
    }
    for dimension in ("width", "height"):
        value = stored.get(dimension)
        if isinstance(value, int) and value > 0:
            attachment[dimension] = value
    if not attachment["id"] or not attachment["hash"] or attachment["size"] <= 0:
        raise GeneratedArtifactPublicationError("upload store returned incomplete artifact metadata")
    return attachment
