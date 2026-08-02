#!/usr/bin/env python3
"""Read one fixed, redacted Podman Compose capability observation.

This is an observation-only contract for a future live deployment decision. It
never starts, builds, recreates, removes, or inspects containers. A bounded
local source-AST audit establishes both parser availability and conservative
service/dependency semantics before ``ok`` is possible. Otherwise the honest
terminal result is ``needs_live_observation``.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


SCHEMA_ID = "odysseus.redacted_podman_compose_capability_observation.v1"
SOURCE_AUDIT_SCHEMA_ID = "odysseus.podman_compose_source_audit.v1"
# Bound to the selected official-provenance result for this Gate-B observer.
EXPECTED_VERSION = "1.6.0"
OFFICIAL_SOURCE_SHA256 = "10df1662477a673dc803c03e89c1bc1fba6c8c091e716fb6c7dd09c0081e1255"
COMMAND_TIMEOUT_SECONDS = 1
OUTER_TIMEOUT_SECONDS = 10
MAX_OUTPUT_CHARS = 32_768
MAX_SOURCE_CHARS = 1_000_000

# Keep command and source-audit identity in the running interpreter's
# environment; neither executable path is serialized into the evidence record.
_VERSION_PROGRAM = (
    "import importlib.metadata as metadata,importlib.util as util,pathlib,sys;"
    "root=pathlib.Path(sys.prefix).resolve();"
    "spec=util.find_spec('podman_compose');"
    "origin_path=pathlib.Path(spec.origin) if spec is not None and isinstance(spec.origin,str) else None;"
    "module=origin_path.resolve() if origin_path is not None else None;"
    "distribution=metadata.distribution('podman-compose');"
    "distribution_root=pathlib.Path(distribution.locate_file('')).resolve();"
    "print(distribution.version if module is not None and origin_path.is_file() and not origin_path.is_symlink() "
    "and root in module.parents and root in distribution_root.parents and distribution_root in module.parents "
    "else 'identity-mismatch')"
)
VERSION_COMMAND = (sys.executable, "-I", "-c", _VERSION_PROGRAM)
# This fixed isolated program locates but never imports the installed module.
# It reads one provenance-bound file and emits only a bounded boolean projection;
# source, paths, environment, exceptions and package metadata are never emitted.
_SOURCE_AUDIT_PRELUDE = """
import importlib.metadata as metadata
import importlib.util as util
import pathlib
import sys

root = pathlib.Path(sys.prefix).resolve()
spec = util.find_spec("podman_compose")
origin_path = pathlib.Path(spec.origin) if spec is not None and isinstance(spec.origin, str) else None
if origin_path is None or not origin_path.is_file() or origin_path.is_symlink():
    raise RuntimeError("bounded source unavailable")
origin = origin_path.resolve()
distribution = metadata.distribution("podman-compose")
distribution_root = pathlib.Path(distribution.locate_file("")).resolve()
if not (
    root in origin.parents
    and root in distribution_root.parents
    and distribution_root in origin.parents
):
    raise RuntimeError("bounded source unavailable")
source_bytes = origin.read_bytes()
if not source_bytes or len(source_bytes) > 1000000:
    raise RuntimeError("bounded source unavailable")
source = source_bytes.decode("utf-8", errors="strict")
"""
_SOURCE_AUDIT_LOGIC = """
import ast
import hashlib
import json

OFFICIAL_SOURCE_SHA256 = "10df1662477a673dc803c03e89c1bc1fba6c8c091e716fb6c7dd09c0081e1255"
REQUIRE_OFFICIAL_SOURCE = True
source_exact = hashlib.sha256(source.encode("utf-8")).hexdigest() == OFFICIAL_SOURCE_SHA256
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

def assigns_fixed_exclusion_helper(nodes):
    for node in nodes:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "excluded"):
            continue
        value = node.value
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "get_excluded"
                and len(value.args) == 2 and all(isinstance(arg, ast.Name) for arg in value.args)
                and not value.keywords and value.args[0].id == "compose" and value.args[1].id == "args"):
            return True
    return False

def is_excluded_assignment(node, value):
    return (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "excluded"
            and value(node.value))

def is_empty_set(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "set"
            and not node.args and not node.keywords)

def is_compose_services_set(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "set"
            and not node.keywords and len(node.args) == 1
            and isinstance(node.args[0], ast.Attribute) and isinstance(node.args[0].value, ast.Name)
            and node.args[0].value.id == "compose" and node.args[0].attr == "services")

