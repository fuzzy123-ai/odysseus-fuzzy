"""Worker registration boundary for the sandboxed Temporal Light workflow."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable, Sequence
from types import ModuleType
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from src.temporal_runtime import workflows as workflow_module
from src.temporal_runtime.workflows import ABCExecutionWorkflow


FORBIDDEN_WORKFLOW_IMPORTS = (
    "http",
    "multiprocessing",
    "os",
    "pathlib",
    "random",
    "secrets",
    "shutil",
    "socket",
    "subprocess",
    "tempfile",
    "threading",
    "time",
    "urllib",
)
FORBIDDEN_WORKFLOW_CALLS = (
    "date.today",
    "datetime.now",
    "datetime.today",
    "datetime.utcnow",
    "open",
)


class WorkflowRegistrationError(ValueError):
    """Raised before Worker creation when source violates sandbox policy."""


def assert_deterministic_workflow_source(source: str) -> None:
    """Reject direct I/O, nondeterministic clocks/randomness and global writes."""

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise WorkflowRegistrationError("workflow source is not valid Python") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".", 1)[0] for alias in node.names}
            denied = sorted(roots.intersection(FORBIDDEN_WORKFLOW_IMPORTS))
            if denied:
                raise WorkflowRegistrationError(f"forbidden workflow import: {denied[0]}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in FORBIDDEN_WORKFLOW_IMPORTS:
                raise WorkflowRegistrationError(f"forbidden workflow import: {root}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            raise WorkflowRegistrationError("workflow global-state mutation is forbidden")
        elif isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in FORBIDDEN_WORKFLOW_CALLS:
                raise WorkflowRegistrationError(f"forbidden workflow call: {call_name}")

    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            names = _assigned_names(statement)
            if any(not name.isupper() for name in names):
                raise WorkflowRegistrationError("mutable module-level workflow state is forbidden")
            value = statement.value
            if isinstance(value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)):
                raise WorkflowRegistrationError("mutable module-level workflow state is forbidden")


def assert_registered_workflow_is_deterministic(module: ModuleType = workflow_module) -> None:
    assert_deterministic_workflow_source(inspect.getsource(module))


def create_temporal_worker(
    client: Client,
    *,
    task_queue: str,
    activities: Sequence[Callable[..., Any]],
    max_concurrent_activities: int = 3,
) -> Worker:
    """Create, but do not start, one default-sandbox Worker."""

    if not isinstance(task_queue, str) or not task_queue or len(task_queue) > 128:
        raise WorkflowRegistrationError("task_queue must be a bounded non-empty string")
    if isinstance(max_concurrent_activities, bool) or not 1 <= max_concurrent_activities <= 3:
        raise WorkflowRegistrationError("max_concurrent_activities must be 1 through 3")
    assert_registered_workflow_is_deterministic()
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[ABCExecutionWorkflow],
        activities=list(activities),
        max_concurrent_activities=max_concurrent_activities,
        max_concurrent_workflow_tasks=3,
    )


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _assigned_names(statement: ast.Assign | ast.AnnAssign) -> tuple[str, ...]:
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
    return tuple(names)
