"""V2 detects drift in reviewed AST surfaces; behavioural pytest owns runtime proof."""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_telegram_todo_domain_truth.py"
V2 = ROOT / "specs" / "todo-domain-truth.v2.json"


def _audit():
    spec = importlib.util.spec_from_file_location("ttd_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path: Path, audit) -> Path:
    for relative in audit.CURRENT_SOURCES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def _replace(root: Path, path: str, before: str, after: str) -> None:
    target = root / path
    source = target.read_text(encoding="utf-8-sig")
    assert before in source, (path, before)
    target.write_text(source.replace(before, after, 1), encoding="utf-8")


def _committed() -> dict:
    return json.loads(V2.read_text(encoding="utf-8"))


def test_two_era_check_is_exact_and_explicitly_not_behavioral_proof():
    audit = _audit()
    committed = _committed()
    assert audit.validate_frozen_artifacts(ROOT) == []
    assert committed == audit.build_current_contract(ROOT)
    assert audit.validate_current_contract(committed, ROOT) == []
    assert committed["evidence_basis"] == audit.EVIDENCE_BASIS
    assert "repo_contracts" not in committed
    assert all(item["status"] == "reviewed_named_surfaces_unchanged_from_related_accepted_slices" for item in committed["reviewed_repo_contracts"])
    assert "runtime_behavior_is_owned_by_existing_behavioral_pytest" in committed["gaps"]
    assert audit.main(["--check", "--root", str(ROOT)]) == 0


def test_formatting_does_not_drift_normalized_ast(tmp_path):
    audit = _audit()
    fixture = _fixture(tmp_path, audit)
    _replace(fixture, "plugins/telegram/plugin.py", "truth_gate = gate_telegram_reply_text(\n", "truth_gate = gate_telegram_reply_text(\n\n")
    assert audit.validate_current_contract(_committed(), fixture) == []


def test_version_only_type_params_do_not_enter_ast_canonicalization():
    audit = _audit()

    class SyntheticNode(ast.AST):
        _fields = ("name", "type_params")

    node = SyntheticNode()
    node.name = "reviewed"
    node.type_params = []
    baseline = audit._normalized_ast_bytes(node)
    node.type_params = [ast.Name(id="T", ctx=ast.Load())]
    assert audit._normalized_ast_bytes(node) == baseline


@pytest.mark.parametrize(
    ("path", "before", "after", "surface"),
    (
        # Sol's original eight adversarial bypasses.
        ("src/memory_category_policy.py", "if normalized in TODO_ALIASES:", "if False:", "memory-todo-rejection"),
        ("src/tool_domains/todos.py", "if action == \"remove\" and not _is_confirmed(args):", "if False:", "todo-facade"),
        ("src/tool_domains/todos.py", "idempotency_key=args.get(\"idempotency_key\"),", "idempotency_key=None,", "todo-facade"),
        ("src/todo_domain_service.py", "self._Note.items == old,", "True,", "todo-cas-mutation"),
        ("src/todo_domain_service.py", "self._Note.owner == owner", "True", "todo-owner-clause"),
        ("src/telegram_truth_gate.py", "item.get(\"raw_content_visible\") is not False", "item.get(\"raw_content_visible\") is not True", "telegram-transaction-projector"),
        ("plugins/telegram/polling.py", "todo_truth_envelope = result.get(\"todo_truth_envelope\")", "todo_truth_envelope = None", "telegram-poll-normalize"),
        ("plugins/telegram/plugin.py", "        truth_gate = gate_telegram_reply_text(\n", "        if False:\n            truth_gate = gate_telegram_reply_text(\n", "telegram-pre-send-gate"),
        # Twelve additional reviewed-surface adversarial mutations.
        ("src/memory_category_policy.py", '"task",', '"task_alias",', "memory-aliases"),
        ("src/request_models.py", "return normalize_memory_category(v)", "return v", "memory-add-validator"),
        ("src/request_models.py", "return None if v is None else normalize_memory_category(v)", "return v", "memory-update-validator"),
        ("src/memory.py", "category = normalize_memory_category(category)", "category = category", "memory-writer"),
        ("routes/memory_routes.py", "category = normalize_memory_category(category)", "category = category", "memory-route"),
        ("src/ai_interaction.py", "category = normalize_memory_category(category)", "category = category", "memory-agent"),
        ("src/todo_transaction_receipts.py", '"evidence_refs",\n    }', '"evidence_refs", "raw",\n    }', "semantic-receipt-validator"),
        ("src/telegram_truth_gate.py", '"todo_item_removed": "remove",', '"todo_item_removed": "complete",', "transaction-claim-actions"),
        ("src/telegram_todo_truth.py", '"raw_content_visible": False,\n        "raw_identifiers_visible": False,', '"raw_content_visible": True,\n        "raw_identifiers_visible": False,', "telegram-truth-envelope"),
        ("plugins/telegram/polling.py", 'kwargs["todo_truth_envelope"] = todo_truth_envelope', 'kwargs["todo_truth_envelope"] = None', "telegram-poll-deliver"),
        ("plugins/telegram/webhook_service.py", 'kwargs["todo_truth_envelope"] = todo_truth_envelope', 'kwargs["todo_truth_envelope"] = None', "telegram-webhook-deliver"),
        ("src/telegram_context_policy.py", "max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES", "max_history_messages: int = 0", "telegram-context-policy"),
    ),
)
def test_reviewed_surface_mutations_drift_against_committed_v2(tmp_path, path, before, after, surface):
    audit = _audit()
    fixture = _fixture(tmp_path, audit)
    _replace(fixture, path, before, after)
    errors = audit.validate_current_contract(_committed(), fixture)
    assert f"semantic surface drift: {surface}" in errors


def test_missing_or_unparseable_reviewed_source_fails_closed(tmp_path):
    audit = _audit()
    fixture = _fixture(tmp_path, audit)
    (fixture / "src/todo_domain_service.py").unlink()
    assert any("malformed or missing reviewed source" in error for error in audit.validate_current_contract(_committed(), fixture))
    fixture = _fixture(tmp_path / "syntax", audit)
    (fixture / "src/todo_domain_service.py").write_text("def broken(:\n", encoding="utf-8")
    assert any("malformed or missing reviewed source" in error for error in audit.validate_current_contract(_committed(), fixture))


def test_v2_has_only_node_hashes_not_whole_file_hashes():
    value = _committed()
    assert {item["node_kind"] for item in value["semantic_surfaces"]} == {"constant", "function", "method"}
    assert all(set(item) == {"id", "path", "node_kind", "node_name", "normalized_ast_sha256"} for item in value["semantic_surfaces"])
    assert "sources" not in value
