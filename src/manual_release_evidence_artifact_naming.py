"""Safe deterministic naming helpers for manual release evidence artifacts."""

from __future__ import annotations

import re
import unicodedata

from src.manual_release_evidence_artifact import ManualReleaseEvidenceArtifact


_SAFE_DEFAULT_LABEL = "manual-release-evidence"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXT_RE = re.compile(r"^[a-z0-9]+$")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def build_manual_release_evidence_artifact_filename(
    artifact: ManualReleaseEvidenceArtifact,
    *,
    extension: str,
    sha_prefix_length: int = 8,
) -> str:
    if not isinstance(artifact, ManualReleaseEvidenceArtifact):
        raise TypeError("artifact must be a ManualReleaseEvidenceArtifact")
    if not isinstance(sha_prefix_length, int) or sha_prefix_length <= 0 or sha_prefix_length > 64:
        raise ValueError("sha_prefix_length must be between 1 and 64")
    if not _SHA256_RE.fullmatch(artifact.sha256):
        raise ValueError("artifact sha256 must be a 64-character lowercase hex digest")

    normalized_extension = _normalize_extension(extension)
    label_slug = _slugify(artifact.label) or _SAFE_DEFAULT_LABEL
    generated_slug = _slugify(artifact.generated_at)
    sha_prefix = artifact.sha256[:sha_prefix_length]

    parts = [label_slug]
    if generated_slug:
        parts.append(generated_slug)
    parts.append(sha_prefix)
    return f"{'-'.join(parts)}.{normalized_extension}"


def _normalize_extension(extension: str) -> str:
    original = str(extension or "")
    raw = original.lower()
    if original != original.strip():
        raise ValueError("extension must be a simple alphanumeric suffix")
    if raw.startswith("."):
        raw = raw[1:]
    if not raw or not _EXT_RE.fullmatch(raw):
        raise ValueError("extension must be a simple alphanumeric suffix")
    return raw


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_SLUG_RE.sub("-", ascii_text).strip("-")
    return re.sub(r"-{2,}", "-", slug)
