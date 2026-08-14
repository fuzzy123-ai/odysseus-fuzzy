import copy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "audit_unified_source_index_lifecycle_owners.py"
INVENTORY = REPO_ROOT / "docs" / "plans" / "unified-source-index-lifecycle-owner-inventory.json"
RECOVERY_CONTRACT = REPO_ROOT / "docs" / "plans" / "unified-source-index-owner-inventory-recovery-contract.json"


def _audit_module():
    spec = importlib.util.spec_from_file_location("usi_lifecycle_owner_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _invalid(audit, value):
    with pytest.raises(ValueError):
        audit.validate_inventory(value)


def test_committed_inventory_is_the_exact_canonical_recovery_contract():
    audit = _audit_module()
    committed_bytes = INVENTORY.read_text(encoding="utf-8")
    committed = json.loads(committed_bytes)
    contract = json.loads(RECOVERY_CONTRACT.read_text(encoding="utf-8"))
    contract_ulo = contract["ULO-00"]
    expected_from_contract = {
        "schema_version": 1,
        "kind": "odysseus.unified_source_index.lifecycle_owner_inventory",
        "action_fields": contract_ulo["action_fields"],
        "actions": contract_ulo["actions"],
        "unimplemented_requirement_ids": contract_ulo["unimplemented_requirement_ids"],
        "artifact_fields": contract_ulo["artifact_fields"],
        "artifacts": contract_ulo["artifacts"],
        "effect_flags": contract["effect_flags"],
    }

    assert audit.EXPECTED_INVENTORY == expected_from_contract
    assert committed == audit.EXPECTED_INVENTORY
    assert committed_bytes == audit.canonical_json(audit.EXPECTED_INVENTORY)
    audit.validate_inventory(committed)


def test_exact_action_artifact_blocker_and_effect_contract():
    audit = _audit_module()
    value = audit.EXPECTED_INVENTORY

    assert tuple(value["action_fields"]) == audit.ACTION_FIELDS
    assert tuple(action["action_id"] for action in value["actions"]) == (
        "auth_user_rename", "auth_user_delete", "personal_memory_record_delete",
        "personal_docs_exclude_file", "personal_docs_remove_directory", "user_portability_export",
        "user_portability_import", "system_backup_now", "admin_category_wipe", "usi13_sqlite_backup",
        "usi13_sqlite_restore", "usi13_projection_rebuild",
    )
    assert tuple(value["unimplemented_requirement_ids"]) == audit.UNIMPLEMENTED_REQUIREMENT_IDS
    assert tuple(value["artifact_fields"]) == audit.ARTIFACT_FIELDS
    assert tuple(artifact["artifact_id"] for artifact in value["artifacts"]) == (
        "usi_store_state_and_snapshots", "usi_index_truth_records", "usi_jobs",
        "usi_tombstones_and_record_history", "usi_projection_manifest_records", "usi_derived_run_records",
        "usi_chunk_fts", "usi_sqlite_backup_copy",
    )
    assert value["effect_flags"] == audit.EFFECT_FLAGS
    assert all(flag is False for key, flag in audit.EFFECT_FLAGS.items() if key != "synthetic_only")
    assert audit.EFFECT_FLAGS["synthetic_only"] is True
    assert all(action["execution_authorized"] is False and action["synthetic_only"] is True for action in value["actions"])
    assert len({action["canonical_action_owner"] for action in value["actions"]}) == len(value["actions"])
    assert all("system:usi_lifecycle" not in action["canonical_action_owner"] for action in value["actions"])
    assert len({artifact["artifact_id"] for artifact in value["artifacts"]}) == len(value["artifacts"])
    assert all(artifact["lifecycle_policy_owner"] for artifact in value["artifacts"])
    assert "account_domain_erasure_usi_integration" not in {action["action_id"] for action in value["actions"]}


def test_each_scalar_contract_value_is_fail_closed_against_drift():
    audit = _audit_module()
    expected = audit.EXPECTED_INVENTORY

    for top_level in ("actions", "artifacts"):
        for index, row in enumerate(expected[top_level]):
            for field, original in row.items():
                changed = copy.deepcopy(expected)
                if isinstance(original, str):
                    changed[top_level][index][field] = f"{original} drift"
                elif isinstance(original, bool):
                    changed[top_level][index][field] = not original
                elif isinstance(original, list):
                    assert original
                    changed[top_level][index][field][0] = f"{original[0]} drift"
                else:
                    raise AssertionError((top_level, index, field, type(original)))
                _invalid(audit, changed)

    for key, original in expected["effect_flags"].items():
        changed = copy.deepcopy(expected)
        changed["effect_flags"][key] = not original
        _invalid(audit, changed)
        changed = copy.deepcopy(expected)
        del changed["effect_flags"][key]
        _invalid(audit, changed)
        changed = copy.deepcopy(expected)
        changed["effect_flags"][key] = "false"
        _invalid(audit, changed)


def test_structural_order_cardinality_and_forbidden_authority_drift_fail_closed():
    audit = _audit_module()
    expected = audit.EXPECTED_INVENTORY

    for key in tuple(expected):
        changed = copy.deepcopy(expected)
        del changed[key]
        _invalid(audit, changed)
    changed = copy.deepcopy(expected)
    changed["unexpected"] = "not allowed"
    _invalid(audit, changed)

    for collection, id_key in (("actions", "action_id"), ("artifacts", "artifact_id")):
        changed = copy.deepcopy(expected)
        changed[collection].append(copy.deepcopy(changed[collection][0]))
        _invalid(audit, changed)
        changed = copy.deepcopy(expected)
        changed[collection].reverse()
        _invalid(audit, changed)
        changed = copy.deepcopy(expected)
        changed[collection][0][id_key] = "invented_lifecycle_authority"
        _invalid(audit, changed)

    for fields_key in ("action_fields", "artifact_fields", "unimplemented_requirement_ids"):
        changed = copy.deepcopy(expected)
        changed[fields_key].reverse()
        _invalid(audit, changed)
        changed = copy.deepcopy(expected)
        changed[fields_key].append("unexpected")
        _invalid(audit, changed)

    changed = copy.deepcopy(expected)
    changed["actions"][0]["canonical_action_owner"] = "system:usi_lifecycle"
    _invalid(audit, changed)
    changed = copy.deepcopy(expected)
    changed["actions"][1]["scope"] = "completed domain and USI erasure"
    _invalid(audit, changed)
    changed = copy.deepcopy(expected)
    changed["actions"].append({"action_id": "factory_reset_mapping"})
    _invalid(audit, changed)
    changed = copy.deepcopy(expected)
    changed["artifacts"][6]["lifecycle_policy_owner"] = "selected external provider complete rebuild receipt"
    _invalid(audit, changed)
    changed = copy.deepcopy(expected)
    changed["artifacts"][7]["persistent_members"].append("query_cache")
    _invalid(audit, changed)


def test_parser_rejects_malformed_duplicate_and_non_object_roots():
    audit = _audit_module()
    with TemporaryDirectory() as temporary:
        tmp_path = Path(temporary)
        malformed = tmp_path / "malformed.json"
        malformed.write_bytes(b"{")
        with pytest.raises(ValueError, match="invalid lifecycle owner inventory"):
            audit.load_inventory(malformed)

        duplicate = tmp_path / "duplicate.json"
        duplicate.write_bytes(b'{"a": {"b": 1, "b": 2}}')
        with pytest.raises(ValueError, match="duplicate JSON object key"):
            audit.load_inventory(duplicate)

        array_root = tmp_path / "array.json"
        array_root.write_bytes(b"[]")
        with pytest.raises(ValueError, match="root must be an object"):
            audit.load_inventory(array_root)


def test_check_is_read_only_requires_canonical_bytes_and_rejects_obsolete_name(capsys):
    audit = _audit_module()
    with TemporaryDirectory() as temporary:
        tmp_path = Path(temporary)
        canonical = tmp_path / "canonical.json"
        canonical.write_bytes(audit.canonical_json(audit.EXPECTED_INVENTORY).encode("utf-8"))
        before = canonical.read_bytes()
        assert audit.main(["--check", "--path", str(canonical)]) == 0
        assert canonical.read_bytes() == before
        assert capsys.readouterr().out == audit.canonical_json(audit.EXPECTED_INVENTORY)

        noncanonical = tmp_path / "noncanonical.json"
        noncanonical.write_bytes((json.dumps(audit.EXPECTED_INVENTORY, sort_keys=True) + "\n").encode("utf-8"))
        with pytest.raises(SystemExit, match="byte-canonical"):
            audit.main(["--check", "--path", str(noncanonical)])
        assert json.loads(noncanonical.read_text(encoding="utf-8")) == audit.EXPECTED_INVENTORY

        obsolete = tmp_path / "unified-source-index-lifecycle-inventory.json"
        obsolete.write_bytes(audit.canonical_json(audit.EXPECTED_INVENTORY).encode("utf-8"))
        with pytest.raises(SystemExit, match="obsolete lifecycle inventory filename"):
            audit.main(["--check", "--path", str(obsolete)])
