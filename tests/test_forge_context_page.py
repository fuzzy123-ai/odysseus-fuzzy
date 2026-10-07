from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import subprocess
from typing import Callable

import pytest

from core.database import ChatMessage
from src.forge_context_metadata import ForgeContextMetadata
from src.forge_context_reader import ImmutableForgeContextReader
from src.private_context_confirmation import (
    ConfirmationError,
    DisclosureBinding,
    ManifestItem,
)
from src.private_context_confirmation_app import (
    ContextSelection,
    CookieConfirmationAuthority,
    ForgeSelectionLocator,
    PrivateContextConfirmationApp,
    SourceSelection,
)
from src.forge_context_page import (
    ForgeContextPageConsumer,
    ForgeContextPageError,
    ForgeContextPageRequest,
    read_native_forge_context_page,
)
from src.project_forge_local import LocalProjectForge
from src.repo_git_adapter import ForgeContentRange
from tests.test_context_manifest_producer import real_three_domain_context


def _run_git(work: Path, *arguments: str, stdout=subprocess.DEVNULL) -> None:
    hook_dir = work / ".no-hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            (
                "git", "-c", f"core.hooksPath={hook_dir}",
                "-c", "core.fsmonitor=false", "-c", "commit.gpgSign=false",
                "-c", "tag.gpgSign=false", "-C", str(work), *arguments,
            ),
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        raise AssertionError("bounded git fixture command failed") from None


