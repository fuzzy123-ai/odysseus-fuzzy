#!/usr/bin/env python3
"""Read one fixed, redacted Podman Compose capability observation.

This is an observation-only contract for a future live deployment decision. It
never starts, builds, recreates, removes, or inspects containers.  Help flags
establish parser availability only; a separate bounded local source audit must
also establish the conservative service/dependency semantics before ``ok`` is
possible.  Otherwise the honest terminal result is ``needs_live_observation``.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from typing import Any, Callable, Mapping, Sequence


SCHEMA_ID = "odysseus.redacted_podman_compose_capability_observation.v1"
SOURCE_AUDIT_SCHEMA_ID = "odysseus.podman_compose_source_audit.v1"
EXPECTED_VERSION = "1.3.0"
COMMAND_TIMEOUT_SECONDS = 1
OUTER_TIMEOUT_SECONDS = 10
MAX_OUTPUT_CHARS = 32_768
MAX_SOURCE_CHARS = 1_000_000

VERSION_COMMAND = ("podman-compose", "--version")
GLOBAL_HELP_COMMAND = ("podman-compose", "--help")
BUILD_HELP_COMMAND = ("podman-compose", "build", "--help")
UP_HELP_COMMAND = ("podman-compose", "up", "--help")
# This fixed isolated program reads only the installed package's public source
# structure and emits a bounded boolean projection; it never emits source,
# paths, environment, exceptions, or package metadata.
_SOURCE_AUDIT_PROGRAM = """
import ast
import hashlib
import inspect
import json
import podman_compose as module

source = inspect.getsource(module)
if not isinstance(source, str) or len(source) > 1000000:
    raise RuntimeError("bounded source unavailable")
tree = ast.parse(source)
handlers = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

def is_args_attribute(node, name):
    return isinstance(node, ast.Attribute) and node.attr == name and isinstance(node.value, ast.Name) and node.value.id == "args"

class LocalNodes(ast.NodeVisitor):
    def __init__(self):
        self.nodes = []
    def visit_FunctionDef(self, node):
        return
    def visit_AsyncFunctionDef(self, node):
        return
    def visit_Lambda(self, node):
        return
    def visit_If(self, node):
        self.nodes.append(node)
        if isinstance(node.test, ast.Constant) and isinstance(node.test.value, bool):
            for child in (node.body if node.test.value else node.orelse):
                self.visit(child)
            return
        self.generic_visit(node)
    def generic_visit(self, node):
        self.nodes.append(node)
        super().generic_visit(node)

def local_nodes(handler):
    visitor = LocalNodes()
    for statement in handler.body:
        visitor.visit(statement)
    return visitor.nodes

def contains_attr(nodes, name):
    return any(is_args_attribute(node, name) for node in nodes)

def branch_nodes(statements):
    visitor = LocalNodes()
    for statement in statements:
        visitor.visit(statement)
    return visitor.nodes

def assigns_selected_services(nodes):
    for node in nodes:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "services" for target in node.targets) and is_args_attribute(node.value, "services"):
            return True
    return False

def assigns_dependency_expansion(nodes):
    for node in nodes:
        if not isinstance(node, ast.Assign) or not any(isinstance(target, ast.Name) and target.id == "services" for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, (ast.Name, ast.Attribute)):
            function_name = value.func.id if isinstance(value.func, ast.Name) else value.func.attr
            if function_name in {"rec_deps", "resolve_dependencies"}:
                return True
    return False

def no_deps_controls_expansion(nodes):
    for node in nodes:
        if not isinstance(node, ast.If) or not contains_attr(list(ast.walk(node.test)), "no_deps"):
            continue
        body, otherwise = branch_nodes(node.body), branch_nodes(node.orelse)
        if (assigns_selected_services(body) and assigns_dependency_expansion(otherwise)) or (assigns_selected_services(otherwise) and assigns_dependency_expansion(body)):
            return True
    return False