def is_dependency_lookup(node, service_name):
    legacy = (
        isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
        and node.slice.value == "_deps" and isinstance(node.value, ast.Subscript)
        and isinstance(node.value.slice, ast.Name) and node.value.slice.id == service_name
        and isinstance(node.value.value, ast.Attribute)
        and isinstance(node.value.value.value, ast.Name)
        and node.value.value.value.id == "compose" and node.value.value.attr == "services"
    )
    official = (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Subscript)
        and isinstance(node.func.value.slice, ast.Name)
        and node.func.value.slice.id == service_name
        and isinstance(node.func.value.value, ast.Attribute)
        and isinstance(node.func.value.value.value, ast.Name)
        and node.func.value.value.value.id == "compose"
        and node.func.value.value.attr == "services"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "dep_field"
        and is_empty_set(node.args[1])
        and not node.keywords
    )
    return legacy or official

def is_no_deps_dependency_guard(node, service_name):
    if not isinstance(node, ast.If):
        return False
    has_service_membership = any(
        isinstance(item, ast.Compare)
        and isinstance(item.left, ast.Name)
        and item.left.id == service_name
        and any(isinstance(operator, ast.In) for operator in item.ops)
        and any(
            isinstance(comparator, ast.Attribute)
            and isinstance(comparator.value, ast.Name)
            and comparator.value.id == "compose"
            and comparator.attr == "services"
            for comparator in item.comparators
        )
        for item in ast.walk(node.test)
    )
    has_negated_no_deps_getattr = any(
        isinstance(item, ast.UnaryOp)
        and isinstance(item.op, ast.Not)
        and isinstance(item.operand, ast.Call)
        and isinstance(item.operand.func, ast.Name)
        and item.operand.func.id == "getattr"
        and len(item.operand.args) == 3
        and isinstance(item.operand.args[0], ast.Name)
        and item.operand.args[0].id == "args"
        and isinstance(item.operand.args[1], ast.Constant)
        and item.operand.args[1].value == "no_deps"
        and isinstance(item.operand.args[2], ast.Constant)
        and item.operand.args[2].value is False
        for item in ast.walk(node.test)
    )
    return has_service_membership and has_negated_no_deps_getattr and not node.orelse

def is_dependency_subtraction(node, service_name):
    if not (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == "excluded"
            and isinstance(node.op, ast.Sub) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name) and node.value.func.id == "set"
            and not node.value.keywords and len(node.value.args) == 1
            and isinstance(node.value.args[0], ast.GeneratorExp)):
        return False
    expression = node.value.args[0]
    if not (isinstance(expression.elt, ast.Attribute) and isinstance(expression.elt.value, ast.Name)
            and expression.elt.attr == "name" and len(expression.generators) == 1):
        return False
    generator = expression.generators[0]
    return (isinstance(generator.target, ast.Name) and generator.target.id == expression.elt.value.id
            and not generator.ifs and not generator.is_async and is_dependency_lookup(generator.iter, service_name))

def is_selected_service_discard(node, service_name):
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute) and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "excluded" and node.value.func.attr == "discard"
            and not node.value.keywords and len(node.value.args) == 1
            and isinstance(node.value.args[0], ast.Name) and node.value.args[0].id == service_name)

def helper_runtime_shape(handler):
    shape = {
        "exact_signature": False,
        "empty_set_initialization": False,
        "args_services_branch": False,
        "compose_services_set": False,
        "requested_service_loop": False,
        "dependency_lookup_subtraction": False,
        "selected_service_discard": False,
    }
    if handler is None:
        return shape
    signature = [argument.arg for argument in handler.args.args]
    legacy_signature = signature == ["compose", "args"] and not handler.args.defaults
    official_signature = (
        signature == ["compose", "args", "dep_field"]
        and len(handler.args.defaults) == 1
        and isinstance(handler.args.defaults[0], ast.Attribute)
        and isinstance(handler.args.defaults[0].value, ast.Name)
        and handler.args.defaults[0].value.id == "DependField"
        and handler.args.defaults[0].attr == "DEPENDENCIES"
    )
    shape["exact_signature"] = legacy_signature or official_signature
    statements = handler.body
    if statements:
        shape["empty_set_initialization"] = is_excluded_assignment(statements[0], is_empty_set)
    if len(statements) < 2:
        return shape
    branch = statements[1]
    shape["args_services_branch"] = (
        isinstance(branch, ast.If) and is_args_attribute(branch.test, "services") and not branch.orelse
    )
    if not shape["args_services_branch"]:
        return shape
    if branch.body:
        shape["compose_services_set"] = is_excluded_assignment(branch.body[0], is_compose_services_set)
    if len(branch.body) < 2:
        return shape
    loop = branch.body[1]
    shape["requested_service_loop"] = (
        isinstance(loop, ast.For) and isinstance(loop.target, ast.Name)
        and is_args_attribute(loop.iter, "services")
    )
    if not shape["requested_service_loop"] or len(loop.body) < 2:
        return shape
    service_name = loop.target.id
    dependency_nodes = [
        item
        for item in loop.body
        if is_dependency_subtraction(item, service_name)
    ]
    guarded_dependency_nodes = [
        item
        for statement in loop.body
        if is_no_deps_dependency_guard(statement, service_name)
        for item in statement.body
        if is_dependency_subtraction(item, service_name)
    ]
    shape["dependency_lookup_subtraction"] = bool(
        guarded_dependency_nodes if official_signature else dependency_nodes
    )
    shape["selected_service_discard"] = any(
        is_selected_service_discard(item, service_name) for item in loop.body
    )
    return shape

