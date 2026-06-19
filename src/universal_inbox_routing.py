"""Offline routing planner for the Universal Inbox.

The planner turns metadata and classification results into a safe placement
decision. It never copies, moves, deletes, or reads file contents.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import string
import unicodedata
from typing import Any, Mapping

from src.universal_inbox_policy import evaluate_universal_inbox_policy


DEFAULT_RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "universal_inbox_routing_rules.json"
ROUTING_SCHEMA = "odysseus.universal_inbox.routing_decision.v1"
_RULES_SCHEMA = "odysseus.universal_inbox.routing_rules.v1"
_HEX_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{32,128})$")
_UNSAFE_PATH_CHARS = set('<>:"|?*')


class UniversalInboxRoutingError(ValueError):
    """Raised when inbox routing rules or inputs are unsafe."""


@dataclass(frozen=True)
class UniversalInboxRouteRule:
    domain: str
    document_type: str
    target_template: str


@dataclass(frozen=True)
class UniversalInboxRoutingRules:
    schema: str
    version: int
    policy_name: str
    incoming_root: str
    review_root: str
    metadata_root: str
    documents_root: str
    min_auto_route_confidence: float
    copy_only: bool
    no_delete: bool
    no_overwrite: bool
    allowed_domains: tuple[str, ...]
    fallback_document_type: str
    review_triggers: tuple[str, ...]
    routes: tuple[UniversalInboxRouteRule, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "UniversalInboxRoutingRules":
        schema = str(payload.get("schema", "")).strip()
        if schema != _RULES_SCHEMA:
            raise UniversalInboxRoutingError(f"routing rules schema must be {_RULES_SCHEMA}")
        defaults = dict(payload.get("defaults") or {})
        routes = tuple(_route_from_dict(item) for item in payload.get("routes") or ())
        if not routes:
            raise UniversalInboxRoutingError("routing rules must define at least one route")

        allowed_domains = tuple(
            _normalize_token(value, field="allowed domain")
            for value in defaults.get("allowed_domains") or ()
        )
        if not allowed_domains:
            raise UniversalInboxRoutingError("routing rules must define allowed domains")

        rules = cls(
            schema=schema,
            version=int(payload.get("version", 1)),
            policy_name=_normalize_token(payload.get("policy_name", "default"), field="policy name"),
            incoming_root=_normalize_relative_path(defaults.get("incoming_root", "")),
            review_root=_normalize_relative_path(defaults.get("review_root", "")),
            metadata_root=_normalize_relative_path(defaults.get("metadata_root", "")),
            documents_root=_normalize_relative_path(defaults.get("documents_root", "")),
            min_auto_route_confidence=_normalize_confidence(
                defaults.get("min_auto_route_confidence", 0.82)
            ),
            copy_only=bool(defaults.get("copy_only", True)),
            no_delete=bool(defaults.get("no_delete", True)),
            no_overwrite=bool(defaults.get("no_overwrite", True)),
            allowed_domains=allowed_domains,
            fallback_document_type=_normalize_token(
                defaults.get("fallback_document_type", "reference"),
                field="fallback document type",
            ),
            review_triggers=tuple(
                _normalize_token(value, field="review trigger")
                for value in payload.get("review_triggers") or ()
            ),
            routes=routes,
        )
        if not rules.copy_only or not rules.no_delete or not rules.no_overwrite:
            raise UniversalInboxRoutingError("mvp routing rules must be copy-only/no-delete/no-overwrite")
        for route in rules.routes:
            if route.domain not in rules.allowed_domains:
                raise UniversalInboxRoutingError("route domain must be listed in allowed domains")
            _validate_template(route.target_template)
        return rules

    def find_route(self, domain: str, document_type: str) -> UniversalInboxRouteRule | None:
        for route in self.routes:
            if route.domain == domain and route.document_type == document_type:
                return route
        return None


@dataclass(frozen=True)
class UniversalInboxItem:
    original_path: str
    filename: str = ""
    domain: str = ""
    document_type: str = ""
    title: str = ""
    project: str = ""
    confidence: float = 0.0
    source_hash: str = ""
    observed_at: str = ""
    year: int | None = None
    sensitive: bool = False
    secret_detected: bool = False
    duplicate: bool = False
    partial_extraction: bool = False
    target_conflict: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "UniversalInboxItem":
        return cls(
            original_path=str(payload.get("original_path") or payload.get("path") or ""),
            filename=str(payload.get("filename") or ""),
            domain=str(payload.get("domain") or ""),
            document_type=str(payload.get("document_type") or ""),
            title=str(payload.get("title") or payload.get("suggested_title") or ""),
            project=str(payload.get("project") or ""),
            confidence=payload.get("confidence", payload.get("routing_confidence", 0.0)),
            source_hash=str(payload.get("source_hash") or payload.get("sha256") or ""),
            observed_at=str(payload.get("observed_at") or payload.get("mtime") or ""),
            year=payload.get("year"),
            sensitive=bool(payload.get("sensitive", False)),
            secret_detected=bool(payload.get("secret_detected", False)),
            duplicate=bool(payload.get("duplicate", False)),
            partial_extraction=bool(payload.get("partial_extraction", False)),
            target_conflict=bool(payload.get("target_conflict", False)),
        )


@dataclass(frozen=True)
class UniversalInboxRoutingDecision:
    schema: str
    status: str
    decision: str
    reason: str
    confidence: float
    domain: str
    document_type: str
    original_path: str
    target_path: str
    review_path: str
    sidecar_path: str
    safe_operation: str
    ledger_status: str
    routing_policy: str
    review_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    copy_only: bool = True
    delete_original: bool = False
    overwrite_existing: bool = False
    private_content_visible: bool = False
    secret_values_visible: bool = False
    raptorgraph_event: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "decision": self.decision,
            "reason": self.reason,
            "confidence": self.confidence,
            "domain": self.domain,
            "document_type": self.document_type,
            "original_path": self.original_path,
            "target_path": self.target_path,
            "review_path": self.review_path,
            "sidecar_path": self.sidecar_path,
            "safe_operation": self.safe_operation,
            "ledger_status": self.ledger_status,
            "routing_policy": self.routing_policy,
            "review_reasons": self.review_reasons,
            "warnings": self.warnings,
            "copy_only": self.copy_only,
            "delete_original": self.delete_original,
            "overwrite_existing": self.overwrite_existing,
            "private_content_visible": self.private_content_visible,
            "secret_values_visible": self.secret_values_visible,
            "raptorgraph_event": dict(self.raptorgraph_event or {}),
        }


def load_universal_inbox_routing_rules(
    path: str | Path = DEFAULT_RULES_PATH,
) -> UniversalInboxRoutingRules:
    """Load and validate the file-backed routing rules."""

    with Path(path).open("r", encoding="utf-8") as handle:
        return UniversalInboxRoutingRules.from_dict(json.load(handle))


def plan_universal_inbox_route(
    item: UniversalInboxItem | Mapping[str, Any],
    *,
    rules: UniversalInboxRoutingRules | Mapping[str, Any] | None = None,
) -> UniversalInboxRoutingDecision:
    """Return a safe routing decision without mutating any file or remote state."""

    normalized_rules = _coerce_rules(rules)
    normalized_item = item if isinstance(item, UniversalInboxItem) else UniversalInboxItem.from_dict(item)
    original_path = _normalize_relative_path(normalized_item.original_path)
    filename = _filename_for_item(normalized_item, original_path)
    ext = _safe_extension(filename)
    title = normalized_item.title.strip() or Path(filename).stem or "document"
    domain = _normalize_token(normalized_item.domain, field="domain") if normalized_item.domain else ""
    document_type = (
        _normalize_token(normalized_item.document_type, field="document type")
        if normalized_item.document_type
        else ""
    )
    confidence = _normalize_confidence(normalized_item.confidence)

    policy = evaluate_universal_inbox_policy(
        normalized_item,
        allowed_domains=normalized_rules.allowed_domains,
        min_auto_route_confidence=normalized_rules.min_auto_route_confidence,
        domain=domain,
        document_type=document_type,
        confidence=confidence,
    )
    review_reasons = policy.review_reasons + policy.no_go_reasons
    sidecar_path = _sidecar_path(normalized_rules, normalized_item, title)
    route = normalized_rules.find_route(domain, document_type) if not review_reasons else None

    if route is None and not review_reasons:
        review_reasons = ("unknown_document_type",)

    if review_reasons:
        review_path = _join_path(normalized_rules.review_root, filename)
        reason = "review_required:" + ",".join(review_reasons)
        return _decision(
            normalized_rules,
            normalized_item,
            status="needs_review",
            decision="copy_to_review",
            reason=reason,
            confidence=confidence,
            domain=domain or "unknown",
            document_type=document_type or "unknown",
            original_path=original_path,
            target_path=review_path,
            review_path=review_path,
            sidecar_path=sidecar_path,
            ledger_status="needs_review",
            review_reasons=review_reasons,
            warnings=review_reasons,
        )

    assert route is not None
    target_path = _render_target_path(route.target_template, normalized_item, title, ext)
    return _decision(
        normalized_rules,
        normalized_item,
        status="routed",
        decision="copy_to_target",
        reason="matched_domain_and_document_type",
        confidence=confidence,
        domain=domain,
        document_type=document_type,
        original_path=original_path,
        target_path=target_path,
        review_path="",
        sidecar_path=sidecar_path,
        ledger_status="routed",
        review_reasons=(),
        warnings=(),
    )


def _coerce_rules(
    rules: UniversalInboxRoutingRules | Mapping[str, Any] | None,
) -> UniversalInboxRoutingRules:
    if rules is None:
        return load_universal_inbox_routing_rules()
    if isinstance(rules, UniversalInboxRoutingRules):
        return rules
    if isinstance(rules, Mapping):
        return UniversalInboxRoutingRules.from_dict(rules)
    raise TypeError("rules must be UniversalInboxRoutingRules, mapping, or None")


def _route_from_dict(payload: Mapping[str, Any]) -> UniversalInboxRouteRule:
    return UniversalInboxRouteRule(
        domain=_normalize_token(payload.get("domain", ""), field="route domain"),
        document_type=_normalize_token(payload.get("document_type", ""), field="route document type"),
        target_template=str(payload.get("target_template", "")).strip(),
    )


def _review_reasons(
    item: UniversalInboxItem,
    rules: UniversalInboxRoutingRules,
    domain: str,
    document_type: str,
    confidence: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if item.duplicate:
        reasons.append("duplicate")
    if item.partial_extraction:
        reasons.append("partial_extraction")
    if item.secret_detected:
        reasons.append("secret_detected")
    if item.sensitive:
        reasons.append("sensitive")
    if item.target_conflict:
        reasons.append("target_conflict")
    if domain not in rules.allowed_domains:
        reasons.append("unknown_domain")
    if not document_type:
        reasons.append("unknown_document_type")
    if confidence < rules.min_auto_route_confidence:
        reasons.append("low_confidence")
    return tuple(dict.fromkeys(reasons))


def _decision(
    rules: UniversalInboxRoutingRules,
    item: UniversalInboxItem,
    *,
    status: str,
    decision: str,
    reason: str,
    confidence: float,
    domain: str,
    document_type: str,
    original_path: str,
    target_path: str,
    review_path: str,
    sidecar_path: str,
    ledger_status: str,
    review_reasons: tuple[str, ...],
    warnings: tuple[str, ...],
) -> UniversalInboxRoutingDecision:
    return UniversalInboxRoutingDecision(
        schema=ROUTING_SCHEMA,
        status=status,
        decision=decision,
        reason=reason,
        confidence=confidence,
        domain=domain,
        document_type=document_type,
        original_path=original_path,
        target_path=target_path,
        review_path=review_path,
        sidecar_path=sidecar_path,
        safe_operation="copy",
        ledger_status=ledger_status,
        routing_policy=f"{rules.policy_name}:v{rules.version}",
        review_reasons=review_reasons,
        warnings=warnings,
        raptorgraph_event=_raptorgraph_event(
            rules,
            item,
            status=status,
            domain=domain,
            document_type=document_type,
            original_path=original_path,
            target_path=target_path,
            confidence=confidence,
            review_reasons=review_reasons,
        ),
    )


def _raptorgraph_event(
    rules: UniversalInboxRoutingRules,
    item: UniversalInboxItem,
    *,
    status: str,
    domain: str,
    document_type: str,
    original_path: str,
    target_path: str,
    confidence: float,
    review_reasons: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "event": "document_routing_planned",
        "source_provider": "nextcloud_inbox",
        "permission_scope": "no_delete:copy_only:no_overwrite",
        "source_hash": item.source_hash,
        "original_path": original_path,
        "planned_path": target_path,
        "domain": domain,
        "document_type": document_type,
        "title": item.title.strip(),
        "confidence": confidence,
        "status": status,
        "review_reasons": review_reasons,
        "routing_policy": f"{rules.policy_name}:v{rules.version}",
    }


def _render_target_path(
    template: str,
    item: UniversalInboxItem,
    title: str,
    ext: str,
) -> str:
    values = {
        "year": str(_year_for_item(item)),
        "safe_title": _slug(title),
        "ext": ext,
        "project": _slug(item.project or "unassigned"),
        "document_type": _normalize_token(item.document_type or "reference", field="document type"),
    }
    try:
        rendered = template.format(**values)
    except KeyError as exc:
        raise UniversalInboxRoutingError(f"unknown routing template placeholder: {exc}") from exc
    return _normalize_relative_path(rendered)


def _sidecar_path(
    rules: UniversalInboxRoutingRules,
    item: UniversalInboxItem,
    title: str,
) -> str:
    match = _HEX_RE.fullmatch(item.source_hash.strip())
    sidecar_id = match.group(1).lower() if match else _slug(title)
    return _join_path(rules.metadata_root, f"{sidecar_id}.odysseus.json")


def _filename_for_item(item: UniversalInboxItem, original_path: str) -> str:
    filename = item.filename.strip() or original_path.rsplit("/", 1)[-1]
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename or filename in {".", ".."}:
        raise UniversalInboxRoutingError("filename is required")
    if any(ord(ch) < 32 for ch in filename):
        raise UniversalInboxRoutingError("filename contains control characters")
    return filename


def _safe_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if not suffix:
        return ""
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
        return ""
    return suffix


def _year_for_item(item: UniversalInboxItem) -> int:
    if item.year is not None:
        year = int(item.year)
        if year < 1970 or year > 9999:
            raise UniversalInboxRoutingError("year must be between 1970 and 9999")
        return year
    if item.observed_at:
        try:
            return datetime.fromisoformat(item.observed_at.replace("Z", "+00:00")).year
        except ValueError:
            pass
    return datetime.now(timezone.utc).year


def _join_path(*parts: str) -> str:
    return _normalize_relative_path("/".join(part.strip("/") for part in parts if part))


def _validate_template(template: str) -> None:
    if not template:
        raise UniversalInboxRoutingError("route target_template is required")
    fields = [field_name for _, field_name, _, _ in string.Formatter().parse(template) if field_name]
    allowed_fields = {"year", "safe_title", "ext", "project", "document_type"}
    unknown = sorted(set(fields) - allowed_fields)
    if unknown:
        raise UniversalInboxRoutingError(f"unknown routing template placeholders: {unknown}")
    probe = template.format(
        year="2026",
        safe_title="example",
        ext=".pdf",
        project="project",
        document_type="reference",
    )
    _normalize_relative_path(probe)


def _normalize_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise UniversalInboxRoutingError("path is required")
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", raw):
        raise UniversalInboxRoutingError("path must be relative")
    parts = [part.strip() for part in raw.split("/") if part.strip() and part.strip() != "."]
    if not parts or any(part == ".." for part in parts):
        raise UniversalInboxRoutingError("path must not contain traversal segments")
    for part in parts:
        if any(ord(ch) < 32 for ch in part):
            raise UniversalInboxRoutingError("path contains control characters")
        if any(ch in _UNSAFE_PATH_CHARS for ch in part):
            raise UniversalInboxRoutingError("path contains unsafe segment")
    return "/".join(parts)


def _normalize_token(value: Any, *, field: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", token):
        raise UniversalInboxRoutingError(f"{field} must be a safe token")
    return token


def _normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        raise UniversalInboxRoutingError("confidence must be numeric") from None
    if confidence < 0 or confidence > 1:
        raise UniversalInboxRoutingError("confidence must be between 0 and 1")
    return confidence


def _slug(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug[:96].strip("-") or "document"
