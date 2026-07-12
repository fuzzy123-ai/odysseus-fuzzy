"""Shared Universal Inbox review reason vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


REVIEW_REASON_SCHEMA = "odysseus.universal_inbox.review_reason.v1"
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_ALIASES = {
    "needs_review": "operator_review_required",
    "review_required": "operator_review_required",
    "routing_needs_review": "operator_review_required",
    "redacted_reviews_require_operator_decision": "operator_review_required",
    "nextcloud_review_candidates": "operator_review_required",
    "partial_extract": "partial_extraction",
    "extraction_partial": "partial_extraction",
    "extraction_failed": "failed_extraction",
    "failed_extractions_require_review": "failed_extraction",
    "policy_no_go": "analysis_policy_no_go",
    "destructive_token": "dry_run_contains_destructive_token",
    "operator_go_missing": "operator_live_go_missing",
    "memory_gate_not_open": "memory_write_gate_not_open",
    "raptor_gate_not_open": "raptorgraph_write_gate_not_open",
}

_NO_GO_REASONS = frozenset(
    {
        "unsafe_target_path",
        "unsafe_original_path",
        "unsafe_sidecar_path",
        "destructive_operation",
        "delete_original",
        "overwrite_existing",
        "raw_content_persistence",
        "analysis_policy_no_go",
        "dry_run_contains_destructive_token",
        "operator_local_extraction_go_required",
        "memory_write_gate_not_open",
        "raptorgraph_write_gate_not_open",
        "local_only_subset_not_approved_for_memory",
        "source_missing",
        "target_exists",
        "client_missing",
        "credential_capture",
        "secret_detected",
    }
)

_STAGE_PREFIXES = (
    ("dry_run_", "copied_exported"),
    ("transfer_", "copied_exported"),
    ("target_", "routed"),
    ("source_", "received"),
    ("routing_", "routed"),
    ("memory_", "memory_intent"),
    ("raptor", "graph_provenance"),
    ("graph_", "graph_provenance"),
    ("extraction_", "extracted"),
    ("failed_extraction", "extracted"),
    ("partial_extraction", "extracted"),
    ("analysis_", "abstracted"),
    ("operator_", "reviewed"),
)

_CATEGORY_KEYWORDS = (
    ("secret", "privacy"),
    ("raw_content", "privacy"),
    ("credential", "privacy"),
    ("chat_id", "privacy"),
    ("private", "privacy"),
    ("delete", "destructive_operation"),
    ("overwrite", "destructive_operation"),
    ("destructive", "destructive_operation"),
    ("dry_run", "copy_safety"),
    ("copy", "copy_safety"),
    ("target", "routing"),
    ("route", "routing"),
    ("domain", "classification"),
    ("document_type", "classification"),
    ("confidence", "classification"),
    ("duplicate", "classification"),
    ("extraction", "extraction"),
    ("ocr", "extraction"),
    ("memory", "memory"),
    ("raptor", "graph"),
    ("graph", "graph"),
    ("operator", "operator_gate"),
    ("review", "operator_gate"),
)


@dataclass(frozen=True, slots=True)
class UniversalInboxReviewReason:
    code: str
    category: str
    severity: str
    stage: str
    schema: str = REVIEW_REASON_SCHEMA

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "code": self.code,
            "category": self.category,
            "severity": self.severity,
            "stage": self.stage,
        }


def normalize_universal_inbox_review_reasons(values: Any) -> tuple[str, ...]:
    """Normalize arbitrary reason values to stable snake_case codes."""

    return tuple(dict.fromkeys(_normalize_reason(value) for value in _flatten(values)))


def classify_universal_inbox_review_reasons(
    values: Any,
    *,
    no_go_reasons: Any = (),
) -> tuple[UniversalInboxReviewReason, ...]:
    no_go = set(normalize_universal_inbox_review_reasons(no_go_reasons))
    classified = []
    for code in normalize_universal_inbox_review_reasons(values):
        severity = "no_go" if code in no_go or code in _NO_GO_REASONS else "review"
        classified.append(
            UniversalInboxReviewReason(
                code=code,
                category=_category(code),
                severity=severity,
                stage=_stage(code),
            )
        )
    return tuple(classified)


def universal_inbox_review_reason_dicts(
    values: Any,
    *,
    no_go_reasons: Any = (),
) -> tuple[dict[str, str], ...]:
    return tuple(
        reason.to_dict()
        for reason in classify_universal_inbox_review_reasons(values, no_go_reasons=no_go_reasons)
    )


def _flatten(values: Any) -> tuple[Any, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        return (values,)
    if isinstance(values, dict):
        return tuple(values.values())
    if isinstance(values, (tuple, list, set)):
        output: list[Any] = []
        for value in values:
            output.extend(_flatten(value))
        return tuple(output)
    return (values,)


def _normalize_reason(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    text = re.sub(r"_+", "_", text)
    if text and text[0].isdigit():
        text = f"reason_{text}"
    text = _ALIASES.get(text, text)
    if not text:
        text = "unknown"
    if not _SAFE_REASON_RE.fullmatch(text):
        text = text[:64].strip("_") or "unknown"
    return text


def _stage(code: str) -> str:
    for prefix, stage in _STAGE_PREFIXES:
        if code.startswith(prefix):
            return stage
    if code in {"low_confidence", "unknown_domain", "unknown_document_type", "duplicate", "sensitive"}:
        return "classified"
    if code in {"target_conflict", "unsafe_target_path"}:
        return "routed"
    return "reviewed"


def _category(code: str) -> str:
    for keyword, category in _CATEGORY_KEYWORDS:
        if keyword in code:
            return category
    return "review"
