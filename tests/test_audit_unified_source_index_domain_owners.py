"""Focused fail-closed checks for the UDA-00 static owner inventory."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "audit_unified_source_index_domain_owners.py"
CONTRACT_PATH = ROOT / "docs" / "plans" / "unified-source-index-owner-inventory-recovery-contract.json"
INVENTORY_PATH = ROOT / "docs" / "plans" / "unified-source-index-domain-owner-inventory.json"

SPEC = importlib.util.spec_from_file_location("domain_owner_audit", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class DomainOwnerInventoryAuditTests(unittest.TestCase):
    def test_committed_inventory_is_exact_contract_and_canonical(self) -> None:
        raw = INVENTORY_PATH.read_bytes()
        payload = audit.load_payload(raw)
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["UDA-00"]

        audit.validate_file()
        self.assertEqual(raw, audit.canonical_bytes(payload))
        self.assertEqual(payload, audit.EXPECTED_PAYLOAD)
        self.assertEqual(set(payload), set(contract["top_level_fields"]))
        self.assertEqual(payload["domains"], contract["domains"])
        self.assertEqual(payload["future_domains"], contract["future_domains"])
        self.assertEqual(payload["domain_fields"], contract["domain_fields"])
        self.assertEqual(payload["future_domain_fields"], contract["future_domain_fields"])
        self.assertEqual(payload["legacy_excluded_identifiers"], contract["legacy_excluded_identifiers"])
        self.assertEqual(payload["effect_flags"], json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["effect_flags"])

    def test_check_is_read_only_and_cannot_target_stale_filename(self) -> None:
        before = INVENTORY_PATH.read_bytes()
        passed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        stale = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--check",
                "--path",
                "unified-source-index-domain-inventory.json",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(INVENTORY_PATH.read_bytes(), before)
        self.assertEqual(audit.INVENTORY_PATH, INVENTORY_PATH)
        self.assertNotEqual(stale.returncode, 0)

    def test_malformed_duplicate_and_non_object_roots_fail_closed(self) -> None:
        for raw in (b"{", b'{"domain_id": 1, "domain_id": 2}', b"[]", b"null"):
            with self.subTest(raw=raw), self.assertRaises(audit.InventoryError):
                audit.load_payload(raw)

    def test_noncanonical_bytes_fail_even_when_content_is_equal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "inventory.json"
            path.write_text(json.dumps(audit.EXPECTED_PAYLOAD), encoding="utf-8")
            with self.assertRaises(audit.InventoryError):
                audit.validate_file(path)

    def test_schema_row_order_and_tuple_drift_fail_closed(self) -> None:
        mutations = {
            "missing_top_level": lambda value: value.pop("scope"),
            "extra_top_level": lambda value: value.update({"unexpected": True}),
            "reordered_domain_fields": lambda value: value["domain_fields"].reverse(),
            "reordered_rows": lambda value: value["domains"].reverse(),
            "changed_current_id": lambda value: value["domains"][0].update({"domain_id": "native_knowledge"}),
            "personal_docs_truth_drift": lambda value: value["domains"][1].update({"canonical_truth_owner": "USI"}),
            "memory_review_drift": lambda value: value["domains"][0].update({"review_seam": "approved"}),
            "usi_writer_promotion": lambda value: value["domains"][0].update({"write_mutation_owner": "USI"}),
            "plugin_provider_promotion": lambda value: value["domains"][0].update({"provider_seam": "plugin.obsidian.memory"}),
            "native_knowledge_current_domain": lambda value: value["domains"].append(copy.deepcopy(value["future_domains"][0])),
            "native_knowledge_active_reader": lambda value: value["future_domains"][0].update({"exact_reader": "active_reader"}),
            "effect_flag_widening": lambda value: value["effect_flags"].update({"provider_access": True}),
            "effect_flag_missing": lambda value: value["effect_flags"].pop("synthetic_only"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = copy.deepcopy(audit.EXPECTED_PAYLOAD)
                mutate(payload)
                with self.assertRaises(audit.InventoryError):
                    audit.validate_payload(payload)

    def test_exact_id_order_exclusions_and_false_effects(self) -> None:
        payload = audit.EXPECTED_PAYLOAD
        self.assertEqual([row["domain_id"] for row in payload["domains"]], ["personal_memory", "personal_docs"])
        self.assertEqual([row["domain_id"] for row in payload["future_domains"]], ["native_knowledge"])
        self.assertEqual(payload["future_domains"][0]["current_selection_state"], "not_selected_no_current_truth")
        self.assertEqual(
            payload["legacy_excluded_identifiers"],
            ["plugin.obsidian.memory", "plugin.obsidian.orca", "plugin.obsidian.raptor"],
        )
        self.assertEqual(
            payload["effect_flags"],
            {
                "adapter_registration": False,
                "domain_mutation": False,
                "execution_authorized": False,
                "index_write": False,
                "live_action": False,
                "productive_source_access": False,
                "provider_access": False,
                "synthetic_only": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
