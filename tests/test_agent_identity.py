import pytest

from src.agent_identity import AgentIdentity, AgentIdentityError, scope_key_for


def test_agent_identity_normalizes_stably():
    identity = AgentIdentity.create(
        agent_id="  Bob Worker  ",
        role_id="Backend Owner",
        project_id="Odysseus Fork",
        memory_scope=" Shared Notes ",
        workspace_scope=" Repo Root ",
        run_id=" Run 001 ",
    )

    assert identity == AgentIdentity(
        agent_id="bob-worker",
        role_id="backend-owner",
        project_id="odysseus-fork",
        memory_scope="shared-notes",
        workspace_scope="repo-root",
        run_id="run-001",
    )
    assert identity.identity_key() == "project:odysseus-fork|agent:bob-worker|role:backend-owner|run:run-001"


def test_agent_identities_with_same_project_get_distinct_scope_keys():
    alice = AgentIdentity.create(
        agent_id="Alice",
        role_id="planner",
        project_id="Odysseus",
        memory_scope="team",
        workspace_scope="repo",
        run_id="run-a",
    )
    bob = AgentIdentity.create(
        agent_id="Bob",
        role_id="backend",
        project_id="Odysseus",
        memory_scope="team",
        workspace_scope="repo",
        run_id="run-b",
    )

    assert alice.memory_scope_key() != bob.memory_scope_key()
    assert alice.workspace_scope_key() != bob.workspace_scope_key()
    assert "agent:alice" in alice.memory_scope_key()
    assert "agent:bob" in bob.memory_scope_key()
    assert "role:planner" in alice.workspace_scope_key()
    assert "role:backend" in bob.workspace_scope_key()


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("agent_id", ""),
        ("role_id", "   "),
        ("project_id", "../escape"),
        ("memory_scope", "team/shared"),
        ("workspace_scope", r"vault\\root"),
        ("run_id", "x" * 81),
    ],
)
def test_invalid_identity_fields_are_rejected(field_name, bad_value):
    kwargs = {
        "agent_id": "bob",
        "role_id": "backend",
        "project_id": "odysseus",
        "memory_scope": "team",
        "workspace_scope": "repo",
        "run_id": "run-1",
    }
    kwargs[field_name] = bad_value

    with pytest.raises(AgentIdentityError):
        AgentIdentity.create(**kwargs)


def test_scope_keys_do_not_preserve_raw_whitespace_or_path_artifacts():
    identity = AgentIdentity.create(
        agent_id="Bob Agent",
        role_id="Memory Reviewer",
        project_id="Unified Odysseus",
        memory_scope="Review Queue",
        workspace_scope="Team Vault Root",
        run_id="Run 7",
    )

    key = scope_key_for(identity, scope_kind="workspace")

    assert key == (
        "project:unified-odysseus|workspace:team-vault-root|agent:bob-agent|role:memory-reviewer"
    )
    assert " " not in key
    assert "/" not in key
    assert "\\" not in key
    assert ".." not in key
