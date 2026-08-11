from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

import src.unified_source_index_code_policy as policy_module
from src.repo_git_adapter import (
    ForgeSnapshotAuthorityBinding,
    ForgeSnapshotFile,
    ForgeSnapshotInventory,
)
from src.unified_source_index_code_policy import (
    CodePolicyObservationError,
    MAX_CANONICAL_PAYLOAD_BYTES,
    MAX_JSON_NODES,
    MAX_PATH_CHARS,
    MAX_POLICY_DECISIONS,
    MAX_POLICY_FILE_BYTES,
    MAX_TOTAL_STRING_CHARS,
    POLICY_FILE_DECISION_SCHEMA,
    POLICY_OBSERVATION_SCHEMA,
    PolicyDecision,
    PolicyFileDecision,
    PolicyObservation,
)


EVIDENCE = "sha256:" + "e" * 64
POLICY_EVIDENCE = "sha256:" + "f" * 64


def _authority() -> ForgeSnapshotAuthorityBinding:
    return ForgeSnapshotAuthorityBinding(
        "forge.code",
        "v1",
        "usi_generation_" + "9" * 64,
        "fca.forge_code.admission.v1",
    )


def _file(path: str = "src/main.py", digit: str = "1", size: int = 23) -> ForgeSnapshotFile:
    return ForgeSnapshotFile(path, "sha256:" + digit * 64, size)


def _inventory(*files: ForgeSnapshotFile) -> ForgeSnapshotInventory:
    return ForgeSnapshotInventory(
        "user:alice",
        "demo",
        "pv_" + "a" * 32,
        "b" * 40,
        "sha256:" + "c" * 64,
        _authority(),
        tuple(files),
    )


def _decision(
    file: ForgeSnapshotFile,
    decision: PolicyDecision = PolicyDecision.IN_SCOPE,
    evidence_ref: str = EVIDENCE,
) -> PolicyFileDecision:
    return PolicyFileDecision(
        file.path,
        file.content_sha256,
        file.byte_count,
        decision,
        evidence_ref,
    )


