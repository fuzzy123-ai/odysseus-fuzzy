"""Fenced HWA authority adapter for bounded Temporal Light Activities.

Workflow payloads can identify only a pre-registered immutable slice.  They
cannot select a backend, command, path or provider, and every effect is bound
to the existing HWA authority store before execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from src.headless_write_agent_state import (
    AdmissionLimits,
    AuthorityScope,
    ClaimRecord,
    EffectRecord,
    HeadlessWriteAgentStateError,
    HeadlessWriteAgentStateStore,
)


ACTIVITY_TYPE_EXECUTE_SLICE = "execute_slice"
DEFAULT_LEASE_SECONDS = 90
_PAYLOAD_FIELDS = frozenset(
    {"agent_run_id", "manifest_hash", "node_id", "history_segment"}
)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_MANIFEST_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_KEY_PARTS = (
    "access_token",
    "api_key",
    "backend",
    "command",
    "credential",
    "password",
    "private",
    "provider",
    "raw_output",
    "secret",
)


class ActivityAuthorityError(RuntimeError):
    """Fail-closed adapter error with a stable reason code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class RegisteredActivitySpec:
    agent_run_id: str
    node_id: str
    manifest_hash: str
    scope: AuthorityScope
    backend_id: str
    claimant_ref: str
    claimed_paths: tuple[str, ...]
    hotfiles: tuple[str, ...]
    admission_limits: AdmissionLimits
    input_digest: str
    lease_seconds: int = DEFAULT_LEASE_SECONDS

    @classmethod
    def create(
        cls,
        *,
        agent_run_id: Any,
        node_id: Any,
        manifest_hash: Any,
        scope: AuthorityScope,
        backend_id: Any,
        claimant_ref: Any,
        claimed_paths: Iterable[Any],
        hotfiles: Iterable[Any] = (),
        admission_limits: AdmissionLimits,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> "RegisteredActivitySpec":
        run = _safe_id(agent_run_id, "agent_run_id")
        node = _safe_id(node_id, "node_id")
        manifest = str(manifest_hash or "")
        if not _MANIFEST_HASH_RE.fullmatch(manifest):
            _fail("invalid_manifest", "manifest_hash is not a sha256 reference")
        if not isinstance(scope, AuthorityScope) or scope.agent_run_id != run:
            _fail("scope_violation", "authority scope is not bound to agent_run_id")
        backend = _safe_id(backend_id, "backend_id")
        claimant = _safe_id(claimant_ref, "claimant_ref")
        if len(f"{run}:{node}:{ACTIVITY_TYPE_EXECUTE_SLICE}:1000") > 180:
            _fail("scope_violation", "agent_run_id and node_id exceed effect id budget")
        paths = _repo_paths(claimed_paths, "claimed_paths")
        hot = _repo_paths(hotfiles, "hotfiles", allow_empty=True)
        if not set(hot).issubset(paths):
            _fail("scope_violation", "hotfiles must be a subset of claimed_paths")
        if not isinstance(admission_limits, AdmissionLimits):
            _fail("scope_violation", "AdmissionLimits is required")
        if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 15 * 60:
            _fail("scope_violation", "lease_seconds must be 1 through 900")
        digest_payload = {
            "agent_run_id": run,
            "backend_id": backend,
            "claimed_paths": list(paths),
            "hotfiles": list(hot),
            "manifest_hash": manifest,
            "node_id": node,
            "scope_key": scope.key,
        }
        digest = "sha256:" + hashlib.sha256(
            _canonical_json(digest_payload).encode("utf-8")
        ).hexdigest()
        return cls(
            agent_run_id=run,
            node_id=node,
            manifest_hash=manifest,
            scope=scope,
            backend_id=backend,
            claimant_ref=claimant,
            claimed_paths=paths,
            hotfiles=hot,
            admission_limits=admission_limits,
            input_digest=digest,
            lease_seconds=lease_seconds,
        )


@dataclass(frozen=True, slots=True)
class AuthorizedActivity:
    spec: RegisteredActivitySpec
    claim: ClaimRecord
    effect: EffectRecord
    effect_id: str
    duplicate_result_ref: str | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_result_ref is not None


class ActivitySpecRegistry:
    """Immutable lookup of activity authority; runtime payloads cannot widen it."""

    def __init__(self, specs: Iterable[RegisteredActivitySpec]) -> None:
        resolved: dict[tuple[str, str], RegisteredActivitySpec] = {}
        for spec in specs:
            if not isinstance(spec, RegisteredActivitySpec):
                _fail("scope_violation", "registry contains an invalid spec")
            key = (spec.agent_run_id, spec.node_id)
            if key in resolved:
                _fail("claim_collision", "duplicate activity registration")
            resolved[key] = spec
        self._specs = MappingProxyType(resolved)

    def resolve(self, payload: Mapping[str, Any]) -> RegisteredActivitySpec:
        if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_FIELDS:
            _fail("scope_violation", "activity payload fields are not exact")
        _assert_safe_payload(payload)
        run = _safe_id(payload["agent_run_id"], "agent_run_id")
        node = _safe_id(payload["node_id"], "node_id")
        history_segment = payload["history_segment"]
        if (
            isinstance(history_segment, bool)
            or not isinstance(history_segment, int)
            or history_segment < 0
        ):
            _fail("scope_violation", "history_segment must be a non-negative integer")
        spec = self._specs.get((run, node))
        if spec is None:
            _fail("scope_violation", "activity is not registered")
        if payload["manifest_hash"] != spec.manifest_hash:
            _fail("invalid_manifest", "manifest hash differs from registered authority")
        return spec


class ActivityAuthorityAdapter:
    """Maps a registered Activity to HWA claims, fences and effect receipts."""

    def __init__(
        self,
        store: HeadlessWriteAgentStateStore,
        registry: ActivitySpecRegistry,
    ) -> None:
        if not isinstance(store, HeadlessWriteAgentStateStore):
            _fail("scope_violation", "HeadlessWriteAgentStateStore is required")
        if not isinstance(registry, ActivitySpecRegistry):
            _fail("scope_violation", "ActivitySpecRegistry is required")
        self.store = store
        self.registry = registry

    def authorize(
        self,
        payload: Mapping[str, Any],
        *,
        attempt: int,
    ) -> AuthorizedActivity:
        normalized_attempt = _attempt(attempt)
        spec = self.registry.resolve(payload)
        effect_id = (
            f"{spec.agent_run_id}:{spec.node_id}:{ACTIVITY_TYPE_EXECUTE_SLICE}:"
            f"{normalized_attempt}"
        )
        existing_effect = self.store.get_effect(effect_id)
        if existing_effect is not None and existing_effect.status == "succeeded":
            if existing_effect.scope != spec.scope or not existing_effect.result_ref:
                _fail("scope_violation", "terminal effect receipt has invalid binding")
            return AuthorizedActivity(
                spec=spec,
                claim=_synthetic_released_claim(spec, existing_effect),
                effect=existing_effect,
                effect_id=effect_id,
                duplicate_result_ref=existing_effect.result_ref,
            )
        if existing_effect is not None and existing_effect.status != "reserved":
            _fail("claim_collision", "duplicate delivery targets a terminal failed effect")

        claim_id = _claim_id(spec)
        claim = self.store.get_claim(spec.scope)
        if claim is not None and claim.state == "active" and claim.claim_id == claim_id:
            try:
                claim = self.store.renew_claim(
                    spec.scope,
                    claim_id=claim_id,
                    fence=claim.fence,
                    lease_seconds=spec.lease_seconds,
                )
            except HeadlessWriteAgentStateError as exc:
                if exc.code != "stale_fence":
                    raise
                claim = None
        else:
            claim = None
        if claim is None:
            claim = self.store.acquire_admitted_claim(
                spec.scope,
                claim_id=claim_id,
                claimant_ref=spec.claimant_ref,
                lease_seconds=spec.lease_seconds,
                claimed_paths=spec.claimed_paths,
                hotfiles=spec.hotfiles,
                limits=spec.admission_limits,
            )
        effect = self.store.reserve_effect(
            spec.scope,
            claim_id=claim.claim_id,
            fence=claim.fence,
            effect_id=effect_id,
            activity_type=ACTIVITY_TYPE_EXECUTE_SLICE,
            input_digest=spec.input_digest,
            attempt=normalized_attempt,
        )
        if existing_effect is not None and existing_effect.lease_fence == claim.fence:
            _fail("claim_collision", "effect is already reserved by the current fence")
        return AuthorizedActivity(
            spec=spec,
            claim=claim,
            effect=effect,
            effect_id=effect_id,
        )

    def heartbeat(self, authorized: AuthorizedActivity) -> ClaimRecord:
        claim = self.store.renew_claim(
            authorized.spec.scope,
            claim_id=authorized.claim.claim_id,
            fence=authorized.claim.fence,
            lease_seconds=authorized.spec.lease_seconds,
        )
        return self.store.record_progress(
            authorized.spec.scope,
            claim_id=claim.claim_id,
            fence=claim.fence,
        )

    def succeed(self, authorized: AuthorizedActivity) -> EffectRecord:
        result_ref = _result_ref(authorized)
        receipt = self.store.complete_effect(
            authorized.spec.scope,
            claim_id=authorized.claim.claim_id,
            fence=authorized.claim.fence,
            effect_id=authorized.effect_id,
            status="succeeded",
            result_ref=result_ref,
        )
        self.release(authorized)
        return receipt

    def fail(self, authorized: AuthorizedActivity, *, failure_code: str) -> EffectRecord:
        receipt = self.store.complete_effect(
            authorized.spec.scope,
            claim_id=authorized.claim.claim_id,
            fence=authorized.claim.fence,
            effect_id=authorized.effect_id,
            status="failed",
            failure_code=_safe_id(failure_code, "failure_code"),
        )
        self.release(authorized)
        return receipt

    def cancel(self, authorized: AuthorizedActivity) -> EffectRecord:
        receipt = self.store.complete_effect(
            authorized.spec.scope,
            claim_id=authorized.claim.claim_id,
            fence=authorized.claim.fence,
            effect_id=authorized.effect_id,
            status="cancelled",
            failure_code="cancelled_by_operator",
        )
        self.release(authorized)
        return receipt

    def release(self, authorized: AuthorizedActivity) -> ClaimRecord:
        return self.store.release_claim(
            authorized.spec.scope,
            claim_id=authorized.claim.claim_id,
            fence=authorized.claim.fence,
        )


def _claim_id(spec: RegisteredActivitySpec) -> str:
    value = f"{spec.scope.key}\0{spec.node_id}".encode("utf-8")
    return "tlr-claim-" + hashlib.sha256(value).hexdigest()[:32]


def _result_ref(authorized: AuthorizedActivity) -> str:
    value = f"{authorized.effect_id}\0{authorized.claim.fence}".encode("utf-8")
    return "tlr-receipt-" + hashlib.sha256(value).hexdigest()[:32]


def _synthetic_released_claim(
    spec: RegisteredActivitySpec, effect: EffectRecord
) -> ClaimRecord:
    return ClaimRecord(
        claim_id=effect.claim_id,
        scope=spec.scope,
        claimant_ref=spec.claimant_ref,
        fence=effect.lease_fence,
        state="released",
        acquired_at=effect.reserved_at,
        lease_expires_at=effect.completed_at or effect.reserved_at,
        last_heartbeat_at=effect.reserved_at,
        last_progress_at=effect.reserved_at,
        released_at=effect.completed_at,
    )


def _repo_paths(values: Iterable[Any], field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        _fail("scope_violation", f"{field} must be an iterable of paths")
    paths: set[str] = set()
    for raw in values:
        path = str(raw or "").strip().rstrip("/")
        if not path or "\\" in path or path.startswith("/") or ".." in path.split("/"):
            _fail("scope_violation", f"{field} contains an unsafe path")
        paths.add(path)
    if not paths and not allow_empty:
        _fail("scope_violation", f"{field} must not be empty")
    return tuple(sorted(paths))


def _safe_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(text) or ".." in text:
        _fail("scope_violation", f"{field} is invalid")
    return text


def _attempt(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000:
        _fail("scope_violation", "attempt must be 1 through 1000")
    return value


def _assert_safe_payload(value: Mapping[str, Any]) -> None:
    for key, item in value.items():
        lowered = str(key).lower()
        if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
            _fail("secret_detected", f"forbidden field: {key}")
        if isinstance(item, (Mapping, list, tuple)):
            _fail("scope_violation", "nested activity payload is forbidden")
        if isinstance(item, str) and len(item) > 512:
            _fail("scope_violation", "activity payload value is too large")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _fail(code: str, detail: str) -> None:
    raise ActivityAuthorityError(code, detail)
