"""Durable owner-scoped authority state for headless write-agent execution.

The store uses short SQLite transactions for compare-and-set semantics across
independent coordinator instances.  It owns records only: it starts no thread,
scheduler, worker, Git command, provider call, or other external effect.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterator, Mapping

from src.headless_write_agent_pipeline import (
    ApprovalCapability,
    HeadlessCommitEvidence,
)


SCHEMA_VERSION = 1
MAX_LEASE_SECONDS = 15 * 60
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_EFFECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,179}$")
_ABSOLUTE_WINDOWS_RE = re.compile(r"^[A-Za-z]:[/\\]")
_SAFE_REPO_PATH_RE = re.compile(r"^[A-Za-z0-9._/ -]{1,512}$")
_FORBIDDEN_FIELD_PARTS = (
    "access_token",
    "api_key",
    "credential",
    "password",
    "private_content",
    "private_key",
    "provider_response",
    "raw_output",
    "secret",
)


class HeadlessWriteAgentStateError(RuntimeError):
    """Fail-closed store error with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class AuthorityScope:
    owner_id: str
    repo_id: str
    task_id: str
    plan_id: str
    slice_id: str
    agent_run_id: str

    @classmethod
    def create(
        cls,
        *,
        owner_id: Any,
        repo_id: Any,
        task_id: Any,
        plan_id: Any,
        slice_id: Any,
        agent_run_id: Any,
    ) -> "AuthorityScope":
        return cls(
            owner_id=_opaque_id(owner_id, "owner_id"),
            repo_id=_opaque_id(repo_id, "repo_id"),
            task_id=_opaque_id(task_id, "task_id"),
            plan_id=_opaque_id(plan_id, "plan_id"),
            slice_id=_opaque_id(slice_id, "slice_id"),
            agent_run_id=_opaque_id(agent_run_id, "agent_run_id"),
        )

    @classmethod
    def from_capability(cls, value: ApprovalCapability) -> "AuthorityScope":
        return cls.create(
            owner_id=value.owner_id,
            repo_id=value.repo_id,
            task_id=value.task_id,
            plan_id=value.plan_id,
            slice_id=value.slice_id,
            agent_run_id=value.agent_run_id,
        )

    @classmethod
    def from_evidence(cls, value: HeadlessCommitEvidence) -> "AuthorityScope":
        return cls.create(
            owner_id=value.owner_id,
            repo_id=value.repo_id,
            task_id=value.task_id,
            plan_id=value.plan_id,
            slice_id=value.slice_id,
            agent_run_id=value.agent_run_id,
        )

    @property
    def key(self) -> str:
        encoded = _canonical_json(asdict(self)).encode("utf-8")
        return "hwa_scope_" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    claim_id: str
    scope: AuthorityScope
    claimant_ref: str
    fence: int
    state: str
    acquired_at: str
    lease_expires_at: str
    last_heartbeat_at: str
    last_progress_at: str
    released_at: str | None


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    capability_id: str
    nonce: str
    scope: AuthorityScope
    stage: str
    input_digest: str
    policy_version: str
    lease_fence: int
    max_attempts: int
    issued_at: str
    expires_at: str
    status: str
    reservation_id: str | None
    reserved_at: str | None
    consumed_at: str | None


@dataclass(frozen=True, slots=True)
class ControlRecord:
    level: str
    owner_id: str
    repo_id: str | None
    agent_run_id: str | None
    paused: bool
    killed: bool
    version: int
    reason_ref: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class EffectRecord:
    effect_id: str
    scope: AuthorityScope
    claim_id: str
    activity_type: str
    input_digest: str
    attempt: int
    lease_fence: int
    status: str
    reserved_at: str
    completed_at: str | None
    result_ref: str | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class AdmissionLimits:
    max_global_active: int
    max_owner_active: int
    max_project_active: int
    max_agent_active: int

    @classmethod
    def create(
        cls,
        *,
        max_global_active: Any,
        max_owner_active: Any,
        max_project_active: Any,
        max_agent_active: Any,
    ) -> "AdmissionLimits":
        values = {
            "max_global_active": _bounded_limit(max_global_active, "max_global_active"),
            "max_owner_active": _bounded_limit(max_owner_active, "max_owner_active"),
            "max_project_active": _bounded_limit(max_project_active, "max_project_active"),
            "max_agent_active": _bounded_limit(max_agent_active, "max_agent_active"),
        }
        if values["max_owner_active"] > values["max_global_active"]:
            _fail("invalid_admission_limit", "owner limit exceeds global limit")
        if values["max_project_active"] > values["max_owner_active"]:
            _fail("invalid_admission_limit", "project limit exceeds owner limit")
        if values["max_agent_active"] > values["max_global_active"]:
            _fail("invalid_admission_limit", "agent limit exceeds global limit")
        return cls(**values)


Clock = Callable[[], datetime]


