"""Static, content-free audit of Git and Project Version lineage capabilities.

The audit deliberately never executes Git or any subprocess.  It reads bounded
Python source metadata, inventories public APIs and direct process boundaries,
then emits repository-relative JSON or Markdown decisions.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


AUDIT_SCHEMA = "odysseus.code_lineage.capability_audit.v1"
MAX_SOURCE_FILES = 5_000
MAX_SOURCE_BYTES = 2 * 1024 * 1024


CANONICAL_MODULES = {
    "src/repo_git_adapter.py": "canonical_read_adapter",
    "src/project_version_store.py": "canonical_project_version_truth",
    "src/project_forge_local.py": "canonical_commit_retention_and_verification",
    "src/repo_commit_runner.py": "canonical_commit_mutation_boundary",
    "src/repo_push_runner.py": "canonical_push_mutation_boundary",
    "src/repo_registry.py": "canonical_repo_identity",
}

REQUIRED_API = {
    "src/repo_git_adapter.py": {
        "RepoGitAdapter.status": "working_tree_status",
        "RepoGitAdapter.current_branch": "current_branch",
        "RepoGitAdapter.log": "bounded_recent_commit_log",
        "RepoGitAdapter.changed_paths": "working_tree_changed_paths",
        "RepoGitAdapter.diff_stat": "working_tree_diff_stat",
        "RepoGitAdapter.remotes": "redacted_remotes",
        "RepoGitAdapter.snapshot": "bounded_repo_snapshot",
        "git_read_command_is_allowed": "read_command_allowlist",
        "run_git_read_subprocess_command": "canonical_read_process_boundary",
    },
    "src/project_version_store.py": {
        "ProjectVersionStore.load_version": "immutable_version_load",
        "ProjectVersionStore.verify_version": "immutable_version_verification",
        "ProjectVersionStore.iter_verified_versions": "ordered_verified_versions",
    },
    "src/project_forge_local.py": {
        "LocalProjectForge.store_commit": "retain_existing_commit",
        "LocalProjectForge.verify_version": "verify_retained_commit",
    },
}

EXTENSION_DECISIONS = (
    (
        "bounded_revision_graph",
        "extend_repo_git_adapter_after_owner_handoff",
        "commit ids, parent ids, authored_at and committed_at for an explicit revision range",
    ),
    (
        "historical_path_changes",
        "extend_repo_git_adapter_after_owner_handoff",
        "typed add/modify/delete/rename/copy records between explicit revisions",
    ),
    (
        "blob_and_object_metadata",
        "extend_repo_git_adapter_after_owner_handoff",
        "bounded blob ids and object-presence facts without source bodies",
    ),
    (
        "history_boundary_state",
        "extend_repo_git_adapter_after_owner_handoff",
        "shallow, missing-object and rewritten-range evidence",
    ),
    (
        "immutable_project_versions",
        "reuse_project_version_store",
        "verified version manifests remain the commit/version authority",
    ),
    (
        "repo_identity_and_roots",
        "reuse_repo_registry",
        "canonical repository identity and contained root resolution",
    ),
    (
        "commit_or_push",
        "out_of_scope_existing_mutation_authorities_only",
        "lineage code never invokes commit, push, fetch or branch mutation",
    ),
)

TIME_SEMANTICS = (
    ("first_seen_at", "first observation by Odysseus"),
    ("history_first_observed_at", "earliest reachable supporting revision"),
    ("authored_at", "Git author timestamp; never topology order"),
    ("committed_at", "Git committer timestamp; never creation proof"),
    ("indexed_at", "time evidence was indexed"),
    ("valid_from", "start of one evidence validity window"),
    ("valid_to", "end of one evidence validity window"),
)

CONFIDENCE_SEMANTICS = (
    ("same_blob_same_path", "exact_continuation"),
    ("same_blob_renamed_path", "high_confidence_move"),
    ("git_rename_detection", "git_supported_candidate"),
    ("stable_symbol_signature", "strong_only_when_unique"),
    ("ast_normalized_match", "reviewable_structural_candidate"),
    ("bounded_diff_overlap", "probable_modified_continuation"),
    ("copy_candidate", "one_to_many_candidate_never_silent_rename"),
    ("semantic_candidate", "discovery_hint_never_accepted_alone"),
    ("manual_assertion", "reviewed_assertion_with_exact_evidence"),
)

UNCERTAINTY_RULES = (
    "shallow_history_means_earliest_reachable_not_creation",
    "rewritten_history_invalidates_affected_generation",
    "missing_objects_produce_partial_or_unknown",
    "imported_old_code_is_not_repository_creation",
    "vendored_and_generated_code_keep_policy_markers",
    "copy_split_merge_and_resurrection_never_force_one_parent",
)

PRIVACY_RULES = (
    "author_name_and_email_excluded_by_default",
    "absolute_paths_source_bodies_and_raw_git_output_excluded",
    "commit_subjects_are_not_required_for_lineage_identity",
    "reports_and_metrics_use_content_free_aggregate_labels",
)

QUERY_SCOPES = (
    ("current", "only occurrences reachable from the selected current USI snapshot"),
    ("historical", "bounded revision range including removed occurrences where policy permits"),
    ("deleted", "removed occurrences only; content may remain hidden while metadata survives"),
    ("all_history", "explicit bounded union; never an unbounded default"),
)


class CodeLineageAuditError(ValueError):
    """Raised when the static audit input or source tree is unsafe/incomplete."""


@dataclass(frozen=True, slots=True)
class Capability:
    module: str
    api: str
    capability: str
    present: bool
    decision: str


@dataclass(frozen=True, slots=True)
class DirectGitBoundary:
    module: str
    function: str
    role: str
    decision: str


@dataclass(frozen=True, slots=True)
class ExtensionDecision:
    capability: str
    decision: str
    rationale: str


@dataclass(frozen=True, slots=True)
class CodeLineageCapabilityAudit:
    schema: str
    status: str
    source_file_count: int
    capabilities: tuple[Capability, ...]
    direct_git_boundaries: tuple[DirectGitBoundary, ...]
    extension_decisions: tuple[ExtensionDecision, ...]
    time_semantics: tuple[tuple[str, str], ...]
    confidence_semantics: tuple[tuple[str, str], ...]
    uncertainty_rules: tuple[str, ...]
    privacy_rules: tuple[str, ...]
    query_scopes: tuple[tuple[str, str], ...]
    default_user_language: str
    git_commands_executed: int
    subprocesses_executed: int
    live_actions: int
    audit_digest: str

    @property
    def missing_required_capabilities(self) -> tuple[Capability, ...]:
        return tuple(item for item in self.capabilities if not item.present)

    @property
    def duplicate_review_boundaries(self) -> tuple[DirectGitBoundary, ...]:
        return tuple(
            item for item in self.direct_git_boundaries if item.decision == "route_to_canonical_adapter"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "source_file_count": self.source_file_count,
            "capabilities": [asdict(item) for item in self.capabilities],
            "direct_git_boundaries": [asdict(item) for item in self.direct_git_boundaries],
            "extension_decisions": [asdict(item) for item in self.extension_decisions],
            "time_semantics": [
                {"field": field, "meaning": meaning} for field, meaning in self.time_semantics
            ],
            "confidence_semantics": [
                {"method": method, "meaning": meaning}
                for method, meaning in self.confidence_semantics
            ],
            "uncertainty_rules": list(self.uncertainty_rules),
            "privacy_rules": list(self.privacy_rules),
            "query_scopes": [
                {"scope": scope, "meaning": meaning} for scope, meaning in self.query_scopes
            ],
            "default_user_language": self.default_user_language,
            "missing_required_capability_count": len(self.missing_required_capabilities),
            "duplicate_review_boundary_count": len(self.duplicate_review_boundaries),
            "git_commands_executed": self.git_commands_executed,
            "subprocesses_executed": self.subprocesses_executed,
            "live_actions": self.live_actions,
            "audit_digest": self.audit_digest,
        }


def _bounded_python_files(root: Path) -> tuple[Path, ...]:
    source_root = (root / "src").resolve()
    if not source_root.is_dir():
        raise CodeLineageAuditError("source root is missing")
    files: list[Path] = []
    for path in source_root.rglob("*.py"):
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(source_root)
        except ValueError as exc:
            raise CodeLineageAuditError("source path escapes audit root") from exc
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise CodeLineageAuditError("source file exceeds static audit size bound")
        files.append(path)
        if len(files) > MAX_SOURCE_FILES:
            raise CodeLineageAuditError("source tree exceeds static audit file bound")
    return tuple(sorted(files))


def _parse(path: Path) -> ast.Module:
    try:
        # ``utf-8-sig`` accepts ordinary UTF-8 while stripping an optional BOM.
        # A BOM is valid repository metadata and must not turn a static audit
        # into a false parse failure.
        return ast.parse(path.read_text(encoding="utf-8-sig"), filename=path.name)
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise CodeLineageAuditError(f"cannot parse required source metadata: {path.name}") from exc


def _public_apis(tree: ast.Module) -> set[str]:
    result: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result.add(f"{node.name}.{child.name}")
    return result


def _imports_subprocess(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(item.name == "subprocess" for item in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            return True
    return False


def _call_name(node: ast.Call) -> str:
    value = node.func
    if isinstance(value, ast.Attribute):
        base = value.value.id if isinstance(value.value, ast.Name) else ""
        return f"{base}.{value.attr}" if base else value.attr
    if isinstance(value, ast.Name):
        return value.id
    return ""


def _enclosing_functions(
    tree: ast.Module,
) -> dict[ast.AST, tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    functions: dict[
        ast.AST, tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]
    ] = {}

    def walk(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                for descendant in ast.walk(child):
                    functions[descendant] = (name, child)
                walk(child, prefix)
            else:
                walk(child, prefix)

    walk(tree)
    return functions


def _has_git_process_evidence(
    module: str,
    function_name: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> bool:
    # Canonical modules are already catalogued process authorities. Their
    # allowlist owns whether a dynamic argv is Git, so the static audit retains
    # the boundary without trying to reconstruct values.
    if module in CANONICAL_MODULES:
        return True
    name_tokens = function_name.lower().replace(".", "_").split("_")
    if "git" in name_tokens:
        return True
    if function is None:
        return False
    # For uncatalogued code, require local executable evidence. Looking at the
    # whole module caused unrelated Tesseract/Tailscale subprocesses to be
    # classified as Git merely because another function mentioned Git.
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.strip().lower() in {"git", "git.exe"}
        for node in ast.walk(function)
    )


def _direct_boundaries(module: str, tree: ast.Module) -> tuple[DirectGitBoundary, ...]:
    if not _imports_subprocess(tree):
        return ()
    functions = _enclosing_functions(tree)
    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = _call_name(node)
        if call not in {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output"}:
            continue
        function_name, function = functions.get(node, ("module_scope", None))
        if _has_git_process_evidence(module, function_name, function):
            calls.add(function_name)
    role = CANONICAL_MODULES.get(module, "uncatalogued_direct_git_process_boundary")
    decision = (
        "retain_existing_boundary"
        if role.startswith("canonical_")
        else "route_to_canonical_adapter"
    )
    return tuple(
        DirectGitBoundary(module, name, role, decision) for name in sorted(calls)
    )


def audit_code_lineage_capabilities(root: str | Path) -> CodeLineageCapabilityAudit:
    root_path = Path(root).resolve()
    files = _bounded_python_files(root_path)
    by_module = {
        path.relative_to(root_path).as_posix(): _parse(path)
        for path in files
    }
    missing_modules = tuple(module for module in CANONICAL_MODULES if module not in by_module)
    if missing_modules:
        raise CodeLineageAuditError(
            "required canonical modules are missing: " + ", ".join(missing_modules)
        )
    capabilities: list[Capability] = []
    for module, expected in sorted(REQUIRED_API.items()):
        public = _public_apis(by_module[module])
        for api, capability in sorted(expected.items()):
            present = api in public
            capabilities.append(
                Capability(
                    module,
                    api,
                    capability,
                    present,
                    "reuse" if present else "required_before_clt_02",
                )
            )
    boundaries = tuple(
        item
        for module, tree in sorted(by_module.items())
        for item in _direct_boundaries(module, tree)
    )
    extensions = tuple(ExtensionDecision(*item) for item in EXTENSION_DECISIONS)
    status = "go_clt_01_contract_only" if all(item.present for item in capabilities) else "blocked_missing_canonical_api"
    core = {
        "schema": AUDIT_SCHEMA,
        "status": status,
        "capabilities": [asdict(item) for item in capabilities],
        "direct_git_boundaries": [asdict(item) for item in boundaries],
        "extension_decisions": [asdict(item) for item in extensions],
        "time_semantics": TIME_SEMANTICS,
        "confidence_semantics": CONFIDENCE_SEMANTICS,
        "uncertainty_rules": UNCERTAINTY_RULES,
        "privacy_rules": PRIVACY_RULES,
        "query_scopes": QUERY_SCOPES,
        "default_user_language": "first observable in available history",
        "git_commands_executed": 0,
        "subprocesses_executed": 0,
        "live_actions": 0,
    }
    digest = hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return CodeLineageCapabilityAudit(
        AUDIT_SCHEMA,
        status,
        len(files),
        tuple(capabilities),
        boundaries,
        extensions,
        TIME_SEMANTICS,
        CONFIDENCE_SEMANTICS,
        UNCERTAINTY_RULES,
        PRIVACY_RULES,
        QUERY_SCOPES,
        "first observable in available history",
        0,
        0,
        0,
        "sha256:" + digest,
    )


def render_markdown(report: CodeLineageCapabilityAudit) -> str:
    lines = [
        "# Code Lineage Capability Audit",
        "",
        f"- Status: `{report.status}`",
        f"- Audit digest: `{report.audit_digest}`",
        f"- Python source files inspected: `{report.source_file_count}`",
        "- Git commands executed: `0`",
        "- Subprocesses executed: `0`",
        "- Live actions: `0`",
        "- Default wording: `first observable in available history`",
        "",
        "## Canonical capabilities",
        "",
        "| Module | API | Capability | Present | Decision |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report.capabilities:
        lines.append(
            f"| `{item.module}` | `{item.api}` | `{item.capability}` | `{str(item.present).lower()}` | `{item.decision}` |"
        )
    lines.extend(
        [
            "",
            "## Direct Git process boundaries",
            "",
            "| Module | Function | Role | Decision |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in report.direct_git_boundaries:
        lines.append(
            f"| `{item.module}` | `{item.function}` | `{item.role}` | `{item.decision}` |"
        )
    lines.extend(["", "## Extension decisions", ""])
    for item in report.extension_decisions:
        lines.append(f"- `{item.capability}` -> `{item.decision}`: {item.rationale}.")
    lines.extend(["", "## Frozen truth language", ""])
    for field, meaning in report.time_semantics:
        lines.append(f"- `{field}`: {meaning}.")
    lines.extend(["", "## Confidence language", ""])
    for method, meaning in report.confidence_semantics:
        lines.append(f"- `{method}`: `{meaning}`.")
    lines.extend(["", "## Uncertainty and privacy", ""])
    for item in (*report.uncertainty_rules, *report.privacy_rules):
        lines.append(f"- `{item}`")
    lines.extend(["", "## Query scopes", ""])
    for scope, meaning in report.query_scopes:
        lines.append(f"- `{scope}`: {meaning}.")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Static code-lineage capability audit")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(tuple(argv) if argv is not None else None)
    report = audit_code_lineage_capabilities(args.root)
    if args.format == "markdown":
        print(render_markdown(report), end="")
    else:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.status == "go_clt_01_contract_only" else 2


if __name__ == "__main__":
    raise SystemExit(main())
