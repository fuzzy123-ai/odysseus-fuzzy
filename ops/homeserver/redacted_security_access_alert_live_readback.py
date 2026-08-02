#!/usr/bin/env python3
"""Read-only, fixed-key proof of one synthetic access-alert delivery."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import math
import re
import sqlite3
import subprocess
import time
from typing import Any, Callable, Mapping

from src.security_auth_incident_bridge import (
    AUTH_ALERT_DEDUPE_WINDOW_SECONDS,
    operator_notification_policy_revision,
    operator_notification_scope_fingerprint,
)
from src.security_evidence_broker import build_security_evidence_envelope
from src.security_evidence_sources import auth_outcome_projection
from src.security_incident_delivery import delivery_idempotency_identity
from src.security_incident_network_context import NETWORK_CONTEXT_POLICY_VERSION, canonical_ip
from src.security_incident_notifications import (
    canonical_access_alert_body_ref,
    canonical_operator_notification_target_class_ref,
)


SCHEMA_ID = "odysseus.redacted_security_access_alert_live_readback.v1"
TARGET_ROOT = "/opt/odysseus"
DATABASE_PATH = "/opt/odysseus/data/security_incidents.sqlite"
APP_CONTAINER = "odysseus_odysseus_1"
SYNTHETIC_USERNAME = "codex_ops_alert_live_smoke_7fe3"
EVENT_CLASS = "authentication_failure"
MAX_CLOCK_SKEW_SECONDS = 15
MAX_OBSERVATION_SECONDS = 75
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT = re.compile(r"^receipt:sha256:[0-9a-f]{64}$")
_PROOFS = frozenset(
    {
        "synthetic_login_rejected",
        "revision_matches",
        "manifest_matches",
        "action_executed",
        "receipt_bound",
        "context_bound",
        "source_ip_public",
        "trusted_proxy_forwarded",
        "suppression_notify",
        "body_ref_bound",
        "approval_consumed",
        "audit_bound",
    }
)
_VISIBILITY = frozenset(
    {
        "source_ip_visible",
        "action_id_visible",
        "receipt_ref_visible",
        "raw_stdout_visible",
        "raw_stderr_visible",
        "exception_text_visible",
        "environment_visible",
        "paths_visible",
        "hostnames_visible",
        "secret_values_visible",
    }
)
_KEYS = frozenset(
    {"schema_id", "status", "retry_permitted", *_PROOFS, *_VISIBILITY, "evidence_sha256"}
)
_MANIFEST_PROGRAM = (
    "import hashlib,sys;"
    "sys.path.insert(0,'/app');"
    "from src.constants import RELEASE_MANIFEST_FILE;"
    "print('ok' if hashlib.sha256(open(RELEASE_MANIFEST_FILE,'rb').read()).hexdigest()==sys.argv[1] else 'bad')"
)


def _digest(payload: Mapping[str, Any]) -> str:
    projected = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(
        json.dumps(projected, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _envelope(status: str, flags: Mapping[str, bool] | None = None) -> dict[str, Any]:
    values = {key: False for key in _PROOFS | _VISIBILITY}
    if flags is not None:
        values.update({key: value for key, value in flags.items() if key in _PROOFS})
    payload = {
        "schema_id": SCHEMA_ID,
        "status": status,
        "retry_permitted": False,
        **values,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def validate_envelope(value: Any) -> bool:
    if (
        type(value) is not dict
        or set(value) != _KEYS
        or value.get("schema_id") != SCHEMA_ID
        or value.get("status") not in {"ok", "observed", "blocked"}
        or value.get("retry_permitted") is not False
    ):
        return False
    if any(type(value.get(key)) is not bool for key in _PROOFS | _VISIBILITY):
        return False
    if any(value[key] is not False for key in _VISIBILITY):
        return False
    digest = value.get("evidence_sha256")
    if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None or digest != _digest(value):
        return False
    return (value["status"] == "ok") == all(value[key] for key in _PROOFS)


@dataclass(frozen=True, slots=True)
class LiveReadbackExpectation:
    revision: str
    manifest_sha256: str
    source_ip: str
    issued_at: float
    expires_at: float
    synthetic_login_rejected: bool

    @classmethod
    def from_mapping(cls, value: Any) -> "LiveReadbackExpectation | None":
        if not isinstance(value, Mapping) or set(value) != {
            "revision",
            "manifest_sha256",
            "source_ip",
            "issued_at",
            "expires_at",
            "synthetic_login_rejected",
        }:
            return None
        try:
            candidate = cls(
                revision=value["revision"],
                manifest_sha256=value["manifest_sha256"],
                source_ip=canonical_ip(value["source_ip"]),
                issued_at=float(value["issued_at"]),
                expires_at=float(value["expires_at"]),
                synthetic_login_rejected=value["synthetic_login_rejected"],
            )
        except (KeyError, TypeError, ValueError):
            return None
        return candidate if candidate.valid() else None

    def valid(self) -> bool:
        try:
            address = ipaddress.ip_address(self.source_ip)
        except ValueError:
            return False
        return bool(
            _HEX40.fullmatch(self.revision)
            and _HEX64.fullmatch(self.manifest_sha256)
            and canonical_ip(self.source_ip) == self.source_ip
            and address.is_global
            and type(self.issued_at) is float
            and type(self.expires_at) is float
            and math.isfinite(self.issued_at)
            and math.isfinite(self.expires_at)
            and 0 <= self.issued_at < self.expires_at
            and 30 <= self.expires_at - self.issued_at <= 180
            and self.synthetic_login_rejected is True
        )


def _run(runner: Callable[..., Any], command: tuple[str, ...]) -> str | None:
    try:
        result = runner(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except Exception:
        return None
    output = getattr(result, "stdout", None)
    if (
        getattr(result, "returncode", None) != 0
        or type(output) is not str
        or len(output) > 128
    ):
        return None
    return output


def _runtime_proofs(
    expectation: LiveReadbackExpectation,
    *,
    runner: Callable[..., Any],
) -> dict[str, bool]:
    revision = _run(runner, ("git", "-C", TARGET_ROOT, "rev-parse", "HEAD"))
    manifest = _run(
        runner,
        (
            "podman",
            "exec",
            APP_CONTAINER,
            "python",
            "-I",
            "-c",
            _MANIFEST_PROGRAM,
            expectation.manifest_sha256,
        ),
    )
    return {
        "revision_matches": revision == expectation.revision + "\n",
        "manifest_matches": manifest == "ok\n",
    }


def _sha(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _ref(kind: str, *parts: str) -> str:
    return f"{kind}:sha256:{_sha(*parts)}"


def _candidate_identities(expectation: LiveReadbackExpectation, now: float):
    principal_ref = "principal:sha256:" + hashlib.sha256(
        SYNTHETIC_USERNAME.encode("utf-8")
    ).hexdigest()
    envelope = build_security_evidence_envelope(
        auth_outcome_projection(
            outcome="failed",
            principal_ref=principal_ref,
            source_familiarity="unknown",
            session_created="no",
        )
    )
    source_ref = "source_ip:sha256:" + hashlib.sha256(
        (NETWORK_CONTEXT_POLICY_VERSION + "|" + expectation.source_ip).encode("utf-8")
    ).hexdigest()
    body_ref = canonical_access_alert_body_ref(
        event_class=EVENT_CLASS,
        accessing_ip=expectation.source_ip,
    )
    lower = max(0.0, expectation.issued_at - MAX_CLOCK_SKEW_SECONDS)
    upper = min(expectation.expires_at, now + MAX_CLOCK_SKEW_SECONDS)
    first = int(lower // AUTH_ALERT_DEDUPE_WINDOW_SECONDS)
    last = int(upper // AUTH_ALERT_DEDUPE_WINDOW_SECONDS)
    for generation_number in range(first, min(last, first + 2) + 1):
        generation = str(generation_number)
        incident_id = "inc-auth-" + _sha(
            "incident",
            envelope.evidence_ref,
            EVENT_CLASS,
            source_ref,
            generation,
        )[:24]
        incident_ref = _ref(
            "incident",
            envelope.evidence_ref,
            EVENT_CLASS,
            source_ref,
            generation,
        )
        action_id = "notify-" + _sha("action", incident_id, body_ref)[:32]
        scope = operator_notification_scope_fingerprint(incident_id)
        policy = operator_notification_policy_revision(EVENT_CLASS)
        idempotency = delivery_idempotency_identity(
            incident_id=incident_id,
            action_id=action_id,
            scope_fingerprint=scope,
            policy_revision=policy,
            body_ref=body_ref,
            approved_target_class_ref=canonical_operator_notification_target_class_ref(),
        )
        yield {
            "incident_id": incident_id,
            "incident_ref": incident_ref,
            "action_id": action_id,
            "scope": scope,
            "policy": policy,
            "idempotency": idempotency,
            "body_ref": body_ref,
        }


def _read_action(
    expectation: LiveReadbackExpectation,
    *,
    clock: Callable[[], float],
) -> dict[str, bool] | None:
    now = float(clock())
    connection = sqlite3.connect(
        f"file:{DATABASE_PATH}?mode=ro",
        uri=True,
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    try:
        for identity in _candidate_identities(expectation, now):
            row = connection.execute(
                """SELECT a.action_id,a.incident_id,a.action_type,a.state,a.version,
                          a.scope_fingerprint,a.policy_revision,a.idempotency_key,a.receipt_ref,
                          i.incident_ref,i.created_at,
                          c.event_class,c.accessing_ip,c.provenance,c.is_public,c.reason_code,
                          c.suppression_decision,c.suppression_reason,c.notification_binding_ref,
                          p.action_version AS approval_version,p.scope_fingerprint AS approval_scope,
                          p.policy_revision AS approval_policy,p.consumed_at
                   FROM actions AS a
                   JOIN incidents AS i ON i.incident_id=a.incident_id
                   JOIN incident_contexts AS c ON c.incident_id=a.incident_id
                   LEFT JOIN approvals AS p ON p.action_id=a.action_id
                   WHERE a.action_id=?""",
                (identity["action_id"],),
            ).fetchone()
            if row is None:
                continue
            audit = connection.execute(
                """SELECT action_version,event_type,reference
                   FROM audit_references WHERE action_id=? ORDER BY sequence""",
                (identity["action_id"],),
            ).fetchall()
            created_in_window = (
                expectation.issued_at - MAX_CLOCK_SKEW_SECONDS
                <= float(row["created_at"])
                <= expectation.expires_at
            )
            receipt_bound = _RECEIPT.fullmatch(str(row["receipt_ref"])) is not None
            context_bound = (
                row["incident_id"] == identity["incident_id"]
                and row["incident_ref"] == identity["incident_ref"]
                and row["action_type"] == "operator_notification"
                and row["scope_fingerprint"] == identity["scope"]
                and row["policy_revision"] == identity["policy"]
                and row["idempotency_key"] == identity["idempotency"]
                and row["event_class"] == EVENT_CLASS
                and row["accessing_ip"] == expectation.source_ip
                and row["reason_code"] == "trusted_proxy_forwarded"
                and created_in_window
            )
            event_types = {item["event_type"] for item in audit}
            versions = [int(item["action_version"]) for item in audit]
            return {
                "action_executed": row["state"] == "executed" and int(row["version"]) == 5,
                "receipt_bound": receipt_bound,
                "context_bound": context_bound,
                "source_ip_public": bool(row["is_public"]),
                "trusted_proxy_forwarded": row["provenance"] == "trusted_proxy_forwarded",
                "suppression_notify": (
                    row["suppression_decision"] == "notify"
                    and row["suppression_reason"] == "notification_required_security_critical"
                ),
                "body_ref_bound": row["notification_binding_ref"] == identity["body_ref"],
                "approval_consumed": (
                    row["approval_version"] == 3
                    and row["approval_scope"] == identity["scope"]
                    and row["approval_policy"] == identity["policy"]
                    and row["consumed_at"] is not None
                ),
                "audit_bound": (
                    {
                        "action_proposed",
                        "action_prepared",
                        "action_approved",
                        "approval_consumed",
                        "action_executing",
                        "action_executed",
                    }.issubset(event_types)
                    and versions == sorted(versions)
                    and receipt_bound
                ),
            }
    finally:
        connection.close()
    return None


def collect_live_readback(
    expectation: Any,
    *,
    runner: Callable[..., Any] = subprocess.run,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    parsed = (
        expectation
        if isinstance(expectation, LiveReadbackExpectation)
        else LiveReadbackExpectation.from_mapping(expectation)
    )
    if parsed is None:
        return _envelope("blocked")
    try:
        now = float(clock())
        if not math.isfinite(now) or not parsed.issued_at <= now < parsed.expires_at:
            return _envelope("blocked")
    except Exception:
        return _envelope("blocked")
    flags = {
        "synthetic_login_rejected": parsed.synthetic_login_rejected,
        **_runtime_proofs(parsed, runner=runner),
    }
    deadline = min(parsed.expires_at, now + MAX_OBSERVATION_SECONDS)
    while True:
        try:
            durable = _read_action(parsed, clock=clock)
        except Exception:
            durable = None
        if durable is not None:
            flags.update(durable)
            return _envelope("ok" if all(flags.get(key, False) for key in _PROOFS) else "observed", flags)
        try:
            remaining = deadline - float(clock())
        except Exception:
            return _envelope("observed", flags)
        if remaining <= 0:
            return _envelope("observed", flags)
        sleeper(min(3.0, remaining))


def production_entrypoint(packet: Any, *, execute: bool = False) -> dict[str, Any]:
    if execute is not True:
        return _envelope("blocked")
    return collect_live_readback(packet)


def main(argv: list[str] | None = None) -> int:
    payload = _envelope("blocked")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
