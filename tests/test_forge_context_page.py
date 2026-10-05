from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import subprocess

import pytest

from core.database import ChatMessage
from src.private_context_confirmation import ConfirmationError
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
)
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


def _empty_forge_selection(context, tmp_path: Path):
    metadata = context["forge"]
    roots = getattr(metadata, "_roots", ())
    if type(roots) is not tuple or len(roots) != 2:
        raise AssertionError("Forge roots were not retained by the provider")
    source = Path(roots[0]) / "repos" / "demo"
    empty_path = source / "empty.txt"
    empty_path.write_bytes(b"")
    _run_git(source, "add", "empty.txt")
    _run_git(source, "commit", "-m", "retain empty Forge context file")
    commit = _commit_sha(source, tmp_path / "empty-commit.sha")
    owner_sid = context["auth"].subject_id_for_username("fuzzy")
    if type(owner_sid) is not str or not owner_sid:
        raise AssertionError("fixture owner SID unavailable")
    stored = context["local_forge"].store_existing_commit(
        owner_id=owner_sid,
        repo_id="demo",
        source_repo=source,
        commit_sha=commit,
        idempotency_key="forge-context-empty-file-0001",
        policy_snapshot={"forge_context": "public_registered"},
        version_label="Empty retained fixture",
        artifacts=(),
    )
    with CookieConfirmationAuthority(context["auth"]).hold(
        context["cookie"], context["chat_id"],
    ) as binding:
        manifest = metadata.capture_version_metadata(
            binding, "demo", stored.version_id, ("empty.txt",),
        )
    selection = SourceSelection(
        "forge",
        manifest.descriptor.source_id,
        manifest.descriptor.source_version_id,
        ForgeSelectionLocator("demo", stored.version_id, "empty.txt"),
    )
    return selection, manifest


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
