"""Staged native proof for the retained global 64-entry consent cap."""
from __future__ import annotations

from types import SimpleNamespace

import bcrypt
import pytest

from core.database import ApiToken, Session as DbSession
from src.context_manifest_producer import CombinedContextManifestProducer
from src.mcp_authenticated_dispatch import McpAuthenticatedIdentity
from src.mcp_context_authority import (
    McpContextAuthority,
    McpContextAuthorityError,
    issue_mcp_bearer_proof,
)
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationError,
    McpPrivateContextConfirmationService,
)
from src.planning_context_reader import PlanningContextManifestProducer
from src.planning_durable_context_source import (
    DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
    DurablePlanningContextSource,
)
from src.private_context_confirmation_app import (
    ContextSelection,
    CookieConfirmationAuthority,
    ForgeSelectionLocator,
    SourceSelection,
)
from src.repo_registry import RepoRecord, RepoRegistry
from tests.test_context_manifest_producer import (
    _commit_sha,
    _real_three_domain_context_body,
    _run_git,
)
from tests.test_mcp_private_context_confirmation import (
    _entry_snapshot,
    _entry_unchanged,
)
from tests.test_planning_durable_context_source import _append_clean_descendants


@pytest.fixture
def native_global_capacity_fixture(tmp_path, monkeypatch):
    """Build nine genuine owner contexts before installing native authority."""
    body = _real_three_domain_context_body(tmp_path, monkeypatch)
    fixture = next(body)
    authority = None
    try:
        auth = fixture["auth"]
        forge = fixture["forge"]
        local_forge = fixture["local_forge"]
        workspace = forge._roots[0]
        registry = RepoRegistry.load_or_empty(forge._registry_path)
        owners = []

        for owner_number in range(9):
            owner = f"consent-global-owner-{owner_number}"
            password = "Password-123"
            assert auth.create_user(owner, password, is_admin=True)
            cookie = auth.create_session(owner, password)
            assert cookie
            owner_sid = auth.subject_id_for_username(owner)
            assert owner_sid

            repo_id = f"consent-global-repo-{owner_number}"
            source = workspace / "repos" / repo_id
            source.mkdir(parents=True)
            _run_git(source, "init", "-b", "main")
            _run_git(source, "config", "user.email", "consent-capacity@example.invalid")
            _run_git(source, "config", "user.name", "Consent Capacity Fixture")
            (source / "README.md").write_bytes(
                f"Retained global capacity owner {owner_number}\n".encode("utf-8")
            )
            _run_git(source, "add", "README.md")
            _run_git(source, "commit", "-m", "retained global capacity fixture")
            commit = _commit_sha(source, tmp_path / f"global-capacity-{owner_number}.sha")
            stored = local_forge.store_existing_commit(
                owner_id=owner_sid,
                repo_id=repo_id,
                source_repo=source,
                commit_sha=commit,
                idempotency_key=f"global-capacity-retain-{owner_number}",
                policy_snapshot={"forge_context": "public_registered"},
                version_label="Global capacity fixture",
                artifacts=(),
            )
            registry.add(RepoRecord.create(
                repo_id=repo_id,
                title=f"Global Capacity {owner_number}",
                repo_kind="project",
                owner=owner_sid,
                path_ref=f"workspace/repos/{repo_id}",
                workspace_root="workspace",
                project_root=f"workspace/repos/{repo_id}",
                default_branch="main",
                current_branch="main",
                privacy_class="public",
                provider_scope="default",
                remotes=(),
                created_at="2026-10-07T00:00:00Z",
            ))
            owners.append({
                "owner": owner,
                "owner_sid": owner_sid,
                "cookie": cookie,
                "repo_id": repo_id,
                "version_id": stored.version_id,
            })
        registry.save_json(forge._registry_path)

        with fixture["session_factory"]() as db:
            for owner_number, owner_spec in enumerate(owners):
                records = []
                token_count = 8 if owner_number < 8 else 1
                for token_number in range(token_count):
                    raw = "ody_" + f"global{owner_number}{token_number}".ljust(43, "x")
                    token_id = f"consent-global-token-{owner_number}-{token_number}"
                    chat_id = f"consent-global-chat-{owner_number}-{token_number}"
                    db.add(ApiToken(
                        id=token_id,
                        owner=owner_spec["owner"],
                        name="global consent capacity",
                        token_hash=bcrypt.hashpw(
                            raw.encode("ascii"), bcrypt.gensalt(rounds=4),
                        ).decode("ascii"),
                        token_prefix=raw[:8],
                        scopes="mcp:harbour:context:read",
                        owner_subject_id=owner_spec["owner_sid"],
                        is_active=True,
                    ))
                    db.add(DbSession(
                        id=chat_id,
                        name="Global Consent Capacity",
                        endpoint_url="local",
                        model="test",
                        owner=owner_spec["owner"],
                        message_count=0,
                    ))
                    records.append({
                        "token_id": token_id,
                        "chat_id": chat_id,
                        "raw": raw,
                    })
                owner_spec["records"] = tuple(records)
            db.commit()

        for owner_spec in owners:
            with CookieConfirmationAuthority(auth).hold(
                owner_spec["cookie"], owner_spec["records"][0]["chat_id"],
            ) as binding:
                manifest = forge.capture_version_metadata(
                    binding,
                    owner_spec["repo_id"],
                    owner_spec["version_id"],
                    ("README.md",),
                )
            owner_spec["selection"] = ContextSelection((SourceSelection(
                "forge",
                manifest.descriptor.source_id,
                manifest.descriptor.source_version_id,
                ForgeSelectionLocator(
                    owner_spec["repo_id"], owner_spec["version_id"], "README.md",
                ),
            ),))

        runtime = fixture["planning_producer"]._runtime
        fixture_owner_sid = auth.subject_id_for_username("fuzzy")
        principal = runtime.issuer.issue(
            subject_id=fixture_owner_sid,
            owner_id="fuzzy",
            auth_context_digest="sha256:" + "5" * 64,
            ttl_seconds=3600,
            nonce="native-global-capacity-principal",
        )
        with CookieConfirmationAuthority(auth).hold(
            fixture["cookie"], fixture["chat_id"],
        ) as setup_binding:
            _append_clean_descendants(
                {"store": runtime.durable_store, "principal": principal}, setup_binding,
            )
            durable_source = DurablePlanningContextSource(
                runtime=runtime,
                auth_manager=auth,
                project_id="harbour-one",
                policy=DEFAULT_DURABLE_PLANNING_CONTEXT_POLICY,
            )
            durable_source.discover(setup_binding)
        native_planning = PlanningContextManifestProducer(
            runtime,
            auth,
            None,
            "harbour-one",
            durable_source=durable_source,
        )

        security_revision = auth.security_action_revision()
        state = SimpleNamespace(
            _token_cache_generation=4,
            _token_cache_dirty=False,
            mcp_context_plugin_enabled=True,
            allow_context_reads=True,
            _process_epoch="epoch_" + "c" * 32,
        )
        authority = McpContextAuthority(
            auth, fixture["session_factory"], app_state=state,
        )
        combined = CombinedContextManifestProducer(
            auth_manager=auth,
            forge=forge,
            planning=native_planning,
            knowledge=fixture["knowledge_producer"],
            mcp_authority=authority,
        )
        assert auth.security_action_revision() == security_revision
        fixture.update({
            "authority": authority,
            "combined": combined,
            "durable_source": durable_source,
            "global_capacity_owners": tuple(owners),
            "mcp_state": state,
            "planning_producer": native_planning,
            "principal": principal,
            "security_revision": security_revision,
        })
        yield fixture
    finally:
        if authority is not None:
            authority.close()
        try:
            next(body)
        except StopIteration:
            pass


