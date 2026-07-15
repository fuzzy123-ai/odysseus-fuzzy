"""Build and verify the content-free TAX0 tool registry inventory.

The audit intentionally reads source code as data instead of importing runtime
modules.  That keeps the result deterministic and avoids plugin, provider,
database, network, or application-startup side effects.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("docs/plans/tool-taxonomy-inventory.json")

SOURCE_PATHS = (
    "src/agent_tools/__init__.py",
    "src/tool_schema_definitions.py",
    "src/tool_index.py",
    "src/agent_loop_prompts.py",
    "src/tool_execution.py",
    "static/js/admin.js",
    "src/tool_catalog.py",
    "src/tool_registry.py",
    "src/plugin_system.py",
    "src/mcp_manager.py",
    "src/sensitive_local_worker.py",
    "plugins/telegram/plugin.py",
    "plugins/obsidian/plugin.py",
)

EXPECTED_COUNTS = {
    "runtime_tags": 78,
    "function_schemas": 83,
    "schema_without_runtime_tag": 6,
    "runtime_without_function_schema": 1,
    "admin_metadata": 31,
    "runtime_without_admin_metadata": 48,
    "stale_admin_metadata": 1,
}
EXPECTED_SCHEMA_WITHOUT_RUNTIME = (
    "manage_assistant",
    "manage_embeddings",
    "manage_personal_docs",
    "manage_plugins",
    "manage_presets",
    "tail_serve_output",
)
EXPECTED_RUNTIME_WITHOUT_SCHEMA = ("generate_image",)
EXPECTED_STALE_ADMIN_METADATA = ("manage_rag",)


class InventoryError(ValueError):
    """Raised when a source cannot be read as the declared static contract."""


def _read(root: Path, relative_path: str) -> str:
    path = root / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise InventoryError(f"cannot read declared source {relative_path}: {exc}") from exc


def _parse(root: Path, relative_path: str) -> ast.Module:
    try:
        return ast.parse(_read(root, relative_path), filename=relative_path)
    except SyntaxError as exc:
        raise InventoryError(f"cannot parse declared source {relative_path}: {exc}") from exc


def _assignment_node(tree: ast.Module, name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    raise InventoryError(f"missing declared assignment {name}")


def _literal_assignment(root: Path, relative_path: str, name: str) -> Any:
    node = _assignment_node(_parse(root, relative_path), name)
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError) as exc:
        raise InventoryError(f"assignment {name} in {relative_path} is not static") from exc


def _dict_keys(root: Path, relative_path: str, name: str) -> set[str]:
    node = _assignment_node(_parse(root, relative_path), name)
    if not isinstance(node, ast.Dict):
        raise InventoryError(f"assignment {name} in {relative_path} is not a dict")
    keys = {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    if len(keys) != len(node.keys):
        raise InventoryError(f"assignment {name} in {relative_path} has dynamic keys")
    return keys


def _schema_names(root: Path) -> set[str]:
    schemas = _literal_assignment(
        root,
        "src/tool_schema_definitions.py",
        "FUNCTION_TOOL_SCHEMAS",
    )
    names: set[str] = set()
    for schema in schemas:
        if not isinstance(schema, dict):
            raise InventoryError("function schema entry is not an object")
        function = schema.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or not name:
            raise InventoryError("function schema entry has no static name")
        if name in names:
            raise InventoryError(f"duplicate function schema name {name}")
        names.add(name)
    return names


def _admin_metadata_keys(root: Path) -> set[str]:
    text = _read(root, "static/js/admin.js")
    match = re.search(r"^const TOOL_META = \{(?P<body>.*?)^\};", text, re.MULTILINE | re.DOTALL)
    if not match:
        raise InventoryError("static/js/admin.js has no static TOOL_META object")
    return set(
        re.findall(
            r"^\s*([A-Za-z_][A-Za-z0-9_]*):\s*\{",
            match.group("body"),
            re.MULTILINE,
        )
    )


def _string_values(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values: set[str] = set()
        for item in node.elts:
            values.update(_string_values(item))
        return values
    return set()


def _dispatcher_condition_ids(root: Path) -> set[str]:
    tree = _parse(root, "src/tool_execution.py")
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_tool_block_impl"
        ),
        None,
    )
    if function is None:
        raise InventoryError("src/tool_execution.py has no _execute_tool_block_impl")
    names: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
            continue
        if not isinstance(node.left, ast.Name) or node.left.id != "tool":
            continue
        if isinstance(node.ops[0], (ast.Eq, ast.In)):
            names.update(_string_values(node.comparators[0]))
    names.update(_dict_keys(root, "src/tool_execution.py", "_MCP_TOOL_MAP"))
    return names


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _literal_tool_spec_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "ToolSpec":
            continue
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    names.add(keyword.value.value)
    return names


def _registration_summary(root: Path, relative_path: str) -> dict[str, Any]:
    tree = _parse(root, relative_path)
    registration_calls = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) in {"register_tool", "_register_tool"}
    )
    return {
        "path": relative_path,
        "classification": "dynamic",
        "registration_call_count": registration_calls,
        "literal_tool_ids": sorted(_literal_tool_spec_names(tree)),
    }


def _source_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative_path in sorted(SOURCE_PATHS):
        path = root / relative_path
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise InventoryError(f"cannot hash declared source {relative_path}: {exc}") from exc
        hashes[relative_path] = hashlib.sha256(payload).hexdigest()
    return hashes


def _records(tool_ids: Iterable[str], classification: str, reason_code: str) -> list[dict[str, str]]:
    return [
        {
            "tool_id": tool_id,
            "classification": classification,
            "reason_code": reason_code,
        }
        for tool_id in sorted(tool_ids)
    ]


def validate_baseline(inventory: dict[str, Any]) -> list[str]:
    """Return stable drift errors for the TAX0 acceptance baseline."""

    errors: list[str] = []
    counts = inventory.get("counts") or {}
    for key, expected in EXPECTED_COUNTS.items():
        actual = counts.get(key)
        if actual != expected:
            errors.append(f"{key}: expected {expected}, found {actual}")

    drift = inventory.get("drift") or {}
    exact_sets = {
        "schema_without_runtime_tag": EXPECTED_SCHEMA_WITHOUT_RUNTIME,
        "runtime_without_function_schema": EXPECTED_RUNTIME_WITHOUT_SCHEMA,
        "stale_admin_metadata": EXPECTED_STALE_ADMIN_METADATA,
    }
    for key, expected in exact_sets.items():
        actual = tuple(item.get("tool_id") for item in drift.get(key, []))
        if actual != expected:
            errors.append(f"{key}: expected {list(expected)}, found {list(actual)}")
    return errors


def build_inventory(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    runtime_tags = set(
        _literal_assignment(root, "src/agent_tools/__init__.py", "TOOL_TAGS")
    )
    function_schemas = _schema_names(root)
    tool_index = _dict_keys(root, "src/tool_index.py", "BUILTIN_TOOL_DESCRIPTIONS")
    prompt_sections = _dict_keys(root, "src/agent_loop_prompts.py", "TOOL_SECTIONS")
    agent_handlers = _dict_keys(root, "src/agent_tools/__init__.py", "TOOL_HANDLERS")
    admin_metadata = _admin_metadata_keys(root)
    mcp_legacy_routes = _dict_keys(root, "src/tool_execution.py", "_MCP_TOOL_MAP")
    dispatcher_conditions = _dispatcher_condition_ids(root)

    schema_without_runtime = function_schemas - runtime_tags
    runtime_without_schema = runtime_tags - function_schemas
    stale_admin = admin_metadata - runtime_tags
    runtime_without_admin = runtime_tags - admin_metadata

    source_hashes = _source_hashes(root)
    source_digest = hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    inventory: dict[str, Any] = {
        "schema_version": "odysseus.tool_taxonomy_inventory.v1",
        "deterministic": True,
        "scope": "static_repository_metadata_only",
        "privacy": {
            "raw_content_visible": False,
            "tool_arguments_visible": False,
            "tool_results_visible": False,
            "prompt_text_visible": False,
            "private_paths_visible": False,
            "provider_output_visible": False,
            "secret_values_visible": False,
        },
        "counts": {
            "runtime_tags": len(runtime_tags),
            "function_schemas": len(function_schemas),
            "schema_without_runtime_tag": len(schema_without_runtime),
            "runtime_without_function_schema": len(runtime_without_schema),
            "tool_index_entries": len(tool_index),
            "prompt_sections": len(prompt_sections),
            "agent_handlers": len(agent_handlers),
            "dispatcher_condition_ids": len(dispatcher_conditions),
            "admin_metadata": len(admin_metadata),
            "runtime_without_admin_metadata": len(runtime_without_admin),
            "stale_admin_metadata": len(stale_admin),
            "mcp_legacy_routes": len(mcp_legacy_routes),
        },
        "projections": {
            "runtime_tags": sorted(runtime_tags),
            "function_schemas": sorted(function_schemas),
            "tool_index_entries": sorted(tool_index),
            "prompt_sections": sorted(prompt_sections),
            "agent_handlers": sorted(agent_handlers),
            "dispatcher_condition_ids": sorted(dispatcher_conditions),
            "admin_metadata": sorted(admin_metadata),
            "mcp_legacy_routes": sorted(mcp_legacy_routes),
        },
        "drift": {
            "schema_without_runtime_tag": _records(
                schema_without_runtime,
                "missing",
                "native_schema_rejected_before_dispatch_without_runtime_tag",
            ),
            "runtime_without_function_schema": _records(
                runtime_without_schema,
                "intentional",
                "text_only_or_non_native_projection_requires_explicit_contract",
            ),
            "tool_index_without_runtime_tag": _records(
                tool_index - runtime_tags,
                "missing",
                "index_entry_has_no_runtime_registration",
            ),
            "runtime_without_tool_index": _records(
                runtime_tags - tool_index,
                "missing",
                "runtime_tag_has_no_searchable_index_entry",
            ),
            "prompt_without_runtime_tag": _records(
                prompt_sections - runtime_tags,
                "missing",
                "prompt_section_has_no_runtime_registration",
            ),
            "runtime_without_prompt_section": _records(
                runtime_tags - prompt_sections,
                "missing",
                "runtime_tag_has_no_dedicated_prompt_section",
            ),
            "stale_admin_metadata": _records(
                stale_admin,
                "stale",
                "admin_metadata_has_no_runtime_registration",
            ),
            "runtime_without_admin_metadata": _records(
                runtime_without_admin,
                "missing",
                "runtime_tag_uses_generic_admin_fallback",
            ),
        },
        "dynamic_sources": {
            "registry": {
                "path": "src/tool_registry.py",
                "classification": "dynamic",
                "registration_mode": "runtime_ToolSpec_registry",
                "default_permission": "admin",
                "static_runtime_count": None,
            },
            "mcp": {
                "path": "src/mcp_manager.py",
                "classification": "dynamic",
                "registration_mode": "qualified_mcp_tool_names",
                "qualified_prefix": "mcp__",
                "legacy_routes": sorted(mcp_legacy_routes),
            },
            "plugin_registration_sources": [
                _registration_summary(root, "src/plugin_system.py"),
                _registration_summary(root, "src/sensitive_local_worker.py"),
                _registration_summary(root, "plugins/telegram/plugin.py"),
                _registration_summary(root, "plugins/obsidian/plugin.py"),
            ],
        },
        "source_hashes_sha256": source_hashes,
        "source_digest_sha256": source_digest,
    }
    errors = validate_baseline(inventory)
    inventory["baseline"] = {
        "status": "matches" if not errors else "drift",
        "expected_counts": dict(sorted(EXPECTED_COUNTS.items())),
        "errors": errors,
    }
    return inventory


def render_inventory(inventory: dict[str, Any]) -> str:
    return json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def snapshot_errors(inventory: dict[str, Any], output_path: Path) -> list[str]:
    errors = validate_baseline(inventory)
    if not output_path.is_file():
        return [*errors, "snapshot is missing"]
    try:
        current = output_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [*errors, f"snapshot cannot be read: {exc}"]
    if current != render_inventory(inventory):
        errors.append("snapshot differs from deterministic repository inventory")
    return errors


def _resolve_output(root: Path, output: Path) -> Path:
    return output if output.is_absolute() else root / output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build or verify the deterministic content-free TAX0 tool registry inventory."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    output_path = _resolve_output(root, args.output)
    try:
        inventory = build_inventory(root)
    except InventoryError as exc:
        print(f"TAX0 inventory error: {exc}")
        return 1

    if args.check:
        errors = snapshot_errors(inventory, output_path)
        if errors:
            for error in errors:
                print(f"TAX0 drift: {error}")
            return 1
        counts = inventory["counts"]
        print(
            "TAX0 inventory check passed: "
            f"{counts['runtime_tags']} runtime tags, "
            f"{counts['function_schemas']} schemas, "
            f"{counts['admin_metadata']} admin metadata entries"
        )
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_inventory(inventory), encoding="utf-8", newline="\n")
    for error in validate_baseline(inventory):
        print(f"TAX0 drift: {error}")
    print(f"Wrote deterministic TAX0 inventory to {args.output.as_posix()}")
    return 0 if not validate_baseline(inventory) else 1


if __name__ == "__main__":
    raise SystemExit(main())