def _observation(*files: ForgeSnapshotFile) -> PolicyObservation:
    inventory = _inventory(*files)
    decisions = tuple(_decision(file) for file in inventory.files)
    return PolicyObservation.from_inventory(
        inventory,
        "code.index.policy.v1",
        POLICY_EVIDENCE,
        decisions,
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_policy_observation_is_closed_content_addressed_and_roundtrips() -> None:
    first = _file("src/z.py", "1", 10)
    second = _file("README.md", "2", 20)
    inventory = _inventory(first, second)
    observation = PolicyObservation.from_inventory(
        inventory,
        "code.index.policy.v1",
        POLICY_EVIDENCE,
        (
            _decision(first, PolicyDecision.POLICY_OUT_OF_SCOPE),
            _decision(second),
        ),
    )

    assert tuple(item.path for item in observation.decisions) == (
        "README.md",
        "src/z.py",
    )
    assert {item.decision for item in observation.decisions} == set(PolicyDecision)
    assert observation.schema == POLICY_OBSERVATION_SCHEMA
    assert all(item.schema == POLICY_FILE_DECISION_SCHEMA for item in observation.decisions)
    assert observation.observation_digest.startswith("sha256:")
    assert PolicyObservation.from_canonical_bytes(
        observation.to_canonical_bytes()
    ) == observation
    assert observation.to_dict() == json.loads(observation.to_canonical_bytes())
    rendered = json.dumps(observation.to_dict(), sort_keys=True)
    assert "content" not in rendered.replace("content_sha256", "")
    assert "select" not in dir(observation)
    assert "exclude" not in dir(observation)
    assert "index" not in dir(observation)
    assert "activate" not in dir(observation)


def test_policy_observation_digest_binds_every_semantic_field() -> None:
    file = _file()
    first = _observation(file)
    changed = PolicyObservation.from_inventory(
        _inventory(file),
        "code.index.policy.v1",
        POLICY_EVIDENCE,
        (_decision(file, PolicyDecision.POLICY_OUT_OF_SCOPE),),
    )

    assert first.observation_digest != changed.observation_digest
    tampered = json.loads(first.to_canonical_bytes())
    tampered["observation_digest"] = "sha256:" + "0" * 64
    with pytest.raises(CodePolicyObservationError, match="^invalid_payload$"):
        PolicyObservation.from_canonical_bytes(_canonical(tampered))


def test_policy_file_decision_signature_is_exact_and_schema_is_fixed() -> None:
    assert tuple(inspect.signature(PolicyFileDecision).parameters) == (
        "path",
        "content_sha256",
        "byte_count",
        "decision",
        "evidence_ref",
    )
    decision = _decision(_file())
    assert decision.schema == POLICY_FILE_DECISION_SCHEMA
    with pytest.raises(TypeError):
        PolicyFileDecision(
            decision.path,
            decision.content_sha256,
            decision.byte_count,
            decision.decision,
            decision.evidence_ref,
            schema="other",
        )


def test_policy_observation_returned_decisions_are_fresh_detached_projections() -> None:
    observation = _observation(_file())
    first = observation.decisions
    second = observation.decisions

    assert first is not second
    assert first[0] is not second[0]
    assert first[0] == second[0]
    object.__setattr__(first[0], "path", "src/mutated.py")

    assert observation.decisions[0].path == "src/main.py"


def test_policy_observation_nested_projection_mutation_cannot_split_bytes_and_digest() -> None:
    observation = _observation(_file())
    canonical = observation.to_canonical_bytes()
    digest = observation.observation_digest
    returned = observation.decisions[0]

    object.__setattr__(returned, "decision", PolicyDecision.POLICY_OUT_OF_SCOPE)
    object.__setattr__(returned, "evidence_ref", "sha256:" + "0" * 64)

    assert observation.to_canonical_bytes() == canonical
    assert observation.observation_digest == digest
    assert observation.decisions[0].decision is PolicyDecision.IN_SCOPE


def test_policy_observation_private_snapshot_corruption_fails_closed() -> None:
    observation = _observation(_file())
    object.__setattr__(observation, "_snapshot", ())

    for projection in (
        lambda: observation.to_dict(),
        lambda: observation.to_canonical_bytes(),
        lambda: observation.schema,
        lambda: observation.owner_scope,
        lambda: observation.repo_id,
        lambda: observation.version_id,
        lambda: observation.commit_sha,
        lambda: observation.manifest_sha256,
        lambda: observation.snapshot_digest,
        lambda: observation.authority_binding,
        lambda: observation.indexing_policy_generation,
        lambda: observation.policy_evidence_ref,
        lambda: observation.decisions,
        lambda: observation.observation_digest,
        lambda: hash(observation),
    ):
        with pytest.raises(CodePolicyObservationError, match="^invalid_observation$") as caught:
            projection()
        assert caught.value.args == ("invalid_observation",)
        assert caught.value.__cause__ is None


def test_policy_observation_roundtrip_digest_binds_every_private_semantic_field() -> None:
    observation = _observation(_file())
    payload = json.loads(observation.to_canonical_bytes())
    mutations = (
        (payload, "owner_scope", "user:bob"),
        (payload, "repo_id", "other"),
        (payload, "version_id", "pv_" + "b" * 32),
        (payload, "commit_sha", "d" * 40),
        (payload, "manifest_sha256", "sha256:" + "d" * 64),
        (payload, "snapshot_digest", "sha256:" + "d" * 64),
        (payload["authority_binding"], "adapter_id", "other.adapter"),
        (payload, "indexing_policy_generation", "code.index.policy.v2"),
        (payload, "policy_evidence_ref", "sha256:" + "d" * 64),
        (payload["decisions"][0], "path", "src/other.py"),
        (payload["decisions"][0], "content_sha256", "sha256:" + "d" * 64),
        (payload["decisions"][0], "byte_count", 24),
        (payload["decisions"][0], "decision", "policy_out_of_scope"),
        (payload["decisions"][0], "evidence_ref", "sha256:" + "d" * 64),
    )
    for target, key, replacement in mutations:
        changed = json.loads(json.dumps(payload))
        if target is payload:
            changed[key] = replacement
        elif target is payload["authority_binding"]:
            changed["authority_binding"][key] = replacement
        else:
            changed["decisions"][0][key] = replacement
        with pytest.raises(CodePolicyObservationError, match="^invalid_payload$"):
            PolicyObservation.from_canonical_bytes(_canonical(changed))


def test_policy_observation_detaches_inventory_authority_files_and_decisions() -> None:
    file = _file()
    inventory = _inventory(file)
    decision = _decision(file)
    observation = PolicyObservation.from_inventory(
        inventory,
        "code.index.policy.v1",
        POLICY_EVIDENCE,
        (decision,),
    )
    canonical = observation.to_canonical_bytes()
    digest = observation.observation_digest

    assert observation.authority_binding is not inventory.authority_binding
    assert observation.decisions[0] is not decision
    object.__setattr__(inventory.authority_binding, "adapter_id", "mutated")
    object.__setattr__(file, "path", "src/mutated.py")
    object.__setattr__(decision, "path", "src/decision-mutated.py")

    assert observation.to_canonical_bytes() == canonical
    assert observation.observation_digest == digest
    assert observation.decisions[0].path == "src/main.py"


@pytest.mark.parametrize(
    "mutation",
    ("missing", "extra", "duplicate", "path", "digest", "byte_count", "case_alias"),
)
def test_policy_observation_requires_complete_canonical_inventory_bijection(mutation: str) -> None:
    first = _file("src/a.py", "1", 10)
    second = _file("src/b.py", "2", 20)
    inventory = _inventory(first, second)
    decisions = [_decision(first), _decision(second)]
    if mutation == "missing":
        decisions.pop()
    elif mutation == "extra":
        decisions.append(_decision(_file("src/c.py", "3", 30)))
    elif mutation == "duplicate":
        decisions[1] = decisions[0]
    elif mutation == "path":
        object.__setattr__(decisions[0], "path", "src/other.py")
    elif mutation == "digest":
        object.__setattr__(decisions[0], "content_sha256", "sha256:" + "4" * 64)
    elif mutation == "byte_count":
        object.__setattr__(decisions[0], "byte_count", 11)
    elif mutation == "case_alias":
        object.__setattr__(decisions[1], "path", "SRC/A.PY")

    with pytest.raises(CodePolicyObservationError, match="^invalid_observation$"):
        PolicyObservation.from_inventory(
            inventory,
            "code.index.policy.v1",
            POLICY_EVIDENCE,
            tuple(decisions),
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"decision": "policy_out_of_scope"},
        {"decision": True},
        {"evidence_ref": "manual-exclusion"},
        {"byte_count": True},
        {"byte_count": MAX_POLICY_FILE_BYTES + 1},
        {"path": "src/../secret.py"},
        {"path": "src/cafe\u0301.py"},
        {"path": "src/CON.py"},
        {"path": "src/\ud800.py"},
    ),
)
def test_policy_file_decision_rejects_arbitrary_authority_aliases_and_budgets(changes) -> None:
    file = _file()
    values = {
        "path": file.path,
        "content_sha256": file.content_sha256,
        "byte_count": file.byte_count,
        "decision": PolicyDecision.IN_SCOPE,
        "evidence_ref": EVIDENCE,
    }
    values.update(changes)

    with pytest.raises(CodePolicyObservationError, match="^invalid_decision$") as caught:
        PolicyFileDecision(**values)
    assert caught.value.args == ("invalid_decision",)
    assert caught.value.__cause__ is None


