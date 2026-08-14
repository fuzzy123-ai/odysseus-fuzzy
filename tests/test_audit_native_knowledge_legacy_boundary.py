import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_native_knowledge_legacy_boundary.py"
SPEC = importlib.util.spec_from_file_location("legacy_boundary_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def committed_payload():
    return json.loads(
        (Path(__file__).parents[1] / "docs" / "plans" / "native-knowledge-runtime-legacy-boundary-inventory.json").read_text(encoding="utf-8")
    )


def test_committed_inventory_is_deterministic_content_free_and_complete():
    payload = committed_payload()
    validated = audit.validate_inventory(payload)

    assert audit.canonical_json(validated) == audit.canonical_json(audit.validate_inventory(payload))
    assert validated["boundaries"] == [audit.EXPECTED_BOUNDARIES[source_id] for source_id in sorted(audit.EXPECTED_BOUNDARIES)]
    assert {entry["boundary_kind"] for entry in validated["boundaries"]} == {"core", "app", "plugin"}
    assert {entry["disposition"] for entry in validated["boundaries"]} == {"retire", "excluded"}
    assert validated["content_policy"] == audit.CONTENT_POLICY
    assert validated["live_effect"] is False


def test_check_mode_accepts_canonical_temporary_inventory(tmp_path, capsys):
    payload = audit.validate_inventory(committed_payload())
    inventory = tmp_path / "inventory.json"
    inventory.write_text(audit.canonical_json(payload), encoding="utf-8")

    assert audit.main(["--inventory", str(inventory), "--check"]) == 0
    assert capsys.readouterr().out == audit.canonical_json(payload)


def test_validation_safely_rejects_incomplete_or_content_bearing_inventory():
    payload = committed_payload()
    payload["boundaries"] = payload["boundaries"][:-1]
    with pytest.raises(audit.InventoryError, match="declared legacy boundary tuples"):
        audit.validate_inventory(payload)

    payload = committed_payload()
    payload["boundaries"][0]["raw_content"] = "forbidden"
    with pytest.raises(audit.InventoryError, match="declared keys"):
        audit.validate_inventory(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("boundary_kind", "plugin"),
        ("disposition", "excluded"),
        ("retirement_gate", "not_current_product_runtime"),
        ("legacy_family", "arbitrary-content-is-not-an-identifier"),
    ],
)
def test_validation_rejects_semantically_mismatched_boundary_tuples(field, value):
    payload = committed_payload()
    payload["boundaries"][0][field] = value

    with pytest.raises(audit.InventoryError, match="declared legacy boundary tuples"):
        audit.validate_inventory(payload)


def test_load_inventory_safely_hides_invalid_json_details(tmp_path):
    inventory = tmp_path / "invalid.json"
    inventory.write_text("{", encoding="utf-8")

    with pytest.raises(audit.InventoryError, match="unavailable or invalid JSON"):
        audit.load_inventory(inventory)
