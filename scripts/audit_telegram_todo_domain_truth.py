#!/usr/bin/env python3
"""Two-era Telegram Todo audit: frozen V1 provenance plus V2 surface drift.

V2 is deliberately not a behavioural proof.  Existing behavioural pytest owns
runtime behaviour; this no-import, formatting-independent audit detects drift
in the reviewed accepted implementation surfaces.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


V2_SCHEMA = "odysseus.todo_domain_truth.v2"
V2_PATH = "specs/todo-domain-truth.v2.json"
EVIDENCE_BASIS = "accepted_slice_plus_normalized_ast_drift_detection_not_behavioral_proof"
FROZEN_ARTIFACTS = (
    ("docs/plans/telegram-todo-domain-truth-contract.json", "odysseus.telegram_todo_domain_truth.v1", "8ef058b191f2b92e986fd1d1b9b28c6a7ae68cac", "a3b909044a95676180959c65d13db9bed874d4686b731bf11a0f7d917699e032"),
    ("specs/todo-domain-truth.v1.json", "odysseus.todo_domain_truth.v1", "b50245eaf5e44434e1b748aea0621d46f139ec38", "5fec677791eccb3c6c8f37a266b5adeeb0a3527898cc019a25bf712205f0ac6d"),
)
ACCEPTED_SLICES = ("TTD-01", "TTD-02A", "TTD-02B", "TTD-03A", "TTD-03B", "TTD-04", "TTD-05A", "TTD-05B", "TTD-06", "TTD-07", "TTD-07A0", "TTD-07A1", "TTD-07A2", "TTD-07A3", "TTD-07A4", "TTD-08A", "TTD-08B")
EXTERNAL_AUTHORITY = {"data_repair": False, "deploy": False, "environment_read": False, "live_telegram": False, "productive_data_access": False, "productive_rollover": False, "provider_access": False}
_VERSION_ONLY_AST_FIELDS = frozenset({"type_params"})

# id, path, node kind, node name.  Each node is intentionally small and exact.
SURFACE_SPECS = (
    ("memory-aliases", "src/memory_category_policy.py", "constant", "TODO_ALIASES"),
    ("memory-todo-rejection", "src/memory_category_policy.py", "function", "normalize_memory_category"),
    ("memory-add-validator", "src/request_models.py", "method", "MemoryAddRequest.validate_category"),
    ("memory-update-validator", "src/request_models.py", "method", "MemoryUpdateRequest.validate_category"),
    ("memory-writer", "src/memory.py", "method", "MemoryManager.add_entry"),
    ("memory-route", "routes/memory_routes.py", "function", "update_memory"),
    ("memory-agent", "src/ai_interaction.py", "function", "do_manage_memory"),
    ("todo-cas-mutation", "src/todo_domain_service.py", "method", "TodoDomainService._mutate"),
    ("todo-owner-clause", "src/todo_domain_service.py", "method", "TodoDomainService._owner_clause"),
    ("todo-facade", "src/tool_domains/todos.py", "function", "do_manage_todos"),
    ("semantic-receipt-validator", "src/todo_transaction_receipts.py", "function", "_validate_semantic_receipt"),
    ("transaction-claim-actions", "src/telegram_truth_gate.py", "constant", "_TODO_TRANSACTION_CLAIM_ACTIONS"),
    ("telegram-transaction-projector", "src/telegram_truth_gate.py", "function", "_project_telegram_todo_transaction"),
    ("telegram-truth-envelope", "src/telegram_todo_truth.py", "function", "build_telegram_todo_truth_envelope"),
    ("telegram-pre-send-gate", "plugins/telegram/plugin.py", "function", "_reply_with_gate"),
    ("telegram-poll-normalize", "plugins/telegram/polling.py", "function", "_normalize_agent_turn_result"),
    ("telegram-poll-deliver", "plugins/telegram/polling.py", "function", "_deliver_agent_reply"),
    ("telegram-webhook-deliver", "plugins/telegram/webhook_service.py", "function", "_deliver_agent_reply"),
    ("telegram-context-policy", "src/telegram_context_policy.py", "function", "build_telegram_turn_context"),
    ("telegram-audit-projection", "plugins/telegram/history_privacy.py", "function", "project_telegram_audit_record"),
    ("telegram-audit-receipt", "plugins/telegram/audit_store.py", "function", "_valid_receipt"),
    ("telegram-audit-history", "plugins/telegram/stores.py", "method", "TelegramInboxStore.audit_history"),
)
CURRENT_SOURCES = tuple(sorted({path for _, path, _, _ in SURFACE_SPECS}))

REVIEWED_REPO_CONTRACTS = (
    ("memory_todo_rejection", ("TTD-02A", "TTD-02B"), ("memory-aliases", "memory-todo-rejection", "memory-add-validator", "memory-update-validator", "memory-writer", "memory-route", "memory-agent")),
    ("canonical_todo_mutation", ("TTD-01", "TTD-03A", "TTD-03B"), ("todo-cas-mutation", "todo-owner-clause", "todo-facade")),
    ("semantic_todo_receipts", ("TTD-04", "TTD-05A", "TTD-05B", "TTD-06"), ("semantic-receipt-validator", "transaction-claim-actions", "telegram-transaction-projector", "telegram-truth-envelope")),
    ("telegram_truth_delivery_surface", ("TTD-07", "TTD-07A0", "TTD-07A1", "TTD-07A2", "TTD-07A3", "TTD-07A4"), ("telegram-pre-send-gate", "telegram-poll-normalize", "telegram-poll-deliver", "telegram-webhook-deliver")),
    ("bounded_non_authoritative_telegram_context", ("TTD-07",), ("telegram-context-policy",)),
    ("content_free_telegram_audit", ("TTD-08A", "TTD-08B"), ("telegram-audit-projection", "telegram-audit-receipt", "telegram-audit-history")),
)


class AuditError(ValueError):
    """The immutable history or reviewed current surface is unavailable."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _render(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"unreadable JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise AuditError(f"artifact must be an object: {path}")
    return value