def test_policy_observation_accepts_empty_and_exact_512_file_boundary() -> None:
    empty = _observation()
    assert empty.decisions == ()

    files = tuple(
        _file(f"src/file-{index:03d}.py", f"{index % 10}", index)
        for index in range(MAX_POLICY_DECISIONS)
    )
    inventory = _inventory(*files)
    observation = PolicyObservation.from_inventory(
        inventory,
        "code.index.policy.v1",
        POLICY_EVIDENCE,
        tuple(_decision(file) for file in files),
    )
    assert len(observation.decisions) == MAX_POLICY_DECISIONS


def test_policy_observation_rejects_513_files_and_decoder_reports_budget() -> None:
    observation = _observation(_file())
    payload = json.loads(observation.to_canonical_bytes())
    payload["decisions"] = payload["decisions"] * (MAX_POLICY_DECISIONS + 1)

    with pytest.raises(CodePolicyObservationError, match="^budget_exceeded$"):
        PolicyObservation.from_canonical_bytes(_canonical(payload))


@pytest.mark.parametrize("kind", ("duplicate", "unknown", "missing", "pretty", "trailing", "bool", "nan"))
def test_canonical_decoder_rejects_noncanonical_or_malformed_payloads(kind: str) -> None:
    observation = _observation(_file())
    value = observation.to_dict()
    if kind == "duplicate":
        payload = b'{"schema":"a","schema":"b"}'
    elif kind == "unknown":
        value["unknown"] = True
        payload = _canonical(value)
    elif kind == "missing":
        value.pop("repo_id")
        payload = _canonical(value)
    elif kind == "pretty":
        payload = json.dumps(value, indent=2).encode("utf-8")
    elif kind == "trailing":
        payload = observation.to_canonical_bytes() + b"\n"
    elif kind == "bool":
        value["decisions"][0]["byte_count"] = True
        payload = _canonical(value)
    else:
        payload = observation.to_canonical_bytes().replace(b'"byte_count":23', b'"byte_count":NaN')

    with pytest.raises(CodePolicyObservationError, match="^invalid_payload$") as caught:
        PolicyObservation.from_canonical_bytes(payload)
    assert caught.value.args == ("invalid_payload",)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "payload",
    (
        b"x" * (MAX_CANONICAL_PAYLOAD_BYTES + 1),
        _canonical({"nested": [[[[[[[0]]]]]]]}),
        _canonical({"items": [0] * (MAX_JSON_NODES + 1)}),
        _canonical({"text": "x" * (MAX_TOTAL_STRING_CHARS + 1)}),
    ),
    ids=("payload_bytes", "depth", "nodes", "string_chars"),
)
def test_canonical_decoder_reports_exact_numeric_budgets(payload: bytes) -> None:
    with pytest.raises(CodePolicyObservationError, match="^budget_exceeded$"):
        PolicyObservation.from_canonical_bytes(payload)


