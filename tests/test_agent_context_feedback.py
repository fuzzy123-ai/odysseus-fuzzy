from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from src.agent_context_feedback import (
    AgentContextFeedbackStore,
    AgentContextFeedbackStoreError,
    FEEDBACK_STORE_SCHEMA,
    adapt_feedback_action_payload,
    normalize_feedback_action,
)
from src.agent_context_transparency import build_context_item_from_evidence


NOW = "2026-07-10T08:00:00Z"


def quiet_review() -> dict:
    return {"required": False, "reason_codes": [], "summary": None}


def review(*reasons: str) -> dict:
    return {"required": True, "reason_codes": list(reasons), "summary": "Synthetic review metadata."}


def source_ref(ref_type: str = "document", ref_id: str = "source-001", **extra) -> dict:
    return {"ref_type": ref_type, "ref_id": ref_id, **extra}


def feedback_payload(
    feedback_id: str = "feedback-001",
    *,
    action: str = "pin",
    target_ref: dict | None = None,
    classification: str = "public",
    redaction_state: str = "none",
    feedback_review: dict | None = None,
    candidate_review: bool = False,
    reason: str | None = "Synthetic feedback reason.",
    proposed_label: str | None = None,
) -> dict:
    candidate_type = {
        "pin": "prefer",
        "remove": "exclude",
        "approve": "confirm",
        "hide": "hide",
        "rename": "display_label",
    }[action]
    if action == "rename" and proposed_label is None:
        proposed_label = "Synthetic label"
    return {
        "schema_version": 1,
        "kind": "agent.user_context_feedback",
        "feedback_id": feedback_id,
        "context_id": f"context-{feedback_id}",
        "target_ref": target_ref or source_ref(),
        "action": action,
        "scope": "project",
        "actor_ref": "local-user",
        "result": "review_required" if candidate_review else "candidate_created",
        "policy_effect": "none",
        "reason": reason,
        "proposed_label": proposed_label if action == "rename" else None,
        "learned_rule_candidate": {
            "candidate_id": f"candidate-{feedback_id}",
            "status": "proposed",
            "candidate_type": candidate_type,
            "scope": "project",
            "target_ref": target_ref or source_ref(),
            "summary": "Synthetic learnable preference.",
            "evidence_feedback_refs": [feedback_id],
            "requires_review": candidate_review,
        },
        "created_at": NOW,
        "truth_level": "runtime_trace",
        "classification": classification,
        "redaction_state": redaction_state,
        "review": feedback_review or quiet_review(),
    }


def context_item_payload() -> dict:
    return build_context_item_from_evidence({
        "context_id": "context-not-feedback",
        "created_at": NOW,
        "context_kind": "memory",
        "label": "Synthetic memory",
        "source_ref": source_ref("memory", "memory-001"),
        "selection_state": "included",
        "scope": "project",
        "reason_flags": ["memory_preference"],
        "evidence_refs": ["event-001"],
        "classification": "private",
        "redaction_state": "summary_only",
        "freshness": {"state": "current", "observed_at": NOW, "source_updated_at": NOW, "age_seconds": 0, "reason": None},
        "confidence": {"level": "high", "score": 1.0, "basis": "user_confirmed", "summary": None},
        "summary": "Synthetic summary.",
        "redacted_preview": "Synthetic preview.",
    }).to_dict()