build, up = handlers.get("compose_build"), handlers.get("compose_up")
build_nodes = local_nodes(build) if build is not None else []
up_nodes = local_nodes(up) if up is not None else []
payload = {
    "schema_id": "odysseus.podman_compose_source_audit.v1",
    "build_service_selection_handler_local": contains_attr(build_nodes, "services"),
    "up_service_selection_handler_local": contains_attr(up_nodes, "services"),
    "up_no_deps_guard_controls_dependency_expansion": no_deps_controls_expansion(up_nodes),
    "rollback_force_recreate_consumed_in_up": contains_attr(up_nodes, "force_recreate"),
}
encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
payload["evidence_sha256"] = hashlib.sha256(encoded).hexdigest()
print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
"""
SOURCE_AUDIT_COMMAND = ("python3", "-I", "-c", _SOURCE_AUDIT_PROGRAM)

# Debian package releases have documented both of these exact renderings.
_VERSION = re.compile(r"^podman-compose version:? 1\.3\.0$")
_COMPOSE_VERSION_LINE = re.compile(r"^podman-compose version:? [0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")
_PODMAN_VERSION_LINE = re.compile(r"^podman version [0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ERRORS = frozenset({
    "version_unavailable", "version_mismatch", "help_unavailable", "source_audit_unavailable",
    "source_audit_invalid", "malformed_output", "output_too_large", "timeout", "internal_error",
})
_NEEDS_REASONS = frozenset({"semantic_proof_insufficient"})
_VISIBILITY_KEYS = frozenset({
    "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible",
    "source_text_visible", "paths_visible", "hostnames_visible", "secret_values_visible",
})
_SOURCE_AUDIT_KEYS = frozenset({
    "schema_id", "build_service_selection_handler_local", "up_service_selection_handler_local",
    "up_no_deps_guard_controls_dependency_expansion", "rollback_force_recreate_consumed_in_up",
    "evidence_sha256",
})
_OK_KEYS = frozenset({
    "schema_id", "status", "podman_compose_version", "global_env_file_parser_present",
    "global_project_name_parser_present", "service_scoped_build_parser_present",
    "service_scoped_up_parser_present", "no_deps_parser_present", "no_build_parser_present",
    "rollback_force_recreate_parser_present", "service_scoped_dependency_exclusion_proven",
    "rollback_force_recreate_proven", "deployment_capability_supported", *_VISIBILITY_KEYS, "evidence_sha256",
})
_BLOCKED_KEYS = frozenset({"schema_id", "status", "error_code", "retry_permitted", "evidence_sha256"})
_NEEDS_KEYS = frozenset({"schema_id", "status", "reason_code", "retry_permitted", "evidence_sha256"})


class CapabilityFailure(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code if code in _ERRORS else "internal_error"


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def blocked(code: str) -> dict[str, Any]:
    payload = {"schema_id": SCHEMA_ID, "status": "blocked", "error_code": code if code in _ERRORS else "internal_error", "retry_permitted": False}
    payload["evidence_sha256"] = _digest(payload)
    return payload


def needs_live_observation(reason: str) -> dict[str, Any]:
    payload = {"schema_id": SCHEMA_ID, "status": "needs_live_observation", "reason_code": reason if reason in _NEEDS_REASONS else "semantic_proof_insufficient", "retry_permitted": False}
    payload["evidence_sha256"] = _digest(payload)
    return payload


def _command_failure_code(command: Sequence[str]) -> str:
    if tuple(command) == VERSION_COMMAND:
        return "version_unavailable"
    if tuple(command) == SOURCE_AUDIT_COMMAND:
        return "source_audit_unavailable"
    return "help_unavailable"


def _run(command: Sequence[str], runner: Callable[..., Any]) -> str:
    try:
        result = runner(list(command), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                        timeout=COMMAND_TIMEOUT_SECONDS, check=False, encoding="utf-8", errors="replace",
                        env={"PATH": "/usr/bin:/bin"})
    except subprocess.TimeoutExpired:
        raise CapabilityFailure("timeout") from None
    except FileNotFoundError:
        raise CapabilityFailure(_command_failure_code(command)) from None
    except Exception:
        raise CapabilityFailure("internal_error") from None
    if getattr(result, "returncode", None) != 0:
        raise CapabilityFailure(_command_failure_code(command))
    stdout = getattr(result, "stdout", None)
    if not isinstance(stdout, str):
        raise CapabilityFailure("malformed_output")
    if len(stdout) > MAX_OUTPUT_CHARS:
        raise CapabilityFailure("output_too_large")
    return stdout


def _single_line(raw: str) -> str:
    value = raw.strip()
    if not value or "\n" in value or "\r" in value:
        raise CapabilityFailure("malformed_output")
    return value


def _parse_version_output(raw: str) -> str:
    """Accept the bounded compose 1.3.0 rendering without retaining Podman output."""
    if not raw or any((ord(character) < 32 and character != "\n") or ord(character) == 127 for character in raw):
        raise CapabilityFailure("malformed_output")
    body = raw[:-1] if raw.endswith("\n") else raw
    lines = body.split("\n")
    if len(lines) not in (1, 2) or any(not line for line in lines) or not _COMPOSE_VERSION_LINE.fullmatch(lines[0]):
        raise CapabilityFailure("malformed_output")
    if len(lines) == 2 and not _PODMAN_VERSION_LINE.fullmatch(lines[1]):
        raise CapabilityFailure("malformed_output")
    return lines[0]


def _has_flag(help_text: str, flag: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_-])" + re.escape(flag) + r"(?![A-Za-z0-9_-])", help_text) is not None


def _has_service_argument(help_text: str, subcommand: str) -> bool:
    # Require an explicit upper-case positional service grammar in the usage,
    # rather than treating any descriptive use of the word as parser evidence.
    return re.search(r"(?im)^\s*usage:.*\b" + re.escape(subcommand) + r"\b.*\bSERVICE(?:\s|\[|$)", help_text) is not None


def _parse_source_audit(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(_single_line(raw))
    except (TypeError, json.JSONDecodeError, CapabilityFailure):
        raise CapabilityFailure("source_audit_invalid") from None
    if type(payload) is not dict or set(payload) != _SOURCE_AUDIT_KEYS:
        raise CapabilityFailure("source_audit_invalid")
    if payload.get("schema_id") != SOURCE_AUDIT_SCHEMA_ID:
        raise CapabilityFailure("source_audit_invalid")
    if any(type(payload.get(key)) is not bool for key in _SOURCE_AUDIT_KEYS - {"schema_id", "evidence_sha256"}):
        raise CapabilityFailure("source_audit_invalid")
    digest = payload.get("evidence_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest) or digest != _digest(payload):
        raise CapabilityFailure("source_audit_invalid")
    return payload


def collect_podman_compose_capability_observation(*, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Return one redacted capability result; every command is fixed and read-only."""
    try:
        version = _parse_version_output(_run(VERSION_COMMAND, runner))
        if not _VERSION.fullmatch(version):
            raise CapabilityFailure("version_mismatch")
        global_help = _run(GLOBAL_HELP_COMMAND, runner)
        build_help = _run(BUILD_HELP_COMMAND, runner)
        up_help = _run(UP_HELP_COMMAND, runner)
        source_audit = _parse_source_audit(_run(SOURCE_AUDIT_COMMAND, runner))

        parser_evidence = (
            _has_flag(global_help, "--env-file") and _has_flag(global_help, "--project-name")
            and _has_service_argument(build_help, "build") and _has_service_argument(up_help, "up")
            and _has_flag(up_help, "--no-deps") and _has_flag(up_help, "--no-build")
            and _has_flag(up_help, "--force-recreate")
        )
        semantic_evidence = all(source_audit[key] is True for key in (
            "build_service_selection_handler_local", "up_service_selection_handler_local",
            "up_no_deps_guard_controls_dependency_expansion", "rollback_force_recreate_consumed_in_up",
        ))
        if not parser_evidence or not semantic_evidence:
            return needs_live_observation("semantic_proof_insufficient")
        payload = {
            "schema_id": SCHEMA_ID, "status": "ok", "podman_compose_version": EXPECTED_VERSION,
            "global_env_file_parser_present": True, "global_project_name_parser_present": True,
            "service_scoped_build_parser_present": True, "service_scoped_up_parser_present": True,
            "no_deps_parser_present": True, "no_build_parser_present": True,
            "rollback_force_recreate_parser_present": True,
            "service_scoped_dependency_exclusion_proven": True, "rollback_force_recreate_proven": True,
            "deployment_capability_supported": True,
            **{key: False for key in _VISIBILITY_KEYS},
        }
        payload["evidence_sha256"] = _digest(payload)
        if set(payload) != _OK_KEYS:
            raise CapabilityFailure("internal_error")
        return payload
    except CapabilityFailure as failure:
        return blocked(failure.code)
    except Exception:
        return blocked("internal_error")


def main() -> int:
    payload = collect_podman_compose_capability_observation()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