def test_hostile_types_callbacks_and_mutable_errors_are_content_free(monkeypatch) -> None:
    private = "private-policy-marker"
    file = _file()
    inventory = _inventory(file)
    decision = _decision(file)

    class InventorySubclass(ForgeSnapshotInventory):
        pass

    hostile_inventory = InventorySubclass(
        inventory.owner_scope,
        inventory.repo_id,
        inventory.version_id,
        inventory.commit_sha,
        inventory.manifest_sha256,
        inventory.authority_binding,
        inventory.files,
        inventory.snapshot_digest,
    )
    with pytest.raises(CodePolicyObservationError, match="^invalid_observation$"):
        PolicyObservation.from_inventory(
            hostile_inventory,
            "code.index.policy.v1",
            POLICY_EVIDENCE,
            (decision,),
        )

    dispatches = []

    class HostileStr(str):
        def __eq__(self, other):
            dispatches.append("eq")
            raise AssertionError(private)

        def __hash__(self):
            dispatches.append("hash")
            raise AssertionError(private)

        def encode(self, *args, **kwargs):
            dispatches.append("encode")
            raise AssertionError(private)

    object.__setattr__(decision, "path", HostileStr(decision.path))
    with pytest.raises(CodePolicyObservationError, match="^invalid_observation$"):
        PolicyObservation.from_inventory(
            inventory,
            "code.index.policy.v1",
            POLICY_EVIDENCE,
            (decision,),
        )
    assert dispatches == []

    sentinel = CodePolicyObservationError("invalid_decision")
    sentinel.code = private
    sentinel.args = (private,)
    sentinel.__cause__ = RuntimeError(private)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            PolicyFileDecision,
            "to_dict",
            lambda _value: (_ for _ in ()).throw(sentinel),
        )
        with pytest.raises(CodePolicyObservationError) as caught:
            PolicyObservation.from_inventory(
                _inventory(file),
                "code.index.policy.v1",
                POLICY_EVIDENCE,
                (_decision(file),),
            )
    assert caught.value is not sentinel
    assert caught.value.code == "invalid_observation"
    assert caught.value.args == ("invalid_observation",)
    assert caught.value.__cause__ is None
    assert private not in str(caught.value)
    assert private not in repr(caught.value)