def helper_constructs_service_exclusion(handler):
    return all(helper_runtime_shape(handler).values())

def is_non_control_expression(statement):
    return (isinstance(statement, ast.Expr)
            and not any(isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)) for node in ast.walk(statement.value)))

def loop_has_excluded_service_continue_guard(loop):
    if not (isinstance(loop, ast.For) and isinstance(loop.target, ast.Name)
            and isinstance(loop.iter, ast.Attribute) and isinstance(loop.iter.value, ast.Name)
            and loop.iter.value.id == "compose" and loop.iter.attr == "containers" and loop.body):
        return False
    guard = loop.body[0]
    if not (isinstance(guard, ast.If) and not guard.orelse and guard.body
            and isinstance(guard.body[-1], ast.Continue)):
        return False
    if any(not is_non_control_expression(statement) for statement in guard.body[:-1]):
        return False
    container_name = loop.target.id
    for comparison in ast.walk(guard.test):
        if not (
            isinstance(comparison, ast.Compare)
            and len(comparison.ops) == 1
            and isinstance(comparison.ops[0], ast.In)
            and len(comparison.comparators) == 1
        ):
            continue
        left, right = comparison.left, comparison.comparators[0]
        if (
            isinstance(left, ast.Subscript)
            and isinstance(left.value, ast.Name)
            and left.value.id == container_name
            and isinstance(left.slice, ast.Constant)
            and left.slice.value == "_service"
            and isinstance(right, ast.Name)
            and right.id == "excluded"
        ):
            return True
    return False

def compose_up_runtime_shape(handler):
    shape = {
        "exact_exclusion_helper_assignment": False,
        "compose_containers_loop": False,
        "excluded_service_continue_guard": False,
        "no_deps_dependency_control_branch": False,
    }
    if handler is None:
        return shape
    statements = handler.body
    for index, statement in enumerate(statements):
        if not assigns_fixed_exclusion_helper([statement]):
            continue
        shape["exact_exclusion_helper_assignment"] = True
        for candidate in statements[index + 1:]:
            if not (isinstance(candidate, ast.For) and isinstance(candidate.target, ast.Name)
                    and isinstance(candidate.iter, ast.Attribute) and isinstance(candidate.iter.value, ast.Name)
                    and candidate.iter.value.id == "compose" and candidate.iter.attr == "containers"):
                continue
            shape["compose_containers_loop"] = True
            shape["excluded_service_continue_guard"] = loop_has_excluded_service_continue_guard(candidate)
            break
        break
    shape["no_deps_dependency_control_branch"] = no_deps_controls_expansion(local_nodes(handler))
    return shape

def up_uses_fixed_exclusion_handler(handler, helper_proven):
    shape = compose_up_runtime_shape(handler)
    return (helper_proven and shape["exact_exclusion_helper_assignment"]
            and shape["compose_containers_loop"] and shape["excluded_service_continue_guard"])

def no_deps_controls_expansion(nodes):
    for node in nodes:
        if not isinstance(node, ast.If) or not contains_attr(list(ast.walk(node.test)), "no_deps"):
            continue
        body, otherwise = branch_nodes(node.body), branch_nodes(node.orelse)
        if (assigns_selected_services(body) and assigns_dependency_expansion(otherwise)) or (assigns_selected_services(otherwise) and assigns_dependency_expansion(body)):
            return True
    return False

