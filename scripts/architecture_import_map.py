"""Build a static import map for architecture cleanup planning.

The script parses Python files with ``ast`` and never imports project modules.
It is intended for dry-run architecture inventory before any file move.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Iterable


ARCHITECTURE_IMPORT_MAP_SCHEMA = "odysseus.architecture_import_map.v1"
DEFAULT_SCAN_DIRS = ("src", "routes", "plugins", "core", "services")

DOMAIN_RULES = (
    ("agent", ("src.agent", "routes.coding_agent", "routes.server_project", "routes.codex", "routes.claude")),
    ("orchestration", ("src.orchestration", "src.plan_", "src.heartbeat", "src.handoff", "src.quality_gate")),
    ("memory", ("src.memory", "src.rag", "src.raptor", "routes.memory", "routes.personal")),
    ("inbox", ("src.universal_inbox", "src.nextcloud", "routes.universal_inbox", "routes.universal_file")),
    ("integrations", ("services.", "routes.webhook", "routes.email", "routes.calendar", "routes.contacts")),
    ("ops", ("src.ops", "src.operator_dashboard", "src.system_health", "routes.ops", "plugins.system_health_checker")),
    ("security", ("src.security", "routes.security", "src.gate_evidence", "routes.review_gate", "routes.live_affordance")),
    ("release", ("src.version_one", "src.local_release", "routes.version_one", "routes.system_update")),
    ("plugins", ("plugins.", "src.plugin", "routes.plugin")),
    ("tools", ("src.tool", "routes.mcp", "plugins.mcp_server")),
    ("workspace", ("routes.workspace", "routes.mount", "routes.backup", "routes.vault")),
    ("visual", ("src.visual", "routes.gallery", "routes.editor", "routes.document")),
    ("routes", ("routes.",)),
    ("core", ("core.",)),
)


def build_import_map(
    root: Path,
    *,
    scan_dirs: Iterable[str] = DEFAULT_SCAN_DIRS,
) -> dict[str, Any]:
    root = root.resolve()
    files = _python_files(root, scan_dirs)
    known_modules = {_module_name(root, path): path for path in files}
    modules: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []

    for path in files:
        module = _module_name(root, path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            parse_errors.append(
                {
                    "module": module,
                    "path": _rel(root, path),
                    "error": f"SyntaxError:{exc.lineno or 0}",
                }
            )
            continue
        imports = tuple(_imports_from_tree(tree, module=module))
        modules.append(
            {
                "module": module,
                "path": _rel(root, path),
                "domain": classify_module_domain(module),
                "imports": [
                    {
                        "module": item["module"],
                        "domain": classify_module_domain(item["module"]),
                        "kind": item["kind"],
                        "line": item["line"],
                        "local": _is_known_local(item["module"], known_modules),
                    }
                    for item in imports
                ],
            }
        )

    return _summarize(modules, parse_errors=parse_errors)


def classify_module_domain(module: str) -> str:
    token = str(module or "").strip()
    for domain, prefixes in DOMAIN_RULES:
        if any(token == prefix.rstrip(".") or token.startswith(prefix) for prefix in prefixes):
            return domain
    if token == "app":
        return "app"
    return "external" if "." not in token else "unknown"


def _summarize(modules: list[dict[str, Any]], *, parse_errors: list[dict[str, str]]) -> dict[str, Any]:
    domains: dict[str, dict[str, Any]] = {}
    edges: list[tuple[str, str]] = []
    for item in modules:
        domain = item["domain"]
        domains.setdefault(domain, {"module_count": 0, "inbound_count": 0, "outbound_count": 0, "sample_modules": []})
        domains[domain]["module_count"] += 1
        if len(domains[domain]["sample_modules"]) < 8:
            domains[domain]["sample_modules"].append(item["module"])
        for imported in item["imports"]:
            if not imported["local"]:
                continue
            source_domain = domain
            target_domain = imported["domain"]
            if source_domain == target_domain:
                continue
            edges.append((source_domain, target_domain))
            domains.setdefault(target_domain, {"module_count": 0, "inbound_count": 0, "outbound_count": 0, "sample_modules": []})
            domains[source_domain]["outbound_count"] += 1
            domains[target_domain]["inbound_count"] += 1

    return {
        "schema": ARCHITECTURE_IMPORT_MAP_SCHEMA,
        "scanned_file_count": len(modules) + len(parse_errors),
        "module_count": len(modules),
        "parse_error_count": len(parse_errors),
        "import_edge_count": sum(len(item["imports"]) for item in modules),
        "local_cross_domain_edge_count": len(edges),
        "domains": {key: domains[key] for key in sorted(domains)},
        "modules": sorted(modules, key=lambda item: item["module"]),
        "parse_errors": parse_errors,
        "side_effects": ("none",),
        "files_moved": False,
        "imports_executed": False,
    }


def _python_files(root: Path, scan_dirs: Iterable[str]) -> tuple[Path, ...]:
    files: list[Path] = []
    for item in scan_dirs:
        base = root / item
        if base.is_file() and base.suffix == ".py":
            files.append(base)
        elif base.is_dir():
            files.extend(path for path in base.rglob("*.py") if "__pycache__" not in path.parts)
    return tuple(sorted(dict.fromkeys(files)))


def _module_name(root: Path, path: Path) -> str:
    rel = path.resolve().relative_to(root).with_suffix("")
    if rel.name == "__init__":
        rel = rel.parent
    return ".".join(part for part in rel.parts if part)


def _imports_from_tree(tree: ast.AST, *, module: str) -> Iterable[dict[str, Any]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield {"module": alias.name, "kind": "import", "line": node.lineno}
        elif isinstance(node, ast.ImportFrom):
            imported = _resolve_from_import(node, module=module)
            if imported:
                yield {"module": imported, "kind": "from", "line": node.lineno}


def _resolve_from_import(node: ast.ImportFrom, *, module: str) -> str:
    raw = node.module or ""
    if node.level <= 0:
        return raw
    parts = module.split(".")
    if parts and parts[-1] != "__init__":
        parts = parts[:-1]
    base = parts[: max(0, len(parts) - node.level + 1)]
    return ".".join(part for part in (*base, raw) if part)


def _is_known_local(module: str, known_modules: dict[str, Path]) -> bool:
    token = module
    while token:
        if token in known_modules:
            return True
        token = token.rsplit(".", 1)[0] if "." in token else ""
    return False


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--scan-dir", action="append", dest="scan_dirs", help="directory or file to scan")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    args = parser.parse_args()

    payload = build_import_map(args.root, scan_dirs=args.scan_dirs or DEFAULT_SCAN_DIRS)
    print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
