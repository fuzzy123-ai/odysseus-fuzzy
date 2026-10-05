import base64
import hashlib
from collections.abc import Mapping

import pytest

from src.forge_context_pack import ForgeContextAuthority, ForgeContextPackComposer, ForgeContextPackError, REQUEST_SCHEMA
from src.repo_git_adapter import ForgeContentRange, ForgeExactReaderReference, ForgeSnapshotAuthorityBinding, ForgeSnapshotFile, ForgeSnapshotInventory, ForgeSnapshotRequest, FORGE_CONTENT_RANGE_SCHEMA_V2


class _Reader:
    def __init__(self, request): self.request = request; self.binding = request.authority_binding
    def inventory(self, request, *, paths=None):
        all_files = (
            ForgeSnapshotFile("main.py", "sha256:" + hashlib.sha256(b"print(1)").hexdigest(), 8),
            ForgeSnapshotFile("more.py", "sha256:" + hashlib.sha256(b"print(2)").hexdigest(), 8),
        )
        chosen = all_files if paths is None else tuple(item for item in all_files if item.path in paths)
        self.last_inventory = ForgeSnapshotInventory(request.owner_scope, request.repo_id, request.version_id, request.commit_sha, "sha256:" + "c" * 64, self.binding, chosen)
        return self.last_inventory
    def read_exact_range(self, request, *, path, offset, max_bytes):
        inventory = self.last_inventory; entry = inventory.file(path)
        source = {"main.py": b"print(1)", "more.py": b"print(2)"}[path]
        content=source[offset:offset+max_bytes]
        return ForgeContentRange(FORGE_CONTENT_RANGE_SCHEMA_V2, request.owner_scope, request.repo_id, request.version_id, request.commit_sha, inventory.snapshot_digest, path, entry.content_sha256, 8, offset, content, self.binding)


def _authority():
    binding=ForgeSnapshotAuthorityBinding("forge.code","v1","usi_generation_"+"a"*64,"fca.forge_code.admission.v1")
    request=ForgeSnapshotRequest("owner:alice","opaque","demo","pv_"+"a"*32,"b"*40,binding)
    return ForgeContextAuthority("alice", 1, request, _Reader(request), None)


def _body(cursor=None, paths=None, page_bytes=8):
    return {"schema":REQUEST_SCHEMA,"version_id":"pv_"+"a"*32,"commit_sha":"b"*40,"manifest_sha256":"sha256:"+"c"*64,"code_paths":paths or ["main.py"],"planning":{"project_id":"p","roadmap_id":"r","revision_id":"1","content_sha256":"sha256:"+"d"*64,"head_generation":1},"page_bytes":page_bytes,"cursor":cursor}


def test_pack_is_deterministic_and_contains_only_opaque_planning():
    composer=ForgeContextPackComposer(cursor_key=b"x"*32)
    pack=composer.compose(_authority(), _body())
    assert pack["schema"] == "odysseus.forge_context_pack.v1"
    assert pack["read_only"] is True and pack["send_performed"] is False
    assert pack["planning"]["status"] == "approval_required"
    assert pack["knowledge"]["status"] == "exact_reader_unavailable"
    assert pack["complete"] is False and pack["code_complete"] is True and pack["next_cursor"] is None


def test_cursor_is_bound_to_sid_and_request():
    composer=ForgeContextPackComposer(cursor_key=b"x"*32)
    body=_body(); body["page_bytes"]=1
    first=composer.compose(_authority(), body)
    changed=_body(first["next_cursor"]); changed["commit_sha"]="e"*40
    import pytest
    with pytest.raises(ValueError): composer.compose(_authority(), changed)


def test_cursor_resumes_typed_forge_cursor_across_multiple_files():
    composer = ForgeContextPackComposer(cursor_key=b"x" * 32)
    first = composer.compose(_authority(), _body(paths=["main.py", "more.py"], page_bytes=3))
    assert first["code"][0]["path"] == "main.py"
    assert first["next_cursor"]
    second = composer.compose(_authority(), _body(first["next_cursor"], paths=["main.py", "more.py"], page_bytes=3))
    assert second["code"][0]["path"] == "main.py"
    assert second["code"][0]["offset"] == 3


def test_terminal_cursor_reconstructs_every_requested_file_without_a_partial_final_page():
    composer = ForgeContextPackComposer(cursor_key=b"x" * 32)
    body = _body(paths=["main.py", "more.py"], page_bytes=3)
    pages = []
    while True:
        packet = composer.compose(_authority(), body)
        pages.extend(packet["code"])
        if packet["next_cursor"] is None:
            assert packet["code_complete"] is True
            break
        body = _body(packet["next_cursor"], paths=["main.py", "more.py"], page_bytes=3)
    reconstructed = {}
    for page in pages:
        reconstructed.setdefault(page["path"], bytearray()).extend(base64.b64decode(page["content_b64"]))
    assert {path: bytes(value) for path, value in reconstructed.items()} == {
        "main.py": b"print(1)", "more.py": b"print(2)",
    }


def test_cursor_rejects_wrong_sid_and_process_restart_key():
    original = ForgeContextPackComposer(cursor_key=b"x" * 32)
    first = original.compose(_authority(), _body(page_bytes=3))
    with pytest.raises(ValueError, match="invalid_cursor"):
        ForgeContextPackComposer(cursor_key=b"y" * 32).compose(_authority(), _body(first["next_cursor"], page_bytes=3))
    wrong = ForgeContextAuthority("bob", 1, _authority().reader_request, _Reader(_authority().reader_request), None)
    with pytest.raises(ValueError, match="invalid_cursor"):
        original.compose(wrong, _body(first["next_cursor"], page_bytes=3))