def legacy_parser_argument_contract(main_handler):
    empty = {
        "global_env_file_parser_present": False,
        "global_project_name_parser_present": False,
        "build_service_argument_present": False,
        "up_service_argument_present": False,
        "up_no_deps_parser_present": False,
        "up_no_build_parser_present": False,
        "up_force_recreate_parser_present": False,
    }
    if main_handler is None:
        return empty
    # CLI proof is intentionally limited to direct main() statements. Parser
    # construction or parsing nested under any branch, loop, try/match block,
    # context manager, nested function or lambda is non-dominating and cannot
    # establish the real command-line contract.
    nodes = []
    for statement in main_handler.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)):
            continue
        nodes.append(statement)
        value = statement.value
        if isinstance(value, ast.Call):
            nodes.append(value)
    parse_receivers = [
        node.func.value.id
        for node in nodes
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "parse_args"
            and isinstance(node.func.value, ast.Name)
        )
    ]
    if len(parse_receivers) != 1:
        return empty
    active_parser_name = parse_receivers[0]
    def stored_names(target):
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Starred):
            return stored_names(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            return [name for element in target.elts for name in stored_names(element)]
        return []

    store_counts = {}
    for statement in main_handler.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                store_counts[node.id] = store_counts.get(node.id, 0) + 1
    if store_counts.get(active_parser_name) != 1:
        return empty
    active_parser_assignments = 0
    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == active_parser_name for target in targets):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and (
                isinstance(value.func, ast.Attribute)
                and value.func.attr == "ArgumentParser"
                or isinstance(value.func, ast.Name)
                and value.func.id == "ArgumentParser"
            )
        ):
            active_parser_assignments += 1
    if active_parser_assignments != 1:
        return empty

    arguments = {"global": set(), "build": set(), "up": set()}
    parser_kinds = {}
    subparser_names = set()
    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [name for target in targets for name in stored_names(target)]
        if not names or not isinstance(value, ast.Call):
            continue
        if (
            isinstance(value.func, ast.Attribute)
            and value.func.attr == "ArgumentParser"
        ) or (
            isinstance(value.func, ast.Name)
            and value.func.id == "ArgumentParser"
        ):
            for name in names:
                if name == active_parser_name:
                    parser_kinds[name] = "global"
        elif (
            isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id in parser_kinds
            and parser_kinds[value.func.value.id] == "global"
            and value.func.attr == "add_subparsers"
        ):
            subparser_names.update(names)
        elif (
            isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id in subparser_names
            and value.func.attr == "add_parser"
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and isinstance(value.args[0].value, str)
        ):
            for name in names:
                parser_kinds[name] = value.args[0].value

    if any(store_counts.get(name) != 1 for name in set(parser_kinds) | subparser_names):
        return empty

    allowed_methods = {
        "global": {"add_argument", "add_subparsers", "parse_args"},
        "build": {"add_argument", "set_defaults"},
        "up": {"add_argument", "set_defaults"},
        "subparsers": {"add_parser"},
    }
    parser_roles = dict(parser_kinds)
    parser_roles.update({name: "subparsers" for name in subparser_names})
    for statement in main_handler.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)):
            continue
        parents = {
            child: parent
            for parent in ast.walk(statement)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(statement):
            if not (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in parser_roles
            ):
                continue
            parent = parents.get(node)
            if not (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and parent.attr in allowed_methods[parser_roles[node.id]]
            ):
                return empty

    for node in nodes:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in parser_kinds
        ):
            continue
        kind = parser_kinds[node.func.value.id]
        if kind not in arguments:
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                arguments[kind].add(argument.value)
    service_names = {"service", "services", "SERVICE"}
    return {
        "global_env_file_parser_present": "--env-file" in arguments["global"],
        "global_project_name_parser_present": "--project-name" in arguments["global"],
        "build_service_argument_present": bool(arguments["build"] & service_names),
        "up_service_argument_present": bool(arguments["up"] & service_names),
        "up_no_deps_parser_present": "--no-deps" in arguments["up"],
        "up_no_build_parser_present": "--no-build" in arguments["up"],
        "up_force_recreate_parser_present": "--force-recreate" in arguments["up"],
    }

def class_methods(name):
    node = next((item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == name), None)
    if node is None:
        return {}
    return {
        item.name: item
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

def literal_arguments(handler, receiver_name):
    result = set()
    if handler is None:
        return result
    for node in ast.walk(handler):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == receiver_name
        ):
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                result.add(argument.value)
    return result

def decorator_commands(handler):
    commands = set()
    if handler is None:
        return commands
    for decorator in handler.decorator_list:
        if not (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "cmd_parse"
            and len(decorator.args) >= 2
        ):
            continue
        selected = decorator.args[1]
        if isinstance(selected, ast.Constant) and isinstance(selected.value, str):
            commands.add(selected.value)
        elif isinstance(selected, (ast.List, ast.Tuple, ast.Set)):
            commands.update(
                item.value
                for item in selected.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return commands

def official_dispatch_proven(parse_handler, init_handler):
    if parse_handler is None or init_handler is None:
        return False
    parse_args = [argument.arg for argument in parse_handler.args.args]
    init_args = [argument.arg for argument in init_handler.args.args]
    if parse_args != ["self", "argv"] or init_args != ["parser"]:
        return False
    calls = [node for node in ast.walk(parse_handler) if isinstance(node, ast.Call)]
    has_constructor = any(
        isinstance(call.func, ast.Attribute) and call.func.attr == "ArgumentParser"
        for call in calls
    )
    has_init = any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "_init_global_parser"
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "parser"
        for call in calls
    )
    has_subparsers = any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "add_subparsers"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "parser"
        for call in calls
    )
    has_dynamic_command_parser = any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "add_parser"
        and call.args
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "cmd_name"
        for call in calls
    )
    has_parser_callback = any(
        isinstance(call.func, ast.Name)
        and call.func.id == "cmd_parser"
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "subparser"
        for call in calls
    )
    has_parse = any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "parse_args"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "parser"
        for call in calls
    )
    return all((
        has_constructor, has_init, has_subparsers, has_dynamic_command_parser,
        has_parser_callback, has_parse,
    ))

