"""Review-gated Nextcloud copy executor for Universal Inbox placement plans.

The executor is intentionally provider-agnostic: callers inject a tiny client
that can stat and upload relative Nextcloud paths. This keeps tests offline and
keeps live WebDAV/rclone wiring behind an explicit operator gate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Protocol


TRANSFER_SCHEMA = "odysseus.universal_inbox.nextcloud_transfer.v1"
_UNSAFE_PATH_CHARS = set('<>:"|?*')


class UniversalInboxNextcloudTransferError(ValueError):
    """Raised when a Universal Inbox transfer request is unsafe."""


class NextcloudTransferClient(Protocol):
    """Minimal live/dry-run Nextcloud client surface."""

    def stat(self, relative_path: str) -> Mapping[str, Any] | None:
        """Return metadata for a relative path or None when it does not exist."""

    def put_file(self, source_path: Path, relative_path: str) -> Mapping[str, Any]:
        """Copy a local file to a relative Nextcloud path and return metadata."""

    def put_text(self, relative_path: str, text: str) -> Mapping[str, Any]:
        """Write a small sidecar text object to a relative Nextcloud path."""


@dataclass(frozen=True, slots=True)
class UniversalInboxNextcloudTransferRequest:
    source_path: Path
    target_path: str
    sidecar_path: str
    source_hash: str = ""
    review_approved: bool = False
    operator_live_go: bool = False
    dry_run: bool = True
    write_sidecar: bool = True
    overwrite_existing: bool = False
    delete_original: bool = False
    source_provider: str = "telegram_universal_inbox"
    actor: str = "odysseus"

    @classmethod
    def from_placement_plan(
        cls,
        placement_plan: Mapping[str, Any],
        *,
        source_path: str | Path,
        source_hash: str = "",
        review_approved: bool = False,
        operator_live_go: bool = False,
        dry_run: bool = True,
        actor: str = "odysseus",
    ) -> "UniversalInboxNextcloudTransferRequest":
        if not isinstance(placement_plan, Mapping):
            raise UniversalInboxNextcloudTransferError("placement_plan must be a mapping")
        return cls(
            source_path=Path(source_path),
            target_path=_normalize_relative_path(placement_plan.get("target_path")),
            sidecar_path=_normalize_relative_path(placement_plan.get("sidecar_path")),
            source_hash=_normalize_optional_hash(source_hash),
            review_approved=bool(review_approved),
            operator_live_go=bool(operator_live_go),
            dry_run=bool(dry_run),
            overwrite_existing=bool(placement_plan.get("overwrite_existing", False)),
            delete_original=bool(placement_plan.get("delete_original", False)),
            actor=_normalize_label(actor, field="actor"),
        )


@dataclass(frozen=True, slots=True)
class UniversalInboxNextcloudTransferResult:
    status: str
    reason: str
    target_path: str
    sidecar_path: str
    source_hash: str
    source_size_bytes: int
    target_size_bytes: int = 0
    etag: str = ""
    dry_run: bool = True
    writes_performed: bool = False
    sidecar_written: bool = False
    verified: bool = False
    review_approved: bool = False
    operator_live_go: bool = False
    blocked_reasons: tuple[str, ...] = ()
    schema: str = TRANSFER_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "reason": self.reason,
            "target_path": self.target_path,
            "sidecar_path": self.sidecar_path,
            "source_hash": self.source_hash,
            "source_size_bytes": self.source_size_bytes,
            "target_size_bytes": self.target_size_bytes,
            "etag": self.etag,
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
            "sidecar_written": self.sidecar_written,
            "verified": self.verified,
            "review_approved": self.review_approved,
            "operator_live_go": self.operator_live_go,
            "blocked_reasons": self.blocked_reasons,
            "private_content_visible": False,
            "secret_values_visible": False,
            "host_paths_visible": False,
        }


def execute_universal_inbox_nextcloud_transfer(
    request: UniversalInboxNextcloudTransferRequest,
    *,
    client: NextcloudTransferClient | None = None,
) -> UniversalInboxNextcloudTransferResult:
    """Execute or dry-run a review-gated copy into Nextcloud."""

    if not isinstance(request, UniversalInboxNextcloudTransferRequest):
        raise TypeError("request must be UniversalInboxNextcloudTransferRequest")

    source_size = _source_size(request.source_path)
    blocked = _blocked_reasons(request, client=client)
    if blocked:
        return _result(
            request,
            status="blocked",
            reason=blocked[0],
            source_size_bytes=source_size,
            blocked_reasons=blocked,
        )
    if request.dry_run:
        return _result(
            request,
            status="dry_run_ready",
            reason="dry_run_only",
            source_size_bytes=source_size,
        )

    assert client is not None
    before = client.stat(request.target_path)
    if before is not None and not request.overwrite_existing:
        return _result(
            request,
            status="blocked",
            reason="target_exists",
            source_size_bytes=source_size,
            blocked_reasons=("target_exists",),
        )

    uploaded = dict(client.put_file(request.source_path, request.target_path) or {})
    sidecar_written = False
    if request.write_sidecar:
        client.put_text(request.sidecar_path, _sidecar_json(request, source_size_bytes=source_size))
        sidecar_written = True
    after = dict(client.stat(request.target_path) or uploaded)
    target_size = _metadata_size(after)
    verified = target_size == source_size
    return _result(
        request,
        status="completed" if verified else "copied_unverified",
        reason="verified" if verified else "size_mismatch",
        source_size_bytes=source_size,
        target_size_bytes=target_size,
        etag=str(after.get("etag") or uploaded.get("etag") or ""),
        writes_performed=True,
        sidecar_written=sidecar_written,
        verified=verified,
    )


def _blocked_reasons(
    request: UniversalInboxNextcloudTransferRequest,
    *,
    client: NextcloudTransferClient | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if request.delete_original:
        reasons.append("delete_original_forbidden")
    if request.overwrite_existing:
        reasons.append("overwrite_forbidden")
    if not request.source_path.is_file():
        reasons.append("source_missing")
    if not request.review_approved:
        reasons.append("review_approval_missing")
    if not request.dry_run and not request.operator_live_go:
        reasons.append("operator_live_go_missing")
    if not request.dry_run and client is None:
        reasons.append("client_missing")
    return tuple(dict.fromkeys(reasons))


def _result(
    request: UniversalInboxNextcloudTransferRequest,
    *,
    status: str,
    reason: str,
    source_size_bytes: int,
    target_size_bytes: int = 0,
    etag: str = "",
    writes_performed: bool = False,
    sidecar_written: bool = False,
    verified: bool = False,
    blocked_reasons: tuple[str, ...] = (),
) -> UniversalInboxNextcloudTransferResult:
    return UniversalInboxNextcloudTransferResult(
        status=status,
        reason=reason,
        target_path=request.target_path,
        sidecar_path=request.sidecar_path,
        source_hash=request.source_hash or _hash_file_if_small(request.source_path),
        source_size_bytes=source_size_bytes,
        target_size_bytes=target_size_bytes,
        etag=etag,
        dry_run=request.dry_run,
        writes_performed=writes_performed,
        sidecar_written=sidecar_written,
        verified=verified,
        review_approved=request.review_approved,
        operator_live_go=request.operator_live_go,
        blocked_reasons=blocked_reasons,
    )


def _sidecar_json(request: UniversalInboxNextcloudTransferRequest, *, source_size_bytes: int) -> str:
    payload = {
        "schema": "odysseus.universal_inbox.nextcloud_sidecar.v1",
        "source_provider": request.source_provider,
        "actor": request.actor,
        "target_path": request.target_path,
        "source_hash": request.source_hash or _hash_file_if_small(request.source_path),
        "source_size_bytes": source_size_bytes,
        "copy_only": True,
        "delete_original": False,
        "overwrite_existing": False,
        "review_approved": request.review_approved,
        "operator_live_go": request.operator_live_go,
        "private_content_visible": False,
        "secret_values_visible": False,
        "host_paths_visible": False,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _metadata_size(metadata: Mapping[str, Any]) -> int:
    for key in ("size_bytes", "size", "content_length"):
        try:
            return int(metadata.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _hash_file_if_small(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _normalize_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise UniversalInboxNextcloudTransferError("relative path is required")
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", raw):
        raise UniversalInboxNextcloudTransferError("relative path must not be absolute")
    parts = [part.strip() for part in raw.split("/") if part.strip() and part.strip() != "."]
    if not parts or any(part == ".." for part in parts):
        raise UniversalInboxNextcloudTransferError("relative path must not contain traversal")
    for part in parts:
        if any(ord(ch) < 32 for ch in part):
            raise UniversalInboxNextcloudTransferError("relative path contains control characters")
        if any(ch in _UNSAFE_PATH_CHARS for ch in part):
            raise UniversalInboxNextcloudTransferError("relative path contains unsafe characters")
    return "/".join(parts)


def _normalize_optional_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{32,128}", text):
        raise UniversalInboxNextcloudTransferError("source_hash must be sha256-like")
    return text.removeprefix("sha256:")


def _normalize_label(value: Any, *, field: str) -> str:
    label = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,79}", label):
        raise UniversalInboxNextcloudTransferError(f"{field} must be a safe label")
    return label
