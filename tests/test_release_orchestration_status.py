from src.release_orchestration_status import build_release_orchestration_status
from src.release_readiness_pipeline import build_current_release_readiness_pipeline


def test_current_status_summarizes_pipeline_for_dashboard():
    pipeline = build_current_release_readiness_pipeline()

    status = build_release_orchestration_status(pipeline)

    assert status.status == "blocked"
    assert status.external_release_go is False
    assert status.active_owners == ("Bob", "Charlie")
    assert status.parallel_candidate_ids == ()
    assert status.sequential_gate_ids == (
        "REL-provider-proof-evidence",
        "REL-partial-manual-evidence-closeout",
    )
    assert status.next_action_ids == (
        "REL-provider-proof-evidence",
        "REL-partial-manual-evidence-closeout",
    )


def test_status_to_dict_is_stable():
    status = build_release_orchestration_status(build_current_release_readiness_pipeline())

    assert status.to_dict() == {
        "status": "blocked",
        "external_release_go": False,
            "active_owners": ("Bob", "Charlie"),
            "parallel_candidate_ids": (),
        "sequential_gate_ids": (
            "REL-provider-proof-evidence",
            "REL-partial-manual-evidence-closeout",
        ),
            "next_action_ids": (
                "REL-provider-proof-evidence",
                "REL-partial-manual-evidence-closeout",
            ),
    }
