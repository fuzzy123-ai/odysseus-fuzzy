import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER_PATH = ROOT / "docs" / "plans" / "ai-lens-master-roadmap.json"
TECH_PATH = ROOT / "docs" / "plans" / "ai-lens-technical-roadmap.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _by_id(items: list[dict]) -> dict[str, dict]:
    return {item["id"]: item for item in items}


def test_microscope_offline_contract_is_separate_from_live_sampling() -> None:
    roadmap = _load(TECH_PATH)
    slices = _by_id(roadmap["slice_queue"])

    offline = slices["AIL-TECH-9A-local-model-capability-contract"]
    assert offline["class"] == "repo_only"
    assert offline["status"] == "open"
    assert offline["depends_on"] == ["AIL-TECH-1-event-contract"]
    assert offline["allowed_paths"] == [
        "src/ai_lens_local_model.py",
        "tests/test_ai_lens_local_model.py",
    ]
    assert "import, load or start a model runtime" in offline["forbidden_actions"]

    live = slices["AIL-TECH-9B-local-model-sampling-smoke"]
    assert live["class"] == "needs_live_go"
    assert live["status"] == "blocked_by_live_gate"
    assert live["depends_on"] == [offline["id"]]
    assert live["gate_required"] == "AIL-TECH-GATE-local-internals-runtime"
    assert live["allowed_paths"] == ["data/reports/ai_lens/**"]


def test_local_runtime_gate_blocks_only_the_live_sampling_smoke() -> None:
    roadmap = _load(TECH_PATH)
    gates = _by_id(roadmap["gate_queue"])
    gate = gates["AIL-TECH-GATE-local-internals-runtime"]

    assert gate["blocks"] == ["AIL-TECH-9B-local-model-sampling-smoke"]
    assert gate["next_safe_slice"] == "AIL-TECH-9A-local-model-capability-contract"
    assert "without runtime imports or I/O" in gate["safe_preparation_done"]


def test_ai_lens_master_points_to_the_offline_frontier() -> None:
    master = _load(MASTER_PATH)
    sub_roadmaps = _by_id(master["sub_roadmaps"])
    gates = _by_id(master["gate_queue"])

    assert sub_roadmaps["AIL-TECH"]["status"] == (
        "offline_microscope_contract_open_live_sampling_gated"
    )
    assert gates["AIL-GATE-local-model-internals"]["next_safe_slice"] == (
        "AIL-TECH-9A-local-model-capability-contract"
    )
    assert "AIL-TECH-9A" in master["recommended_next_step"]


def test_ai_lens_technical_slice_dependencies_resolve_and_are_acyclic() -> None:
    roadmap = _load(TECH_PATH)
    slices = _by_id(roadmap["slice_queue"])
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(slice_id: str) -> None:
        if slice_id in visiting:
            raise AssertionError(f"cycle at {slice_id}")
        if slice_id in visited:
            return
        visiting.add(slice_id)
        for dependency in slices[slice_id].get("depends_on", []):
            assert dependency in slices
            visit(dependency)
        visiting.remove(slice_id)
        visited.add(slice_id)

    for slice_id in slices:
        visit(slice_id)
