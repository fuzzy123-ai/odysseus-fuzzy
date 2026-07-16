import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER_ROADMAP = ROOT / "docs" / "plans" / "open-work-completion-master-roadmap.json"


def test_uix_abc13_remains_open_until_product_semantics_are_confirmed():
    roadmap = json.loads(MASTER_ROADMAP.read_text(encoding="utf-8"))
    queue_items = roadmap["abc_execution_queue"]
    queue = {item["id"]: item for item in queue_items}
    current = roadmap["current_position"]

    assert len(queue) == len(queue_items)

    uix13 = queue["UIX-ABC13"]
    assert uix13["status"] == "open"
    assert uix13["claimable"] is False
    assert uix13["claim_blocker"] == "UIX-WORKBENCH-PRODUCT-SEMANTICS-CONFIRMATION"
    assert current["product_semantics_gated_open_slices"] == ["UIX-ABC13"]
    assert current["dependency_ready_repo_only_slices"] == []

    expected_dependencies = {
        "UIX-ABC13": [],
        "UIX-ABC14": ["UIX-ABC13"],
        "UIX-ABC15": ["UIX-ABC13"],
        "UIX-ABC16": ["UIX-ABC13", "UIX-ABC14"],
        "UIX-ABC17": ["UIX-ABC16"],
        "UIX-ABC18": ["UIX-ABC14"],
        "UIX-ABC19": ["UIX-ABC18"],
        "UIX-ABC20": ["UIX-ABC16", "UIX-ABC19"],
        "UIX-ABC21": ["UIX-ABC17", "UIX-ABC19", "UIX-ABC20"],
        "UIX-ABC22": ["UIX-ABC13", "UIX-ABC14", "UIX-ABC19"],
        "UIX-ABC23": ["UIX-ABC17", "UIX-ABC21"],
        "UIX-ABC24": [
            "UIX-ABC13",
            "UIX-ABC14",
            "UIX-ABC15",
            "UIX-ABC16",
            "UIX-ABC17",
            "UIX-ABC18",
            "UIX-ABC19",
            "UIX-ABC20",
            "UIX-ABC21",
            "UIX-ABC22",
            "UIX-ABC23",
        ],
    }
    for slice_id, dependencies in expected_dependencies.items():
        assert queue[slice_id].get("depends_on", []) == dependencies
        assert set(dependencies) <= queue.keys()
        if slice_id != "UIX-ABC13":
            assert queue[slice_id]["status"] == "blocked_by_dependency"

    visiting = set()
    visited = set()

    def visit(slice_id):
        assert slice_id not in visiting
        if slice_id in visited:
            return
        visiting.add(slice_id)
        for dependency in expected_dependencies[slice_id]:
            if dependency in expected_dependencies:
                visit(dependency)
        visiting.remove(slice_id)
        visited.add(slice_id)

    for slice_id in expected_dependencies:
        visit(slice_id)
    assert visited == set(expected_dependencies)

    decision = roadmap["recommended_next_human_decision"]
    assert "do not claim UIX-ABC13" in decision
    assert "continue TAX0" not in decision