def test_pack_requires_a_real_positive_integer_planning_generation():
    composer = ForgeContextPackComposer(cursor_key=b"x" * 32)
    for generation in ("1", 0, -1, True):
        body = _body()
        body["planning"]["head_generation"] = generation
        with pytest.raises(ValueError, match="invalid_request"):
            composer.compose(_authority(), body)


class _HostileMapping(Mapping):
    calls = 0

    def _callback(self, *args):
        self.calls += 1
        raise RuntimeError("untrusted mapping callback")

    __getitem__ = _callback
    __iter__ = _callback
    __len__ = _callback
    get = _callback
    keys = _callback


class _HostileDict(dict):
    calls = 0

    def _callback(self, *args):
        self.calls += 1
        raise RuntimeError("untrusted dict callback")

    __iter__ = _callback
    get = _callback
    keys = _callback


@pytest.mark.parametrize("factory", (_HostileMapping, _HostileDict))
def test_hostile_body_mappings_fail_without_callbacks_or_raw_causes(factory):
    body = factory()
    with pytest.raises(ForgeContextPackError) as error:
        ForgeContextPackComposer(cursor_key=b"x" * 32).compose(_authority(), body)
    assert error.value.code == "invalid_request"
    assert error.value.__cause__ is None
    assert body.calls == 0


@pytest.mark.parametrize("location", ("schema", "body_key", "planning_key"))
def test_string_subclasses_are_rejected_before_hash_or_equality_callbacks(location):
    calls = []

    class HostileString(str):
        def __hash__(self):
            calls.append("hash")
            return str.__hash__(self)

        def __eq__(self, other):
            calls.append("equality")
            return str.__eq__(self, other)

    body = _body()
    if location == "schema":
        body["schema"] = HostileString(REQUEST_SCHEMA)
    elif location == "body_key":
        body[HostileString("schema")] = body.pop("schema")
    else:
        body["planning"][HostileString("project_id")] = body["planning"].pop("project_id")
    calls.clear()
    with pytest.raises(ForgeContextPackError, match="^invalid_request$"):
        ForgeContextPackComposer(cursor_key=b"x" * 32).compose(_authority(), body)
    assert calls == []


def test_reader_callback_cannot_change_admitted_request_or_planning_refs():
    body = _body(page_bytes=3)
    base = _authority()
    ref = {"ref": "sha256:" + "e" * 64, "head_binding": "sha256:" + "f" * 64}
    snapshot = {"request": dict(body["planning"]), "refs": (ref,)}
    expected_ref = dict(ref)
    original_inventory = base.reader.inventory

    def mutate_after_admission(request, *, paths=None):
        inventory = original_inventory(request, paths=paths)
        body["code_paths"][:] = ["foreign.py"]
        body["planning"]["head_generation"] = 99
        body["cursor"] = "foreign.cursor"
        snapshot["request"]["head_generation"] = 100
        ref["ref"] = "sha256:" + "0" * 64
        return inventory

    base.reader.inventory = mutate_after_admission
    authority = ForgeContextAuthority("alice", 0, base.reader_request, base.reader, None, snapshot)
    composer = ForgeContextPackComposer(cursor_key=b"x" * 32)
    first = composer.compose(authority, body)
    assert first["code"][0]["path"] == "main.py"
    assert first["planning"]["refs"] == [expected_ref]
    # The continuation authenticates the original request, not its mutated aliases.
    resumed_authority = ForgeContextAuthority("alice", 0, base.reader_request, _Reader(base.reader_request), None)
    second = composer.compose(resumed_authority, _body(first["next_cursor"], page_bytes=3))
    assert second["code"][0]["offset"] == 3
    assert first["planning"]["refs"] == [expected_ref]


@pytest.mark.parametrize("factory", (_HostileMapping, _HostileDict))
def test_hostile_planning_snapshots_fail_before_reader_callback(factory):
    snapshot = factory()
    base = _authority()
    calls = []
    base.reader.inventory = lambda *args, **kwargs: calls.append("reader")
    authority = ForgeContextAuthority("alice", 0, base.reader_request, base.reader, None, snapshot)
    with pytest.raises(ForgeContextPackError) as error:
        ForgeContextPackComposer(cursor_key=b"x" * 32).compose(authority, _body())
    assert error.value.code == "planning_authority_invalid"
    assert error.value.__cause__ is None
    assert snapshot.calls == 0 and calls == []


@pytest.mark.parametrize("kind", ("request", "reference"))
def test_nested_hostile_planning_objects_fail_without_callbacks(kind):
    hostile = _HostileDict()
    snapshot = {"request": hostile if kind == "request" else _body()["planning"],
                "refs": (hostile,) if kind == "reference" else ()}
    base = _authority()
    authority = ForgeContextAuthority("alice", 0, base.reader_request, base.reader, None, snapshot)
    with pytest.raises(ForgeContextPackError, match="^planning_authority_invalid$"):
        ForgeContextPackComposer(cursor_key=b"x" * 32).compose(authority, _body())
    assert hostile.calls == 0


@pytest.mark.parametrize("mismatch", (False, True))
def test_missing_or_mismatched_planning_snapshot_preserves_approval_required(mismatch):
    base = _authority()
    snapshot = None
    if mismatch:
        request = _body()["planning"]
        request["head_generation"] = 2
        snapshot = {"request": request, "refs": ()}
    authority = ForgeContextAuthority("alice", 0, base.reader_request, base.reader, None, snapshot)
    pack = ForgeContextPackComposer(cursor_key=b"x" * 32).compose(authority, _body())
    assert pack["planning"] == {"status": "approval_required", "refs": []}
    assert pack["code_complete"] is True