def _commit_sha(work: Path, output_path: Path) -> str:
    with output_path.open("wb") as output:
        _run_git(work, "rev-parse", "HEAD", stdout=output)
    raw = output_path.read_bytes()
    if len(raw) > 128 or re.fullmatch(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?\r?\n", raw) is None:
        raise AssertionError("git commit identity was not a closed SHA")
    return raw.strip().decode("ascii")


def _forge_selection_with_payload(
    context, tmp_path: Path, *, path: str, payload: bytes, idempotency_key: str,
):
    metadata = context["forge"]
    roots = getattr(metadata, "_roots", ())
    if type(roots) is not tuple or len(roots) != 2:
        raise AssertionError("Forge roots were not retained by the provider")
    source = Path(roots[0]) / "repos" / "demo"
    selected_path = source / path
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    selected_path.write_bytes(payload)
    _run_git(source, "add", path)
    _run_git(source, "commit", "-m", "retain Forge context page fixture")
    commit = _commit_sha(source, tmp_path / (path.replace("/", "-") + ".sha"))
    owner_sid = context["auth"].subject_id_for_username("fuzzy")
    if type(owner_sid) is not str or not owner_sid:
        raise AssertionError("fixture owner SID unavailable")
    stored = context["local_forge"].store_existing_commit(
        owner_id=owner_sid,
        repo_id="demo",
        source_repo=source,
        commit_sha=commit,
        idempotency_key=idempotency_key,
        policy_snapshot={"forge_context": "public_registered"},
        version_label="Retained page fixture",
        artifacts=(),
    )
    with CookieConfirmationAuthority(context["auth"]).hold(
        context["cookie"], context["chat_id"],
    ) as binding:
        manifest = metadata.capture_version_metadata(
            binding, "demo", stored.version_id, (path,),
        )
    selection = SourceSelection(
        "forge",
        manifest.descriptor.source_id,
        manifest.descriptor.source_version_id,
        ForgeSelectionLocator("demo", stored.version_id, path),
    )
    return selection, manifest


def _empty_forge_selection(context, tmp_path: Path):
    return _forge_selection_with_payload(
        context,
        tmp_path,
        path="empty.txt",
        payload=b"",
        idempotency_key="forge-context-empty-file-0001",
    )


def _native_page(context, binding: DisclosureBinding, *, selection=None, manifest=None,
                 offset=0, page_bytes=17):
    selected = selection or context["forge_selection"]
    native_manifest = manifest or context["forge_manifest"]
    return read_native_forge_context_page(
        metadata_provider=context["forge"],
        local_forge=context["local_forge"],
        binding=binding,
        selection=selected,
        locator=selected.forge_locator,
        expected_item=native_manifest.item,
        expected_manifest_digest=native_manifest.digest,
        offset=offset,
        page_bytes=page_bytes,
    )


class _StringSubclass(str):
    pass


class _IntegerSubclass(int):
    pass


class _BindingSubclass(DisclosureBinding):
    pass


class _SelectionSubclass(SourceSelection):
    pass


class _LocatorSubclass(ForgeSelectionLocator):
    pass


class _ManifestItemSubclass(ManifestItem):
    pass


class _MetadataSubclass(ForgeContextMetadata):
    pass


class _LocalForgeSubclass(LocalProjectForge):
    pass


def _consumer(context):
    confirmation = PrivateContextConfirmationApp(
        context["session_factory"], context["auth"], context["combined"],
    )
    consumer = ForgeContextPageConsumer(
        metadata_provider=context["forge"],
        local_forge=context["local_forge"],
        auth_manager=context["auth"],
        session_factory=context["session_factory"],
        confirmation_app=confirmation,
        manifest_producer=context["combined"],
    )
    return consumer, confirmation


def _request(context, *, selection=None, target=None, offset=0, limit=17, disclosure=None):
    selection = selection or context["selection"]
    target = target or context["forge_selection"]
    return ForgeContextPageRequest(
        selection,
        target,
        context["chat_id"],
        context["forge_manifest"].digest,
        offset,
        limit,
        disclosure,
    )


def test_real_forge_page_reads_multiple_public_ranges_with_honest_hashes(real_three_domain_context):
    context = real_three_domain_context
    consumer, _confirmation = _consumer(context)
    descriptor = context["forge_manifest"].descriptor
    total = descriptor.selected_files[0].byte_count
    assert total >= 2
    selection = ContextSelection((context["forge_selection"],))
    cookie = context["cookie"]
    first = consumer.read_page(
        cookie,
        _request(context, selection=selection, limit=max(1, total // 2)),
    )
    second = consumer.read_page(
        cookie,
        _request(
            context, selection=selection, offset=first.end,
            limit=total - first.end,
        ),
    )
    payload = first.payload_bytes + second.payload_bytes
    assert len(payload) == total
    assert first.end == second.offset
    assert first.whole_sha256 == second.whole_sha256
    assert first.whole_sha256 == "sha256:" + sha256(payload).hexdigest()
    assert first.page_sha256 == "sha256:" + sha256(first.payload_bytes).hexdigest()
    assert second.page_sha256 == "sha256:" + sha256(second.payload_bytes).hexdigest()


def test_real_empty_forge_page_uses_zero_reader_range(real_three_domain_context, tmp_path: Path):
    context = real_three_domain_context
    selection, manifest = _empty_forge_selection(context, tmp_path)
    consumer, _confirmation = _consumer(context)
    page = consumer.read_page(
        context["cookie"],
        ForgeContextPageRequest(
            ContextSelection((selection,)), selection, context["chat_id"],
            manifest.digest, 0, 1,
        ),
    )
    assert page.payload_bytes == b""
    assert page.total == page.offset == page.end == 0
    assert page.whole_sha256 == page.page_sha256 == "sha256:" + sha256(b"").hexdigest()


def test_mixed_private_selection_requires_real_grant_and_rechecks_tampered_row(real_three_domain_context):
    context = real_three_domain_context
    consumer, confirmation = _consumer(context)
    challenge = confirmation.issue_for_selection(
        context["cookie"], context["chat_id"], context["selection"],
    )
    verified = confirmation.consume_challenge(
        context["cookie"], context["chat_id"], challenge,
    )
    request = _request(context, disclosure=verified.disclosure_message_id)
    first = consumer.read_page(context["cookie"], request)
    db = context["session_factory"]()
    try:
        row = db.query(ChatMessage).filter(
            ChatMessage.id == str(verified.disclosure_message_id),
            ChatMessage.session_id == context["chat_id"],
        ).one()
        row.content = "tampered confirmation"
        db.commit()
    finally:
        db.close()
    with pytest.raises(ConfirmationError):
        consumer.read_page(
            context["cookie"],
            _request(context, offset=first.end, disclosure=verified.disclosure_message_id),
        )


def test_forge_canonical_reference_mismatch_fails_closed(real_three_domain_context):
    context = real_three_domain_context
    target = context["forge_selection"]
    forged_target = SourceSelection(
        "forge", target.selection_ref + ".forged", target.revision_ref,
        target.forge_locator,
    )
    forged_items = tuple(
        forged_target if item is target else item for item in context["selection"].items
    )
    selection = ContextSelection(tuple(sorted(
        forged_items,
        key=lambda item: (item.source, item.selection_ref, item.revision_ref),
    )))
    consumer, _confirmation = _consumer(context)
    with pytest.raises((ForgeContextPageError, ConfirmationError)):
        consumer.read_page(
            context["cookie"],
            _request(context, selection=selection, target=forged_target),
        )


def test_native_forge_page_reads_true_git_bytes_in_order(real_three_domain_context, tmp_path: Path):
    context = real_three_domain_context
    selection = context["forge_selection"]
    manifest = context["forge_manifest"]
    descriptor = manifest.descriptor
    roots = getattr(context["forge"], "_roots", ())
    source = Path(roots[0]) / "repos" / selection.forge_locator.repo_id
    expected_path = tmp_path / "native-expected.bin"
    with expected_path.open("wb") as output:
        _run_git(
            source,
            "show",
            f"{descriptor.commit_sha}:{selection.forge_locator.path}",
            stdout=output,
        )
    expected = expected_path.read_bytes()
    assert len(expected) == descriptor.selected_files[0].byte_count >= 2
    first_size = max(1, len(expected) // 2)
    with CookieConfirmationAuthority(context["auth"]).hold(
        context["cookie"], context["chat_id"],
    ) as binding:
        first = _native_page(context, binding, page_bytes=first_size)
        second = _native_page(context, binding, offset=first.end, page_bytes=16_384)
    assert first.payload_bytes == expected[:first_size]
    assert second.payload_bytes == expected[first.end:]
    assert first.payload_bytes + second.payload_bytes == expected
    assert first.whole_sha256 == second.whole_sha256 == "sha256:" + sha256(expected).hexdigest()
    assert first.page_sha256 == "sha256:" + sha256(first.payload_bytes).hexdigest()
    assert second.page_sha256 == "sha256:" + sha256(second.payload_bytes).hexdigest()
    assert first.manifest_digest == second.manifest_digest == manifest.digest
    assert first.byteform == second.byteform == "git_blob"


def test_native_empty_page_and_nonempty_eof_rules(real_three_domain_context, tmp_path: Path):
    context = real_three_domain_context
    empty_selection, empty_manifest = _empty_forge_selection(context, tmp_path)
    total = context["forge_manifest"].descriptor.selected_files[0].byte_count
    assert total > 0
    with CookieConfirmationAuthority(context["auth"]).hold(
        context["cookie"], context["chat_id"],
    ) as binding:
        empty = _native_page(
            context,
            binding,
            selection=empty_selection,
            manifest=empty_manifest,
            offset=0,
            page_bytes=1,
        )
        with pytest.raises(ForgeContextPageError, match="range_invalid"):
            _native_page(context, binding, offset=total, page_bytes=1)
    assert empty.payload_bytes == b""
    assert empty.total == empty.offset == empty.end == 0
    assert empty.whole_sha256 == empty.page_sha256 == "sha256:" + sha256(b"").hexdigest()


def test_native_cap_and_legacy_one_megabyte_limit_remain_distinct(
    real_three_domain_context, tmp_path: Path,
):
    context = real_three_domain_context
    payload = bytes(range(256)) * 64 + b"x"
    selection, manifest = _forge_selection_with_payload(
        context,
        tmp_path,
        path="native-large.bin",
        payload=payload,
        idempotency_key="forge-context-native-large-0001",
    )
    with CookieConfirmationAuthority(context["auth"]).hold(
        context["cookie"], context["chat_id"],
    ) as binding:
        native = _native_page(
            context,
            binding,
            selection=selection,
            manifest=manifest,
            page_bytes=16_384,
        )
        with pytest.raises(ForgeContextPageError, match="invalid_context"):
            _native_page(
                context,
                binding,
                selection=selection,
                manifest=manifest,
                page_bytes=16_385,
            )
    consumer, _confirmation = _consumer(context)
    legacy = consumer.read_page(
        context["cookie"],
        ForgeContextPageRequest(
            ContextSelection((selection,)),
            selection,
            context["chat_id"],
            manifest.digest,
            0,
            1_000_000,
        ),
    )
    assert native.payload_bytes == payload[:16_384]
    assert native.end == 16_384
    assert legacy.payload_bytes == payload
    assert legacy.end == legacy.total == len(payload)


@pytest.mark.parametrize(
    ("entrypoint", "returned_length"),
    (
        pytest.param("native", 1, id="native-truncated"),
        pytest.param("native", 0, id="native-empty"),
        pytest.param("legacy", 1, id="legacy-truncated"),
        pytest.param("legacy", 0, id="legacy-empty"),
    ),
)
def test_native_and_legacy_reject_exact_typed_short_reader_ranges(
    real_three_domain_context,
    monkeypatch,
    entrypoint: str,
    returned_length: int,
):
    context = real_three_domain_context
    original = ImmutableForgeContextReader.read_exact_range

    def short_read(reader, request, *, path, offset, max_bytes):
        honest = original(
            reader, request, path=path, offset=offset, max_bytes=max_bytes,
        )
        assert len(honest.content) == 2
        return ForgeContentRange(
            honest.schema,
            honest.owner_scope,
            honest.repo_id,
            honest.version_id,
            honest.commit_sha,
            honest.snapshot_digest,
            honest.path,
            honest.file_content_sha256,
            honest.file_byte_count,
            honest.offset,
            honest.content[:returned_length],
            honest.authority_binding,
        )

    monkeypatch.setattr(ImmutableForgeContextReader, "read_exact_range", short_read)
    with pytest.raises(ForgeContextPageError, match="^read_unavailable$") as failure:
        if entrypoint == "native":
            with CookieConfirmationAuthority(context["auth"]).hold(
                context["cookie"], context["chat_id"],
            ) as binding:
                _native_page(context, binding, page_bytes=2)
        else:
            consumer, _confirmation = _consumer(context)
            consumer.read_page(
                context["cookie"],
                _request(
                    context,
                    selection=ContextSelection((context["forge_selection"],)),
                    limit=2,
                ),
            )
    assert failure.value.code == "read_unavailable"
    assert failure.value.args == ("read_unavailable",)


def _native_kwargs(context, binding: DisclosureBinding) -> dict[str, object]:
    selection = context["forge_selection"]
    manifest = context["forge_manifest"]
    return {
        "metadata_provider": context["forge"],
        "local_forge": context["local_forge"],
        "binding": binding,
        "selection": selection,
        "locator": selection.forge_locator,
        "expected_item": manifest.item,
        "expected_manifest_digest": manifest.digest,
        "offset": 0,
        "page_bytes": 1,
    }


def _binding_subclass(_context, binding: DisclosureBinding):
    return _BindingSubclass(
        binding.owner_sid,
        binding.chat_session_id,
        binding.auth_session_binding,
        binding.security_revision,
        binding.audience,
    )


def _selection_subclass(context, _binding):
    selection = context["forge_selection"]
    return _SelectionSubclass(
        selection.source,
        selection.selection_ref,
        selection.revision_ref,
        selection.forge_locator,
    )


def _locator_subclass(context, _binding):
    locator = context["forge_selection"].forge_locator
    return _LocatorSubclass(locator.repo_id, locator.version_id, locator.path)


def _item_subclass(context, _binding):
    item = context["forge_manifest"].item
    return _ManifestItemSubclass(
        item.source,
        item.selection_ref,
        item.revision_ref,
        item.boundary_sha256,
        item.record_sha256,
        item.payload_sha256,
        item.classification,
        item.provenance_ref,
    )


@pytest.mark.parametrize(
    ("field", "value_factory"),
    (
        pytest.param(
            "metadata_provider", lambda _context, _binding: object.__new__(_MetadataSubclass),
            id="metadata-subclass",
        ),
        pytest.param(
            "local_forge", lambda _context, _binding: object.__new__(_LocalForgeSubclass),
            id="local-forge-subclass",
        ),
        pytest.param("binding", _binding_subclass, id="binding-subclass"),
        pytest.param("selection", _selection_subclass, id="selection-subclass"),
        pytest.param("locator", _locator_subclass, id="locator-subclass"),
        pytest.param("expected_item", _item_subclass, id="manifest-item-subclass"),
        pytest.param(
            "expected_manifest_digest",
            lambda context, _binding: _StringSubclass(context["forge_manifest"].digest),
            id="manifest-digest-string-subclass",
        ),
        pytest.param("offset", lambda _context, _binding: True, id="offset-bool"),
        pytest.param(
            "page_bytes", lambda _context, _binding: _IntegerSubclass(1),
            id="page-bytes-integer-subclass",
        ),
        pytest.param("page_bytes", lambda _context, _binding: 0, id="page-bytes-zero"),
        pytest.param("page_bytes", lambda _context, _binding: 16_385, id="page-bytes-over-limit"),
    ),
)
def test_native_entrypoint_rejects_nonexact_types_and_scalars(
    real_three_domain_context,
    field: str,
    value_factory: Callable[[object, DisclosureBinding], object],
):
    context = real_three_domain_context
    with CookieConfirmationAuthority(context["auth"]).hold(
        context["cookie"], context["chat_id"],
    ) as binding:
        arguments = _native_kwargs(context, binding)
        arguments[field] = value_factory(context, binding)
        with pytest.raises(ForgeContextPageError, match="invalid_context"):
            read_native_forge_context_page(**arguments)


def _mutate_manifest_digest(manifest) -> None:
    object.__setattr__(manifest, "manifest_digest", "0" * 64)


def _mutate_descriptor(field: str, value: object) -> Callable[[object], None]:
    def mutate(manifest) -> None:
        object.__setattr__(manifest.descriptor, field, value)

    return mutate


def _mutate_selected_file(field: str, value: object) -> Callable[[object], None]:
    def mutate(manifest) -> None:
        object.__setattr__(manifest.descriptor.selected_files[0], field, value)

    return mutate


def _mutate_selected_size(manifest) -> None:
    selected = manifest.descriptor.selected_files[0]
    object.__setattr__(selected, "byte_count", selected.byte_count + 1)


def _mutate_item_payload(manifest) -> None:
    object.__setattr__(manifest.item, "payload_sha256", "0" * 64)


def _mutate_owner_scope(manifest) -> None:
    current = manifest.descriptor.owner_scope
    replacement = current[:-1] + ("0" if current[-1] != "0" else "1")
    object.__setattr__(manifest.descriptor, "owner_scope", replacement)


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(_mutate_manifest_digest, id="metadata-digest-drift"),
        pytest.param(_mutate_owner_scope, id="owner-drift"),
        pytest.param(_mutate_descriptor("repo_id", "drift"), id="repo-drift"),
        pytest.param(_mutate_descriptor("version_id", "version_drift"), id="version-drift"),
        pytest.param(_mutate_descriptor("commit_sha", "0" * 40), id="commit-ref-drift"),
        pytest.param(_mutate_descriptor("source_id", "forge.ref.drift"), id="source-ref-drift"),
        pytest.param(_mutate_descriptor("inventory_sha256", "0" * 64), id="snapshot-drift"),
        pytest.param(_mutate_descriptor("payload_sha256", "0" * 64), id="blob-drift"),
        pytest.param(_mutate_selected_file("path", "drift.txt"), id="path-drift"),
        pytest.param(
            _mutate_selected_file("content_sha256", "0" * 64), id="content-hash-drift",
        ),
        pytest.param(_mutate_selected_size, id="byte-size-drift"),
        pytest.param(_mutate_item_payload, id="manifest-item-blob-drift"),
    ),
)
def test_native_entrypoint_rejects_second_capture_drift(
    real_three_domain_context,
    monkeypatch,
    mutation: Callable[[object], None],
):
    context = real_three_domain_context
    original = ForgeContextMetadata.capture_version_metadata
    calls = 0

    def drifting_capture(metadata, binding, repo_id, version_id, paths):
        nonlocal calls
        manifest = original(metadata, binding, repo_id, version_id, paths)
        calls += 1
        if calls == 2:
            mutation(manifest)
        return manifest

    monkeypatch.setattr(ForgeContextMetadata, "capture_version_metadata", drifting_capture)
    with CookieConfirmationAuthority(context["auth"]).hold(
        context["cookie"], context["chat_id"],
    ) as binding:
        with pytest.raises(ForgeContextPageError, match="forge_context_stale"):
            _native_page(context, binding, page_bytes=1)
    assert calls == 2
