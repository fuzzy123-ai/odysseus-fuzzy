from src.release_orchestration_status import build_release_orchestration_status
from src.release_readiness_pipeline import build_current_release_readiness_pipeline


def test_current_status_summarizes_pipeline_for_dashboard():
    pipeline = build_current_release_readiness_pipeline()

    status = build_release_orchestration_status(pipeline)

    assert status.status == "go"
    assert status.external_release_go is True
    assert status.active_owners == ("Charlie",)
    assert status.parallel_candidate_ids == ()
    assert status.sequential_gate_ids == (
        "REL-final-external-review",
    )
    assert status.next_action_ids == (
        "REL-final-external-review",
    )


def test_status_to_dict_is_stable():
    status = build_release_orchestration_status(build_current_release_readiness_pipeline())

    assert status.to_dict() == {
        "status": "go",
        "external_release_go": True,
        "active_owners": ("Charlie",),
        "parallel_candidate_ids": (),
        "sequential_gate_ids": (
            "REL-final-external-review",
        ),
        "next_action_ids": (
            "REL-final-external-review",
        ),
    }
