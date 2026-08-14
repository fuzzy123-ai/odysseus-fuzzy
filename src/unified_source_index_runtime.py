"""Default-off, inert knowledge-runtime composition boundary.

This module deliberately depends only on the Python runtime.  Its exported
handles carry no authority: lifecycle state and the optional planner method
live in private registries created by the factory below.
"""

from __future__ import annotations

import _thread as _thread
import _weakref as _weakref


def _make_api():
    """Create the public API around closure-owned, trusted registries."""

    snapshot_schema = "odysseus.knowledge_runtime.composition_snapshot.v1"
    modes = frozenset(
        {"disabled", "read_only", "shadow", "canary", "active", "degraded", "rollback"}
    )
    error_codes = frozenset(
        {
            "invalid_config",
            "invalid_planner",
            "invalid_runtime",
            "invalid_binder",
            "planner_unavailable",
            "mode_not_composed",
            "bind_already_attempted",
            "bind_failed",
            "closed",
            "close_failed",
            "token_inactive",
            "planner_failed",
        }
    )
    function_type = type(lambda: None)
    allocate_lock = _thread.allocate_lock
    weakref_ref = _weakref.ref

    class _MethodSample:
        def method(self) -> None:
            return None

    method_type = type(_MethodSample().method)
    mapping_proxy_type = type(type.__dict__)
    lock_type = type(allocate_lock())
    runtime_registry: dict[int, tuple[object, object]] = {}
    token_registry: dict[int, tuple[object, object]] = {}

    class KnowledgeRuntimeError(ValueError):
        """A fresh, fixed-code, content-free composition error."""

        __slots__ = ("code",)

        def __init__(self, code: str) -> None:
            if type(code) is not str or code not in error_codes:
                code = "invalid_runtime"
            ValueError.__init__(self, code)
            self.code = code

    def error(code: str) -> KnowledgeRuntimeError:
        return KnowledgeRuntimeError(code)

    class _Record:
        __slots__ = ("lock", "mode", "kind", "cell")

        def __init__(self, mode: str, kind: str, cell: tuple[object, ...]) -> None:
            self.lock = allocate_lock()
            self.mode = mode
            self.kind = kind
            self.cell = cell

    class KnowledgeRuntime:
        """Opaque runtime handle; only the private factory can mint one."""

        __slots__ = ("__weakref__",)

        def __new__(cls, *args: object, **kwargs: object) -> "KnowledgeRuntime":
            raise error("invalid_runtime") from None

        def snapshot(self, /) -> dict[str, object]:
            record = lookup_runtime(self)
            with record.lock:
                validate_record(record)
                return snapshot_for(record)

    class _InvocationToken:
        __slots__ = ("__weakref__",)

        def __new__(cls, *args: object, **kwargs: object) -> "_InvocationToken":
            raise error("token_inactive") from None

        def execute(self, request: object) -> object:
            entry = token_registry.get(id(self))
            if type(entry) is not tuple or len(entry) != 2:
                raise error("token_inactive") from None
            token_ref, runtime_ref = entry
            try:
                if token_ref() is not self:
                    raise error("token_inactive")
                runtime = runtime_ref()
            except KnowledgeRuntimeError:
                raise
            except BaseException:
                raise error("token_inactive") from None
            if runtime is None:
                raise error("token_inactive") from None
            record = lookup_runtime(runtime)
            with record.lock:
                validate_record(record)
                cell = record.cell
                if (
                    type(cell) is not tuple
                    or len(cell) != 4
                    or cell[0] != "bound_ready"
                    or type(cell[2]) is not method_type
                    or cell[3] != id(self)
                ):
                    raise error("token_inactive") from None
                execute = cell[2]
            try:
                return execute(request)
            except BaseException:
                raise error("planner_failed") from None

    def lookup_runtime(runtime: object) -> _Record:
        if type(runtime) is not KnowledgeRuntime:
            raise error("invalid_runtime") from None
        entry = runtime_registry.get(id(runtime))
        if type(entry) is not tuple or len(entry) != 2:
            raise error("invalid_runtime") from None
        runtime_ref, record = entry
        try:
            same_runtime = runtime_ref() is runtime
        except BaseException:
            same_runtime = False
        if not same_runtime or type(record) is not _Record:
            raise error("invalid_runtime") from None
        return record

    def validate_record(record: _Record) -> None:
        if (
            type(record.lock) is not lock_type
            or type(record.mode) is not str
            or record.mode not in modes
            or type(record.kind) is not str
            or record.kind
            not in {
                "disabled",
                "ready",
                "degraded_planner_unavailable",
                "degraded_invalid_planner",
                "degraded_mode_not_composed",
            }
            or type(record.cell) is not tuple
        ):
            raise error("invalid_runtime") from None
        cell = record.cell
        phase = cell[0] if cell else None
        valid = False
        if phase == "unbound" and len(cell) == 2:
            planner = cell[1]
            valid = (record.kind == "ready" and type(planner) is method_type) or (
                record.kind != "ready" and planner is None
            )
        elif phase in {"binding", "close_requested"} and len(cell) == 2:
            token_id = cell[1]
            valid = (record.kind == "ready" and type(token_id) is int) or (
                record.kind != "ready" and token_id is None
            )
        elif phase == "bound_inert" and len(cell) == 2:
            valid = type(cell[1]) is function_type
        elif phase == "bound_ready" and len(cell) == 4:
            valid = (
                record.kind == "ready"
                and type(cell[1]) is function_type
                and type(cell[2]) is method_type
                and type(cell[3]) is int
            )
        elif phase in {"bind_failed", "closing"} and len(cell) == 1:
            valid = True
        elif phase == "closed" and len(cell) == 2:
            valid = cell[1] in {None, "bind_failed", "close_failed"}
        if not valid:
            raise error("invalid_runtime") from None

    def snapshot_for(record: _Record) -> dict[str, object]:
        cell = record.cell
        phase = cell[0]
        if phase in {"close_requested", "closing", "closed"}:
            state = "closed"
        elif phase == "bind_failed":
            state = "degraded"
        elif record.kind == "disabled":
            state = "disabled"
        elif record.kind == "ready":
            state = "ready"
        else:
            state = "degraded"
        if phase == "bind_failed":
            error_code = "bind_failed"
        elif phase == "closed" and cell[1] in {"bind_failed", "close_failed"}:
            error_code = cell[1]
        elif record.kind == "degraded_planner_unavailable":
            error_code = "planner_unavailable"
        elif record.kind == "degraded_invalid_planner":
            error_code = "invalid_planner"
        elif record.kind == "degraded_mode_not_composed":
            error_code = "mode_not_composed"
        else:
            error_code = None
        return {
            "schema": snapshot_schema,
            "state": state,
            "mode": record.mode,
            "planner_bound": phase == "bound_ready",
            "error_code": error_code,
            "live_activation_authorized": False,
        }

    def revoke_token(token_id: object) -> None:
        if type(token_id) is int:
            token_registry.pop(token_id, None)

    def capture_execute(planner: object) -> object | None:
        try:
            planner_type = type(planner)
            if type(planner_type) is not type:
                return None
            namespace = type.__getattribute__(planner_type, "__dict__")
            if type(namespace) is not mapping_proxy_type:
                return None
            try:
                execute = namespace["execute"]
            except KeyError:
                return None
            if type(execute) is not function_type:
                return None
            captured = execute.__get__(planner, planner_type)
            return captured if type(captured) is method_type else None
        except BaseException:
            return None

    def new_runtime(mode: str, kind: str, planner: object | None) -> KnowledgeRuntime:
        runtime = object.__new__(KnowledgeRuntime)
        record = _Record(mode, kind, ("unbound", planner))
        runtime_registry[id(runtime)] = (weakref_ref(runtime), record)
        return runtime

    def build_knowledge_runtime(
        config: tuple[str, bool], *, planner: object | None = None
    ) -> KnowledgeRuntime:
        if type(config) is not tuple or len(config) != 2:
            raise error("invalid_config") from None
        mode = config[0]
        runtime_enabled = config[1]
        if (
            type(mode) is not str
            or mode not in modes
            or type(runtime_enabled) is not bool
            or (mode == "disabled" and runtime_enabled)
            or (mode != "disabled" and not runtime_enabled)
        ):
            raise error("invalid_config") from None
        if mode == "disabled":
            return new_runtime(mode, "disabled", None)
        if mode != "read_only":
            return new_runtime(mode, "degraded_mode_not_composed", None)
        if planner is None:
            return new_runtime(mode, "degraded_planner_unavailable", None)
        execute = capture_execute(planner)
        if execute is None:
            return new_runtime(mode, "degraded_invalid_planner", None)
        return new_runtime(mode, "ready", execute)

    def finish_unbind(record: _Record, binder: object) -> None:
        failed = False
        try:
            binder(None)
        except BaseException:
            failed = True
        with record.lock:
            validate_record(record)
            record.cell = ("closed", "close_failed" if failed else None)
        if failed:
            raise error("close_failed") from None

    def bind_knowledge_runtime(runtime: KnowledgeRuntime, binder: object) -> None:
        if type(binder) is not function_type:
            raise error("invalid_binder") from None
        record = lookup_runtime(runtime)
        with record.lock:
            validate_record(record)
            cell = record.cell
            phase = cell[0]
            if phase in {"close_requested", "closing", "closed"}:
                raise error("closed") from None
            if phase in {"binding", "bound_inert", "bound_ready", "bind_failed"}:
                raise error("bind_already_attempted") from None
            planner = cell[1]
            token = None
            token_id = None
            if type(planner) is method_type:
                token = object.__new__(_InvocationToken)
                token_id = id(token)
                token_registry[token_id] = (weakref_ref(token), weakref_ref(runtime))
            record.cell = ("binding", token_id)
        failed = False
        try:
            binder(token)
        except BaseException:
            failed = True
        unbind = False
        with record.lock:
            validate_record(record)
            phase, current_token_id = record.cell
            if phase not in {"binding", "close_requested"} or current_token_id != token_id:
                raise error("invalid_runtime") from None
            if failed:
                revoke_token(token_id)
                record.cell = (
                    ("closed", "bind_failed")
                    if phase == "close_requested"
                    else ("bind_failed",)
                )
                raise error("bind_failed") from None
            if phase == "close_requested":
                revoke_token(token_id)
                record.cell = ("closing",)
                unbind = True
            elif token is None:
                record.cell = ("bound_inert", binder)
            else:
                record.cell = ("bound_ready", binder, planner, token_id)
        if unbind:
            finish_unbind(record, binder)

    def close_knowledge_runtime(runtime: KnowledgeRuntime) -> None:
        record = lookup_runtime(runtime)
        binder = None
        with record.lock:
            validate_record(record)
            cell = record.cell
            phase = cell[0]
            if phase in {"close_requested", "closing", "closed"}:
                return
            if phase == "binding":
                record.cell = ("close_requested", cell[1])
                return
            if phase == "unbound":
                record.cell = ("closed", None)
                return
            if phase == "bind_failed":
                record.cell = ("closed", "bind_failed")
                return
            if phase == "bound_inert":
                binder = cell[1]
                record.cell = ("closing",)
            elif phase == "bound_ready":
                binder = cell[1]
                revoke_token(cell[3])
                record.cell = ("closing",)
            else:
                raise error("invalid_runtime") from None
        finish_unbind(record, binder)

    KnowledgeRuntime.__module__ = __name__
    KnowledgeRuntimeError.__module__ = __name__
    _InvocationToken.__module__ = __name__
    return (
        KnowledgeRuntime,
        KnowledgeRuntimeError,
        bind_knowledge_runtime,
        build_knowledge_runtime,
        close_knowledge_runtime,
    )


(
    KnowledgeRuntime,
    KnowledgeRuntimeError,
    bind_knowledge_runtime,
    build_knowledge_runtime,
    close_knowledge_runtime,
) = _make_api()

del _make_api, _thread, _weakref

__all__ = (
    "KnowledgeRuntime",
    "KnowledgeRuntimeError",
    "bind_knowledge_runtime",
    "build_knowledge_runtime",
    "close_knowledge_runtime",
)
