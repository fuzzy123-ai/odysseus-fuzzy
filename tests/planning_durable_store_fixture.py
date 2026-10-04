"""Store-only accepted Planning source fixture for durable store tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import pytest

from core.auth import AuthManager
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_revision_store import DurablePlanningRevisionStore
from src.planning_roadmap_migration import build_h1r03_migration_manifest


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}", re.IGNORECASE),
    re.compile(
        r"(?i)(api[_-]?key|authorization|cookie|token|secret|password|chat[_-]?id)"
        r"\s*[:=]\s*['\"]?[^'\"\s,}]+"
    ),
)
_PRIVATE_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/][^\s\"'<>]+"),
    re.compile(r"\\\\[^\s\"'<>]+"),
    re.compile(r"/(?:home|Users|private|var|tmp)/[^\s\"'<>]+", re.IGNORECASE),
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _safe_sanitized_text(prefix: str, path: tuple[object, ...]) -> str:
    suffix = "-".join(str(part) for part in path)
    return f"{prefix}-{suffix}" if suffix else prefix


def _clean_descendant(value: object, path: tuple[object, ...] = ()) -> object:
    if type(value) is str:
        if "\\" in value or re.search(
            r"(?i)(?:[A-Z]:[\\/]|\\\\|/(?:home|Users|private|var|tmp)/)", value,
        ):
            return _safe_sanitized_text("repository-relative-reference", path)
        if re.search(
            r"(?i)(?:password|secret|token|api[_-]?key|authorization|cookie|chat[_-]?id|bearer|sk-[A-Za-z0-9_-]{12,})",
            value,
        ):
            return _safe_sanitized_text("policy-safe-reference", path)
        return value
    if type(value) is list:
        return [_clean_descendant(item, path + (index,)) for index, item in enumerate(value)]
    if type(value) is dict:
        return {key: _clean_descendant(item, path + (key,)) for key, item in value.items()}
    return value


def _contains_sensitive_value(serialized: str) -> bool:
    return any(pattern.search(serialized) for pattern in _SECRET_PATTERNS + _PRIVATE_PATH_PATTERNS)


def _append_clean_descendants(store: DurablePlanningRevisionStore, principal: object) -> None:
    owner, _batch, _records, heads = store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    assert owner == "fuzzy"
    for head in heads:
        payload = store.read_payload(
            principal, owner_id=owner, revision_id=head["current_revision_id"],
        )
        document = _clean_descendant(json.loads(payload.decode("utf-8")), ("document",))
        roadmap = document["roadmaps"][0]
        roadmap["revision"] = head["current_revision"] + 1
        roadmap["title"] = f"Übersicht Prüfung — {roadmap['roadmap_id']}"
        roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
        document["project"]["latest_approved_revision"] = {
            roadmap["roadmap_id"]: {
                "revision": roadmap["revision"],
                "content_hash": roadmap["content_hash"],
            },
        }
        document["project"]["draft_refs"] = []
        serialized = _canonical(document).decode("utf-8")
        assert not _contains_sensitive_value(serialized)
        current = store.get_head(
            principal, owner_id=owner, project_id="harbour-one",
            roadmap_id=roadmap["roadmap_id"],
        )
        appended = store.append_revision(
            principal, owner_id=owner, project_id="harbour-one",
            roadmap_id=roadmap["roadmap_id"], payload_bytes=serialized.encode("utf-8"),
            expected_head=current,
            idempotency_key=f"native-clean-descendant-{roadmap['roadmap_id']}",
        )
        store.approve_revision(
            principal, owner_id=owner, project_id="harbour-one",
            roadmap_id=roadmap["roadmap_id"], revision_id=appended.new_revision_id,
            expected_head=appended.after_head,
            idempotency_key=f"native-clean-approval-{roadmap['roadmap_id']}",
        )

    _owner, _batch, retained_records, retained_heads = store.read_subject_bound_accepted_migration_records(
        principal, project_id="harbour-one",
    )
    assert len(retained_records) == 12
    assert len(heads) == len(retained_heads) == 6
    for head in retained_heads:
        chain = [
            document for _record_owner, document, _revision_ref in retained_records
            if document["roadmaps"][0]["roadmap_id"] == head["roadmap_id"]
        ]
        assert len(chain) >= 2
        assert head["current_revision"] == head["approved_revision"]


@pytest.fixture
def durable_planning_store(tmp_path: Path):
    auth = AuthManager(str(tmp_path / "auth.json"))
    assert auth.create_user("fuzzy", "Password-123", is_admin=True)
    cookie = auth.create_session("fuzzy", "Password-123")
    assert cookie
    owner_sid = auth.subject_id_for_username("fuzzy")
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth",
        key_id="planning-key-1",
        secret=b"native-durable-planning-source-secret-0001",
        clock=lambda: datetime.now(timezone.utc),
    )
    store = DurablePlanningRevisionStore(
        tmp_path / "durable-planning", trusted_issuers=(issuer,)
    )
    principal = issuer.issue(
        subject_id=owner_sid,
        owner_id="fuzzy",
        auth_context_digest="sha256:" + "4" * 64,
        ttl_seconds=3600,
        nonce="native-source-fixture-0001",
    )
    manifest = build_h1r03_migration_manifest(Path(__file__).resolve().parents[1])
    store.prepare_migration(principal, manifest=manifest)
    store.accept_migration(
        principal,
        batch_id=manifest["batch_id"],
        current_manifest=lambda: deepcopy(manifest),
    )
    _append_clean_descendants(store, principal)
    return {
        "auth": auth,
        "cookie": cookie,
        "issuer": issuer,
        "principal": principal,
        "store": store,
    }