class HeadlessWriteAgentStateStore:
    """Transactional authority store; each method opens one bounded connection."""

    def __init__(self, path: str | Path, *, clock: Clock | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def acquire_claim(
        self,
        scope: AuthorityScope,
        *,
        claim_id: str,
        claimant_ref: str,
        lease_seconds: int,
    ) -> ClaimRecord:
        _require_scope(scope)
        normalized_claim = _effect_id(claim_id, "claim_id")
        claimant = _opaque_id(claimant_ref, "claimant_ref")
        duration = _lease_seconds(lease_seconds)
        now = self._now()
        with self._write() as connection:
            self._assert_effect_allowed(connection, scope)
            if connection.execute(
                "SELECT 1 FROM hwa_claim_paths WHERE scope_key = ? LIMIT 1", (scope.key,)
            ).fetchone() is not None:
                _fail(
                    "admission_required",
                    "a scope previously admitted with paths must be reclaimed through HWA3B",
                )
            return self._acquire_claim_locked(
                connection,
                scope,
                claim_id=normalized_claim,
                claimant_ref=claimant,
                lease_seconds=duration,
                now=now,
            )

    def acquire_admitted_claim(
        self,
        scope: AuthorityScope,
        *,
        claim_id: str,
        claimant_ref: str,
        lease_seconds: int,
        claimed_paths: tuple[str, ...],
        hotfiles: tuple[str, ...],
        limits: AdmissionLimits,
    ) -> ClaimRecord:
        """Atomically enforce quotas/path locks and acquire the HWA3A claim."""

        _require_scope(scope)
        if not isinstance(limits, AdmissionLimits):
            _fail("invalid_admission_limit", "AdmissionLimits is required")
        normalized_claim = _effect_id(claim_id, "claim_id")
        claimant = _opaque_id(claimant_ref, "claimant_ref")
        duration = _lease_seconds(lease_seconds)
        paths = _repo_paths(claimed_paths, "claimed_paths")
        hot = _repo_paths(hotfiles, "hotfiles", allow_empty=True)
        if not set(hot).issubset(paths):
            _fail("invalid_hotfile", "hotfiles must be a subset of claimed_paths")
        now = self._now()
        with self._write() as connection:
            self._assert_effect_allowed(connection, scope)
            self._assert_admission_capacity(
                connection,
                scope,
                claimant_ref=claimant,
                paths=paths,
                hotfiles=hot,
                limits=limits,
                now=now,
            )
            claim = self._acquire_claim_locked(
                connection,
                scope,
                claim_id=normalized_claim,
                claimant_ref=claimant,
                lease_seconds=duration,
                now=now,
            )
            connection.execute("DELETE FROM hwa_claim_paths WHERE scope_key = ?", (scope.key,))
            connection.executemany(
                """
                INSERT INTO hwa_claim_paths(scope_key, owner_id, repo_id, path, is_hotfile)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (scope.key, scope.owner_id, scope.repo_id, path, int(path in hot))
                    for path in paths
                ],
            )
            return claim

    def renew_claim(
        self,
        scope: AuthorityScope,
        *,
        claim_id: str,
        fence: int,
        lease_seconds: int,
    ) -> ClaimRecord:
        duration = _lease_seconds(lease_seconds)
        now = self._now()
        with self._write() as connection:
            self._assert_current_claim(connection, scope, claim_id, fence, now=now)
            connection.execute(
                """
                UPDATE hwa_claims
                SET lease_expires_at = ?, last_heartbeat_at = ?
                WHERE scope_key = ?
                """,
                (_format_time(now + timedelta(seconds=duration)), _format_time(now), scope.key),
            )
            return self._claim_from_row(
                connection.execute(
                    "SELECT * FROM hwa_claims WHERE scope_key = ?", (scope.key,)
                ).fetchone()
            )

    def record_progress(
        self,
        scope: AuthorityScope,
        *,
        claim_id: str,
        fence: int,
    ) -> ClaimRecord:
        now = self._now()
        with self._write() as connection:
            self._assert_current_claim(connection, scope, claim_id, fence, now=now)
            connection.execute(
                "UPDATE hwa_claims SET last_progress_at = ? WHERE scope_key = ?",
                (_format_time(now), scope.key),
            )
            return self._claim_from_row(
                connection.execute(
                    "SELECT * FROM hwa_claims WHERE scope_key = ?", (scope.key,)
                ).fetchone()
            )

    def release_claim(
        self,
        scope: AuthorityScope,
        *,
        claim_id: str,
        fence: int,
    ) -> ClaimRecord:
        now = self._now()
        with self._write() as connection:
            self._assert_current_claim(connection, scope, claim_id, fence, now=now)
            connection.execute(
                """
                UPDATE hwa_claims
                SET state = 'released', released_at = ?
                WHERE scope_key = ?
                """,
                (_format_time(now), scope.key),
            )
            return self._claim_from_row(
                connection.execute(
                    "SELECT * FROM hwa_claims WHERE scope_key = ?", (scope.key,)
                ).fetchone()
            )

    def get_claim(self, scope: AuthorityScope) -> ClaimRecord | None:
        _require_scope(scope)
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM hwa_claims WHERE scope_key = ?", (scope.key,)
            ).fetchone()
            return self._claim_from_row(row) if row is not None else None

    def get_effect(self, effect_id: str) -> EffectRecord | None:
        normalized_effect = _effect_id(effect_id, "effect_id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM hwa_effect_receipts WHERE effect_id = ?",
                (normalized_effect,),
            ).fetchone()
            return self._effect_from_row(row) if row is not None else None

    def reserve_effect(
        self,
        scope: AuthorityScope,
        *,
        claim_id: str,
        fence: int,
        effect_id: str,
        activity_type: str,
        input_digest: str,
        attempt: int,
    ) -> EffectRecord:
        """Reserve one idempotent effect under the current fenced claim.

        A timed-out reservation may be recovered only by a later current fence.
        Terminal receipts are immutable and returned for exact duplicate delivery.
        """

        normalized_claim = _effect_id(claim_id, "claim_id")
        normalized_effect = _effect_id(effect_id, "effect_id")
        normalized_type = _opaque_id(activity_type, "activity_type")
        normalized_digest = _effect_id(input_digest, "input_digest")
        normalized_attempt = _positive_attempt(attempt)
        now = self._now()
        with self._write() as connection:
            self._assert_effect_allowed(connection, scope)
            self._assert_current_claim(
                connection,
                scope,
                normalized_claim,
                fence,
                now=now,
            )
            existing = connection.execute(
                "SELECT * FROM hwa_effect_receipts WHERE effect_id = ?",
                (normalized_effect,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["scope_key"] != scope.key
                    or existing["activity_type"] != normalized_type
                    or existing["input_digest"] != normalized_digest
                    or int(existing["attempt"]) != normalized_attempt
                ):
                    _fail("effect_conflict", "effect identity was rebound")
                existing_fence = int(existing["lease_fence"])
                if existing["status"] != "reserved":
                    return self._effect_from_row(existing)
                if existing_fence > fence:
                    _fail("stale_fence", "effect is reserved by a later fence")
                if existing_fence < fence:
                    connection.execute(
                        """
                        UPDATE hwa_effect_receipts
                        SET claim_id = ?, lease_fence = ?, reserved_at = ?
                        WHERE effect_id = ?
                        """,
                        (
                            normalized_claim,
                            _positive_fence(fence),
                            _format_time(now),
                            normalized_effect,
                        ),
                    )
                return self._effect_from_row(
                    connection.execute(
                        "SELECT * FROM hwa_effect_receipts WHERE effect_id = ?",
                        (normalized_effect,),
                    ).fetchone()
                )
            connection.execute(
                """
                INSERT INTO hwa_effect_receipts(
                    effect_id, scope_key, owner_id, repo_id, task_id, plan_id,
                    slice_id, agent_run_id, claim_id, activity_type,
                    input_digest, attempt, lease_fence, status, reserved_at,
                    completed_at, result_ref, failure_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, NULL, NULL, NULL)
                """,
                (
                    normalized_effect,
                    scope.key,
                    *self._scope_values(scope),
                    normalized_claim,
                    normalized_type,
                    normalized_digest,
                    normalized_attempt,
                    _positive_fence(fence),
                    _format_time(now),
                ),
            )
            return self._effect_from_row(
                connection.execute(
                    "SELECT * FROM hwa_effect_receipts WHERE effect_id = ?",
                    (normalized_effect,),
                ).fetchone()
            )

    def complete_effect(
        self,
        scope: AuthorityScope,
        *,
        claim_id: str,
        fence: int,
        effect_id: str,
        status: str,
        result_ref: str | None = None,
        failure_code: str | None = None,
    ) -> EffectRecord:
        """Persist a terminal receipt only while the completing fence is current."""

        normalized_claim = _effect_id(claim_id, "claim_id")
        normalized_effect = _effect_id(effect_id, "effect_id")
        normalized_status = _literal(
            status, ("succeeded", "failed", "cancelled"), "effect_status"
        )
        normalized_result = (
            _opaque_id(result_ref, "result_ref") if result_ref is not None else None
        )
        normalized_failure = (
            _opaque_id(failure_code, "failure_code") if failure_code is not None else None
        )
        if normalized_status == "succeeded" and normalized_result is None:
            _fail("invalid_effect_receipt", "succeeded effect requires result_ref")
        if normalized_status != "succeeded" and normalized_failure is None:
            _fail("invalid_effect_receipt", "failed or cancelled effect requires failure_code")
        now = self._now()
        with self._write() as connection:
            self._assert_current_claim(
                connection,
                scope,
                normalized_claim,
                fence,
                now=now,
            )
            row = connection.execute(
                "SELECT * FROM hwa_effect_receipts WHERE effect_id = ?",
                (normalized_effect,),
            ).fetchone()
            if row is None:
                _fail("effect_missing", "effect must be reserved before completion")
            if row["scope_key"] != scope.key:
                _fail("effect_conflict", "effect belongs to another scope")
            if row["status"] != "reserved":
                if (
                    row["status"] == normalized_status
                    and row["result_ref"] == normalized_result
                    and row["failure_code"] == normalized_failure
                ):
                    return self._effect_from_row(row)
                _fail("effect_terminal", "terminal effect receipt is immutable")
            if row["claim_id"] != normalized_claim or int(row["lease_fence"]) != fence:
                _fail("stale_fence", "effect reservation belongs to another fence")
            connection.execute(
                """
                UPDATE hwa_effect_receipts
                SET status = ?, completed_at = ?, result_ref = ?, failure_code = ?
                WHERE effect_id = ?
                """,
                (
                    normalized_status,
                    _format_time(now),
                    normalized_result,
                    normalized_failure,
                    normalized_effect,
                ),
            )
            return self._effect_from_row(
                connection.execute(
                    "SELECT * FROM hwa_effect_receipts WHERE effect_id = ?",
                    (normalized_effect,),
                ).fetchone()
            )

    def issue_capability(self, capability: ApprovalCapability) -> CapabilityRecord:
        if not isinstance(capability, ApprovalCapability):
            _fail("invalid_capability", "ApprovalCapability is required")
        scope = AuthorityScope.from_capability(capability)
        now = self._now()
        issued = _parse_time(capability.issued_at)
        expires = _parse_time(capability.expires_at)
        if now < issued or now >= expires:
            _fail("capability_inactive", "capability is not active at issuance")
        payload = _capability_payload(capability)
        _assert_safe_payload(payload)
        with self._write() as connection:
            self._assert_effect_allowed(connection, scope)
            self._assert_current_fence(connection, scope, capability.lease_fence, now=now)
            existing = connection.execute(
                "SELECT payload_json FROM hwa_capabilities WHERE capability_id = ? OR nonce = ?",
                (capability.capability_id, capability.nonce),
            ).fetchone()
            encoded = _canonical_json(payload)
            if existing is not None:
                if existing["payload_json"] != encoded:
                    _fail("capability_conflict", "capability id or nonce was reused")
                return self._capability_from_row(
                    connection.execute(
                        "SELECT * FROM hwa_capabilities WHERE capability_id = ?",
                        (capability.capability_id,),
                    ).fetchone()
                )
            connection.execute(
                """
                INSERT INTO hwa_capabilities(
                    capability_id, nonce, scope_key, owner_id, repo_id, task_id,
                    plan_id, slice_id, agent_run_id, stage, input_digest,
                    policy_version, lease_fence, max_attempts, issued_at,
                    expires_at, status, reservation_id, reserved_at,
                    consumed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'issued', NULL, NULL, NULL, ?)
                """,
                (
                    capability.capability_id,
                    capability.nonce,
                    scope.key,
                    *self._scope_values(scope),
                    capability.stage.value,
                    capability.input_digest,
                    capability.policy_version,
                    capability.lease_fence,
                    capability.max_attempts,
                    capability.issued_at,
                    capability.expires_at,
                    encoded,
                ),
            )
            return self._capability_from_row(
                connection.execute(
                    "SELECT * FROM hwa_capabilities WHERE capability_id = ?",
                    (capability.capability_id,),
                ).fetchone()
            )

    def reserve_capability(
        self,
        *,
        capability_id: str,
        nonce: str,
        scope: AuthorityScope,
        stage: str,
        input_digest: str,
        reservation_id: str,
    ) -> CapabilityRecord:
        capability_ref = _effect_id(capability_id, "capability_id")
        nonce_ref = _effect_id(nonce, "nonce")
        reservation = _effect_id(reservation_id, "reservation_id")
        now = self._now()
        with self._write() as connection:
            self._assert_effect_allowed(connection, scope)
            row = connection.execute(
                "SELECT * FROM hwa_capabilities WHERE capability_id = ?", (capability_ref,)
            ).fetchone()
            if row is None:
                _fail("capability_not_found", "capability does not exist")
            self._assert_capability_binding(
                connection,
                row,
                nonce=nonce_ref,
                scope=scope,
                stage=stage,
                input_digest=input_digest,
                now=now,
            )
            if row["status"] == "reserved" and row["reservation_id"] == reservation:
                return self._capability_from_row(row)
            if row["status"] != "issued":
                _fail("capability_already_used", "capability nonce is one-shot")
            connection.execute(
                """
                UPDATE hwa_capabilities
                SET status = 'reserved', reservation_id = ?, reserved_at = ?
                WHERE capability_id = ? AND status = 'issued'
                """,
                (reservation, _format_time(now), capability_ref),
            )
            if connection.total_changes != 1:
                _fail("capability_conflict", "capability reservation lost compare-and-set")
            return self._capability_from_row(
                connection.execute(
                    "SELECT * FROM hwa_capabilities WHERE capability_id = ?", (capability_ref,)
                ).fetchone()
            )

    def consume_capability(
        self,
        *,
        capability_id: str,
        reservation_id: str,
    ) -> CapabilityRecord:
        capability_ref = _effect_id(capability_id, "capability_id")
        reservation = _effect_id(reservation_id, "reservation_id")
        now = self._now()
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM hwa_capabilities WHERE capability_id = ?", (capability_ref,)
            ).fetchone()
            if row is None:
                _fail("capability_not_found", "capability does not exist")
            scope = self._scope_from_row(row)
            self._assert_effect_allowed(connection, scope)
            self._assert_current_fence(connection, scope, int(row["lease_fence"]), now=now)
            if _parse_time(row["expires_at"]) <= now:
                _fail("capability_expired", "capability expired before consumption")
            if row["status"] == "consumed" and row["reservation_id"] == reservation:
                return self._capability_from_row(row)
            if row["status"] != "reserved" or row["reservation_id"] != reservation:
                _fail("capability_reservation_mismatch", "reservation does not own capability")
            connection.execute(
                """
                UPDATE hwa_capabilities SET status = 'consumed', consumed_at = ?
                WHERE capability_id = ? AND status = 'reserved' AND reservation_id = ?
                """,
                (_format_time(now), capability_ref, reservation),
            )
            if connection.total_changes != 1:
                _fail("capability_conflict", "capability consumption lost compare-and-set")
            return self._capability_from_row(
                connection.execute(
                    "SELECT * FROM hwa_capabilities WHERE capability_id = ?", (capability_ref,)
                ).fetchone()
            )

    def get_capability(self, capability_id: str) -> CapabilityRecord | None:
        capability_ref = _effect_id(capability_id, "capability_id")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM hwa_capabilities WHERE capability_id = ?", (capability_ref,)
            ).fetchone()
            return self._capability_from_row(row) if row is not None else None

    def record_evidence(
        self,
        evidence: HeadlessCommitEvidence,
        *,
        claim_id: str,
        fence: int,
    ) -> dict[str, Any]:
        if not isinstance(evidence, HeadlessCommitEvidence):
            _fail("invalid_evidence", "HeadlessCommitEvidence is required")
        scope = AuthorityScope.from_evidence(evidence)
        if evidence.lease_fence != _positive_fence(fence):
            _fail("stale_fence", "evidence fence does not match the requested write fence")
        payload = asdict(evidence)
        _assert_safe_payload(payload)
        now = self._now()
        if _parse_time(evidence.verified_at) > now:
            _fail("invalid_evidence", "evidence cannot be verified in the future")
        encoded = _canonical_json(payload)
        with self._write() as connection:
            self._assert_current_claim(connection, scope, claim_id, fence, now=now)
            existing = connection.execute(
                "SELECT payload_json FROM hwa_evidence WHERE evidence_ref = ?",
                (evidence.evidence_ref,),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != encoded:
                    _fail("evidence_conflict", "evidence reference was reused")
                return json.loads(existing["payload_json"])
            connection.execute(
                """
                INSERT INTO hwa_evidence(
                    evidence_ref, scope_key, owner_id, repo_id, task_id, plan_id,
                    slice_id, agent_run_id, lease_fence, verified_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_ref,
                    scope.key,
                    *self._scope_values(scope),
                    fence,
                    evidence.verified_at,
                    encoded,
                ),
            )
            return json.loads(encoded)

    def get_evidence(self, evidence_ref: str) -> dict[str, Any] | None:
        reference = _effect_id(evidence_ref, "evidence_ref")
        with self._read() as connection:
            row = connection.execute(
                "SELECT payload_json FROM hwa_evidence WHERE evidence_ref = ?", (reference,)
            ).fetchone()
            return json.loads(row["payload_json"]) if row is not None else None

    def record_promotion(
        self,
        scope: AuthorityScope,
        *,
        claim_id: str,
        fence: int,
        effect_id: str,
        capability_id: str,
        evidence_ref: str,
        stage: str,
        status: str,
    ) -> dict[str, Any]:
        payload = {
            "effect_id": _effect_id(effect_id, "effect_id"),
            "capability_id": _effect_id(capability_id, "capability_id"),
            "evidence_ref": _effect_id(evidence_ref, "evidence_ref"),
            "stage": _opaque_id(stage, "stage"),
            "status": _literal(status, ("reserved", "succeeded", "failed"), "status"),
            "scope_key": scope.key,
            "lease_fence": _positive_fence(fence),
        }
        _assert_safe_payload(payload)
        encoded = _canonical_json(payload)
        now = self._now()
        with self._write() as connection:
            self._assert_effect_allowed(connection, scope)
            self._assert_current_claim(connection, scope, claim_id, fence, now=now)
            capability = connection.execute(
                "SELECT * FROM hwa_capabilities WHERE capability_id = ?",
                (payload["capability_id"],),
            ).fetchone()
            evidence = connection.execute(
                "SELECT * FROM hwa_evidence WHERE evidence_ref = ?",
                (payload["evidence_ref"],),
            ).fetchone()
            if capability is None or evidence is None:
                _fail("promotion_prerequisite_missing", "capability and evidence must be persisted")
            capability_payload = json.loads(capability["payload_json"])
            evidence_payload = json.loads(evidence["payload_json"])
            if (
                capability["scope_key"] != scope.key
                or evidence["scope_key"] != scope.key
                or int(capability["lease_fence"]) != fence
                or int(evidence["lease_fence"]) != fence
                or capability["stage"] != payload["stage"]
                or capability["status"] not in ("reserved", "consumed")
                or capability_payload["input_digest"] != evidence_payload["diff_digest"]
                or evidence_payload["checks_passed"] is not True
                or evidence_payload["content_reviewed"] is not True
            ):
                _fail("promotion_binding_mismatch", "promotion prerequisites changed scope or fence")
            existing = connection.execute(
                "SELECT payload_json FROM hwa_promotions WHERE effect_id = ?",
                (payload["effect_id"],),
            ).fetchone()
            if existing is not None:
                prior = json.loads(existing["payload_json"])
                if prior == payload:
                    return prior
                prior_identity = {key: value for key, value in prior.items() if key != "status"}
                current_identity = {key: value for key, value in payload.items() if key != "status"}
                if (
                    prior_identity != current_identity
                    or prior.get("status") != "reserved"
                    or payload["status"] not in ("succeeded", "failed")
                ):
                    _fail("promotion_conflict", "effect id was reused outside its legal transition")
                connection.execute(
                    """
                    UPDATE hwa_promotions SET recorded_at = ?, payload_json = ?
                    WHERE effect_id = ?
                    """,
                    (_format_time(now), encoded, payload["effect_id"]),
                )
                return payload
            connection.execute(
                """
                INSERT INTO hwa_promotions(
                    effect_id, scope_key, lease_fence, recorded_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (payload["effect_id"], scope.key, fence, _format_time(now), encoded),
            )
            return json.loads(encoded)

    def set_control(
        self,
        *,
        level: str,
        owner_id: str,
        repo_id: str | None = None,
        agent_run_id: str | None = None,
        paused: bool,
        killed: bool,
        reason_ref: str,
    ) -> ControlRecord:
        normalized_level = _literal(level, ("owner", "repo", "run"), "level")
        owner = _opaque_id(owner_id, "owner_id")
        repo = _opaque_id(repo_id, "repo_id") if repo_id is not None else None
        run = _opaque_id(agent_run_id, "agent_run_id") if agent_run_id is not None else None
        if normalized_level == "owner" and (repo is not None or run is not None):
            _fail("invalid_control_scope", "owner control cannot include repo or run")
        if normalized_level == "repo" and (repo is None or run is not None):
            _fail("invalid_control_scope", "repo control requires repo only")
        if normalized_level == "run" and (repo is None or run is None):
            _fail("invalid_control_scope", "run control requires repo and agent run")
        if type(paused) is not bool or type(killed) is not bool:
            _fail("invalid_control", "paused and killed must be booleans")
        reason = _effect_id(reason_ref, "reason_ref")
        key = _control_key(normalized_level, owner, repo, run)
        now = self._now()
        with self._write() as connection:
            existing = connection.execute(
                "SELECT * FROM hwa_controls WHERE control_key = ?", (key,)
            ).fetchone()
            if existing is not None and bool(existing["killed"]) and not killed:
                _fail("kill_is_terminal", "a killed authority scope cannot be revived")
            version = (int(existing["version"]) if existing is not None else 0) + 1
            connection.execute(
                """
                INSERT INTO hwa_controls(
                    control_key, level, owner_id, repo_id, agent_run_id,
                    paused, killed, version, reason_ref, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(control_key) DO UPDATE SET
                    paused = excluded.paused,
                    killed = excluded.killed,
                    version = excluded.version,
                    reason_ref = excluded.reason_ref,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    normalized_level,
                    owner,
                    repo,
                    run,
                    int(paused),
                    int(killed),
                    version,
                    reason,
                    _format_time(now),
                ),
            )
            return self._control_from_row(
                connection.execute(
                    "SELECT * FROM hwa_controls WHERE control_key = ?", (key,)
                ).fetchone()
            )

    def export_safe_state(self) -> dict[str, Any]:
        """Return bounded typed records for restart tests and operator adapters."""

        with self._read() as connection:
            claims = [self._claim_from_row(row) for row in connection.execute(
                "SELECT * FROM hwa_claims ORDER BY scope_key"
            ).fetchall()]
            capabilities = [self._capability_from_row(row) for row in connection.execute(
                "SELECT * FROM hwa_capabilities ORDER BY capability_id"
            ).fetchall()]
            evidence = [json.loads(row["payload_json"]) for row in connection.execute(
                "SELECT payload_json FROM hwa_evidence ORDER BY evidence_ref"
            ).fetchall()]
            controls = [self._control_from_row(row) for row in connection.execute(
                "SELECT * FROM hwa_controls ORDER BY control_key"
            ).fetchall()]
        payload = {
            "schema_id": "odysseus.hwa.authority_state.v1",
            "claims": [asdict(record) for record in claims],
            "capabilities": [asdict(record) for record in capabilities],
            "evidence": evidence,
            "controls": [asdict(record) for record in controls],
        }
        _assert_safe_payload(payload)
        return payload

    def admission_metrics(self) -> dict[str, int]:
        """Return bounded aggregate recovery/admission metrics without identifiers."""

        now_text = _format_time(self._now())
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN state = 'active' AND lease_expires_at > ? THEN 1 ELSE 0 END) active_claims,
                    SUM(CASE WHEN state = 'active' AND lease_expires_at <= ? THEN 1 ELSE 0 END) expired_claims,
                    SUM(CASE WHEN state = 'released' THEN 1 ELSE 0 END) released_claims,
                    SUM(CASE WHEN fence > 1 THEN 1 ELSE 0 END) recovered_scopes,
                    COALESCE(MAX(fence), 0) max_fence
                FROM hwa_claims
                """,
                (now_text, now_text),
            ).fetchone()
            path_row = connection.execute(
                """
                SELECT
                    COUNT(*) active_paths,
                    SUM(CASE WHEN p.is_hotfile = 1 THEN 1 ELSE 0 END) active_hotfiles
                FROM hwa_claim_paths p
                JOIN hwa_claims c ON c.scope_key = p.scope_key
                WHERE c.state = 'active' AND c.lease_expires_at > ?
                """,
                (now_text,),
            ).fetchone()
            control_row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN paused = 1 THEN 1 ELSE 0 END) paused_scopes,
                    SUM(CASE WHEN killed = 1 THEN 1 ELSE 0 END) killed_scopes
                FROM hwa_controls
                """
            ).fetchone()
        return {
            "active_claims": int(row["active_claims"] or 0),
            "expired_claims": int(row["expired_claims"] or 0),
            "released_claims": int(row["released_claims"] or 0),
            "recovered_scopes": int(row["recovered_scopes"] or 0),
            "max_fence": int(row["max_fence"] or 0),
            "active_paths": int(path_row["active_paths"] or 0),
            "active_hotfiles": int(path_row["active_hotfiles"] or 0),
            "paused_scopes": int(control_row["paused_scopes"] or 0),
            "killed_scopes": int(control_row["killed_scopes"] or 0),
        }

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS hwa_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hwa_fences(
                    scope_key TEXT PRIMARY KEY,
                    last_fence INTEGER NOT NULL CHECK(last_fence > 0)
                );
                CREATE TABLE IF NOT EXISTS hwa_claims(
                    scope_key TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL UNIQUE,
                    owner_id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    slice_id TEXT NOT NULL,
                    agent_run_id TEXT NOT NULL,
                    claimant_ref TEXT NOT NULL,
                    fence INTEGER NOT NULL CHECK(fence > 0),
                    state TEXT NOT NULL CHECK(state IN ('active', 'released')),
                    acquired_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    last_heartbeat_at TEXT NOT NULL,
                    last_progress_at TEXT NOT NULL,
                    released_at TEXT
                );
                CREATE INDEX IF NOT EXISTS hwa_claims_admission_idx
                    ON hwa_claims(state, lease_expires_at, owner_id, repo_id, plan_id, claimant_ref);
                CREATE TABLE IF NOT EXISTS hwa_claim_paths(
                    scope_key TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    is_hotfile INTEGER NOT NULL CHECK(is_hotfile IN (0, 1)),
                    PRIMARY KEY(scope_key, path),
                    FOREIGN KEY(scope_key) REFERENCES hwa_claims(scope_key) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS hwa_claim_paths_collision_idx
                    ON hwa_claim_paths(owner_id, repo_id, path);
                CREATE TABLE IF NOT EXISTS hwa_capabilities(
                    capability_id TEXT PRIMARY KEY,
                    nonce TEXT NOT NULL UNIQUE,
                    scope_key TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    slice_id TEXT NOT NULL,
                    agent_run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    lease_fence INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('issued', 'reserved', 'consumed')),
                    reservation_id TEXT,
                    reserved_at TEXT,
                    consumed_at TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hwa_evidence(
                    evidence_ref TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    slice_id TEXT NOT NULL,
                    agent_run_id TEXT NOT NULL,
                    lease_fence INTEGER NOT NULL,
                    verified_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hwa_promotions(
                    effect_id TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    lease_fence INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hwa_effect_receipts(
                    effect_id TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    slice_id TEXT NOT NULL,
                    agent_run_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    activity_type TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    attempt INTEGER NOT NULL CHECK(attempt > 0),
                    lease_fence INTEGER NOT NULL CHECK(lease_fence > 0),
                    status TEXT NOT NULL CHECK(status IN ('reserved', 'succeeded', 'failed', 'cancelled')),
                    reserved_at TEXT NOT NULL,
                    completed_at TEXT,
                    result_ref TEXT,
                    failure_code TEXT
                );
                CREATE INDEX IF NOT EXISTS hwa_effect_scope_idx
                    ON hwa_effect_receipts(scope_key, status, lease_fence);
                CREATE TABLE IF NOT EXISTS hwa_controls(
                    control_key TEXT PRIMARY KEY,
                    level TEXT NOT NULL CHECK(level IN ('owner', 'repo', 'run')),
                    owner_id TEXT NOT NULL,
                    repo_id TEXT,
                    agent_run_id TEXT,
                    paused INTEGER NOT NULL CHECK(paused IN (0, 1)),
                    killed INTEGER NOT NULL CHECK(killed IN (0, 1)),
                    version INTEGER NOT NULL CHECK(version > 0),
                    reason_ref TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            existing = connection.execute(
                "SELECT value FROM hwa_meta WHERE key = 'schema_version'"
            ).fetchone()
            if existing is not None and int(existing["value"]) != SCHEMA_VERSION:
                _fail("unsupported_schema", "authority store schema version changed")
            connection.execute(
                "INSERT OR IGNORE INTO hwa_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            _fail("invalid_clock", "clock must return timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _acquire_claim_locked(
        self,
        connection: sqlite3.Connection,
        scope: AuthorityScope,
        *,
        claim_id: str,
        claimant_ref: str,
        lease_seconds: int,
        now: datetime,
    ) -> ClaimRecord:
        existing_id = connection.execute(
            "SELECT scope_key FROM hwa_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if existing_id is not None and existing_id["scope_key"] != scope.key:
            _fail("claim_id_conflict", "claim id already belongs to another scope")
        existing = connection.execute(
            "SELECT * FROM hwa_claims WHERE scope_key = ?", (scope.key,)
        ).fetchone()
        if (
            existing is not None
            and existing["state"] == "active"
            and _parse_time(existing["lease_expires_at"]) > now
        ):
            _fail("claim_conflict", "an unexpired claim already owns this scope")
        counter = connection.execute(
            "SELECT last_fence FROM hwa_fences WHERE scope_key = ?", (scope.key,)
        ).fetchone()
        fence = (int(counter["last_fence"]) if counter is not None else 0) + 1
        connection.execute(
            """
            INSERT INTO hwa_fences(scope_key, last_fence) VALUES (?, ?)
            ON CONFLICT(scope_key) DO UPDATE SET last_fence = excluded.last_fence
            """,
            (scope.key, fence),
        )
        expires = now + timedelta(seconds=lease_seconds)
        values = (
            scope.key,
            claim_id,
            *self._scope_values(scope),
            claimant_ref,
            fence,
            "active",
            _format_time(now),
            _format_time(expires),
            _format_time(now),
            _format_time(now),
            None,
        )
        connection.execute(
            """
            INSERT INTO hwa_claims(
                scope_key, claim_id, owner_id, repo_id, task_id, plan_id,
                slice_id, agent_run_id, claimant_ref, fence, state,
                acquired_at, lease_expires_at, last_heartbeat_at,
                last_progress_at, released_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_key) DO UPDATE SET
                claim_id = excluded.claim_id,
                owner_id = excluded.owner_id,
                repo_id = excluded.repo_id,
                task_id = excluded.task_id,
                plan_id = excluded.plan_id,
                slice_id = excluded.slice_id,
                agent_run_id = excluded.agent_run_id,
                claimant_ref = excluded.claimant_ref,
                fence = excluded.fence,
                state = excluded.state,
                acquired_at = excluded.acquired_at,
                lease_expires_at = excluded.lease_expires_at,
                last_heartbeat_at = excluded.last_heartbeat_at,
                last_progress_at = excluded.last_progress_at,
                released_at = excluded.released_at
            """,
            values,
        )
        return self._claim_from_row(
            connection.execute(
                "SELECT * FROM hwa_claims WHERE scope_key = ?", (scope.key,)
            ).fetchone()
        )

    def _assert_admission_capacity(
        self,
        connection: sqlite3.Connection,
        scope: AuthorityScope,
        *,
        claimant_ref: str,
        paths: tuple[str, ...],
        hotfiles: tuple[str, ...],
        limits: AdmissionLimits,
        now: datetime,
    ) -> None:
        now_text = _format_time(now)
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN state = 'active' AND lease_expires_at > ? THEN 1 ELSE 0 END) global_count,
                SUM(CASE WHEN state = 'active' AND lease_expires_at > ? AND owner_id = ? THEN 1 ELSE 0 END) owner_count,
                SUM(CASE WHEN state = 'active' AND lease_expires_at > ? AND owner_id = ? AND repo_id = ? AND plan_id = ? THEN 1 ELSE 0 END) project_count,
                SUM(CASE WHEN state = 'active' AND lease_expires_at > ? AND claimant_ref = ? THEN 1 ELSE 0 END) agent_count
            FROM hwa_claims
            """,
            (
                now_text,
                now_text,
                scope.owner_id,
                now_text,
                scope.owner_id,
                scope.repo_id,
                scope.plan_id,
                now_text,
                claimant_ref,
            ),
        ).fetchone()
        dimensions = (
            ("global", int(counts["global_count"] or 0), limits.max_global_active),
            ("owner", int(counts["owner_count"] or 0), limits.max_owner_active),
            ("project", int(counts["project_count"] or 0), limits.max_project_active),
            ("agent", int(counts["agent_count"] or 0), limits.max_agent_active),
        )
        for dimension, active, maximum in dimensions:
            if active >= maximum:
                _fail("admission_backpressure", f"{dimension} active-claim quota is exhausted")

        rows = connection.execute(
            """
            SELECT p.path, p.is_hotfile
            FROM hwa_claim_paths p
            JOIN hwa_claims c ON c.scope_key = p.scope_key
            WHERE p.owner_id = ? AND p.repo_id = ?
              AND c.state = 'active' AND c.lease_expires_at > ?
            ORDER BY p.path
            """,
            (scope.owner_id, scope.repo_id, now_text),
        ).fetchall()
        hotfile_set = set(hotfiles)
        for path in paths:
            for row in rows:
                existing_path = row["path"]
                if path == existing_path and (path in hotfile_set or bool(row["is_hotfile"])):
                    _fail("hotfile_collision", f"hotfile {path} already has an active claim")
                if _paths_overlap(path, existing_path):
                    _fail(
                        "path_prefix_collision",
                        f"path {path} overlaps active claim path {existing_path}",
                    )

    def _assert_current_claim(
        self,
        connection: sqlite3.Connection,
        scope: AuthorityScope,
        claim_id: str,
        fence: int,
        *,
        now: datetime,
    ) -> sqlite3.Row:
        row = self._assert_current_fence(connection, scope, fence, now=now)
        if row["claim_id"] != _effect_id(claim_id, "claim_id"):
            _fail("stale_claim", "claim id does not own the current fence")
        return row

    def _assert_current_fence(
        self,
        connection: sqlite3.Connection,
        scope: AuthorityScope,
        fence: int,
        *,
        now: datetime,
    ) -> sqlite3.Row:
        _require_scope(scope)
        normalized_fence = _positive_fence(fence)
        row = connection.execute(
            "SELECT * FROM hwa_claims WHERE scope_key = ?", (scope.key,)
        ).fetchone()
        if row is None or row["state"] != "active":
            _fail("stale_fence", "no active claim owns this scope")
        if int(row["fence"]) != normalized_fence:
            _fail("stale_fence", "fence does not match the active claim")
        if _parse_time(row["lease_expires_at"]) <= now:
            _fail("stale_fence", "claim lease has expired")
        return row

    def _assert_effect_allowed(
        self,
        connection: sqlite3.Connection,
        scope: AuthorityScope,
    ) -> None:
        row = connection.execute(
            """
            SELECT level, paused, killed FROM hwa_controls
            WHERE owner_id = ? AND (
                level = 'owner'
                OR (level = 'repo' AND repo_id = ?)
                OR (level = 'run' AND repo_id = ? AND agent_run_id = ?)
            ) AND (paused = 1 OR killed = 1)
            ORDER BY killed DESC, level ASC
            LIMIT 1
            """,
            (scope.owner_id, scope.repo_id, scope.repo_id, scope.agent_run_id),
        ).fetchone()
        if row is not None:
            state = "killed" if bool(row["killed"]) else "paused"
            _fail("authority_blocked", f"{row['level']} scope is {state}")

    def _assert_capability_binding(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        nonce: str,
        scope: AuthorityScope,
        stage: str,
        input_digest: str,
        now: datetime,
    ) -> None:
        if row["nonce"] != nonce:
            _fail("capability_binding_mismatch", "nonce does not match")
        if row["scope_key"] != scope.key:
            _fail("capability_binding_mismatch", "owner/repo/task/plan/slice/run scope changed")
        if row["stage"] != stage:
            _fail("capability_binding_mismatch", "stage changed")
        if row["input_digest"] != input_digest:
            _fail("capability_input_mismatch", "input digest changed")
        if now < _parse_time(row["issued_at"]) or now >= _parse_time(row["expires_at"]):
            _fail("capability_expired", "capability is outside its approval window")
        self._assert_current_fence(
            connection,
            scope,
            int(row["lease_fence"]),
            now=now,
        )

    @staticmethod
    def _scope_values(scope: AuthorityScope) -> tuple[str, ...]:
        return (
            scope.owner_id,
            scope.repo_id,
            scope.task_id,
            scope.plan_id,
            scope.slice_id,
            scope.agent_run_id,
        )

    @staticmethod
    def _scope_from_row(row: sqlite3.Row) -> AuthorityScope:
        return AuthorityScope.create(
            owner_id=row["owner_id"],
            repo_id=row["repo_id"],
            task_id=row["task_id"],
            plan_id=row["plan_id"],
            slice_id=row["slice_id"],
            agent_run_id=row["agent_run_id"],
        )

    def _claim_from_row(self, row: sqlite3.Row) -> ClaimRecord:
        return ClaimRecord(
            claim_id=row["claim_id"],
            scope=self._scope_from_row(row),
            claimant_ref=row["claimant_ref"],
            fence=int(row["fence"]),
            state=row["state"],
            acquired_at=row["acquired_at"],
            lease_expires_at=row["lease_expires_at"],
            last_heartbeat_at=row["last_heartbeat_at"],
            last_progress_at=row["last_progress_at"],
            released_at=row["released_at"],
        )

    def _capability_from_row(self, row: sqlite3.Row) -> CapabilityRecord:
        return CapabilityRecord(
            capability_id=row["capability_id"],
            nonce=row["nonce"],
            scope=self._scope_from_row(row),
            stage=row["stage"],
            input_digest=row["input_digest"],
            policy_version=row["policy_version"],
            lease_fence=int(row["lease_fence"]),
            max_attempts=int(row["max_attempts"]),
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            status=row["status"],
            reservation_id=row["reservation_id"],
            reserved_at=row["reserved_at"],
            consumed_at=row["consumed_at"],
        )

    def _effect_from_row(self, row: sqlite3.Row) -> EffectRecord:
        return EffectRecord(
            effect_id=row["effect_id"],
            scope=self._scope_from_row(row),
            claim_id=row["claim_id"],
            activity_type=row["activity_type"],
            input_digest=row["input_digest"],
            attempt=int(row["attempt"]),
            lease_fence=int(row["lease_fence"]),
            status=row["status"],
            reserved_at=row["reserved_at"],
            completed_at=row["completed_at"],
            result_ref=row["result_ref"],
            failure_code=row["failure_code"],
        )

    @staticmethod
    def _control_from_row(row: sqlite3.Row) -> ControlRecord:
        return ControlRecord(
            level=row["level"],
            owner_id=row["owner_id"],
            repo_id=row["repo_id"],
            agent_run_id=row["agent_run_id"],
            paused=bool(row["paused"]),
            killed=bool(row["killed"]),
            version=int(row["version"]),
            reason_ref=row["reason_ref"],
            updated_at=row["updated_at"],
        )


def _capability_payload(value: ApprovalCapability) -> dict[str, Any]:
    return {
        "capability_id": value.capability_id,
        "nonce": value.nonce,
        "stage": value.stage.value,
        "owner_id": value.owner_id,
        "repo_id": value.repo_id,
        "task_id": value.task_id,
        "plan_id": value.plan_id,
        "slice_id": value.slice_id,
        "agent_run_id": value.agent_run_id,
        "approver_ref": value.approver_ref,
        "policy_version": value.policy_version,
        "input_digest": value.input_digest,
        "allowed_paths": list(value.allowed_paths),
        "blocked_paths": list(value.blocked_paths),
        "lease_fence": value.lease_fence,
        "max_attempts": value.max_attempts,
        "issued_at": value.issued_at,
        "expires_at": value.expires_at,
    }


def _repo_paths(
    values: Any,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        _fail("invalid_repo_path", f"{field} must be an array")
    normalized: set[str] = set()
    for raw_value in values:
        path = str(raw_value or "").strip().rstrip("/")
        if (
            not path
            or "\\" in path
            or path.startswith("/")
            or _ABSOLUTE_WINDOWS_RE.match(path)
            or ".." in path.split("/")
            or not _SAFE_REPO_PATH_RE.fullmatch(path)
        ):
            _fail("invalid_repo_path", f"{field} contains an unsafe path")
        normalized.add(path)
    if not normalized and not allow_empty:
        _fail("invalid_repo_path", f"{field} must not be empty")
    if len(normalized) > 128:
        _fail("invalid_repo_path", f"{field} exceeds 128 paths")
    return tuple(sorted(normalized))


def _paths_overlap(left: str, right: str) -> bool:
    return (
        left == right
        or left.startswith(right.rstrip("/") + "/")
        or right.startswith(left.rstrip("/") + "/")
    )


def _assert_safe_payload(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _FORBIDDEN_FIELD_PARTS):
                _fail("forbidden_raw_field", f"forbidden persisted field at {path}.{key}")
            _assert_safe_payload(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_safe_payload(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        if value.startswith("/") or value.startswith("\\\\") or _ABSOLUTE_WINDOWS_RE.match(value):
            _fail("absolute_path_forbidden", f"absolute path at {path}")
        if len(value) > 4096:
            _fail("persisted_value_too_large", f"value at {path} is too large")
        return
    if value is not None and type(value) not in (bool, int):
        _fail("unsafe_persisted_value", f"unsupported value at {path}")


def _control_key(level: str, owner: str, repo: str | None, run: str | None) -> str:
    payload = _canonical_json({"level": level, "owner": owner, "repo": repo, "run": run})
    return "hwa_control_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _require_scope(value: Any) -> AuthorityScope:
    if not isinstance(value, AuthorityScope):
        _fail("invalid_scope", "AuthorityScope is required")
    return value


def _opaque_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _OPAQUE_ID_RE.fullmatch(text) or ".." in text:
        _fail("invalid_identifier", field)
    return text


def _effect_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _EFFECT_ID_RE.fullmatch(text) or ".." in text:
        _fail("invalid_identifier", field)
    return text


def _literal(value: Any, allowed: tuple[str, ...], field: str) -> str:
    text = str(value or "")
    if text not in allowed:
        _fail("invalid_literal", field)
    return text


def _lease_seconds(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LEASE_SECONDS:
        _fail("invalid_lease", f"lease_seconds must be 1 through {MAX_LEASE_SECONDS}")
    return value


def _bounded_limit(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000:
        _fail("invalid_admission_limit", f"{field} must be 1 through 1000")
    return value


def _positive_fence(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail("invalid_fence", "fence must be a positive integer")
    return value


def _positive_attempt(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000:
        _fail("invalid_attempt", "attempt must be an integer from 1 through 1000")
    return value


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise HeadlessWriteAgentStateError("invalid_timestamp", "invalid persisted timestamp") from exc
    if parsed.tzinfo is None:
        _fail("invalid_timestamp", "timestamp is missing timezone")
    return parsed.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _fail(code: str, detail: str) -> None:
    raise HeadlessWriteAgentStateError(code, detail)
