import pytest

from src.agent_report_store import AgentReport, AgentReportStoreError, reduce_agent_report


def _report(**overrides):
    payload = {
        "report_id": "ABC2 Bob Report",
        "plan_id": "odysseus-multiagent-roadmap",
        "node_id": "read-only-agent-report-protocol",
        "agent_id": "Bob",
        "role_id": "runtime_mapping",
        "capsule_id": "ABC2-bob-agent-report-protocol-scout",
        "read_scope": ["src/agent_report_store.py", "tests/*.py", "specs/roadmaps/"],
        "observations": [
            {
                "id": "schema-slice",
                "summary": "AgentReport can stay offline and produce reducer events.",
                "source_refs": ["src/agent_report_store.py"],
                "confidence": "high",
            }
        ],
        "source_refs": ["src/agent_report_store.py", "specs/roadmaps/odysseus-multiagent-roadmap.v1.json"],
        "evidence_refs": ["bob-report"],
        "blockers": [],
        "collision_candidates": [
            {
                "id": "markdown-json-authority",
                "summary": "Markdown authority conflicts with JSON source-of-truth.",
                "evidence_refs": ["specs/roadmaps/odysseus-multiagent-roadmap.v1.json"],
            }
        ],
        "gate_observations": [
            {
                "id": "read-only-scope",
                "summary": "Report stays read-only and cannot mutate PlanGraph.",
                "confidence": "medium",
            }
        ],
        "proposed_plan_events": [
            {
                "event_type": "context_summary_updated",
                "reason": "Accepted summary can seed future context after reducer review.",
            }
        ],
        "confidence": "high",
        "redaction_summary": "No secrets, raw chats, provider output, or server logs included.",
        "created_at": "2026-06-21T08:45:00Z",
        "status": "submitted",
    }
    payload.update(overrides)
    return AgentReport.create(**payload)


def test_valid_report_normalizes_and_reduces_deterministically():
    report = _report()

    assert report.report_id == "abc2-bob-report"
    assert report.read_scope == ("specs/roadmaps/", "src/agent_report_store.py", "tests/*.py")

    reduction = reduce_agent_report(report)

    assert reduction.audit_summary() == {
        "report_id": "abc2-bob-report",
        "event_count": 3,
        "context_summary_count": 1,
        "warning_count": 0,
        "rejected_reason_count": 0,
        "event_types": ("collision_observed", "gate_observed", "context_summary_updated"),
        "context_summary_ids": ("schema-slice",),
    }
    assert reduction.to_dict()["context_summaries"][0]["summary"] == (
        "AgentReport can stay offline and produce reducer events."
    )


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.py",
        "/tmp/escape.py",
        r"C:\repo\secret.py",
        r"src\agent_report_store.py",
    ],
)
def test_unsafe_read_scope_paths_are_rejected(bad_path):
    with pytest.raises(AgentReportStoreError):
        _report(read_scope=[bad_path])


def test_source_refs_must_exist_and_stay_in_scope():
    with pytest.raises(AgentReportStoreError, match="source_ref must not be empty"):
        _report(source_refs=[])

    with pytest.raises(AgentReportStoreError, match="inside read_scope"):
        _report(source_refs=["src/outside.py"])


def test_redaction_summary_is_required():
    with pytest.raises(AgentReportStoreError, match="redaction_summary must not be empty"):
        _report(redaction_summary="")


@pytest.mark.parametrize(
    "event_type",
    ["node_completed", "node_promoted_to_claimable", "node_claimed", "report_accepted"],
)
def test_forbidden_completion_or_claimability_events_are_rejected(event_type):
    with pytest.raises(AgentReportStoreError, match="must not complete, claim, promote, or accept"):
        _report(proposed_plan_events=[{"event_type": event_type, "reason": "unsafe shortcut"}])


def test_verified_done_claims_are_rejected_even_inside_structured_items():
    with pytest.raises(AgentReportStoreError, match="must not claim verified done"):
        _report(observations=[{"id": "bad", "summary": "verified_done true"}])


def test_blockers_and_conflicts_reduce_to_events_not_facts():
    report = _report(
        blockers=[{"id": "missing-scope", "summary": "read_scope missing for source"}],
        collision_candidates=[{"id": "hotfile", "summary": "Two nodes touch the same file"}],
        gate_observations=[{"id": "redaction", "summary": "redaction summary is missing"}],
        proposed_plan_events=[],
    )

    reduction = report.reduce()

    assert tuple(event.event_type for event in reduction.reduced_events) == (
        "node_blocked",
        "collision_observed",
        "gate_observed",
    )
    assert "verified_done" not in repr(reduction.to_dict())


def test_audit_summary_avoids_raw_observation_dumps():
    long_summary = "raw output " * 80
    report = _report(observations=[{"id": "long", "summary": long_summary, "source_refs": ["src/agent_report_store.py"]}])

    summary = report.audit_summary()

    assert summary["observation_count"] == 1
    assert summary["proposed_event_count"] == 1
    assert long_summary not in repr(summary)
    assert len(report.observations[0]["summary"]) < len(long_summary)
