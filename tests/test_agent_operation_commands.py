from __future__ import annotations

import pytest

from src.agent_operation_commands import (
    AgentOperationCommandError,
    AgentOperationCommandService,
)
from src.temporal_runtime.commands import CommandReceipt


OWNER = "owner:alice"
RUN_ID = "arun-" + "1" * 32


class FakeCommandAdapter:
    def __init__(self, *, state="running", version=4, allowed=None):
        self.run = {
            "agent_run_id": RUN_ID,
            "state": state,
            "version": version,
            "allowed_commands": allowed
            if allowed is not None
            else ["pause", "cancel", "retry_activity", "steer_run"],
        }
        self.calls = []
        self.receipts = {}

    async def get_run(self, owner_scope_ref, agent_run_id):
        assert owner_scope_ref == OWNER
        assert agent_run_id == RUN_ID
        return {"run": dict(self.run)}

    async def command_readback(self, owner_scope_ref, agent_run_id, request):
        self.calls.append(request)
        existing = self.receipts.get(request.idempotency_key)
        if existing is None:
            result_version = self.run["version"]
            result_code = "requires_plan_revision"
            if request.command != "steer_run" or set(request.payload) == {"steering_ref"}:
                result_version += 1
                result_code = "applied"
                self.run["version"] = result_version
                if request.command == "pause":
                    self.run["state"] = "paused"
                    self.run["allowed_commands"] = ["resume", "cancel", "steer_run"]
            existing = CommandReceipt.create(
                request,
                result_run_version=result_version,
                result_code=result_code,
                state=self.run["state"],
            ).to_payload()
            self.receipts[request.idempotency_key] = existing
        return {"command_receipt": existing, "run": dict(self.run)}


def _body(**overrides):
    value = {
        "command_id": "command-1",
        "command": "pause",
        "expected_run_version": 4,
        "idempotency_key": "idem-1",
        "payload": {},
    }
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_allowed_command_returns_persisted_receipt_and_current_readback():
    adapter = FakeCommandAdapter()
    service = AgentOperationCommandService(adapter)

    result = await service.execute(OWNER, RUN_ID, _body())

    assert result["command_receipt"]["result_code"] == "applied"
    assert result["command_receipt"]["result_run_version"] == 5
    assert result["run"]["version"] == 5
    assert result["run"]["state"] == "paused"


@pytest.mark.asyncio
async def test_duplicate_old_version_reaches_durable_idempotency_ledger_once():
    adapter = FakeCommandAdapter()
    service = AgentOperationCommandService(adapter)

    first = await service.execute(OWNER, RUN_ID, _body())
    second = await service.execute(OWNER, RUN_ID, _body())

    assert second == first
    assert len(adapter.receipts) == 1
    assert len(adapter.calls) == 2


@pytest.mark.asyncio
async def test_current_projection_is_authoritative_for_new_command():
    adapter = FakeCommandAdapter(state="completed", allowed=[])
    service = AgentOperationCommandService(adapter)

    with pytest.raises(AgentOperationCommandError) as caught:
        await service.execute(OWNER, RUN_ID, _body())

    assert caught.value.code == "command_not_allowed"
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_future_version_is_rejected_before_temporal_write():
    adapter = FakeCommandAdapter()
    service = AgentOperationCommandService(adapter)

    with pytest.raises(AgentOperationCommandError) as caught:
        await service.execute(OWNER, RUN_ID, _body(expected_run_version=5))

    assert caught.value.code == "stale_run_version"
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_structural_steer_requires_plan_revision_without_changing_version():
    adapter = FakeCommandAdapter()
    service = AgentOperationCommandService(adapter)
    body = _body(
        command_id="command-structural",
        command="steer_run",
        idempotency_key="idem-structural",
        payload={"allowed_paths": ["src/new.py"]},
    )

    result = await service.execute(OWNER, RUN_ID, body)

    assert result["command_receipt"]["result_code"] == "requires_plan_revision"
    assert result["run"]["version"] == 4


@pytest.mark.asyncio
async def test_command_fields_and_payload_are_exact():
    service = AgentOperationCommandService(FakeCommandAdapter())
    with pytest.raises(AgentOperationCommandError) as caught:
        await service.execute(OWNER, RUN_ID, {**_body(), "secret": "x"})
    assert caught.value.code == "invalid_command"

    with pytest.raises(AgentOperationCommandError) as caught:
        await service.execute(OWNER, RUN_ID, _body(payload={"reason": "free form"}))
    assert caught.value.code == "invalid_command_payload"