def test_projection_is_fresh_and_post_validation_mutation_cannot_change_it() -> None:
    observation = _observation(_file())
    first = observation.to_dict()
    first["authority_binding"]["adapter_id"] = "mutated"
    first["decisions"][0]["path"] = "src/mutated.py"

    second = observation.to_dict()
    assert second["authority_binding"]["adapter_id"] == "forge.code"
    assert second["decisions"][0]["path"] == "src/main.py"


def test_code_policy_module_has_zero_io_git_provider_runtime_or_activation_effects() -> None:
    source_path = Path(policy_module.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = set()
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)

    forbidden_imports = {
        "os",
        "pathlib",
        "socket",
        "subprocess",
        "requests",
        "httpx",
        "src.unified_source_index_stores",
        "src.unified_source_index_sources.forge_code",
    }
    assert not imports & forbidden_imports
    assert not calls & {"open", "read_text", "read_bytes", "run", "Popen", "index", "activate"}
    assert "ForgeSnapshotReader" not in source
    assert "RepoGitAdapter" not in source
    assert ".file(" not in source


def test_public_errors_and_reprs_are_bounded_and_content_free() -> None:
    decision = _decision(_file())
    observation = _observation(_file())
    assert "src/main.py" not in repr(decision)
    assert "user:alice" not in repr(observation)
    assert len(repr(observation)) < 128

    class HostileCode(str):
        def __hash__(self):
            raise AssertionError("private")

        def __eq__(self, other):
            raise AssertionError("private")

    error = CodePolicyObservationError(HostileCode("invalid_payload"))
    assert error.code == "invalid_observation"
    assert error.args == ("invalid_observation",)


def test_public_repr_boundaries_ignore_hostile_post_validation_mutation() -> None:
    marker = "private-repr-property-marker"
    callbacks = []

    class HostileValue:
        @property
        def value(self):
            callbacks.append("property")
            raise AssertionError(marker)

        def __repr__(self) -> str:
            callbacks.append("repr")
            raise AssertionError(marker)

        def __str__(self) -> str:
            callbacks.append("str")
            raise AssertionError(marker)

    hostile = HostileValue()
    decision = _decision(_file())
    observation = _observation(_file())
    error = CodePolicyObservationError("invalid_payload")
    object.__setattr__(decision, "decision", hostile)
    object.__setattr__(observation, "_observation_digest", hostile)
    error.code = hostile
    error.args = (hostile,)

    rendered = (repr(decision), repr(observation), str(error), repr(error))
    assert rendered == (
        "PolicyFileDecision(invalid)",
        "PolicyObservation(invalid)",
        "invalid_observation",
        "CodePolicyObservationError(code='invalid_observation')",
    )
    assert callbacks == []
    assert all(marker not in item and len(item) < 128 for item in rendered)