def official_parser_argument_contract():
    empty = {
        "global_env_file_parser_present": False,
        "global_project_name_parser_present": False,
        "build_service_argument_present": False,
        "up_service_argument_present": False,
        "up_no_deps_parser_present": False,
        "up_no_build_parser_present": False,
        "up_force_recreate_parser_present": False,
    }
    methods = class_methods("PodmanCompose")
    parse_handler = methods.get("_parse_args")
    init_handler = methods.get("_init_global_parser")
    if not official_dispatch_proven(parse_handler, init_handler):
        return empty
    global_arguments = literal_arguments(init_handler, "parser")
    command_arguments = {"build": set(), "up": set()}
    for handler in handlers.values():
        commands = decorator_commands(handler)
        arguments = literal_arguments(handler, "parser")
        for command in command_arguments:
            if command in commands:
                command_arguments[command].update(arguments)
    service_names = {"service", "services", "SERVICE"}
    return {
        "global_env_file_parser_present": "--env-file" in global_arguments,
        "global_project_name_parser_present": "--project-name" in global_arguments,
        "build_service_argument_present": bool(command_arguments["build"] & service_names),
        "up_service_argument_present": bool(command_arguments["up"] & service_names),
        "up_no_deps_parser_present": "--no-deps" in command_arguments["up"],
        "up_no_build_parser_present": "--no-build" in command_arguments["up"],
        "up_force_recreate_parser_present": "--force-recreate" in command_arguments["up"],
    }

build, up, exclusion_helper = handlers.get("compose_build"), handlers.get("compose_up"), handlers.get("get_excluded")
parser_contract = (
    official_parser_argument_contract()
    if source_exact
    else legacy_parser_argument_contract(handlers.get("main"))
)
build_nodes = local_nodes(build) if build is not None else []
up_nodes = local_nodes(up) if up is not None else []
helper_shape = helper_runtime_shape(exclusion_helper)
up_shape = compose_up_runtime_shape(up)
helper_proven = all(helper_shape.values())
if source_exact:
    up_shape["no_deps_dependency_control_branch"] = helper_proven
