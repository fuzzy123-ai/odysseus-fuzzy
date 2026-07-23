#!/usr/bin/env python3
"""Build and verify a deterministic, content-free tool-surface inventory.

The audit deliberately parses source files instead of importing Odysseus.  Its
persisted output contains tool identities, repo-relative source paths and source
hashes, but never schema arguments, prompt text, tool results or host paths.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
INVENTORY_KIND = "odysseus.tool_taxonomy_inventory"
BASELINE_DATE = "2026-07-23"
CLASSIFICATIONS = frozenset({"intentional", "missing", "stale", "dynamic"})


@dataclass(frozen=True)
class SourceSpec:
    surface: str
    path: str
    assignment: str | None = None


SURFACE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec("builtin_tags", "src/agent_tools/__init__.py", "TOOL_TAGS"),
    SourceSpec(
        "function_schemas",
        "src/tool_schema_definitions.py",
        "FUNCTION_TOOL_SCHEMAS",
    ),
    SourceSpec("prompt_sections", "src/agent_loop_prompts.py", "TOOL_SECTIONS"),
    SourceSpec(
        "tool_index",
        "src/tool_index.py",
        "BUILTIN_TOOL_DESCRIPTIONS",
    ),
    SourceSpec("dispatcher", "src/tool_execution.py", "_execute_tool_block_impl"),
    SourceSpec("admin_metadata", "static/js/admin.js", "TOOL_META"),
)

CORE_DYNAMIC_SOURCES: tuple[str, ...] = (
    "src/tool_registry.py",
    "src/plugin_system.py",
    "src/mcp_manager.py",
    "src/sensitive_local_worker.py",
    "plugins/mcp_server/plugin.py",
    "plugins/obsidian/plugin.py",
    "plugins/obsidian/backend/tool_specs.py",
    "plugins/telegram/plugin.py",
)

EXPECTED_COUNTS = {
    "builtin_tag_count": 80,
    "function_schema_count": 85,
    "schema_without_runtime_count": 6,
    "runtime_without_schema_count": 1,
    "admin_metadata_count": 31,
    "admin_fallback_count": 50,
}
EXPECTED_SCHEMA_WITHOUT_RUNTIME = frozenset(
    {
        "manage_assistant",
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
        "manage_presets",
        "tail_serve_output",
    }
)
EXPECTED_RUNTIME_WITHOUT_SCHEMA = frozenset({"generate_image"})
EXPECTED_STALE_ADMIN_METADATA = frozenset({"manage_rag"})
EMAIL_SCHEMA_ADAPTER_TOOLS = frozenset(
    {
        "archive_email",
        "bulk_email",
        "delete_email",
        "list_email_accounts",
        "list_emails",
        "mark_email_read",
        "read_email",
        "reply_to_email",
        "send_email",
    }
)
INTERNAL_DISPATCH_CONTROLS = frozenset(
    {
        "invalid_tool_call",
        "json",
        "vault_get",
        "vault_search",
        "vault_unlock",
        "xml",
    }
)


def _violation(code: str, entity: str, detail: str) -> dict[str, str]:
    return {"code": code, "entity": entity, "detail": detail}


def _repo_path(root: Path, relative: str) -> Path | None:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        return None
    candidate = (root / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _source_bytes(root: Path, relative: str) -> bytes:
    path = _repo_path(root, relative)
    if path is None:
        raise ValueError(f"unsafe repo-relative source path: {relative}")
    return path.read_bytes()


def _source_text(root: Path, relative: str) -> str:
    return _source_bytes(root, relative).decode("utf-8")


def _source_hash(root: Path, relative: str) -> str:
    return hashlib.sha256(_source_bytes(root, relative)).hexdigest()


def _python_tree(root: Path, relative: str) -> ast.Module:
    return ast.parse(_source_text(root, relative), filename=relative)


def _assignment_node(tree: ast.Module, name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                if node.value is None:
                    break
                return node.value
    raise ValueError(f"assignment not found: {name}")


def _literal_assignment(root: Path, relative: str, name: str):
    return ast.literal_eval(_assignment_node(_python_tree(root, relative), name))


def _extract_builtin_tags(root: Path, spec: SourceSpec) -> set[str]:
    value = _literal_assignment(root, spec.path, str(spec.assignment))
    if not isinstance(value, (set, frozenset, list, tuple)):
        raise ValueError("TOOL_TAGS must be a literal collection")
    return {item for item in value if isinstance(item, str) and item}


def _extract_function_schemas(root: Path, spec: SourceSpec) -> set[str]:
    value = _literal_assignment(root, spec.path, str(spec.assignment))
    if not isinstance(value, list):
        raise ValueError("FUNCTION_TOOL_SCHEMAS must be a literal list")
    names: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("function schema entry must be an object")
        function = item.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError("function schema entry has no string name")
        if name in names:
            raise ValueError(f"duplicate function schema name: {name}")
        names.add(name)
    return names


def _extract_mapping_keys(root: Path, spec: SourceSpec) -> set[str]:
    value = _literal_assignment(root, spec.path, str(spec.assignment))
    if not isinstance(value, dict):
        raise ValueError(f"{spec.assignment} must be a literal mapping")
    names: set[str] = set()
    for key in value:
        if isinstance(key, str):
            names.add(key)
        elif isinstance(key, tuple) and all(isinstance(item, str) for item in key):
            names.update(key)
        else:
            raise ValueError(f"unsupported key in {spec.assignment}")
    return names


_ADMIN_KEY_RE = re.compile(
    r"^\s{2}(?:(?:\"([^\"]+)\")|(?:'([^']+)')|([A-Za-z_$][\w$]*))\s*:",
    re.MULTILINE,
)


def _extract_admin_metadata_text(text: str) -> set[str]:
    marker = "const TOOL_META = {"
    start = text.find(marker)
    if start < 0:
        raise ValueError("TOOL_META declaration not found")
    body_start = start + len(marker)
    end = text.find("\n};", body_start)
    if end < 0:
        raise ValueError("TOOL_META terminator not found")
    body = text[body_start:end]
    return {
        next(group for group in match.groups() if group is not None)
        for match in _ADMIN_KEY_RE.finditer(body)
    }


def _extract_admin_metadata(root: Path, spec: SourceSpec) -> set[str]:
    return _extract_admin_metadata_text(_source_text(root, spec.path))


def _strings_from_literal(node: ast.AST) -> set[str]:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (tuple, list, set, frozenset)):
        return {item for item in value if isinstance(item, str)}
    return set()


def _contains_tool_name(node: ast.AST) -> bool:
    return any(isinstance(item, ast.Name) and item.id == "tool" for item in ast.walk(node))


def _extract_dispatcher(root: Path, spec: SourceSpec) -> tuple[set[str], set[str]]:
    tree = _python_tree(root, spec.path)
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == spec.assignment
        ),
        None,
    )
    if function is None:
        raise ValueError(f"dispatcher function not found: {spec.assignment}")

    names: set[str] = set()
    patterns: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(_contains_tool_name(operand) for operand in operands):
                for operand in operands:
                    if not _contains_tool_name(operand):
                        names.update(_strings_from_literal(operand))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "tool"
            and node.args
        ):
            patterns.update(f"{value}*" for value in _strings_from_literal(node.args[0]))

    mcp_map = ast.literal_eval(_assignment_node(tree, "_MCP_TOOL_MAP"))
    if not isinstance(mcp_map, dict) or not all(isinstance(key, str) for key in mcp_map):
        raise ValueError("_MCP_TOOL_MAP must be a literal string-keyed mapping")
    names.update(mcp_map)
    return names, patterns


def _surface_specs_by_name(specs: Sequence[SourceSpec]) -> dict[str, SourceSpec]:
    result = {spec.surface: spec for spec in specs}
    if len(result) != len(specs):
        raise ValueError("surface names must be unique")
    return result


def _extract_surfaces(
    root: Path,
    specs: Sequence[SourceSpec],
) -> tuple[dict[str, set[str]], set[str]]:
    by_name = _surface_specs_by_name(specs)
    required = {
        "builtin_tags",
        "function_schemas",
        "prompt_sections",
        "tool_index",
        "dispatcher",
        "admin_metadata",
    }
    missing = required - set(by_name)
    if missing:
        raise ValueError(f"missing surface specs: {', '.join(sorted(missing))}")

    dispatcher, patterns = _extract_dispatcher(root, by_name["dispatcher"])
    surfaces = {
        "builtin_tags": _extract_builtin_tags(root, by_name["builtin_tags"]),
        "function_schemas": _extract_function_schemas(
            root, by_name["function_schemas"]
        ),
        "prompt_sections": _extract_mapping_keys(root, by_name["prompt_sections"]),
        "tool_index": _extract_mapping_keys(root, by_name["tool_index"]),
        "dispatcher": dispatcher,
        "admin_metadata": _extract_admin_metadata(root, by_name["admin_metadata"]),
    }
    return surfaces, patterns


def _dynamic_source_kind(relative: str) -> str:
    if relative == "src/tool_registry.py":
        return "dynamic_registry"
    if relative == "src/plugin_system.py":
        return "plugin_registration_bridge"
    if relative == "src/mcp_manager.py":
        return "mcp_runtime"
    if relative == "plugins/mcp_server/plugin.py":
        return "mcp_schema_projection"
    if relative.startswith("plugins/"):
        return "plugin_registration_source"
    return "runtime_registration_source"


def _discover_dynamic_sources(root: Path) -> list[str]:
    candidates = set(CORE_DYNAMIC_SOURCES)
    markers = (
        "register_tool(",
        "ToolSpec(",
        "VaultToolSpec(",
        "get_function_schemas()",
    )
    for top in ("src", "plugins"):
        base = _repo_path(root, top)
        if base is None or not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            parts = PurePosixPath(relative).parts
            if "tests" in parts or path.name.startswith("test_"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if any(marker in text for marker in markers):
                candidates.add(relative)
    return sorted(candidates)


def _source_records(
    root: Path,
    specs: Sequence[SourceSpec],
    dynamic_sources: Sequence[str],
) -> list[dict[str, str]]:
    records = [
        {
            "kind": "static_surface",
            "path": spec.path,
            "sha256": _source_hash(root, spec.path),
            "surface": spec.surface,
        }
        for spec in specs
    ]
    records.extend(
        {
            "kind": _dynamic_source_kind(relative),
            "path": relative,
            "sha256": _source_hash(root, relative),
            "surface": "dynamic",
        }
        for relative in dynamic_sources
    )
    return sorted(records, key=lambda item: (item["path"], item["surface"], item["kind"]))


def _fingerprint(records: Sequence[Mapping[str, str]]) -> str:
    payload = json.dumps(list(records), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _difference(
    tool_id: str,
    surface: str,
    relation: str,
    classification: str,
    explanation: str,
) -> dict[str, str]:
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"invalid classification: {classification}")
    return {
        "classification": classification,
        "explanation": explanation,
        "relation": relation,
        "surface": surface,
        "tool_id": tool_id,
    }


def _classify_differences(
    surfaces: Mapping[str, set[str]],
    dispatcher_patterns: Iterable[str],
) -> list[dict[str, str]]:
    runtime = surfaces["builtin_tags"]
    rows: list[dict[str, str]] = []
    for surface in (
        "function_schemas",
        "prompt_sections",
        "tool_index",
        "dispatcher",
        "admin_metadata",
    ):
        values = surfaces[surface]
        for tool_id in sorted(runtime - values):
            if surface == "function_schemas" and tool_id in EXPECTED_RUNTIME_WITHOUT_SCHEMA:
                classification = "intentional"
                explanation = "legacy runtime projection intentionally has no native Function schema"
            elif surface == "dispatcher" and tool_id in EMAIL_SCHEMA_ADAPTER_TOOLS:
                classification = "intentional"
                explanation = "Function-call normalization routes this built-in identity through the qualified email MCP adapter"
            else:
                classification = "missing"
                explanation = f"runtime tool is absent from the {surface} projection"
            rows.append(
                _difference(
                    tool_id,
                    surface,
                    "runtime_not_surface",
                    classification,
                    explanation,
                )
            )
        for tool_id in sorted(values - runtime):
            if surface == "admin_metadata":
                classification = "stale"
                explanation = "Admin metadata is present for a non-runtime legacy identity"
            elif tool_id in EXPECTED_SCHEMA_WITHOUT_RUNTIME:
                classification = "missing"
                explanation = "projection exists but the built-in runtime registration is missing"
            elif surface == "dispatcher" and tool_id in INTERNAL_DISPATCH_CONTROLS:
                classification = "intentional"
                explanation = "internal dispatcher control is not an advertised built-in tool"
            else:
                classification = "stale"
                explanation = f"{surface} identity is not present in the built-in runtime allowlist"
            rows.append(
                _difference(
                    tool_id,
                    surface,
                    "surface_not_runtime",
                    classification,
                    explanation,
                )
            )
    for pattern in sorted(set(dispatcher_patterns)):
        rows.append(
            _difference(
                pattern,
                "dispatcher",
                "dynamic_pattern",
                "dynamic",
                "runtime-qualified identity is supplied by an MCP or plugin source",
            )
        )
    return sorted(
        rows,
        key=lambda item: (
            item["surface"],
            item["relation"],
            item["tool_id"],
            item["classification"],
        ),
    )


def _count_summary(surfaces: Mapping[str, set[str]]) -> dict[str, int]:
    runtime = surfaces["builtin_tags"]
    schemas = surfaces["function_schemas"]
    admin = surfaces["admin_metadata"]
    return {
        "builtin_tag_count": len(runtime),
        "function_schema_count": len(schemas),
        "schema_without_runtime_count": len(schemas - runtime),
        "runtime_without_schema_count": len(runtime - schemas),
        "admin_metadata_count": len(admin),
        "admin_fallback_count": len(runtime - admin),
    }


def _baseline_violations(surfaces: Mapping[str, set[str]]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    counts = _count_summary(surfaces)
    for key, expected in EXPECTED_COUNTS.items():
        actual = counts[key]
        if actual != expected:
            violations.append(
                _violation(
                    "baseline_count_drift",
                    key,
                    f"expected {expected}, observed {actual}",
                )
            )

    runtime = surfaces["builtin_tags"]
    schemas = surfaces["function_schemas"]
    admin = surfaces["admin_metadata"]
    exact_checks = (
        (
            "schema_without_runtime",
            schemas - runtime,
            EXPECTED_SCHEMA_WITHOUT_RUNTIME,
        ),
        (
            "runtime_without_schema",
            runtime - schemas,
            EXPECTED_RUNTIME_WITHOUT_SCHEMA,
        ),
        (
            "stale_admin_metadata",
            admin - runtime,
            EXPECTED_STALE_ADMIN_METADATA,
        ),
    )
    for entity, actual, expected in exact_checks:
        if actual != expected:
            violations.append(
                _violation(
                    "baseline_identity_drift",
                    entity,
                    "expected="
                    + ",".join(sorted(expected))
                    + "; observed="
                    + ",".join(sorted(actual)),
                )
            )
    return sorted(violations, key=lambda item: (item["code"], item["entity"], item["detail"]))


def audit_inventory(
    root: Path,
    *,
    specs: Sequence[SourceSpec] = SURFACE_SPECS,
    dynamic_sources: Sequence[str] | None = None,
) -> dict:
    """Return the deterministic TAX0 inventory for ``root`` without imports."""
    root = root.resolve()
    violations: list[dict[str, str]] = []
    try:
        surfaces, dispatcher_patterns = _extract_surfaces(root, specs)
        discovered_dynamic_sources = (
            sorted(set(dynamic_sources))
            if dynamic_sources is not None
            else _discover_dynamic_sources(root)
        )
        records = _source_records(root, specs, discovered_dynamic_sources)
    except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": INVENTORY_KIND,
            "baseline_date": BASELINE_DATE,
            "summary": {
                "clean": False,
                "content_fields_recorded": False,
                "private_paths_recorded": False,
                "runtime_modules_imported": False,
                "violation_count": 1,
            },
            "surfaces": [],
            "differences": [],
            "dynamic_sources": [],
            "sources": [],
            "violations": [
                _violation("inventory_source_error", "repository", type(exc).__name__)
            ],
        }

    violations.extend(_baseline_violations(surfaces))
    differences = _classify_differences(surfaces, dispatcher_patterns)
    counts = _count_summary(surfaces)
    surface_rows = [
        {
            "count": len(names),
            "name": name,
            "tool_ids": sorted(names),
        }
        for name, names in sorted(surfaces.items())
    ]
    dynamic_rows = [
        {
            "classification": "dynamic",
            "kind": _dynamic_source_kind(relative),
            "path": relative,
        }
        for relative in discovered_dynamic_sources
    ]
    ordered_violations = sorted(
        violations, key=lambda item: (item["code"], item["entity"], item["detail"])
    )
    classification_counts = {
        name: sum(item["classification"] == name for item in differences)
        for name in sorted(CLASSIFICATIONS)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": INVENTORY_KIND,
        "baseline_date": BASELINE_DATE,
        "summary": {
            **counts,
            "source_count": len(records),
            "dynamic_source_count": len(dynamic_rows),
            "difference_count": len(differences),
            "difference_classification_counts": classification_counts,
            "source_fingerprint": _fingerprint(records),
            "content_fields_recorded": False,
            "private_paths_recorded": False,
            "runtime_modules_imported": False,
            "violation_count": len(ordered_violations),
            "clean": not ordered_violations,
        },
        "surfaces": surface_rows,
        "differences": differences,
        "dynamic_sources": dynamic_rows,
        "sources": records,
        "violations": ordered_violations,
    }


def render_inventory(payload: Mapping) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read tool inventory: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ValueError("tool inventory must be a JSON object")
    return value


def _write_json(path: Path, payload: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_inventory(payload), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/plans/tool-taxonomy-inventory.json"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the persisted inventory differs from the current checkout",
    )
    parser.add_argument("--print", action="store_true", dest="print_payload")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    payload = audit_inventory(root)
    if args.print_payload:
        print(render_inventory(payload), end="")

    if args.check:
        if not output.is_file():
            print("Tool taxonomy inventory missing; generate the declared output first.", file=sys.stderr)
            return 1
        try:
            existing = _read_json(output)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if payload != existing:
            print("Tool taxonomy inventory drift detected; regenerate the declared output.", file=sys.stderr)
            return 1
    else:
        _write_json(output, payload)

    if payload["violations"]:
        for item in payload["violations"]:
            print(
                f"{item['code']}: {item['entity']}: {item['detail']}",
                file=sys.stderr,
            )
        return 1

    summary = payload["summary"]
    print(
        "Tool taxonomy inventory clean: "
        f"{summary['builtin_tag_count']} tags, "
        f"{summary['function_schema_count']} schemas, "
        f"{summary['admin_metadata_count']} Admin metadata entries, "
        f"{summary['admin_fallback_count']} Admin fallbacks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
