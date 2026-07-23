import pytest

from src.unified_source_index_runtime_contract import (
    DomainScope,
    FallbackReason,
    ProviderCapability,
    ProviderHealth,
    ProviderKind,
    RuntimeGeneration,
    RuntimeHealth,
    RuntimeHealthState,
    RuntimeMode,
    RuntimeStateContractError,
    RuntimeStateRecord,
    SelectedScope,
    WorkerPolicy,
)


GENERATION = "usi_generation_" + "a" * 64
PREVIOUS_GENERATION = "usi_generation_" + "b" * 64
SCOPE = "usi_scope_" + "c" * 64


def _generation(*, previous=""):
    return RuntimeGeneration(GENERATION, previous)


def _scope(*, eligible=True, domains=(DomainScope.DOCUMENT,)):
    return SelectedScope(SCOPE, domains, 2, eligible)


def _ready_health(*, providers=(ProviderCapability(ProviderKind.LEXICAL, ProviderHealth.READY),)):
    return RuntimeHealth(RuntimeHealthState.READY, providers)


def _record(mode, **changes):
    values = {
        "mode": mode,
        "generation": _generation(),
        "selected_scopes": (_scope(),),
        "health": _ready_health(),
        "worker_policy": WorkerPolicy.STOPPED,
        "legacy_authoritative": False,
        "prompt_injection": True,
        "fallback_enabled": True,
    }
    if mode is RuntimeMode.DISABLED:
        values.update(
            generation=None,
            selected_scopes=(),
            health=RuntimeHealth(RuntimeHealthState.DISABLED, ()),
            legacy_authoritative=True,
            prompt_injection=False,
        )
    elif mode is RuntimeMode.READ_ONLY:
        values.update(
            selected_scopes=(),
            legacy_authoritative=True,
            prompt_injection=False,
        )
    elif mode is RuntimeMode.SHADOW:
        values.update(legacy_authoritative=True, prompt_injection=False)
    elif mode is RuntimeMode.DEGRADED:
        values.update(
            health=RuntimeHealth(
                RuntimeHealthState.DEGRADED,
                (),
                (FallbackReason.CORE_UNAVAILABLE,),
            ),
            legacy_authoritative=True,
            prompt_injection=False,
        )
    elif mode is RuntimeMode.ROLLBACK:
        values.update(
            generation=_generation(previous=PREVIOUS_GENERATION),
            health=RuntimeHealth(
                RuntimeHealthState.READY,
                (ProviderCapability(ProviderKind.LEXICAL, ProviderHealth.READY),),
                (FallbackReason.ROLLBACK_ACTIVE,),
            ),
        )
    values.update(changes)
    return RuntimeStateRecord(**values)


def test_all_modes_are_immutable_deterministic_and_never_authorize_live_activation():
    records = tuple(_record(mode) for mode in RuntimeMode)

    assert tuple(item.mode for item in records) == tuple(RuntimeMode)
    for record in records:
        payload = record.to_dict()
        assert payload["live_activation_authorized"] is False
        restored = RuntimeStateRecord.from_dict(payload)
        assert restored == record
        assert hash(restored) == hash(record)


def test_selected_scopes_capabilities_and_reasons_are_canonicalized_content_free():
    second_scope = "usi_scope_" + "d" * 64
    record = _record(
        RuntimeMode.ACTIVE,
        selected_scopes=(
            SelectedScope(second_scope, (DomainScope.MEMORY, DomainScope.CODE), 3, True),
            _scope(domains=(DomainScope.DOCUMENT, DomainScope.CODE)),
        ),
        health=RuntimeHealth(
            RuntimeHealthState.READY,
            (
                ProviderCapability(ProviderKind.SEMANTIC, ProviderHealth.UNAVAILABLE),
                ProviderCapability(ProviderKind.LEXICAL, ProviderHealth.READY),
            ),
            (FallbackReason.OPTIONAL_PROVIDER_UNAVAILABLE,),
        ),
    )

    payload = record.to_dict()
    assert [item["scope_ref"] for item in payload["selected_scopes"]] == [SCOPE, second_scope]
    assert [item["provider"] for item in payload["health"]["providers"]] == ["lexical", "semantic"]
    assert "owner" not in repr(payload).lower()
    assert "query" not in repr(payload).lower()
    assert "content" not in repr(payload).lower()


