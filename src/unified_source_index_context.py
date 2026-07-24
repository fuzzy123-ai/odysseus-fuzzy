"""Bounded bridge from USI query results to context/provenance contracts.

The bridge is deliberately read-free: it never resolves an EvidenceRef or
copies provider snippets into transparency/AI Lens payloads.  Exact source
version and locator references are retained as typed answer provenance for a
separate, policy-checked reader call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re

from src.agent_context_transparency import (
    AnswerPackSummary,
    ContextItem,
    build_context_item_from_evidence,
    classify_review,
    project_to_ai_lens,
    strongest_classification,
)
from src.ai_lens_events import (
    AiLensEvent,
    AiLensEventType,
    AiLensPrivacyLevel,
    AiLensRedactionLevel,
    AiLensSourceKind,
    AiLensSourceRef,
    AiLensStatus,
)
from src.unified_source_index_contract import (
    Classification,
    EvidenceRef,
    RecordKind,
    RecordRef,
    canonical_json,
)
from src.unified_source_index_query import (
    FederatedQueryPage,
    FederatedResultItem,
    ProviderStatus,
)


MAX_CONTEXT_ITEMS = 64
MAX_CONTEXT_TOKENS = 1_000_000
MAX_ITEM_TOKENS = 100_000
MAX_UNREPRESENTED_RESULTS = 100

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_OWNER_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}:[^\s:*]{1,127}$")
_RESTRICTED_NORMAL_MODE = {
    Classification.SENSITIVE,
    Classification.SECRET,
    Classification.UNKNOWN,
}


class UnifiedSourceIndexContextError(ValueError):
    """Raised when a context projection is invalid or crosses policy."""


@dataclass(frozen=True, slots=True)
class ContextProjectionBudget:
    max_context_items: int = 32
    max_included_items: int = 20
    max_tokens: int = 8_192
    max_tokens_per_item: int = 2_048

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_context_items",
            _integer(self.max_context_items, "max_context_items", 1, MAX_CONTEXT_ITEMS),
        )
        object.__setattr__(
            self,
            "max_included_items",
            _integer(self.max_included_items, "max_included_items", 0, MAX_CONTEXT_ITEMS),
        )
        if self.max_included_items > self.max_context_items:
            raise UnifiedSourceIndexContextError(
                "max_included_items exceeds max_context_items"
            )
        object.__setattr__(
            self,
            "max_tokens",
            _integer(self.max_tokens, "max_tokens", 0, MAX_CONTEXT_TOKENS),
        )
        object.__setattr__(
            self,
            "max_tokens_per_item",
            _integer(
                self.max_tokens_per_item,
                "max_tokens_per_item",
                1,
                MAX_ITEM_TOKENS,
            ),
        )


@dataclass(frozen=True, slots=True)
class ContextProjectionRequest:
    owner_scope: str
    conversation_ref: str
    turn_ref: str
    created_at: str
    model_ref: str
    model_locality: str = "api"
    security_mode: str = "normal"
    scope: str = "turn"
    allow_stale: bool = False
    first_event_sequence: int = 1
    budget: ContextProjectionBudget = field(default_factory=ContextProjectionBudget)

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_scope", _owner_scope(self.owner_scope))
        for field_name in ("conversation_ref", "turn_ref", "model_ref"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name), field_name))
        object.__setattr__(self, "created_at", _utc_timestamp(self.created_at))
        if self.model_locality not in {"local", "api"}:
            raise UnifiedSourceIndexContextError("model_locality is invalid")
        if self.security_mode not in {"normal", "secure"}:
            raise UnifiedSourceIndexContextError("security_mode is invalid")
        if self.security_mode == "secure" and self.model_locality != "local":
            raise UnifiedSourceIndexContextError("secure mode requires a local model")
        if self.scope not in {"turn", "conversation", "project", "workspace", "global"}:
            raise UnifiedSourceIndexContextError("scope is invalid")
        if not isinstance(self.allow_stale, bool):
            raise UnifiedSourceIndexContextError("allow_stale must be boolean")
        object.__setattr__(
            self,
            "first_event_sequence",
            _integer(self.first_event_sequence, "first_event_sequence", 1, 1_000_000_000),
        )
        if not isinstance(self.budget, ContextProjectionBudget):
            raise UnifiedSourceIndexContextError("budget must be typed")


@dataclass(frozen=True, slots=True)
class AnswerProvenanceRef:
    """Exact supporting occurrence retained for a later authorized read."""

    provenance_id: str
    context_id: str
    evidence: EvidenceRef
    provider_ids: tuple[str, ...]
    fused_score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance_id", _identifier(self.provenance_id, "provenance_id"))
        object.__setattr__(self, "context_id", _identifier(self.context_id, "context_id"))
        if not isinstance(self.evidence, EvidenceRef):
            raise UnifiedSourceIndexContextError("answer provenance requires exact evidence")
        if (
            not isinstance(self.provider_ids, tuple)
            or not self.provider_ids
            or len(self.provider_ids) > 5
            or len(set(self.provider_ids)) != len(self.provider_ids)
        ):
            raise UnifiedSourceIndexContextError("provider_ids must be unique and bounded")
        if not all(isinstance(value, str) and 0 < len(value) <= 128 for value in self.provider_ids):
            raise UnifiedSourceIndexContextError("provider_ids contains an invalid value")
        object.__setattr__(self, "fused_score", _score(self.fused_score))

    @property
    def source_ref(self) -> RecordRef:
        return RecordRef(RecordKind.SOURCE, self.evidence.source_id)

    @property
    def source_version_ref(self) -> RecordRef:
        return RecordRef(RecordKind.SOURCE_VERSION, self.evidence.source_version_id)

    @property
    def record_ref(self) -> RecordRef:
        return RecordRef(self.evidence.record_kind, self.evidence.record_id)

    @property
    def locator(self):
        return self.evidence.locator

    def to_exact_read_dict(self) -> dict:
        """Serialize only the typed reference needed by a separate reader."""

        return {
            "provenance_id": self.provenance_id,
            "context_id": self.context_id,
            "evidence": self.evidence.to_dict(),
            "provider_ids": list(self.provider_ids),
            "fused_score": self.fused_score,
        }


@dataclass(frozen=True, slots=True)
class ContextProjectionPage:
    context_items: tuple[ContextItem, ...]
    answer_pack: AnswerPackSummary
    provenance: tuple[AnswerProvenanceRef, ...]
    lens_events: tuple[AiLensEvent, ...]
    represented_count: int
    unrepresented_count: int
    partial: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.context_items, tuple)
            or len(self.context_items) > MAX_CONTEXT_ITEMS
            or not all(isinstance(item, ContextItem) for item in self.context_items)
        ):
            raise UnifiedSourceIndexContextError("context_items must be typed and bounded")
        if not isinstance(self.answer_pack, AnswerPackSummary):
            raise UnifiedSourceIndexContextError("answer_pack must be typed")
        embedded_items = tuple(
            item for item in self.answer_pack.items if isinstance(item, ContextItem)
        )
        if embedded_items != self.context_items:
            raise UnifiedSourceIndexContextError("answer_pack must embed the projected context items")
        if not isinstance(self.provenance, tuple) or not all(
            isinstance(item, AnswerProvenanceRef) for item in self.provenance
        ):
            raise UnifiedSourceIndexContextError("provenance must be typed")
        if len({item.context_id for item in self.provenance}) != len(self.provenance):
            raise UnifiedSourceIndexContextError("provenance contains duplicate context ids")
        included_ids = {
            item.context_id
            for item in self.context_items
            if item.selection_state == "included"
        }
        if {item.context_id for item in self.provenance} != included_ids:
            raise UnifiedSourceIndexContextError("provenance must exactly cover included context")
        if (
            not isinstance(self.lens_events, tuple)
            or len(self.lens_events) != len(self.context_items) + 1
            or not all(isinstance(item, AiLensEvent) for item in self.lens_events)
        ):
            raise UnifiedSourceIndexContextError("lens_events must cover every item and pack")
        object.__setattr__(
            self,
            "represented_count",
            _integer(self.represented_count, "represented_count", 0, MAX_CONTEXT_ITEMS),
        )
        object.__setattr__(
            self,
            "unrepresented_count",
            _integer(
                self.unrepresented_count,
                "unrepresented_count",
                0,
                MAX_UNREPRESENTED_RESULTS,
            ),
        )
        if self.represented_count != len(self.context_items):
            raise UnifiedSourceIndexContextError("represented_count is inconsistent")
        if not isinstance(self.partial, bool):
            raise UnifiedSourceIndexContextError("partial must be boolean")


class UnifiedSourceIndexContextBridge:
    """Project already-selected USI results without reading source content."""

    def project(
        self,
        page: FederatedQueryPage,
        request: ContextProjectionRequest,
    ) -> ContextProjectionPage:
        if not isinstance(page, FederatedQueryPage):
            raise UnifiedSourceIndexContextError("page must be a federated query page")
        if not isinstance(request, ContextProjectionRequest):
            raise UnifiedSourceIndexContextError("request must be typed")

        represented = page.items[: request.budget.max_context_items]
        unrepresented_count = len(page.items) - len(represented)
        context_items: list[ContextItem] = []
        provenance: list[AnswerProvenanceRef] = []
        used_tokens = 0
        included_count = 0

        for result in represented:
            if result.evidence.policy_evidence.owner_scope != request.owner_scope:
                raise UnifiedSourceIndexContextError("query result crosses owner scope")
            token_estimate = _token_estimate(result, request.budget.max_tokens_per_item)
            state, exclusion_reason, policy_blocked = _selection_state(
                result,
                request,
                included_count=included_count,
                used_tokens=used_tokens,
                token_estimate=token_estimate,
            )
            context_id = _context_id(request.turn_ref, result.evidence)
            provenance_id = (
                _provenance_id(context_id, result.evidence) if state == "included" else None
            )
            item = _context_item(
                result,
                request,
                context_id=context_id,
                selection_state=state,
                exclusion_reason=exclusion_reason,
                policy_blocked=policy_blocked,
                token_estimate=token_estimate,
                provenance_id=provenance_id,
            )
            context_items.append(item)
            if state == "included":
                included_count += 1
                used_tokens += token_estimate
                assert provenance_id is not None
                provenance.append(_provenance(provenance_id, context_id, result))

        clipped = any(item.selection_state == "clipped" for item in context_items)
        truncated = bool(page.clipped or unrepresented_count or clipped)
        complete = not page.partial and not truncated
        pack = _answer_pack(
            tuple(context_items),
            page,
            request,
            used_tokens=used_tokens,
            complete=complete,
            truncated=truncated,
        )
        events = _lens_events(tuple(context_items), pack, request)
        partial = page.partial or truncated or any(
            item.selection_state != "included" for item in context_items
        )
        return ContextProjectionPage(
            tuple(context_items),
            pack,
            tuple(provenance),
            events,
            len(context_items),
            unrepresented_count,
            partial,
        )


def _selection_state(
    result: FederatedResultItem,
    request: ContextProjectionRequest,
    *,
    included_count: int,
    used_tokens: int,
    token_estimate: int,
) -> tuple[str, str | None, bool]:
    classification = result.evidence.policy_evidence.classification
    if request.security_mode == "normal" and classification in _RESTRICTED_NORMAL_MODE:
        return "blocked", "Blocked by context policy.", True
    if result.stale and not request.allow_stale:
        return "excluded", "Excluded because the result is stale.", False
    if (
        included_count >= request.budget.max_included_items
        or used_tokens + token_estimate > request.budget.max_tokens
    ):
        return "clipped", "Excluded by the context budget.", False
    return "included", None, False


def _context_item(
    result: FederatedResultItem,
    request: ContextProjectionRequest,
    *,
    context_id: str,
    selection_state: str,
    exclusion_reason: str | None,
    policy_blocked: bool,
    token_estimate: int,
    provenance_id: str | None,
) -> ContextItem:
    evidence = result.evidence
    classification = evidence.policy_evidence.classification.value
    confidence_level = (
        "high" if result.fused_score >= 0.8 else "medium" if result.fused_score >= 0.5 else "low"
    )
    freshness = {
        "state": "stale" if result.stale else "unknown",
        "observed_at": request.created_at,
        "source_updated_at": None,
        "age_seconds": None,
        "reason": (
            "The provider marked this result stale."
            if result.stale
            else "No source update timestamp was exposed."
        ),
    }
    evidence_refs = tuple(
        dict.fromkeys(
            value
            for value in (provenance_id, evidence.record_id, evidence.source_version_id)
            if value is not None
        )
    )
    return build_context_item_from_evidence(
        {
            "context_id": context_id,
            "created_at": request.created_at,
            "context_kind": "rag",
            "label": f"Indexed {evidence.record_kind.value} evidence",
            "source_ref": {
                "ref_type": "rag_chunk",
                "ref_id": evidence.source_id,
                "section_ref": evidence.record_id,
            },
            "selection_state": selection_state,
            "scope": request.scope,
            "reason_flags": ["semantic_match"],
            "evidence_refs": list(evidence_refs),
            "classification": classification,
            "redaction_state": "blocked" if policy_blocked else "metadata_only",
            "freshness": freshness,
            "confidence": {
                "level": confidence_level,
                "score": result.fused_score,
                "basis": "retrieval_score",
                "summary": "Combined bounded provider score.",
            },
            "pinned": False,
            "removable": True,
            "summary": "Selected from the Unified Source Index.",
            "redacted_preview": None,
            "exclusion_reason": exclusion_reason,
            "token_estimate": token_estimate,
            "source_revision_ref": evidence.source_version_id,
            "parent_context_id": None,
            "policy_blocked": policy_blocked,
            "source_disagreement": False,
            "secure_mode_boundary": False,
            "provider_boundary": False,
            "tool_boundary": False,
        }
    )


def _provenance(
    provenance_id: str,
    context_id: str,
    result: FederatedResultItem,
) -> AnswerProvenanceRef:
    provider_ids = tuple(score.provider_id for score in result.provider_scores)
    return AnswerProvenanceRef(
        provenance_id,
        context_id,
        result.evidence,
        provider_ids,
        result.fused_score,
    )


def _answer_pack(
    items: tuple[ContextItem, ...],
    page: FederatedQueryPage,
    request: ContextProjectionRequest,
    *,
    used_tokens: int,
    complete: bool,
    truncated: bool,
) -> AnswerPackSummary:
    excluded_items = []
    for item in items:
        if item.selection_state == "included":
            continue
        reason_code = (
            "policy"
            if item.selection_state == "blocked"
            else "stale"
            if item.freshness.state in {"stale", "expired"}
            else "budget"
        )
        excluded_items.append(
            {
                "context_id": item.context_id,
                "reason_code": reason_code,
                "reason_summary": item.exclusion_reason,
            }
        )
    classifications = [item.classification for item in items]
    classification = strongest_classification(classifications) if classifications else "public"
    observations = []
    if page.partial:
        observations.append("confidence_unknown")
    if any(item.freshness.state in {"stale", "expired"} for item in items):
        observations.append("freshness_stale")
    if any(item.selection_state == "blocked" for item in items):
        observations.append("classification_boundary")
    if not observations:
        observations.append("answer_pack_inspection")
    review = classify_review(observations)
    missing = tuple(
        outcome.provider_kind.value
        for outcome in page.outcomes
        if outcome.status is not ProviderStatus.COMPLETED
    )
    return AnswerPackSummary.create(
        pack_id=_stable_id(
            "pack",
            {"conversation_ref": request.conversation_ref, "turn_ref": request.turn_ref},
        ),
        conversation_ref=request.conversation_ref,
        turn_ref=request.turn_ref,
        phase="pre_generation",
        model_route={
            "model_ref": request.model_ref,
            "locality": request.model_locality,
            "security_mode": request.security_mode,
        },
        token_budget={
            "total": request.budget.max_tokens,
            "used": used_tokens,
            "remaining": request.budget.max_tokens - used_tokens,
            "unit": "tokens",
        },
        context_used_ratio=(
            used_tokens / request.budget.max_tokens if request.budget.max_tokens else 0.0
        ),
        items=[item.to_dict() for item in items],
        included_count=sum(item.selection_state == "included" for item in items),
        excluded_count=sum(item.selection_state in {"excluded", "blocked"} for item in items),
        clipped_count=sum(item.selection_state == "clipped" for item in items),
        stale_count=sum(item.freshness.state in {"stale", "expired"} for item in items),
        sensitive_count=sum(item.classification in {"sensitive", "secret"} for item in items),
        excluded_items=excluded_items,
        complete=complete,
        response_ref=None,
        missing_expected_source_types=list(dict.fromkeys(missing)),
        conflict_count=0,
        truncated=truncated,
        created_at=request.created_at,
        truth_level="runtime_trace",
        classification=classification,
        redaction_state="metadata_only",
        review=review.to_dict(),
    )


def _lens_events(
    items: tuple[ContextItem, ...],
    pack: AnswerPackSummary,
    request: ContextProjectionRequest,
) -> tuple[AiLensEvent, ...]:
    events = []
    sequence = request.first_event_sequence
    for item in items:
        projection = project_to_ai_lens(item)
        assert projection is not None
        status = (
            AiLensStatus.BLOCKED
            if item.selection_state == "blocked"
            else AiLensStatus.SKIPPED
            if item.selection_state in {"excluded", "clipped"}
            else AiLensStatus.WARNING
            if item.freshness.state in {"stale", "expired"}
            else AiLensStatus.SUCCEEDED
        )
        events.append(
            AiLensEvent.create(
                event_id=_stable_id(
                    "event",
                    {"context_id": item.context_id, "sequence": sequence},
                ),
                session_id=request.conversation_ref,
                turn_id=request.turn_ref,
                sequence=sequence,
                created_at=request.created_at,
                event_type=projection["event_type"],
                status=status,
                privacy_level=_privacy(item.classification),
                redaction_level=AiLensRedactionLevel.METADATA_ONLY,
                source_refs=(
                    AiLensSourceRef.create(
                        source_id=item.source_ref.ref_id,
                        kind=AiLensSourceKind.RAG,
                        redaction_level=AiLensRedactionLevel.METADATA_ONLY,
                    ),
                ),
                summary=_event_summary(item),
                payload={
                    **projection,
                    "source_version_ref": item.source_revision_ref,
                    "freshness_state": item.freshness.state,
                },
            )
        )
        sequence += 1
    pack_projection = project_to_ai_lens(pack)
    assert pack_projection is not None
    events.append(
        AiLensEvent.create(
            event_id=_stable_id("event", {"pack_id": pack.pack_id, "sequence": sequence}),
            session_id=request.conversation_ref,
            turn_id=request.turn_ref,
            sequence=sequence,
            created_at=request.created_at,
            event_type=AiLensEventType.CONTEXT_PACK_COMPOSED,
            status=AiLensStatus.SUCCEEDED if pack.complete else AiLensStatus.PARTIAL,
            privacy_level=_privacy(pack.classification),
            redaction_level=AiLensRedactionLevel.METADATA_ONLY,
            summary="Unified Source Index context pack composed.",
            payload=pack_projection,
        )
    )
    return tuple(events)


def _event_summary(item: ContextItem) -> str:
    if item.selection_state == "included":
        return "Unified Source Index context metadata selected."
    if item.selection_state == "blocked":
        return "Unified Source Index context metadata blocked by policy."
    if item.selection_state == "clipped":
        return "Unified Source Index context metadata clipped by budget."
    return "Unified Source Index context metadata excluded."


def _privacy(classification: str) -> AiLensPrivacyLevel:
    if classification == "public":
        return AiLensPrivacyLevel.METADATA
    if classification == "private":
        return AiLensPrivacyLevel.PRIVATE_METADATA
    return AiLensPrivacyLevel.SENSITIVE_METADATA


def _token_estimate(result: FederatedResultItem, maximum: int) -> int:
    # Only the length is observed; snippet content is never copied downstream.
    return min(maximum, max(1, (len(result.snippet) + 3) // 4))


def _context_id(turn_ref: str, evidence: EvidenceRef) -> str:
    return _stable_id(
        "ctx",
        {
            "turn_ref": turn_ref,
            "record_kind": evidence.record_kind.value,
            "record_id": evidence.record_id,
            "source_version_id": evidence.source_version_id,
        },
    )


def _provenance_id(context_id: str, evidence: EvidenceRef) -> str:
    return _stable_id(
        "prv",
        {"context_id": context_id, "evidence": evidence.to_dict()},
    )


def _stable_id(prefix: str, value: dict) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:32]}"


def _utc_timestamp(value: str) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise UnifiedSourceIndexContextError("created_at must be a bounded timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UnifiedSourceIndexContextError("created_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise UnifiedSourceIndexContextError("created_at must be UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _owner_scope(value: str) -> str:
    if (
        not isinstance(value, str)
        or not _OWNER_SCOPE_RE.fullmatch(value)
        or value.lower().endswith(":all")
    ):
        raise UnifiedSourceIndexContextError("owner_scope must be explicit and bounded")
    return value


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise UnifiedSourceIndexContextError(f"{field_name} must be a bounded identifier")
    return value


def _integer(value: int, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise UnifiedSourceIndexContextError(
            f"{field_name} must be between {minimum} and {maximum}"
        )
    return value


def _score(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnifiedSourceIndexContextError("fused_score must be numeric")
    normalized = round(float(value), 12)
    if not 0.0 <= normalized <= 1.0:
        raise UnifiedSourceIndexContextError("fused_score is outside its bound")
    return normalized


__all__ = [
    "AnswerProvenanceRef",
    "ContextProjectionBudget",
    "ContextProjectionPage",
    "ContextProjectionRequest",
    "UnifiedSourceIndexContextBridge",
    "UnifiedSourceIndexContextError",
]
