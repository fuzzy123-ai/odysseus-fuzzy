#!/usr/bin/env python3
"""Build/check the content-free static runtime-caller inventory for UIR-00."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


SCHEMA_VERSION = 1
INVENTORY_KIND = "odysseus.unified_source_index.runtime_caller_inventory"
TARGETS = ("app.py", "src", "routes")
CATEGORIES = frozenset({
    "composition_startup", "active_read", "active_write", "lifecycle_owner",
    "compatibility_fallback", "generic_context_boundary", "health_diagnostics",
    "route_admin", "backend_implementation", "scheduler_excluded", "background_excluded",
})
DECISIONS = frozenset({"keep", "adapt", "fallback", "retire", "exclude"})
CALLER_FIELDS = ("path", "line", "symbol", "call_type", "receiver", "method", "category", "decision", "owner_track")
CLASSIFICATION_RULES = {
    "active_read": "live retrieval reads current indexed state",
    "active_write": "live projection lifecycle write",
    "backend_implementation": "backend implementation is not a consumer caller seam",
    "background_excluded": "background job wiring is not active runtime caller state",
    "compatibility_fallback": "legacy or dynamic compatibility seam",
    "composition_startup": "application composition or lazy startup seam",
    "generic_context_boundary": "generic provider dispatch without provider inference",
    "health_diagnostics": "health or statistics surface only",
    "lifecycle_owner": "runtime owner of a source or projection lifecycle transition",
    "route_admin": "route-owned query or index lifecycle surface",
    "scheduler_excluded": "scheduler wiring is not active runtime caller state",
}
RAG_METHODS = frozenset({
    "search", "retrieve", "index_personal_documents", "rebuild_index", "remove_directory",
    "add_document", "add_documents_batch", "owner_inventory", "get_stats",
})
MEMORY_VECTOR_METHODS = frozenset({"search", "add", "remove", "rebuild", "count", "get_stats"})
PERSONAL_DOCS_METHODS = frozenset({
    "retrieve", "add_directory", "remove_directory", "refresh_index", "index_all_directories",
})
CONSTRUCTORS = frozenset({"RAGManager", "VectorRAG", "MemoryVectorStore", "PersonalDocsManager"})
FUNCTION_CANDIDATES = frozenset({"get_rag_manager", "get_memory_vector_store"})
AST_MARKERS = (
    b"RAG",
    b"rag_",
    b"rag.",
    b"rag =",
    b"memory_vector",
    b"PersonalDocs",
    b"provider.retrieve",
    b"getattr(",
)
EXCLUSIONS = (
    {
        "path": "src/bg_jobs.py",
        "category": "background_excluded",
        "decision": "exclude",
        "owner_track": "canonical:bg_jobs",
        "reason": "background job wiring is recorded as an explicit non-runtime exclusion",
    },
    {
        "path": "src/memory_vector.py",
        "category": "backend_implementation",
        "decision": "exclude",
        "owner_track": "UIR-12",
        "reason": "vector-store implementation is recorded separately from consumer caller seams",
    },
    {
        "path": "src/rag_vector.py",
        "category": "backend_implementation",
        "decision": "exclude",
        "owner_track": "UIR-12",
        "reason": "RAG backend implementation is recorded separately from consumer caller seams",
    },
    {
        "path": "src/rag_reindex_dry_run.py",
        "category": "compatibility_fallback",
        "decision": "exclude",
        "owner_track": "UIR-06/07",
        "reason": "offline dry-run helper; not an active runtime caller",
    },
    {
        "path": "src/task_scheduler.py",
        "category": "scheduler_excluded",
        "decision": "exclude",
        "owner_track": "canonical:task_scheduler",
        "reason": "scheduler wiring is recorded as an explicit non-runtime exclusion",
    },
)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _attribute_path(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attribute_path(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
        return "dynamic_getattr"
    return ""


def _symbol(stack: list[str]) -> str:
    return ".".join(stack) if stack else "<module>"


def _classify(path: str, call_type: str, method: str) -> tuple[str, str]:
    if call_type == "dynamic_getattr":
        if path == "routes/auth_user_rename.py":
            return "lifecycle_owner", "account rename owns the personal-source lifecycle transition"
        if path == "src/chat_processor.py":
            return "compatibility_fallback", "dynamic manager resolution recorded; provider identity is not inferred"
        return "unclassified", "dynamic manager resolution has no explicit classification rule"
    if path == "app.py":
        if method == "owner_inventory":
            return "active_read", "active Telegram turn reads the current owner inventory"
        return "composition_startup", "application composition or lazy startup seam"
    if path == "src/rag_manager.py":
        return "compatibility_fallback", "legacy facade delegates to VectorRAG"
    if path == "src/context_orchestrator.py" and call_type == "provider_retrieve":
        return "generic_context_boundary", "generic provider dispatch; concrete provider is intentionally not inferred"
    if path in {"routes/diagnostics_routes.py", "src/service_health.py"}:
        return "health_diagnostics", "health/statistics surface only"
    if path in {"routes/admin_wipe_routes.py", "routes/memory_routes.py", "routes/personal_routes.py"}:
        return "route_admin", "route-owned query/index lifecycle surface"
    if path in {"src/app_initializer.py", "src/rag_singleton.py"}:
        return "composition_startup", "application composition or lazy startup seam"
    if path == "src/personal_docs.py":
        if method in {"search", "retrieve"}:
            return "compatibility_fallback", "personal-doc vector-first/keyword fallback seam"
        return "lifecycle_owner", "personal-doc source/index lifecycle owner"
    if path == "src/chat_processor.py":
        if method == "search":
            return "active_read", "active chat retrieval path"
    if path == "src/memory_provider.py":
        if method == "search":
            return "active_read", "active memory retrieval path"
        if method in {"add", "remove"}:
            return "lifecycle_owner", "memory provider owns the projected-memory lifecycle transition"
    if path in {"src/ai_interaction.py", "src/planning_source_memory.py", "src/tool_capability_maintenance.py"}:
        return "active_write", "active projection lifecycle write path"
    return "unclassified", "recognized runtime caller has no explicit classification rule"


def _ownership(path: str, category: str) -> tuple[str, str]:
    if category == "unclassified":
        return "unclassified", ""
    if category == "composition_startup":
        return "adapt", "UIR-03"
    if category == "health_diagnostics":
        return "keep", "UIR-05"
    if category == "backend_implementation":
        return "retire", "UIR-12"
    if category == "generic_context_boundary":
        return "adapt", "UIR-08"
    if category == "active_write":
        return "adapt", "UIR-09"
    if category == "active_read":
        return "adapt", "UIR-08" if path in {"app.py", "src/chat_processor.py"} else "UIR-09"
    if category == "route_admin":
        return "adapt", "canonical:route_admin"
    if category == "lifecycle_owner":
        return "adapt", "canonical:personal_docs" if path == "src/personal_docs.py" else "UIR-09"
    if category == "compatibility_fallback":
        return "fallback", "UIR-08" if path == "src/chat_processor.py" else "UIR-06/07"
    return "unclassified", ""


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.stack: list[str] = []
        self.items: list[dict[str, Any]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        call_type = ""
        method = ""
        receiver = ""
        if isinstance(node.func, ast.Name):
            if node.func.id in CONSTRUCTORS:
                call_type, method = "constructor", node.func.id
            elif node.func.id in FUNCTION_CANDIDATES:
                call_type, method = "runtime_factory", node.func.id
            elif node.func.id == "getattr" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                target = str(node.args[1].value)
                if target in {"rag_manager", "memory_vector", "personal_docs_manager"}:
                    call_type, method, receiver = "dynamic_getattr", target, _attribute_path(node.args[0])
        elif isinstance(node.func, ast.Attribute):
            method = node.func.attr
            receiver = _attribute_path(node.func.value)
            lowered = receiver.lower()
            if receiver == "provider" and method == "retrieve":
                call_type = "provider_retrieve"
            elif "rag" in lowered and method in RAG_METHODS:
                call_type = "rag_call"
            elif "memory_vector" in lowered and method in MEMORY_VECTOR_METHODS:
                call_type = "memory_vector_call"
            elif ("personal_docs" in lowered or lowered.endswith("personal_docs_manager")) and method in PERSONAL_DOCS_METHODS:
                call_type = "personal_docs_call"
        if call_type:
            category, reason = _classify(self.path, call_type, method)
            decision, owner_track = _ownership(self.path, category)
            self.items.append({
                "path": self.path,
                "line": node.lineno,
                "symbol": _symbol(self.stack),
                "call_type": call_type,
                "receiver": receiver,
                "method": method,
                "category": category,
                "decision": decision,
                "owner_track": owner_track,
                "reason": reason,
            })
        self.generic_visit(node)


def _target_files(root: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", *TARGETS],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    paths = (root / line for line in result.stdout.splitlines() if line.endswith(".py"))
    return tuple(sorted((path for path in paths if path.is_file()), key=lambda path: _relative(root, path)))


def build_inventory(root: Path) -> dict[str, Any]:
    root = root.resolve()
    candidates: list[dict[str, Any]] = []
    file_hashes: dict[str, str] = {}
    target_files = _target_files(root)
    ast_files_parsed = 0
    for path in target_files:
        relative = _relative(root, path)
        source = path.read_bytes()
        file_hashes[relative] = hashlib.sha256(source).hexdigest()
        if not any(marker in source for marker in AST_MARKERS):
            continue
        tree = ast.parse(source, filename=relative)
        ast_files_parsed += 1
        if any(item["path"] == relative for item in EXCLUSIONS):
            continue
        visitor = _Visitor(relative)
        visitor.visit(tree)
        candidates.extend(visitor.items)
    candidates.sort(key=lambda item: (item["path"], item["line"], item["call_type"], item["method"]))
    files = sorted({item["path"] for item in candidates} | {item["path"] for item in EXCLUSIONS})
    categories = Counter(item["category"] for item in candidates)
    categories.update(item["category"] for item in EXCLUSIONS)
    unclassified = [
        item for item in candidates
        if item["category"] not in CATEGORIES or item["decision"] not in DECISIONS or not item["owner_track"]
    ]
    invalid_exclusions = [
        item for item in EXCLUSIONS
        if item["category"] not in CATEGORIES or item["decision"] != "exclude" or not item["owner_track"]
    ]
    unclassified.extend(invalid_exclusions)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": INVENTORY_KIND,
        "scan": {
            "mode": "static_ast",
            "imports_executed": False,
            "network_accessed": False,
            "private_sources_read": False,
            "targets": list(TARGETS),
            "exclusions": list(EXCLUSIONS),
        },
        "files": [{"path": path, "sha256": file_hashes[path]} for path in files],
        "caller_fields": list(CALLER_FIELDS),
        "classification_rules": CLASSIFICATION_RULES,
        "callers": [[item[field] for field in CALLER_FIELDS] for item in candidates],
        "unclassified": unclassified,
        "summary": {
            "candidate_count": len(candidates),
            "category_counts": dict(sorted(categories.items())),
            "explicit_exclusion_count": len(EXCLUSIONS),
            "tracked_python_files_scanned": len(target_files),
            "ast_files_parsed": ast_files_parsed,
            "hashed_relevant_files": len(files),
            "unclassified_count": len(unclassified),
            "content_free": True,
            "deterministic": True,
        },
    }


def _render(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--print", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    value = build_inventory(args.root)
    if value["unclassified"]:
        raise SystemExit("runtime audit found unclassified direct caller candidates")
    rendered = _render(value)
    inventory = args.root / "docs/plans/unified-source-index-runtime-caller-inventory.json"
    if args.check:
        if not inventory.exists() or inventory.read_text(encoding="utf-8") != rendered:
            raise SystemExit("runtime caller inventory is missing or stale")
    if args.print or not args.check:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