def test_store_persists_redacted_feedback_and_proposed_candidate(tmp_path):
    store = AgentContextFeedbackStore(tmp_path / "feedback")
    target = source_ref("repo_file", "file-001", repo_rel_path="fixtures/synthetic.md")
    result = store.append(feedback_payload(target_ref=target))

    assert result.created is True
    assert result.idempotent is False
    assert result.content_hash.startswith("sha256:")
    persisted = store.get("feedback-001")
    assert persisted == result.record
    assert persisted["feedback"]["reason"] is None
    assert persisted["feedback"]["target_ref"] == {"ref_type": "repo_file", "ref_id": "file-001"}
    candidate = persisted["feedback"]["learned_rule_candidate"]
    assert candidate["status"] == "proposed"
    assert candidate["summary"] == "Prefer this context source in the selected scope."
    assert candidate["target_ref"] == {"ref_type": "repo_file", "ref_id": "file-001"}
    assert persisted["feedback"]["policy_effect"] == "none"
    assert persisted["recording_review"] == quiet_review()
    assert not hasattr(store, "apply")

    snapshot = json.loads(store.path.read_text(encoding="utf-8"))
    assert snapshot["schema"] == FEEDBACK_STORE_SCHEMA
    assert snapshot["revision"] == 1
    assert len(snapshot["records"]) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("pin", "pin"),
        ("remove", "remove"),
        ("approve", "approve"),
        ("hide", "hide"),
        ("rename", "rename"),
        ("useful", "approve"),
        ("not_useful", "remove"),
    ],
)
def test_action_adapter_maps_only_explicit_bounded_aliases(value, expected):
    assert normalize_feedback_action(value) == expected
    adapted = adapt_feedback_action_payload({"action": value, "marker": "synthetic"})
    assert adapted == {"action": expected, "marker": "synthetic"}


@pytest.mark.parametrize("value", ["liked", "not-useful", "Useful", 1, None])
def test_action_adapter_rejects_unknown_aliases(value):
    with pytest.raises(AgentContextFeedbackStoreError):
        normalize_feedback_action(value)


def test_store_accepts_only_validated_user_context_feedback(tmp_path):
    store = AgentContextFeedbackStore(tmp_path / "feedback")
    with pytest.raises(AgentContextFeedbackStoreError):
        store.append(context_item_payload())
    invalid = feedback_payload()
    invalid["policy_effect"] = "applied"
    with pytest.raises(AgentContextFeedbackStoreError):
        store.append(invalid)
    assert store.list() == ()


def test_append_is_idempotent_by_feedback_id_and_content_hash(tmp_path):
    store = AgentContextFeedbackStore(tmp_path / "feedback")
    payload = feedback_payload()
    first = store.append(payload)
    second = store.append(payload)
    assert first.created is True
    assert second.created is False
    assert second.idempotent is True
    assert first.content_hash == second.content_hash
    assert len(store.list()) == 1

    conflict = feedback_payload(reason="A different synthetic reason.")
    with pytest.raises(AgentContextFeedbackStoreError):
        store.append(conflict)
    assert len(store.list()) == 1


def test_idempotency_includes_review_metadata_without_applying_it(tmp_path):
    store = AgentContextFeedbackStore(tmp_path / "feedback")
    payload = feedback_payload()
    store.append(payload, disagreement=True)
    with pytest.raises(AgentContextFeedbackStoreError):
        store.append(payload, durable_apply_requested=True)
    persisted = store.get("feedback-001")
    assert persisted["recording_review"]["reason_codes"] == ["conflict"]
    assert persisted["feedback"]["policy_effect"] == "none"


def test_recording_review_is_quiet_unless_disagreement_or_apply_request(tmp_path):
    store = AgentContextFeedbackStore(tmp_path / "feedback")
    store.append(feedback_payload("feedback-001"))
    store.append(feedback_payload("feedback-002"), disagreement=True)
    store.append(feedback_payload("feedback-003"), durable_apply_requested=True)
    store.append(feedback_payload("feedback-004"), disagreement=True, durable_apply_requested=True)

    assert store.get("feedback-001")["recording_review"]["reason_codes"] == []
    assert store.get("feedback-002")["recording_review"]["reason_codes"] == ["conflict"]
    assert store.get("feedback-003")["recording_review"]["reason_codes"] == ["user_visible_writeback"]
    assert store.get("feedback-004")["recording_review"]["reason_codes"] == [
        "conflict", "user_visible_writeback",
    ]
    assert all(item["feedback"]["policy_effect"] == "none" for item in store.list())


def test_restricted_rename_is_persisted_as_redacted_proposed_signal(tmp_path):
    store = AgentContextFeedbackStore(tmp_path / "feedback")
    payload = feedback_payload(
        action="rename",
        classification="sensitive",
        redaction_state="metadata_only",
        feedback_review=review("policy_risk", "user_visible_writeback"),
        candidate_review=True,
        proposed_label="Synthetic restricted label",
        reason="Synthetic restricted reason.",
    )
    store.append(payload, durable_apply_requested=True)
    feedback = store.get("feedback-001")["feedback"]
    assert feedback["proposed_label"] == "Redacted label"
    assert feedback["reason"] is None
    assert feedback["learned_rule_candidate"]["status"] == "proposed"
    assert feedback["policy_effect"] == "none"