source_authorized = source_exact or not REQUIRE_OFFICIAL_SOURCE
payload = {
    "schema_id": "odysseus.podman_compose_source_audit.v1",
    "build_service_selection_handler_local": source_authorized and (contains_attr(build_nodes, "services") or (assigns_fixed_exclusion_helper(build_nodes) and helper_proven)),
    "up_service_selection_handler_local": source_authorized and up_uses_fixed_exclusion_handler(up, helper_proven),
    "up_no_deps_guard_controls_dependency_expansion": source_authorized and up_shape["no_deps_dependency_control_branch"],
    "rollback_force_recreate_consumed_in_up": source_authorized and contains_attr(up_nodes, "force_recreate"),
    **{key: source_authorized and value for key, value in parser_contract.items()},
    "runtime_shape_profile": {
        "source_ast": {
            "compose_build_handler_present": build is not None,
            "compose_up_handler_present": up is not None,
            "get_excluded_handler_present": exclusion_helper is not None,
            "exclusion_helper": helper_shape,
            "compose_up": up_shape,
        },
    },
}
encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
payload["evidence_sha256"] = hashlib.sha256(encoded).hexdigest()
print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
"""
_SOURCE_AUDIT_PROGRAM = _SOURCE_AUDIT_PRELUDE + _SOURCE_AUDIT_LOGIC
SOURCE_AUDIT_COMMAND = (sys.executable, "-I", "-c", _SOURCE_AUDIT_PROGRAM)

_VERSION = re.compile(r"^1\.6\.0$")
_SHORT_VERSION_LINE = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_USAGE_BLOCK_LINES = 8
MAX_USAGE_BLOCK_CHARS = 4_096
_ERRORS = frozenset({
    "version_unavailable", "version_mismatch", "help_unavailable", "source_audit_unavailable",
    "source_audit_invalid", "malformed_output", "output_too_large", "timeout", "internal_error",
})
_VERSION_DIAGNOSTIC_CODES = frozenset({
    "version_output_empty", "version_output_controls", "version_output_multiline",
    "version_output_line_shape", "version_output_version_mismatch",
})
_NEEDS_REASONS = frozenset({"semantic_proof_insufficient"})
_MISSING_PROOF_CODES = (
    "global_env_file_parser_missing", "global_project_name_parser_missing",
    "build_service_argument_missing", "up_service_argument_missing",
    "up_no_deps_parser_missing", "up_no_build_parser_missing",
    "up_force_recreate_parser_missing", "source_build_service_selection_missing",
    "source_up_service_selection_missing", "source_up_no_deps_guard_missing",
    "source_rollback_force_recreate_missing",
)
_VISIBILITY_KEYS = frozenset({
    "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible",
    "source_text_visible", "paths_visible", "hostnames_visible", "secret_values_visible",
})
_SOURCE_AUDIT_KEYS = frozenset({
    "schema_id", "build_service_selection_handler_local", "up_service_selection_handler_local",
    "up_no_deps_guard_controls_dependency_expansion", "rollback_force_recreate_consumed_in_up",
    "global_env_file_parser_present", "global_project_name_parser_present",
    "build_service_argument_present", "up_service_argument_present",
    "up_no_deps_parser_present", "up_no_build_parser_present",
    "up_force_recreate_parser_present", "runtime_shape_profile", "evidence_sha256",
})
_SOURCE_AUDIT_BOOL_KEYS = frozenset({
    "build_service_selection_handler_local", "up_service_selection_handler_local",
    "up_no_deps_guard_controls_dependency_expansion", "rollback_force_recreate_consumed_in_up",
    "global_env_file_parser_present", "global_project_name_parser_present",
    "build_service_argument_present", "up_service_argument_present",
    "up_no_deps_parser_present", "up_no_build_parser_present",
    "up_force_recreate_parser_present",
})
_RUNTIME_SHAPE_KEYS = frozenset({"help_grammar", "source_ast"})
_HELP_GRAMMAR_KEYS = frozenset({"build", "up"})
_USAGE_SHAPE_KEYS = frozenset({
    "usage_line_present", "uppercase_service_positional_grammar_present",
    "bracketed_lowercase_services_positional_grammar_present",
    "bare_lowercase_services_positional_grammar_present",
})
_SOURCE_AST_KEYS = frozenset({
    "compose_build_handler_present", "compose_up_handler_present", "get_excluded_handler_present",
    "exclusion_helper", "compose_up",
})
_EXCLUSION_HELPER_SHAPE_KEYS = frozenset({
    "exact_signature", "empty_set_initialization", "args_services_branch", "compose_services_set",
    "requested_service_loop", "dependency_lookup_subtraction", "selected_service_discard",
})
_COMPOSE_UP_SHAPE_KEYS = frozenset({
    "exact_exclusion_helper_assignment", "compose_containers_loop", "excluded_service_continue_guard",
    "no_deps_dependency_control_branch",
})
_OK_KEYS = frozenset({
    "schema_id", "status", "podman_compose_version", "global_env_file_parser_present",
    "global_project_name_parser_present", "service_scoped_build_parser_present",
    "service_scoped_up_parser_present", "no_deps_parser_present", "no_build_parser_present",
    "rollback_force_recreate_parser_present", "service_scoped_dependency_exclusion_proven",
    "rollback_force_recreate_proven", "deployment_capability_supported", *_VISIBILITY_KEYS, "evidence_sha256",
})
_BLOCKED_KEYS = frozenset({"schema_id", "status", "error_code", "retry_permitted", "evidence_sha256"})
_VERSION_BLOCKED_KEYS = _BLOCKED_KEYS | {"diagnostic_code"}
_NEEDS_KEYS = frozenset({
    "schema_id", "status", "reason_code", "missing_proofs", "retry_permitted",
    "runtime_shape_profile", "evidence_sha256",
})


class CapabilityFailure(ValueError):
    def __init__(self, code: str, diagnostic_code: str | None = None) -> None:
        super().__init__(code)
        self.code = code if code in _ERRORS else "internal_error"
        self.diagnostic_code = diagnostic_code if diagnostic_code in _VERSION_DIAGNOSTIC_CODES else None


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _all_literal_bools(payload: Mapping[str, Any], keys: frozenset[str]) -> bool:
    return set(payload) == keys and all(type(payload[key]) is bool for key in keys)


def _valid_source_runtime_shape(value: Any) -> bool:
    if type(value) is not dict or set(value) != {"source_ast"}:
        return False
    source_ast = value["source_ast"]
    if type(source_ast) is not dict or set(source_ast) != _SOURCE_AST_KEYS:
        return False
    if any(type(source_ast[key]) is not bool for key in {
        "compose_build_handler_present", "compose_up_handler_present", "get_excluded_handler_present",
    }):
        return False
    helper, compose_up = source_ast["exclusion_helper"], source_ast["compose_up"]
    return (
        type(helper) is dict and _all_literal_bools(helper, _EXCLUSION_HELPER_SHAPE_KEYS)
        and type(compose_up) is dict and _all_literal_bools(compose_up, _COMPOSE_UP_SHAPE_KEYS)
    )


def _valid_runtime_shape_profile(value: Any) -> bool:
    if type(value) is not dict or set(value) != _RUNTIME_SHAPE_KEYS:
        return False
    help_grammar, source_ast = value["help_grammar"], value["source_ast"]
    if type(help_grammar) is not dict or set(help_grammar) != _HELP_GRAMMAR_KEYS:
        return False
    if any(type(help_grammar[name]) is not dict or not _all_literal_bools(help_grammar[name], _USAGE_SHAPE_KEYS)
           for name in _HELP_GRAMMAR_KEYS):
        return False
    return _valid_source_runtime_shape({"source_ast": source_ast})


def _usage_lines(help_text: str, subcommand: str) -> tuple[str, ...]:
    pattern = re.compile(r"(?im)^\s*usage:.*\b" + re.escape(subcommand) + r"\b.*$")
    lines = help_text.splitlines()
    for index, line in enumerate(lines):
        if pattern.fullmatch(line) is None:
            continue
        # An over-limit header is not a bounded usage block and is represented
        # conservatively as absent, rather than partially inspecting it.
        if len(line) > MAX_USAGE_BLOCK_CHARS:
            return ()
        block = [line]
        block_chars = len(line)
        for continuation in lines[index + 1:index + 1 + MAX_USAGE_BLOCK_LINES]:
            stripped = continuation.lstrip()
            if (not continuation or not continuation[0].isspace()
                    or re.match(r"(?i)^(?:usage|options|commands|description)\s*:", stripped)):
                break
            if block_chars + len(continuation) > MAX_USAGE_BLOCK_CHARS:
                break
            block.append(continuation)
            block_chars += len(continuation)
        return tuple(block)
    return ()


def _is_positional_grammar_continuation(line: str) -> bool:
    """Accept only an all-metavariable continuation, never descriptive prose."""
    normalized = line.strip()
    if not normalized or normalized.startswith("-") or ("SERVICE" not in normalized and "[services ...]" not in normalized):
        return False
    token = r"(?:SERVICE|\[SERVICE \.\.\.\]|\[services \.\.\.\]|\[OPTIONS?\]|\.\.\.|[|()])"
    return re.fullmatch(token + r"(?:\s+" + token + r")*", normalized) is not None


def _usage_runtime_shape(help_text: str, subcommand: str) -> dict[str, bool]:
    lines = _usage_lines(help_text, subcommand)
    grammar_lines = tuple(
        line for index, line in enumerate(lines)
        if index == 0 or _is_positional_grammar_continuation(line)
    )
    return {
        "usage_line_present": bool(lines),
        "uppercase_service_positional_grammar_present": any(
            re.search(r"(?:^|\s)SERVICE(?=\s|\[|$)", line) is not None for line in grammar_lines
        ),
        "bracketed_lowercase_services_positional_grammar_present": any("[services ...]" in line for line in grammar_lines),
        "bare_lowercase_services_positional_grammar_present": any(
            re.search(r"\b" + re.escape(subcommand) + r"\b\s+services(?=\s|$)", line) is not None
            for line in grammar_lines
        ),
    }


def _runtime_shape_profile(source_audit: Mapping[str, Any]) -> dict[str, Any]:
    source_profile = source_audit.get("runtime_shape_profile")
    if not _valid_source_runtime_shape(source_profile):
        raise CapabilityFailure("source_audit_invalid")
    return {
        "help_grammar": {
            "build": {
                "usage_line_present": source_audit["build_service_argument_present"],
                "uppercase_service_positional_grammar_present": source_audit["build_service_argument_present"],
                "bracketed_lowercase_services_positional_grammar_present": False,
                "bare_lowercase_services_positional_grammar_present": False,
            },
            "up": {
                "usage_line_present": source_audit["up_service_argument_present"],
                "uppercase_service_positional_grammar_present": source_audit["up_service_argument_present"],
                "bracketed_lowercase_services_positional_grammar_present": False,
                "bare_lowercase_services_positional_grammar_present": False,
            },
        },
        "source_ast": source_profile["source_ast"],
    }


def blocked(code: str, diagnostic_code: str | None = None) -> dict[str, Any]:
    payload = {"schema_id": SCHEMA_ID, "status": "blocked", "error_code": code if code in _ERRORS else "internal_error", "retry_permitted": False}
    if diagnostic_code in _VERSION_DIAGNOSTIC_CODES and payload["error_code"] in {"malformed_output", "version_mismatch"}:
        payload["diagnostic_code"] = diagnostic_code
    payload["evidence_sha256"] = _digest(payload)
    return payload


def needs_live_observation(reason: str, missing_proofs: Sequence[str], runtime_shape_profile: Mapping[str, Any]) -> dict[str, Any]:
    requested = tuple(missing_proofs)
    if (not requested or len(requested) > len(_MISSING_PROOF_CODES)
            or any(code not in _MISSING_PROOF_CODES for code in requested)):
        return blocked("internal_error")
    canonical = tuple(code for code in _MISSING_PROOF_CODES if code in requested)
    if requested != canonical or not _valid_runtime_shape_profile(runtime_shape_profile):
        return blocked("internal_error")
    payload = {
        "schema_id": SCHEMA_ID, "status": "needs_live_observation",
        "reason_code": reason if reason in _NEEDS_REASONS else "semantic_proof_insufficient",
        "missing_proofs": list(canonical), "retry_permitted": False,
        "runtime_shape_profile": runtime_shape_profile,
    }
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
    """Accept only the fixed one-line short Compose version without retaining output."""
    if not raw:
        raise CapabilityFailure("malformed_output", "version_output_empty")
    if any((ord(character) < 32 and character != "\n") or ord(character) == 127 for character in raw):
        raise CapabilityFailure("malformed_output", "version_output_controls")
    body = raw[:-1] if raw.endswith("\n") else raw
    if not body:
        raise CapabilityFailure("malformed_output", "version_output_empty")
    if "\n" in body:
        raise CapabilityFailure("malformed_output", "version_output_multiline")
    if not _SHORT_VERSION_LINE.fullmatch(body):
        raise CapabilityFailure("malformed_output", "version_output_line_shape")
    if not _VERSION.fullmatch(body):
        raise CapabilityFailure("version_mismatch", "version_output_version_mismatch")
    return body


def _has_flag(help_text: str, flag: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_-])" + re.escape(flag) + r"(?![A-Za-z0-9_-])", help_text) is not None


def _has_service_argument(help_text: str, subcommand: str) -> bool:
    # Require only the exact documented positional grammar in a usage line,
    # rather than treating descriptive uses of the word as parser evidence.
    shape = _usage_runtime_shape(help_text, subcommand)
    return shape["uppercase_service_positional_grammar_present"] or shape["bracketed_lowercase_services_positional_grammar_present"]


def _parse_source_audit(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(_single_line(raw))
    except (TypeError, json.JSONDecodeError, CapabilityFailure):
        raise CapabilityFailure("source_audit_invalid") from None
    if type(payload) is not dict or set(payload) != _SOURCE_AUDIT_KEYS:
        raise CapabilityFailure("source_audit_invalid")
    if payload.get("schema_id") != SOURCE_AUDIT_SCHEMA_ID:
        raise CapabilityFailure("source_audit_invalid")
    if any(type(payload.get(key)) is not bool for key in _SOURCE_AUDIT_BOOL_KEYS):
        raise CapabilityFailure("source_audit_invalid")
    if not _valid_source_runtime_shape(payload.get("runtime_shape_profile")):
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
        source_audit = _parse_source_audit(_run(SOURCE_AUDIT_COMMAND, runner))
        runtime_shape_profile = _runtime_shape_profile(source_audit)

        proof_values = (
            ("global_env_file_parser_missing", source_audit["global_env_file_parser_present"]),
            ("global_project_name_parser_missing", source_audit["global_project_name_parser_present"]),
            ("build_service_argument_missing", source_audit["build_service_argument_present"]),
            ("up_service_argument_missing", source_audit["up_service_argument_present"]),
            ("up_no_deps_parser_missing", source_audit["up_no_deps_parser_present"]),
            ("up_no_build_parser_missing", source_audit["up_no_build_parser_present"]),
            ("up_force_recreate_parser_missing", source_audit["up_force_recreate_parser_present"]),
            ("source_build_service_selection_missing", source_audit["build_service_selection_handler_local"]),
            ("source_up_service_selection_missing", source_audit["up_service_selection_handler_local"]),
            ("source_up_no_deps_guard_missing", source_audit["up_no_deps_guard_controls_dependency_expansion"]),
            ("source_rollback_force_recreate_missing", source_audit["rollback_force_recreate_consumed_in_up"]),
        )
        missing_proofs = tuple(code for code, proven in proof_values if proven is not True)
        if missing_proofs:
            return needs_live_observation("semantic_proof_insufficient", missing_proofs, runtime_shape_profile)
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
        return blocked(failure.code, failure.diagnostic_code)
    except Exception:
        return blocked("internal_error")


def main() -> int:
    payload = collect_podman_compose_capability_observation()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
