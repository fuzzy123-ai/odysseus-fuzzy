"""Owner-scoped Agent command validation and persisted Temporal readback."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from src.temporal_runtime.commands import (
    CommandContractError,
    CommandReceipt,
    CommandRequest,
)


class AgentOperationCommandError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class CommandRunAdapter(Protocol):
    async def get_run(self, owner_scope_ref: str, agent_run_id: str) -> Mapping[str, Any]: ...

    async def command_readback(
        self,
        owner_scope_ref: str,
        agent_run_id: str,
        request: CommandRequest,
    ) -> Mapping[str, Any]: ...


class AgentOperationCommandService:
    """Apply only commands advertised by the current backend projection."""

    def __init__(self, adapter: CommandRunAdapter) -> None:
        self._adapter = adapter

    async def execute(
        self,
        owner_scope_ref: str,
        agent_run_id: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(body, Mapping):
            raise AgentOperationCommandError("invalid_command", "command body must be an object")
        required = {
            "command_id",
            "command",
            "expected_run_version",
            "idempotency_key",
            "payload",
        }
        if set(body) != required:
            raise AgentOperationCommandError(
                "invalid_command", "command fields are not exact"
            )
        try:
            request = CommandRequest.create(
                command_id=body["command_id"],
                command=body["command"],
                expected_run_version=body["expected_run_version"],
                idempotency_key=body["idempotency_key"],
                payload=body["payload"],
            )
        except CommandContractError as exc:
            raise AgentOperationCommandError(exc.code, exc.detail) from exc

        before = dict(await self._adapter.get_run(owner_scope_ref, agent_run_id))
        run = before.get("run")
        if not isinstance(run, Mapping):
            raise AgentOperationCommandError(
                "invalid_projection", "current Agent run projection is unavailable"
            )
        current_version = run.get("version")
        if not isinstance(current_version, int) or isinstance(current_version, bool):
            raise AgentOperationCommandError(
                "invalid_projection", "current Agent run version is invalid"
            )
        if request.expected_run_version > current_version:
            raise AgentOperationCommandError(
                "stale_run_version", "expected_run_version is no longer current"
            )
        # A request at the current version must be advertised by the backend.
        # An older request is forwarded only so Temporal's durable Update id
        # ledger can return an already-applied duplicate; any new stale command
        # is rejected by the workflow validator without mutation.
        if request.expected_run_version == current_version:
            allowed = run.get("allowed_commands")
            if not isinstance(allowed, list) or request.command not in allowed:
                raise AgentOperationCommandError(
                    "command_not_allowed",
                    "the current server projection does not allow this command",
                )

        try:
            result = dict(
                await self._adapter.command_readback(
                    owner_scope_ref, agent_run_id, request
                )
            )
            receipt_payload = result.get("command_receipt")
            readback = result.get("run")
            if not isinstance(receipt_payload, Mapping) or not isinstance(readback, Mapping):
                raise AgentOperationCommandError(
                    "invalid_command_readback", "command write readback is incomplete"
                )
            receipt = CommandReceipt.from_payload(receipt_payload)
            if (
                receipt.command_id != request.command_id
                or receipt.idempotency_key != request.idempotency_key
                or receipt.binding_digest != request.binding_digest
            ):
                raise AgentOperationCommandError(
                    "invalid_command_readback", "command receipt identity changed"
                )
            readback_version = readback.get("version")
            if (
                not isinstance(readback_version, int)
                or isinstance(readback_version, bool)
                or readback_version < receipt.result_run_version
            ):
                raise AgentOperationCommandError(
                    "invalid_command_readback", "current run version predates the receipt"
                )
            return {
                "command_receipt": receipt.to_payload(),
                "run": dict(readback),
            }
        except AgentOperationCommandError:
            raise
        except CommandContractError as exc:
            raise AgentOperationCommandError(exc.code, exc.detail) from exc
