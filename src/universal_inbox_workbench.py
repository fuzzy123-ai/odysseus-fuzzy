"""Pure capability contract for the Universal Inbox document workbench.

The contract is deliberately side-effect free. It does not read a source file,
open a database, call a provider, or grant a live write. Server-side file
classification and owner/policy checks remain authoritative; a browser hint is
recorded only as advisory mismatch evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from src.universal_inbox_file_types import UniversalInboxFileTypeDecision


WORKBENCH_CAPABILITY_SCHEMA = "odysseus.universal_inbox.workbench_capability.v1"


class WorkbenchContractError(ValueError):
    """Raised when capability inputs are structurally invalid."""


class WorkbenchAction(StrEnum):
    INSPECT = "inspect"
    ROUTE_DRY_RUN = "route_dry_run"
    CREATE_WORKING_COPY = "create_working_copy"
    EDIT_WORKING_COPY = "edit_working_copy"
    DOWNLOAD_ORIGINAL = "download_original"
    EXPORT_WORKING_COPY = "export_working_copy"


class WorkbenchActionState(StrEnum):
    ALLOWED = "allowed"
    REVIEW = "review"
    BLOCKED = "blocked"
    NOT_SUPPORTED = "not_supported"
    LIVE_GATE_REQUIRED = "live_gate_required"


_ACTION_ORDER = tuple(WorkbenchAction)
_P0_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".pdf", ".docx"})
_P1_TEXT_WORKING_COPY_SUFFIXES = frozenset(
    {".html", ".htm", ".svg", ".xml", ".csv"}
)
_P1_ORIGINAL_ONLY_SUFFIXES = frozenset({".xls", ".xlsx"})
_P1_SUFFIXES = _P1_TEXT_WORKING_COPY_SUFFIXES | _P1_ORIGINAL_ONLY_SUFFIXES
_P2_SUFFIXES = frozenset(
    {".pptx", ".odp", ".odt", ".ods", ".rtf", ".epub"}
)
_SUPPORTING_SUFFIXES = frozenset(
    {
        ".avif",
        ".bmp",
        ".gif",
        ".heic",
        ".jpeg",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }
)
_PUBLIC_SUFFIXES = (
    _P0_SUFFIXES
    | _P1_SUFFIXES
    | _P2_SUFFIXES
    | _SUPPORTING_SUFFIXES
)
_ADVISORY_FAMILIES = frozenset(
    {
        "archive",
        "asset",
        "audio",
        "dangerous",
        "document",
        "image",
        "message",
        "text",
        "unknown",
        "video",
    }
)


@dataclass(frozen=True, slots=True)
class WorkbenchActionDecision:
    action: WorkbenchAction
    state: WorkbenchActionState
    reason_codes: tuple[str, ...]
    mutates_original: bool = field(default=False, init=False)
    performs_live_write: bool = field(default=False, init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "state": self.state.value,
            "reason_codes": self.reason_codes,
            "mutates_original": False,
            "performs_live_write": False,
        }


@dataclass(frozen=True, slots=True)
class UniversalInboxWorkbenchCapability:
    source_suffix: str
    server_family: str
    mvp_tier: str
    browser_hint: str
    browser_hint_relation: str
    owner_authorized: bool
    has_working_copy: bool
    browser_download_allowed: bool
    actions: tuple[WorkbenchActionDecision, ...]
    original_immutable: bool = field(default=True, init=False)
    working_copy_versioned: bool = field(default=True, init=False)
    server_authoritative: bool = field(default=True, init=False)
    browser_detection_advisory: bool = field(default=True, init=False)
    raw_content_visible: bool = field(default=False, init=False)
    absolute_path_visible: bool = field(default=False, init=False)
    live_write_authorized: bool = field(default=False, init=False)
    schema: str = field(default=WORKBENCH_CAPABILITY_SCHEMA, init=False)

    def action(self, action: WorkbenchAction | str) -> WorkbenchActionDecision:
        try:
            normalized = WorkbenchAction(action)
        except (TypeError, ValueError) as exc:
            raise WorkbenchContractError("unknown workbench action") from exc
        for decision in self.actions:
            if decision.action == normalized:
                return decision
        raise WorkbenchContractError("workbench action is unavailable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source_suffix": self.source_suffix,
            "server_family": self.server_family,
            "mvp_tier": self.mvp_tier,
            "browser_hint": self.browser_hint,
            "browser_hint_relation": self.browser_hint_relation,
            "owner_authorized": self.owner_authorized,
            "has_working_copy": self.has_working_copy,
            "browser_download_allowed": self.browser_download_allowed,
            "original_immutable": True,
            "working_copy_versioned": True,
            "server_authoritative": True,
            "browser_detection_advisory": True,
            "raw_content_visible": False,
            "absolute_path_visible": False,
            "live_write_authorized": False,
            "actions": [decision.to_dict() for decision in self.actions],
        }


def build_universal_inbox_workbench_capability(
    file_type: UniversalInboxFileTypeDecision,
    *,
    owner_authorized: bool,
    has_working_copy: bool = False,
    browser_download_allowed: bool = False,
    provider_write_requested: bool = False,
    browser_family_hint: str | None = None,
) -> UniversalInboxWorkbenchCapability:
    """Build the authoritative, read-only workbench action matrix."""

    if not isinstance(file_type, UniversalInboxFileTypeDecision):
        raise WorkbenchContractError(
            "file_type must be a UniversalInboxFileTypeDecision"
        )
    owner_authorized = _strict_bool(owner_authorized, "owner_authorized")
    has_working_copy = _strict_bool(has_working_copy, "has_working_copy")
    browser_download_allowed = _strict_bool(
        browser_download_allowed,
        "browser_download_allowed",
    )
    provider_write_requested = _strict_bool(
        provider_write_requested,
        "provider_write_requested",
    )

    tier = _mvp_tier(file_type)
    server_family = _server_family(file_type.family)
    hint, relation = _browser_hint(
        browser_family_hint,
        server_family=server_family,
    )
    actions = tuple(
        _decide_action(
            action,
            file_type=file_type,
            tier=tier,
            owner_authorized=owner_authorized,
            has_working_copy=has_working_copy,
            browser_download_allowed=browser_download_allowed,
            provider_write_requested=provider_write_requested,
        )
        for action in _ACTION_ORDER
    )
    return UniversalInboxWorkbenchCapability(
        source_suffix=_public_suffix(file_type.suffix),
        server_family=server_family,
        mvp_tier=tier,
        browser_hint=hint,
        browser_hint_relation=relation,
        owner_authorized=owner_authorized,
        has_working_copy=has_working_copy,
        browser_download_allowed=browser_download_allowed,
        actions=actions,
    )


def _decide_action(
    action: WorkbenchAction,
    *,
    file_type: UniversalInboxFileTypeDecision,
    tier: str,
    owner_authorized: bool,
    has_working_copy: bool,
    browser_download_allowed: bool,
    provider_write_requested: bool,
) -> WorkbenchActionDecision:
    if not owner_authorized:
        return _decision(action, WorkbenchActionState.BLOCKED, "owner_authorization_required")
    if tier == "unsupported":
        return _decision(action, WorkbenchActionState.NOT_SUPPORTED, "format_not_supported")
    if file_type.blocked:
        return _dangerous_decision(action)
    if action == WorkbenchAction.INSPECT:
        return _inspect_decision(action, tier)
    if action == WorkbenchAction.ROUTE_DRY_RUN:
        return _route_decision(action, tier)
    if action == WorkbenchAction.CREATE_WORKING_COPY:
        return _create_decision(action, file_type.suffix, tier, has_working_copy)
    if action == WorkbenchAction.EDIT_WORKING_COPY:
        return _edit_decision(action, file_type.suffix, tier, has_working_copy)
    if action in {
        WorkbenchAction.DOWNLOAD_ORIGINAL,
        WorkbenchAction.EXPORT_WORKING_COPY,
    }:
        return _export_decision(
            action,
            suffix=file_type.suffix,
            tier=tier,
            has_working_copy=has_working_copy,
            browser_download_allowed=browser_download_allowed,
            provider_write_requested=provider_write_requested,
        )
    raise WorkbenchContractError("workbench action is unavailable")


def _inspect_decision(
    action: WorkbenchAction,
    tier: str,
) -> WorkbenchActionDecision:
    if tier == "p0":
        return _decision(action, WorkbenchActionState.ALLOWED, "p0_document_inspection")
    return _decision(action, WorkbenchActionState.REVIEW, f"{tier}_metadata_or_preview_review")


def _route_decision(
    action: WorkbenchAction,
    tier: str,
) -> WorkbenchActionDecision:
    if tier in {"p0", "p1", "p2"}:
        return _decision(action, WorkbenchActionState.ALLOWED, "explainable_dry_run_only")
    return _decision(action, WorkbenchActionState.REVIEW, "review_or_no_go_dry_run")


def _create_decision(
    action: WorkbenchAction,
    suffix: str,
    tier: str,
    has_working_copy: bool,
) -> WorkbenchActionDecision:
    if tier == "p0":
        reason = (
            "reuse_versioned_working_copy"
            if has_working_copy
            else "create_versioned_working_copy"
        )
        return _decision(action, WorkbenchActionState.ALLOWED, reason)
    if suffix in _P1_TEXT_WORKING_COPY_SUFFIXES:
        return _decision(action, WorkbenchActionState.REVIEW, "p1_working_copy_review")
    return _decision(
        action,
        WorkbenchActionState.NOT_SUPPORTED,
        "working_copy_not_supported_for_format",
    )


def _edit_decision(
    action: WorkbenchAction,
    suffix: str,
    tier: str,
    has_working_copy: bool,
) -> WorkbenchActionDecision:
    editable = tier == "p0" or suffix in _P1_TEXT_WORKING_COPY_SUFFIXES
    if not editable:
        return _decision(
            action,
            WorkbenchActionState.NOT_SUPPORTED,
            "working_copy_edit_not_supported_for_format",
        )
    if not has_working_copy:
        return _decision(action, WorkbenchActionState.BLOCKED, "working_copy_required")
    state = (
        WorkbenchActionState.ALLOWED
        if tier == "p0"
        else WorkbenchActionState.REVIEW
    )
    return _decision(action, state, "edit_versioned_working_copy_only")


def _export_decision(
    action: WorkbenchAction,
    *,
    suffix: str,
    tier: str,
    has_working_copy: bool,
    browser_download_allowed: bool,
    provider_write_requested: bool,
) -> WorkbenchActionDecision:
    if action == WorkbenchAction.EXPORT_WORKING_COPY:
        exportable = tier == "p0" or suffix in _P1_TEXT_WORKING_COPY_SUFFIXES
        if not exportable:
            return _decision(
                action,
                WorkbenchActionState.NOT_SUPPORTED,
                "working_copy_export_not_supported_for_format",
            )
        if not has_working_copy:
            return _decision(action, WorkbenchActionState.BLOCKED, "working_copy_required")
    if provider_write_requested:
        return _decision(
            action,
            WorkbenchActionState.LIVE_GATE_REQUIRED,
            "provider_write_requires_uix_nextcloud_live_write",
        )
    if not browser_download_allowed:
        return _decision(
            action,
            WorkbenchActionState.BLOCKED,
            "browser_download_policy_required",
        )
    state = (
        WorkbenchActionState.REVIEW
        if tier in {"p1", "p2", "supporting", "out_of_focus"}
        else WorkbenchActionState.ALLOWED
    )
    reason = (
        "local_browser_original_download"
        if action == WorkbenchAction.DOWNLOAD_ORIGINAL
        else "local_browser_working_copy_export"
    )
    return _decision(action, state, reason)


def _dangerous_decision(
    action: WorkbenchAction,
) -> WorkbenchActionDecision:
    if action == WorkbenchAction.INSPECT:
        return _decision(
            action,
            WorkbenchActionState.REVIEW,
            "dangerous_type_metadata_review_only",
        )
    return _decision(
        action,
        WorkbenchActionState.BLOCKED,
        "dangerous_type_policy_block",
    )


def _decision(
    action: WorkbenchAction,
    state: WorkbenchActionState,
    *reason_codes: str,
) -> WorkbenchActionDecision:
    return WorkbenchActionDecision(
        action=action,
        state=state,
        reason_codes=tuple(reason_codes),
    )


def _mvp_tier(file_type: UniversalInboxFileTypeDecision) -> str:
    suffix = file_type.suffix
    if suffix in _P0_SUFFIXES:
        return "p0"
    if suffix in _P1_SUFFIXES:
        return "p1"
    if suffix in _P2_SUFFIXES:
        return "p2"
    if suffix in _SUPPORTING_SUFFIXES:
        return "supporting"
    if file_type.blocked or file_type.family in {
        "archive",
        "asset",
        "audio",
        "message",
        "video",
    }:
        return "out_of_focus"
    if file_type.mime_type == "application/pdf":
        return "p0"
    if file_type.mime_type.startswith("text/"):
        return "p0"
    return "unsupported"


def _browser_hint(
    value: str | None,
    *,
    server_family: str,
) -> tuple[str, str]:
    if value is None or not str(value).strip():
        return "not_provided", "not_provided"
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in _ADVISORY_FAMILIES:
        return "ignored", "ignored_invalid"
    relation = "match" if normalized == server_family else "mismatch"
    return normalized, relation


def _public_suffix(value: str) -> str:
    return value if value in _PUBLIC_SUFFIXES else "other"


def _server_family(value: str) -> str:
    return value if value in _ADVISORY_FAMILIES else "unknown"


def _strict_bool(value: bool, field_name: str) -> bool:
    if type(value) is not bool:
        raise WorkbenchContractError(f"{field_name} must be a boolean")
    return value


__all__ = [
    "WORKBENCH_CAPABILITY_SCHEMA",
    "UniversalInboxWorkbenchCapability",
    "WorkbenchAction",
    "WorkbenchActionDecision",
    "WorkbenchActionState",
    "WorkbenchContractError",
    "build_universal_inbox_workbench_capability",
]