def _frozen_git_value(root: Path, commit: str, path: str) -> dict[str, Any]:
    try:
        result = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{path}"], check=True, capture_output=True, text=True, encoding="utf-8")
        value = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"historical provenance unavailable for {path}") from exc
    if not isinstance(value, dict):
        raise AuditError(f"historical artifact must be an object: {path}")
    return value


def validate_frozen_artifacts(root: Path) -> list[str]:
    errors: list[str] = []
    for path, schema, commit, digest in FROZEN_ARTIFACTS:
        try:
            current = _read_json(root / path)
            historical = _frozen_git_value(root, commit, path)
            if current.get("schema") != schema:
                errors.append(f"frozen schema drift: {path}")
            if hashlib.sha256(_canonical(current)).hexdigest() != digest or hashlib.sha256(_canonical(historical)).hexdigest() != digest or current != historical:
                errors.append(f"frozen canonical digest or provenance drift: {path}")
        except AuditError as exc:
            errors.append(str(exc))
    return errors


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise AuditError(f"malformed or missing reviewed source: {path}") from exc


def _node(tree: ast.Module, kind: str, name: str) -> ast.AST:
    if kind == "function":
        found = [item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name]
    elif kind == "method":
        class_name, method_name = name.split(".", 1)
        classes = [item for item in ast.walk(tree) if isinstance(item, ast.ClassDef) and item.name == class_name]
        found = [item for cls in classes for item in cls.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name]
    elif kind == "constant":
        found = []
        for item in ast.walk(tree):
            targets = item.targets if isinstance(item, ast.Assign) else (item.target,) if isinstance(item, ast.AnnAssign) else ()
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                found.append(item)
    else:
        raise AuditError(f"unknown reviewed node kind: {kind}")
    if len(found) != 1:
        raise AuditError(f"expected one reviewed {kind}: {name}")
    return found[0]


def _normalized_ast_value(value: Any) -> Any:
    """Serialize AST semantics without locations or minor-version-only fields."""
    if isinstance(value, ast.AST):
        return {
            "node_type": type(value).__name__,
            "fields": {
                field: _normalized_ast_value(getattr(value, field, None))
                for field in sorted(value._fields)
                if field not in _VERSION_ONLY_AST_FIELDS
            },
        }
    if isinstance(value, (list, tuple)):
        return [_normalized_ast_value(item) for item in value]
    if value is Ellipsis:
        return {"literal_type": "ellipsis"}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise AuditError(f"unsupported AST semantic value: {type(value).__name__}")


