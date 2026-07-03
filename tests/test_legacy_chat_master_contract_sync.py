import json
from pathlib import Path

from src.legacy_chat_contracts import build_legacy_chat_contracts


ROADMAP_PATH = Path("docs/plans/legacy-chat-new-functions-master-roadmap.json")


def test_legacy_chat_contract_manifest_matches_master_roadmap_evidence():
    roadmap = json.loads(ROADMAP_PATH.read_text(encoding="utf-8"))
    manifest = build_legacy_chat_contracts()
    manifest_by_id = {contract["slice_id"]: contract for contract in manifest["contracts"]}

    assert set(manifest_by_id) == {f"lc{idx}" for idx in range(1, 11)}
    for idx in range(1, 11):
        key = f"lc{idx}_backend_contract"
        expected = set(roadmap["evidence"][key])
        observed = {
            f"{endpoint['method']} {endpoint['path']}"
            for endpoint in manifest_by_id[f"lc{idx}"]["endpoints"]
        }
        assert observed == expected

    assert roadmap["evidence"]["ui_agent_contract_manifest"] == ["GET /api/legacy-chat/contracts"]
    assert manifest["ui_execution_required"] is True
    assert manifest["ui_code_included"] is False
    assert manifest["live_execution_performed"] is False


def test_legacy_chat_master_roadmap_has_no_open_backend_contracts():
    roadmap = json.loads(ROADMAP_PATH.read_text(encoding="utf-8"))
    statuses = {slice_item["id"]: slice_item["status"] for slice_item in roadmap["slices"]}

    assert roadmap["open_backend_contracts"] == []
    assert statuses["LC10"] == "backend_ready_live_gated"
    assert all(statuses[f"LC{idx}"] == "backend_ready" for idx in range(1, 10))