def test_list_is_bounded_deterministic_and_filterable(tmp_path):
    store = AgentContextFeedbackStore(tmp_path / "feedback")
    store.append(feedback_payload("feedback-003", action="hide"))
    store.append(feedback_payload("feedback-001", action="pin"))
    store.append(feedback_payload("feedback-002", action="approve"))

    records = store.list(limit=2)
    assert len(records) == 2
    assert [record["feedback_id"] for record in records] == sorted(record["feedback_id"] for record in records)
    assert [record["feedback"]["action"] for record in store.list(action="useful")] == ["approve"]
    assert len(store.list(candidate_status="proposed")) == 3
    with pytest.raises(AgentContextFeedbackStoreError):
        store.list(limit=0)
    with pytest.raises(AgentContextFeedbackStoreError):
        store.list(limit=1001)
    with pytest.raises(AgentContextFeedbackStoreError):
        store.list(candidate_status="applied")


def test_multiple_store_instances_and_threads_do_not_lose_records(tmp_path):
    root = tmp_path / "feedback"
    stores = [AgentContextFeedbackStore(root), AgentContextFeedbackStore(root)]

    def append(index: int):
        feedback_id = f"feedback-{index:03d}"
        return stores[index % 2].append(feedback_payload(feedback_id)).feedback_id

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(append, range(24)))
    assert len(set(ids)) == 24
    records = stores[0].list(limit=100)
    assert len(records) == 24
    assert len({record["feedback_id"] for record in records}) == 24
    snapshot = json.loads(stores[0].path.read_text(encoding="utf-8"))
    assert snapshot["revision"] == 24
    assert [item["feedback_id"] for item in snapshot["records"]] == sorted(ids)


def test_snapshot_tampering_and_corruption_fail_closed(tmp_path):
    store = AgentContextFeedbackStore(tmp_path / "feedback")
    store.append(feedback_payload())
    snapshot = json.loads(store.path.read_text(encoding="utf-8"))
    snapshot["records"][0]["feedback"]["raw_secret"] = "synthetic-forbidden-marker"
    store.path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(AgentContextFeedbackStoreError) as caught:
        store.list()
    assert "synthetic-forbidden-marker" not in str(caught.value)

    store.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(AgentContextFeedbackStoreError):
        store.list()


def test_persisted_hash_detects_record_changes(tmp_path):
    store = AgentContextFeedbackStore(tmp_path / "feedback")
    store.append(feedback_payload())
    snapshot = json.loads(store.path.read_text(encoding="utf-8"))
    snapshot["records"][0]["feedback"]["actor_ref"] = "different-actor"
    store.path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(AgentContextFeedbackStoreError, match="persisted hash mismatch"):
        store.get("feedback-001")


def test_root_must_be_injected_absolute_contained_and_link_free(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AgentContextFeedbackStoreError):
        AgentContextFeedbackStore(Path("relative-store"))
    with pytest.raises(AgentContextFeedbackStoreError):
        AgentContextFeedbackStore(tmp_path / "nested" / ".." / "escaped")

    target = tmp_path / "real-root"
    target.mkdir()
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")
    with pytest.raises(AgentContextFeedbackStoreError):
        AgentContextFeedbackStore(link)


def test_store_file_symlink_fails_closed_when_supported(tmp_path):
    root = tmp_path / "feedback"
    store = AgentContextFeedbackStore(root)
    external = tmp_path / "synthetic-external.json"
    external.write_text("{}", encoding="utf-8")
    try:
        store.path.symlink_to(external)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")
    with pytest.raises(AgentContextFeedbackStoreError):
        store.list()


def test_get_rejects_unsafe_or_unknown_identity_without_path_input(tmp_path):
    store = AgentContextFeedbackStore(tmp_path / "feedback")
    assert store.get("feedback-404") is None
    with pytest.raises(AgentContextFeedbackStoreError):
        store.get("../feedback-001")