def _normalized_ast_bytes(node: ast.AST) -> bytes:
    return _canonical(_normalized_ast_value(node))


def _surface_records(root: Path) -> list[dict[str, str]]:
    trees = {path: _parse(root / path) for path in CURRENT_SOURCES}
    records = []
    for surface_id, path, kind, name in SURFACE_SPECS:
        node = _node(trees[path], kind, name)
        records.append({"id": surface_id, "path": path, "node_kind": kind, "node_name": name, "normalized_ast_sha256": hashlib.sha256(_normalized_ast_bytes(node)).hexdigest()})
    return sorted(records, key=lambda value: value["id"])


def _reviewed_contracts() -> list[dict[str, Any]]:
    return [
        {"id": contract_id, "status": "reviewed_named_surfaces_unchanged_from_related_accepted_slices", "related_accepted_slices": list(slices), "semantic_surface_ids": list(surface_ids)}
        for contract_id, slices, surface_ids in REVIEWED_REPO_CONTRACTS
    ]


def build_current_contract(root: Path) -> dict[str, Any]:
    return {
        "schema": V2_SCHEMA,
        "evidence_basis": EVIDENCE_BASIS,
        "accepted_state": list(ACCEPTED_SLICES),
        "blocked_or_gated": {"TTD-07A5": "architecture_gated", "TTD-07A6": "blocked", "TTD-08C": "deferred", "TTD-09": "dependency_blocked", "TTD-10": "dependency_blocked"},
        "reviewed_repo_contracts": _reviewed_contracts(),
        "semantic_surfaces": _surface_records(root),
        "external_authority": EXTERNAL_AUTHORITY,
        "gaps": ["legacy_manage_notes_noncanonical_index_mutation", "legacy_item_ids_materialize_on_successful_canonical_mutation", "runtime_behavior_is_owned_by_existing_behavioral_pytest", "exact_digest_timing_execution_provider_delivery_unproven"],
        "historical_artifacts": [{"path": path, "schema": schema, "introduced_by": commit, "canonical_sha256": digest} for path, schema, commit, digest in FROZEN_ARTIFACTS],
    }


def validate_current_contract(committed: dict[str, Any], root: Path) -> list[str]:
    """Compare a committed V2 projection to a fixture without claiming behaviour."""
    if not isinstance(committed, dict):
        return ["committed v2 artifact must be an object"]
    try:
        generated = build_current_contract(root)
    except AuditError as exc:
        return [str(exc)]
    errors: list[str] = []
    expected_surfaces = {item["id"]: item for item in committed.get("semantic_surfaces", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    actual_surfaces = {item["id"]: item for item in generated["semantic_surfaces"]}
    if set(expected_surfaces) != set(actual_surfaces):
        errors.append("reviewed semantic-surface inventory drift")
    for surface_id in sorted(set(expected_surfaces) & set(actual_surfaces)):
        if expected_surfaces[surface_id] != actual_surfaces[surface_id]:
            errors.append(f"semantic surface drift: {surface_id}")
    for key in ("schema", "evidence_basis", "accepted_state", "blocked_or_gated", "reviewed_repo_contracts", "external_authority", "gaps", "historical_artifacts"):
        if committed.get(key) != generated[key]:
            errors.append(f"v2 metadata drift: {key}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        current = build_current_contract(root)
    except AuditError as exc:
        print(exc)
        return 1
    if args.print:
        print(_render(current), end="")
    if args.check:
        errors = validate_frozen_artifacts(root)
        try:
            committed = _read_json(root / V2_PATH)
        except AuditError as exc:
            errors.append(str(exc))
        else:
            errors.extend(validate_current_contract(committed, root))
            if _render(committed) != _render(current):
                errors.append(f"generated v2 differs: {V2_PATH}")
        if errors:
            print("\n".join(errors))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