def _real_clock_service(fixture):
    return McpPrivateContextConfirmationService(
        auth_manager=fixture["auth"],
        authority=fixture["authority"],
        producer=fixture["combined"],
        cookie_authority=CookieConfirmationAuthority(fixture["auth"]),
        session_factory=fixture["session_factory"],
    )


def _issue_native_handle(fixture, owner_spec, record, request_id):
    auth = fixture["auth"]
    assert auth.security_action_revision() == fixture["security_revision"]
    identity = McpAuthenticatedIdentity(
        record["token_id"],
        owner_spec["owner"],
        owner_spec["owner_sid"],
        fixture["mcp_state"]._token_cache_generation,
        fixture["security_revision"],
        "mcp:harbour:context:read",
    )
    authority = fixture["authority"]
    binding = authority.issue_binding(
        identity,
        issue_mcp_bearer_proof(record["raw"]),
        record["chat_id"],
    )
    lease = authority.issue_read_lease(binding, request_id)
    return binding, lease


def test_native_service_retained_global_registry_rejects_distinct_sixty_fifth_owner_lease(
    native_global_capacity_fixture,
):
    fixture = native_global_capacity_fixture
    authority = fixture["authority"]
    owner_specs = fixture["global_capacity_owners"]
    service = _real_clock_service(fixture)
    handles = []
    incoming_binding = incoming_lease = None
    try:
        for owner_number, owner_spec in enumerate(owner_specs[:8]):
            assert len(owner_spec["records"]) == 8
            for token_number, record in enumerate(owner_spec["records"]):
                binding, lease = _issue_native_handle(
                    fixture,
                    owner_spec,
                    record,
                    f"global-capacity-{owner_number}-{token_number}",
                )
                handles.append((
                    binding, lease, owner_spec["owner_sid"], owner_spec["selection"],
                ))

        assert len(handles) == 64
        assert len({id(binding) for binding, *_rest in handles}) == 64
        assert len({id(lease) for _binding, lease, *_rest in handles}) == 64
        assert all(
            sum(1 for _binding, _lease, sid, _selection in handles if sid == owner_spec["owner_sid"]) == 8
            for owner_spec in owner_specs[:8]
        )
        assert all(
            authority.recheck_lease(lease) is binding
            for binding, lease, _owner_sid, _selection in handles
        )
        assert len(authority._bindings) == len(authority._leases) == 64

        prepared = tuple(
            service.prepare_intent(lease, selection)
            for _binding, lease, _owner_sid, selection in handles
        )
        request_ids = tuple(request.request_id for request in prepared)
        assert len(request_ids) == len(set(request_ids)) == 64
        assert tuple(service._entries) == request_ids
        entries = tuple(service._entries[request_id] for request_id in request_ids)
        assert len(entries) == 64
        assert all(
            entry.state == "intent"
            and entry.intent is True
            and entry.owner_sid == owner_sid
            and entry.binding_ref() is binding
            and entry.lease_ref() is lease
            and entry.selection is selection
            for entry, (binding, lease, owner_sid, selection) in zip(entries, handles)
        )
        assert all(
            entry.challenge is None
            and entry.receipt is None
            and entry.pending_ack_operation is None
            and entry.permit_ref is None
            for entry in entries
        )
        assert not service._permits and not service._exchanges and not service._completions
        before = tuple(_entry_snapshot(entry) for entry in entries)

        first_binding, first_lease, _first_sid, _first_selection = handles[0]
        authority.release_binding(first_binding)
        assert len(authority._bindings) == len(authority._leases) == 63
        with pytest.raises(McpContextAuthorityError) as revoked:
            authority.recheck_lease(first_lease)
        assert revoked.value.code == "unauthorized"
        assert all(
            authority.recheck_lease(lease) is binding
            for binding, lease, _owner_sid, _selection in handles[1:]
        )

        incoming_owner = owner_specs[8]
        assert len(incoming_owner["records"]) == 1
        incoming_binding, incoming_lease = _issue_native_handle(
            fixture,
            incoming_owner,
            incoming_owner["records"][0],
            "global-capacity-incoming-65",
        )
        assert sum(
            1 for entry in service._entries.values()
            if entry.owner_sid == incoming_owner["owner_sid"]
        ) == 0
        assert all(entry.lease_ref() is not incoming_lease for entry in entries)
        assert all(incoming_binding is not binding for binding, *_rest in handles)
        assert authority.recheck_lease(incoming_lease) is incoming_binding
        assert len(authority._bindings) == len(authority._leases) == 64

        with pytest.raises(McpPrivateContextConfirmationError) as capacity:
            service.prepare_intent(incoming_lease, incoming_owner["selection"])
        assert capacity.value.code == "capacity"

        assert len(service._entries) == 64
        assert tuple(service._entries) == request_ids
        assert all(
            service._entries.get(request_id) is entry
            and _entry_unchanged(entry, snapshot)
            for request_id, entry, snapshot in zip(request_ids, entries, before)
        )
        assert all(
            entry.state == "intent"
            and entry.intent is True
            and entry.binding_ref() is binding
            and entry.lease_ref() is lease
            for entry, (binding, lease, _owner_sid, _selection) in zip(entries, handles)
        )
        assert not service._permits and not service._exchanges and not service._completions
        assert all(
            authority.recheck_lease(lease) is binding
            for binding, lease, _owner_sid, _selection in handles[1:]
        )
        assert authority.recheck_lease(incoming_lease) is incoming_binding
        with pytest.raises(McpContextAuthorityError) as still_revoked:
            authority.recheck_lease(first_lease)
        assert still_revoked.value.code == "unauthorized"
        assert len(authority._bindings) == len(authority._leases) == 64
    finally:
        service.close()
