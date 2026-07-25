import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER_ROADMAP = ROOT / "docs" / "plans" / "open-work-completion-master-roadmap.json"


def test_accepted_uix_workbench_slices_are_not_reopened_in_active_queue():
    roadmap = json.loads(MASTER_ROADMAP.read_text(encoding="utf-8"))
    queue_items = roadmap["abc_execution_queue"]
    queue = {item["id"]: item for item in queue_items}

    assert len(queue) == len(queue_items)

    accepted_uix_slices = {f"UIX-ABC{number}" for number in range(13, 25)}
    active_claims = {
        claim["slice_id"]
        for claim in roadmap["current_position"]["active_claimed_slices"]
    }

    assert accepted_uix_slices.isdisjoint(queue)
    assert accepted_uix_slices.isdisjoint(active_claims)
    assert "nach UIX-ABC24" in roadmap["goal_command"]