@pytest.mark.parametrize(
    "record",
    (
        lambda: _record(RuntimeMode.DISABLED, generation=_generation()),
        lambda: _record(RuntimeMode.READ_ONLY, generation=None),
        lambda: _record(RuntimeMode.SHADOW, prompt_injection=True),
        lambda: _record(RuntimeMode.SHADOW, selected_scopes=(_scope(eligible=False),)),
        lambda: _record(
            RuntimeMode.SHADOW,
            health=RuntimeHealth(
                RuntimeHealthState.DEGRADED,
                (ProviderCapability(ProviderKind.LEXICAL, ProviderHealth.READY),),
                (FallbackReason.CORE_UNAVAILABLE,),
            ),
        ),
        lambda: _record(
            RuntimeMode.SHADOW,
            health=RuntimeHealth(RuntimeHealthState.READY, ()),
        ),
        lambda: _record(RuntimeMode.CANARY, selected_scopes=(_scope(eligible=False),)),
        lambda: _record(RuntimeMode.ACTIVE, health=RuntimeHealth(RuntimeHealthState.READY, ())),
        lambda: _record(RuntimeMode.ACTIVE, fallback_enabled=False),
        lambda: _record(RuntimeMode.DEGRADED, health=RuntimeHealth(RuntimeHealthState.DEGRADED, ())),
        lambda: _record(RuntimeMode.ROLLBACK, generation=_generation()),
        lambda: _record(RuntimeMode.ROLLBACK, worker_policy=WorkerPolicy.RUNNING),
    ),
)
def test_unsafe_mode_combinations_fail_closed(record):
    with pytest.raises(RuntimeStateContractError):
        record()


def test_paths_secrets_raw_query_content_and_unknown_fields_are_rejected():
    for invalid in (
        "C:\\private\\source.sqlite3",
        "/home/user/secret",
        "../escape",
        "https://token@example.invalid",
        "api_key=secret",
        "raw query text",
    ):
        with pytest.raises(RuntimeStateContractError):
            RuntimeGeneration(invalid)

    payload = _record(RuntimeMode.ACTIVE).to_dict()
    payload["query_text"] = "secret question"
    with pytest.raises(RuntimeStateContractError):
        RuntimeStateRecord.from_dict(payload)


def test_duplicate_or_unbounded_scopes_and_providers_are_rejected():
    with pytest.raises(RuntimeStateContractError):
        _record(RuntimeMode.ACTIVE, selected_scopes=(_scope(), _scope()))
    with pytest.raises(RuntimeStateContractError):
        _record(
            RuntimeMode.ACTIVE,
            health=RuntimeHealth(
                RuntimeHealthState.READY,
                (
                    ProviderCapability(ProviderKind.LEXICAL, ProviderHealth.READY),
                    ProviderCapability(ProviderKind.LEXICAL, ProviderHealth.READY),
                ),
            ),
        )


def test_health_reasons_and_capabilities_must_be_compatible():
    lexical = ProviderCapability(ProviderKind.LEXICAL, ProviderHealth.READY)
    semantic_unavailable = ProviderCapability(ProviderKind.SEMANTIC, ProviderHealth.UNAVAILABLE)

    with pytest.raises(RuntimeStateContractError):
        RuntimeHealth(RuntimeHealthState.DISABLED, (lexical,))
    with pytest.raises(RuntimeStateContractError):
        RuntimeHealth(RuntimeHealthState.READY, (lexical,), (FallbackReason.CORE_UNAVAILABLE,))
    with pytest.raises(RuntimeStateContractError):
        RuntimeHealth(RuntimeHealthState.READY, (lexical,), (FallbackReason.STALE_GENERATION,))
    with pytest.raises(RuntimeStateContractError):
        RuntimeHealth(
            RuntimeHealthState.DEGRADED,
            (),
            (FallbackReason.CORE_UNAVAILABLE, FallbackReason.RUNTIME_DISABLED),
        )
    with pytest.raises(RuntimeStateContractError):
        RuntimeHealth(RuntimeHealthState.READY, (lexical, semantic_unavailable))
    with pytest.raises(RuntimeStateContractError):
        RuntimeHealth(RuntimeHealthState.READY, (lexical,), (FallbackReason.OPTIONAL_PROVIDER_UNAVAILABLE,))
    with pytest.raises(RuntimeStateContractError):
        _record(RuntimeMode.ROLLBACK, health=_ready_health())
