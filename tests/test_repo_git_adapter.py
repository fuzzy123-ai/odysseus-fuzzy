from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
from pathlib import Path

import pytest
import src.repo_git_adapter as forge

from src.repo_git_adapter import (
    ForgeExactReaderReference,
    ForgeSnapshotAuthorityBinding,
    ForgeSnapshotError,
    ForgeSnapshotFile,
    ForgeSnapshotInventory,
    ForgeSnapshotRequest,
    RepoGitAdapter,
    RepoGitAdapterError,
    RepoGitCommandResult,
    git_read_command_is_allowed,
)
from src.repo_registry import RepoRecord, RepoRegistry, RepoRemote


class EvilStr(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        return True


class EvilInt(int):
    pass


class EvilAuthorityBinding(ForgeSnapshotAuthorityBinding):
    def __eq__(self, other):
        return True


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    readme = repo / "README.md"
    readme.write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial commit")
    _git(repo, "branch", "-M", "main")
    _git(repo, "remote", "add", "origin", "https://x-access-token:secret-value@github.com/fuzzy123-ai/demo.git")
    readme.write_text("two\n", encoding="utf-8")
    return repo


def _registry() -> RepoRegistry:
    registry = RepoRegistry()
    registry.add(
        RepoRecord.create(
            repo_id="demo",
            title="Demo Repo",
            repo_kind="project",
            owner="fuzzy123-ai",
            path_ref="repos/demo",
            workspace_root="repos",
            project_root="repos/demo",
            default_branch="main",
            remotes=[
                RepoRemote.create(
                    name="origin",
                    url="https://github.com/fuzzy123-ai/demo.git",
                    purpose="origin",
                )
            ],
            created_at="2026-06-28T10:00:00Z",
        )
    )
    return registry


def test_adapter_reads_status_log_changed_paths_and_redacted_remotes(tmp_path: Path):
    repo = _make_repo(tmp_path)
    adapter = RepoGitAdapter(registry=_registry(), repo_roots={"demo": repo})

    snapshot = adapter.snapshot("demo")
    dumped = json.dumps(snapshot.to_dict())

    assert snapshot.current_branch == "main"
    assert snapshot.status.dirty is True
    assert any("README.md" in entry for entry in snapshot.status.entries)
    assert snapshot.commits[0].subject == "initial commit"
    assert len(snapshot.changed_paths) == 1
    assert snapshot.changed_paths[0].path == "README.md"
    assert snapshot.remotes[0].url_redacted == "https://github.com/fuzzy123-ai/demo.git"
    assert "secret-value" not in dumped
    assert "x-access-token" not in dumped
    assert str(tmp_path) not in dumped


def test_adapter_rejects_unknown_repo(tmp_path: Path):
    repo = _make_repo(tmp_path)
    adapter = RepoGitAdapter(registry=_registry(), repo_roots={"demo": repo})

    with pytest.raises(RepoGitAdapterError, match="unknown repo"):
        adapter.status("missing")


def test_adapter_rejects_repo_root_outside_allowed_workspace(tmp_path: Path):
    repo = _make_repo(tmp_path / "outside")
    allowed_workspace = tmp_path / "allowed"
    allowed_workspace.mkdir()
    adapter = RepoGitAdapter(
        registry=_registry(),
        repo_roots={"demo": repo},
        workspace_base=allowed_workspace,
    )

    with pytest.raises(RepoGitAdapterError, match="outside the allowed workspace"):
        adapter.status("demo")


def test_adapter_requires_local_git_repository(tmp_path: Path):
    repo = tmp_path / "plain-folder"
    repo.mkdir()
    adapter = RepoGitAdapter(registry=_registry(), repo_roots={"demo": repo})

    with pytest.raises(RepoGitAdapterError, match="local Git repository"):
        adapter.status("demo")


def test_adapter_bounds_and_redacts_command_output(tmp_path: Path):
    repo = _make_repo(tmp_path)

    def fake_runner(argv, *, cwd: Path, timeout_seconds: int, env):
        return RepoGitCommandResult(
            exit_code=0,
            stdout="## main\n M X:/fixtures/secret.txt password=supersecret\n" + ("x" * 20_000),
            stderr="token=abc123",
        )

    adapter = RepoGitAdapter(registry=_registry(), repo_roots={"demo": repo}, command_runner=fake_runner)
    status = adapter.status("demo")

    dumped = json.dumps(status.to_dict())
    assert "C:/Users" not in dumped
    assert "supersecret" not in dumped
    assert len(dumped) < 11_000


def test_git_read_command_allowlist_is_tight():
    assert git_read_command_is_allowed(("git", "status", "--short", "--branch")) is True
    assert git_read_command_is_allowed(("git", "branch", "--show-current")) is True
    assert git_read_command_is_allowed(
        ("git", "log", "--max-count", "10", "--date=iso", "--pretty=format:%H%x09%ad%x09%an%x09%s")
    ) is True
    assert git_read_command_is_allowed(("git", "diff", "--name-status")) is True
    assert git_read_command_is_allowed(("git", "diff", "--stat")) is True
    assert git_read_command_is_allowed(("git", "remote", "-v")) is True
    assert git_read_command_is_allowed(("git", "log", "--max-count", "101", "--date=iso", "--pretty=format:%H")) is False
    assert git_read_command_is_allowed(("git", "add", ".")) is False
    assert git_read_command_is_allowed(("git", "commit", "-m", "x")) is False
    assert git_read_command_is_allowed(("git", "push", "origin", "main")) is False
    assert git_read_command_is_allowed(("git", "reset", "--hard")) is False


def test_source_uses_shell_false_and_no_write_runtime():
    source = Path("src/repo_git_adapter.py").read_text(encoding="utf-8").lower()

    assert "subprocess.run(" in source
    assert "shell=false" in source
    forbidden = ("git add", "git commit", "git push", "reset --hard", "force-push")
    for fragment in forbidden:
        assert fragment not in source


def _forge_snapshot(*files: ForgeSnapshotFile) -> ForgeSnapshotInventory:
    return ForgeSnapshotInventory(
        owner_scope="user:alice",
        repo_id="demo",
        version_id="pv_" + "a" * 32,
        commit_sha="b" * 40,
        manifest_sha256="sha256:" + "c" * 64,
        authority_binding=_forge_authority_binding(),
        files=tuple(files),
    )


def _forge_authority_binding(**changes: str) -> ForgeSnapshotAuthorityBinding:
    values = {
        "adapter_id": "forge.code",
        "adapter_version": "v1",
        "adapter_generation": "usi_generation_" + "9" * 64,
        "admission_policy_generation": "fca.forge_code.admission.v1",
    }
    values.update(changes)
    return ForgeSnapshotAuthorityBinding(**values)


def test_forge_snapshot_inventory_is_content_free_and_deterministic():
    first = ForgeSnapshotFile("src/main.py", "sha256:" + "1" * 64, 23)
    second = ForgeSnapshotFile("README.md", "sha256:" + "2" * 64, 9)

    left = _forge_snapshot(first, second)
    right = _forge_snapshot(second, first)

    assert [item.path for item in left.files] == ["README.md", "src/main.py"]
    assert left.snapshot_digest == right.snapshot_digest
    assert left.file("src/main.py") == first
    dumped = json.dumps(left.to_dict())
    assert "content" not in dumped.replace("content_sha256", "")
    assert "source body" not in dumped


@pytest.mark.parametrize(
    "path",
    (
        "/etc/passwd",
        "C:/Windows/file.py",
        "src\\main.py",
        "src/../main.py",
        "src//main.py",
        "src/\nmain.py",
        "src/file:stream.py",
        "src/file.py.",
        "src/file.py ",
        "src/file?.py",
        "src/CON.py",
    ),
)
def test_forge_snapshot_file_rejects_noncanonical_or_absolute_paths(path: str):
    with pytest.raises(ForgeSnapshotError, match="path"):
        ForgeSnapshotFile(path, "sha256:" + "d" * 64, 1)


def test_forge_snapshot_rejects_duplicate_paths_and_tampered_digest():
    item = ForgeSnapshotFile("src/main.py", "sha256:" + "e" * 64, 1)
    with pytest.raises(ForgeSnapshotError, match="duplicate"):
        _forge_snapshot(item, item)
    with pytest.raises(ForgeSnapshotError, match="digest"):
        ForgeSnapshotInventory(
            owner_scope="user:alice",
            repo_id="demo",
            version_id="pv_" + "a" * 32,
            commit_sha="b" * 40,
            manifest_sha256="sha256:" + "c" * 64,
            authority_binding=_forge_authority_binding(),
            files=(item,),
            snapshot_digest="sha256:" + "0" * 64,
        )


def test_forge_snapshot_rejects_case_insensitive_windows_path_collisions():
    upper = ForgeSnapshotFile("src/Main.py", "sha256:" + "1" * 64, 1)
    lower = ForgeSnapshotFile("src/main.py", "sha256:" + "2" * 64, 1)

    with pytest.raises(ForgeSnapshotError, match="case-insensitive duplicate"):
        _forge_snapshot(upper, lower)


def test_forge_snapshot_normalizes_nfc_and_resolves_casefolded_unicode_aliases():
    canonical = ForgeSnapshotFile("src/Caf\u00e9.py", "sha256:" + "3" * 64, 3)
    nfd_case_alias = "SRC/CAFE\u0301.PY"
    inventory = _forge_snapshot(canonical)

    assert canonical.path == "src/Caf\u00e9.py"
    assert inventory.file(nfd_case_alias) == canonical

    colliding = ForgeSnapshotFile(nfd_case_alias, "sha256:" + "4" * 64, 4)
    with pytest.raises(ForgeSnapshotError, match="case-insensitive duplicate"):
        _forge_snapshot(canonical, colliding)


def test_authority_binding_is_mandatory_and_changes_snapshot_digest():
    item = ForgeSnapshotFile("src/main.py", "sha256:" + "5" * 64, 5)
    baseline = _forge_snapshot(item)
    changed = ForgeSnapshotInventory(
        owner_scope=baseline.owner_scope,
        repo_id=baseline.repo_id,
        version_id=baseline.version_id,
        commit_sha=baseline.commit_sha,
        manifest_sha256=baseline.manifest_sha256,
        authority_binding=_forge_authority_binding(admission_policy_generation="fca.forge_code.admission.v2"),
        files=baseline.files,
    )

    assert baseline.snapshot_digest != changed.snapshot_digest
    assert baseline.to_dict()["authority_binding"] == baseline.authority_binding.to_dict()
    with pytest.raises(ForgeSnapshotError, match="authority_binding"):
        ForgeSnapshotInventory(
            owner_scope=baseline.owner_scope,
            repo_id=baseline.repo_id,
            version_id=baseline.version_id,
            commit_sha=baseline.commit_sha,
            manifest_sha256=baseline.manifest_sha256,
            authority_binding=None,
            files=baseline.files,
        )


def test_forge_authority_contracts_reject_scalar_and_dataclass_subclasses():
    with pytest.raises(ForgeSnapshotError, match="bounded Forge token"):
        _forge_authority_binding(adapter_id=EvilStr("forge.code"))
    with pytest.raises(ForgeSnapshotError, match="byte_count"):
        ForgeSnapshotFile("src/main.py", "sha256:" + "6" * 64, EvilInt(1))

    binding = _forge_authority_binding()
    evil_binding = EvilAuthorityBinding(
        adapter_id=binding.adapter_id,
        adapter_version=binding.adapter_version,
        adapter_generation=binding.adapter_generation,
        admission_policy_generation=binding.admission_policy_generation,
    )
    with pytest.raises(ForgeSnapshotError, match="authority_binding"):
        ForgeSnapshotRequest(
            owner_scope="user:alice",
            authorization_ref="forge.auth_context",
            repo_id="demo",
            version_id="pv_" + "a" * 32,
            commit_sha="b" * 40,
            authority_binding=evil_binding,
        )


def test_forge_request_and_reader_reference_require_immutable_versions_and_relative_paths():
    request = ForgeSnapshotRequest(
        owner_scope="user:alice",
        authorization_ref="forge.auth_context",
        repo_id="demo",
        version_id="pv_" + "a" * 32,
        commit_sha="b" * 40,
        authority_binding=_forge_authority_binding(),
    )
    snapshot = _forge_snapshot(ForgeSnapshotFile("src/main.py", "sha256:" + "f" * 64, 7))
    reference = ForgeExactReaderReference(
        owner_scope=request.owner_scope,
        repo_id=request.repo_id,
        version_id=request.version_id,
        commit_sha=request.commit_sha,
        snapshot_digest=snapshot.snapshot_digest,
        path="src/main.py",
        content_sha256="sha256:" + "f" * 64,
        max_bytes=7,
        authority_binding=request.authority_binding,
    )

    assert reference.snapshot_digest == snapshot.snapshot_digest
    with pytest.raises(ForgeSnapshotError, match="canonical NFC"):
        ForgeExactReaderReference(
            owner_scope=request.owner_scope,
            repo_id=request.repo_id,
            version_id=request.version_id,
            commit_sha=request.commit_sha,
            snapshot_digest=snapshot.snapshot_digest,
            path="src/cafe\u0301.py",
            content_sha256="sha256:" + "f" * 64,
            max_bytes=7,
            authority_binding=request.authority_binding,
        )
    with pytest.raises(ForgeSnapshotError, match="immutable Forge version"):
        ForgeSnapshotRequest(
            owner_scope="user:alice",
            authorization_ref="forge.auth_context",
            repo_id="demo",
            version_id="HEAD",
            commit_sha="b" * 40,
            authority_binding=_forge_authority_binding(),
        )
    with pytest.raises(ForgeSnapshotError, match="immutable Forge commit"):
        ForgeSnapshotRequest(
            owner_scope="user:alice",
            authorization_ref="forge.auth_context",
            repo_id="demo",
            version_id="pv_" + "a" * 32,
            commit_sha="main",
            authority_binding=_forge_authority_binding(),
        )


def _fca03a_world(content: bytes, *, path: str = "src/main.py"):
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    binding = _forge_authority_binding()
    request = ForgeSnapshotRequest(
        owner_scope="user:alice",
        authorization_ref="forge.auth_context",
        repo_id="demo",
        version_id="pv_" + "a" * 32,
        commit_sha="b" * 40,
        authority_binding=binding,
    )
    inventory = _forge_snapshot(ForgeSnapshotFile(path, digest, len(content)))
    return request, inventory


class _Fca03aReader:
    def __init__(self, content: bytes):
        self.content = content
        self.calls = []

    def read_exact_range(self, request, *, path, offset, max_bytes):
        self.calls.append((request, path, offset, max_bytes))
        return forge.ForgeContentRange(
            schema=forge.FORGE_CONTENT_RANGE_SCHEMA_V2,
            owner_scope=request.owner_scope,
            repo_id=request.repo_id,
            version_id=request.version_id,
            commit_sha=request.commit_sha,
            snapshot_digest=self.inventory.snapshot_digest,
            path=path,
            file_content_sha256=self.inventory.file(path).content_sha256,
            file_byte_count=len(self.content),
            offset=offset,
            content=self.content[offset : offset + max_bytes],
            authority_binding=request.authority_binding,
        )


def _fca03a_reader(content: bytes, inventory):
    reader = _Fca03aReader(content)
    reader.inventory = inventory
    return reader


def test_fca03a_v2_public_api_schemas_budgets_and_v1_exports_are_exact():
    assert forge.FORGE_CONTENT_CURSOR_SCHEMA_V2 == "odysseus.forge_content_cursor.v2"
    assert forge.FORGE_CONTENT_RANGE_SCHEMA_V2 == "odysseus.forge_content_range.v2"
    assert forge.FORGE_CONTENT_PAGE_SCHEMA_V2 == "odysseus.forge_content_page.v2"
    assert forge.MAX_FORGE_CONTENT_PAGE_BYTES == 1_000_000
    assert forge.MAX_FORGE_EXACT_READ_BYTES == 1_000_000
    assert tuple(inspect.signature(forge.open_forge_content_cursor).parameters) == (
        "request",
        "inventory",
        "path",
        "page_bytes",
    )
    assert tuple(inspect.signature(forge.read_forge_content_page).parameters) == (
        "reader",
        "request",
        "inventory",
        "cursor",
    )


def test_fca03a_empty_file_returns_one_empty_terminal_page():
    request, inventory = _fca03a_world(b"")
    reader = _fca03a_reader(b"", inventory)
    cursor = forge.open_forge_content_cursor(request, inventory, path="src/main.py")
    page = forge.read_forge_content_page(reader, request, inventory, cursor)
    assert page.content == b""
    assert page.complete is True
    assert page.next_cursor is None
    assert reader.calls[0][2:] == (0, 0)


def test_fca03a_one_million_and_one_bytes_resume_without_gap_or_retention():
    content = b"a" * 1_000_001
    request, inventory = _fca03a_world(content)
    reader = _fca03a_reader(content, inventory)
    cursor = forge.open_forge_content_cursor(request, inventory, path="src/main.py")
    first = forge.read_forge_content_page(reader, request, inventory, cursor)
    second = forge.read_forge_content_page(reader, request, inventory, first.next_cursor)
    assert len(first.content) == 1_000_000
    assert second.content == b"a"
    assert first.complete is False and second.complete is True
    assert not hasattr(first.next_cursor, "content")


def test_fca03a_max_file_completes_in_exactly_seventeen_bounded_pages():
    content = b"z" * forge.MAX_FORGE_FILE_BYTES
    request, inventory = _fca03a_world(content)
    reader = _fca03a_reader(content, inventory)
    cursor = forge.open_forge_content_cursor(request, inventory, path="src/main.py")
    sizes = []
    while cursor is not None:
        page = forge.read_forge_content_page(reader, request, inventory, cursor)
        sizes.append(len(page.content))
        cursor = page.next_cursor
    assert len(sizes) == 17
    assert sum(sizes) == forge.MAX_FORGE_FILE_BYTES
    assert max(sizes) <= forge.MAX_FORGE_CONTENT_PAGE_BYTES


def test_fca03a_file_and_page_max_plus_one_fail_closed():
    request, inventory = _fca03a_world(b"x")
    with pytest.raises(ForgeSnapshotError) as raised:
        forge.open_forge_content_cursor(
            request,
            inventory,
            path="src/main.py",
            page_bytes=forge.MAX_FORGE_CONTENT_PAGE_BYTES + 1,
        )
    assert raised.value.args == ("invalid_content_request",)
    assert raised.value.__cause__ is None
    with pytest.raises(ForgeSnapshotError):
        ForgeSnapshotFile("src/large.bin", "sha256:" + "0" * 64, forge.MAX_FORGE_FILE_BYTES + 1)


def test_fca03a_cursor_hash_binds_every_identity_offset_and_page_field():
    request, inventory = _fca03a_world(b"abcdef")
    first = forge.open_forge_content_cursor(request, inventory, path="src/main.py", page_bytes=2)
    second = forge.open_forge_content_cursor(request, inventory, path="src/main.py", page_bytes=3)
    assert first.cursor_hash != second.cursor_hash
    assert first.to_dict()["cursor_hash"] == first.cursor_hash
    assert "authorization_ref" not in json.dumps(first.to_dict())


def test_fca03a_cursor_cannot_rebind_owner_repo_revision_snapshot_path_digest_or_authority():
    request, inventory = _fca03a_world(b"abcdef")
    cursor = forge.open_forge_content_cursor(request, inventory, path="src/main.py")
    object.__setattr__(cursor, "owner_scope", "user:mallory")
    reader = _fca03a_reader(b"abcdef", inventory)
    with pytest.raises(ForgeSnapshotError) as raised:
        forge.read_forge_content_page(reader, request, inventory, cursor)
    assert raised.value.args == ("invalid_content_cursor",)


def test_fca03a_short_zero_progress_overlong_shifted_and_nonbytes_ranges_fail_closed():
    request, inventory = _fca03a_world(b"abcdef")
    cursor = forge.open_forge_content_cursor(request, inventory, path="src/main.py", page_bytes=4)

    class BadReader:
        def __init__(self, mode):
            self.mode = mode

        def read_exact_range(self, detached, *, path, offset, max_bytes):
            content = {
                "short": b"a",
                "zero": b"",
                "overlong": b"abcde",
                "shifted": b"abcd",
                "nonbytes": "abcd",
            }[self.mode]
            return forge.ForgeContentRange(
                schema=forge.FORGE_CONTENT_RANGE_SCHEMA_V2,
                owner_scope=detached.owner_scope,
                repo_id=detached.repo_id,
                version_id=detached.version_id,
                commit_sha=detached.commit_sha,
                snapshot_digest=inventory.snapshot_digest,
                path=path,
                file_content_sha256=inventory.file(path).content_sha256,
                file_byte_count=6,
                offset=offset + (1 if self.mode == "shifted" else 0),
                content=content,
                authority_binding=detached.authority_binding,
            )

    for mode in ("short", "zero", "overlong", "shifted", "nonbytes"):
        with pytest.raises(ForgeSnapshotError):
            forge.read_forge_content_page(BadReader(mode), request, inventory, cursor)


def test_fca03a_exact_builtin_capture_rejects_subclasses_without_callbacks():
    request, inventory = _fca03a_world(b"x")
    with pytest.raises(ForgeSnapshotError) as raised:
        forge.open_forge_content_cursor(request, inventory, path="src/main.py", page_bytes=EvilInt(1))
    assert raised.value.args == ("invalid_content_request",)
    assert raised.value.__cause__ is None


def test_fca03a_callback_mutation_and_post_validation_tamper_are_detached():
    request, inventory = _fca03a_world(b"abc")
    cursor = forge.open_forge_content_cursor(request, inventory, path="src/main.py", page_bytes=2)
    reader = _fca03a_reader(b"abc", inventory)
    page = forge.read_forge_content_page(reader, request, inventory, cursor)
    object.__setattr__(cursor, "next_offset", 99)
    assert page.source_cursor.next_offset == 0
    assert page.next_cursor.next_offset == 2


def test_fca03a_errors_are_fresh_bounded_content_free_and_suppress_context():
    request, inventory = _fca03a_world(b"abc")
    cursor = forge.open_forge_content_cursor(request, inventory, path="src/main.py")

    class ExplodingReader:
        def read_exact_range(self, *args, **kwargs):
            raise BaseException("PRIVATE_MARKER")

    errors = []
    for _ in range(2):
        with pytest.raises(ForgeSnapshotError) as raised:
            forge.read_forge_content_page(ExplodingReader(), request, inventory, cursor)
        errors.append(raised.value)
    assert errors[0] is not errors[1]
    assert errors[0].args == ("content_read_failed",)
    assert errors[0].__cause__ is None
    assert "PRIVATE_MARKER" not in str(errors[0])


def test_fca03a_static_diff_has_zero_effects_and_cursor_projection_has_zero_content():
    source = inspect.getsource(forge.open_forge_content_cursor) + inspect.getsource(
        forge.read_forge_content_page
    )
    for forbidden in ("subprocess", "pathlib", "open(", "socket", "requests", "provider"):
        assert forbidden not in source.lower()
    request, inventory = _fca03a_world(b"abc")
    cursor = forge.open_forge_content_cursor(request, inventory, path="src/main.py")
    projection = cursor.to_dict()
    dumped = json.dumps(projection)
    assert "authorization_ref" not in dumped
    assert "content" not in projection
    assert not any(type(value) is bytes for value in projection.values())


def _fca03a_page_values(page):
    return {
        "schema": page.schema,
        "source_cursor": page.source_cursor,
        "offset": page.offset,
        "content": page.content,
        "page_content_sha256": page.page_content_sha256,
        "complete": page.complete,
        "next_cursor": page.next_cursor,
        "page_hash": page.page_hash,
    }


def test_fca03a_r2_public_page_constructor_revalidates_exact_valid_output():
    request, inventory = _fca03a_world(b"abc")
    reader = _fca03a_reader(b"abc", inventory)
    cursor = forge.open_forge_content_cursor(
        request, inventory, path="src/main.py", page_bytes=2
    )
    page = forge.read_forge_content_page(reader, request, inventory, cursor)
    rebuilt = forge.ForgeContentPage(**_fca03a_page_values(page))
    assert rebuilt.to_dict() == page.to_dict()
    assert rebuilt.source_cursor is not page.source_cursor
    assert rebuilt.next_cursor is not page.next_cursor
    assert rebuilt.content is page.content


def test_fca03a_r2_page_rejects_each_invalid_field_with_fixed_content_free_error():
    request, inventory = _fca03a_world(b"abc")
    reader = _fca03a_reader(b"abc", inventory)
    cursor = forge.open_forge_content_cursor(
        request, inventory, path="src/main.py", page_bytes=2
    )
    page = forge.read_forge_content_page(reader, request, inventory, cursor)
    invalid = {
        "schema": "bad",
        "source_cursor": None,
        "offset": -1,
        "content": b"x",
        "page_content_sha256": "sha256:" + "0" * 64,
        "complete": 1,
        "next_cursor": None,
        "page_hash": "sha256:" + "0" * 64,
    }
    errors = []
    for field, value in invalid.items():
        values = _fca03a_page_values(page)
        values[field] = value
        with pytest.raises(ForgeSnapshotError) as raised:
            forge.ForgeContentPage(**values)
        errors.append(raised.value)
        assert raised.value.args == ("content_range_mismatch",)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
    assert len({id(error) for error in errors}) == len(errors)


def test_fca03a_r2_terminal_and_nonterminal_page_cursor_hash_matrix_is_exact():
    request, inventory = _fca03a_world(b"abc")
    reader = _fca03a_reader(b"abc", inventory)
    cursor = forge.open_forge_content_cursor(
        request, inventory, path="src/main.py", page_bytes=2
    )
    first = forge.read_forge_content_page(reader, request, inventory, cursor)
    final = forge.read_forge_content_page(reader, request, inventory, first.next_cursor)
    assert forge.ForgeContentPage(**_fca03a_page_values(first)).complete is False
    assert forge.ForgeContentPage(**_fca03a_page_values(final)).complete is True
    for page, field, value in (
        (first, "complete", True),
        (first, "next_cursor", None),
        (final, "complete", False),
        (final, "next_cursor", first.next_cursor),
    ):
        values = _fca03a_page_values(page)
        values[field] = value
        with pytest.raises(ForgeSnapshotError) as raised:
            forge.ForgeContentPage(**values)
        assert raised.value.args == ("content_range_mismatch",)


def test_fca03a_r2_page_constructor_rejects_hostile_subclasses_and_forgery_without_callbacks():
    request, inventory = _fca03a_world(b"abc")
    reader = _fca03a_reader(b"abc", inventory)
    cursor = forge.open_forge_content_cursor(
        request, inventory, path="src/main.py", page_bytes=2
    )
    page = forge.read_forge_content_page(reader, request, inventory, cursor)
    calls = []

    class HostileStr(str):
        def __eq__(self, other):
            calls.append("eq")
            raise BaseException("PRIVATE_MARKER")

        def __str__(self):
            calls.append("str")
            raise BaseException("PRIVATE_MARKER")

        def __repr__(self):
            calls.append("repr")
            raise BaseException("PRIVATE_MARKER")

    forged = object.__new__(forge.ForgeContentCursor)
    for field, value in (
        ("schema", HostileStr(forge.FORGE_CONTENT_PAGE_SCHEMA_V2)),
        ("offset", EvilInt(page.offset)),
        ("content", type("HostileBytes", (bytes,), {})(page.content)),
        ("source_cursor", forged),
    ):
        values = _fca03a_page_values(page)
        values[field] = value
        with pytest.raises(ForgeSnapshotError) as raised:
            forge.ForgeContentPage(**values)
        assert raised.value.args == ("content_range_mismatch",)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert "PRIVATE_MARKER" not in str(raised.value)
    assert calls == []


def test_fca03a_r2_page_projection_rejects_post_construction_tamper():
    request, inventory = _fca03a_world(b"abc")
    reader = _fca03a_reader(b"abc", inventory)
    cursor = forge.open_forge_content_cursor(
        request, inventory, path="src/main.py", page_bytes=2
    )
    tamper = {
        "schema": "bad",
        "source_cursor": object.__new__(forge.ForgeContentCursor),
        "offset": -1,
        "content": b"x",
        "page_content_sha256": "sha256:" + "0" * 64,
        "complete": True,
        "next_cursor": None,
        "page_hash": "sha256:" + "0" * 64,
    }
    errors = []
    for field, value in tamper.items():
        page = forge.read_forge_content_page(reader, request, inventory, cursor)
        object.__setattr__(page, field, value)
        with pytest.raises(ForgeSnapshotError) as raised:
            page.to_dict()
        errors.append(raised.value)
        assert raised.value.args == ("content_range_mismatch",)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
    assert len({id(error) for error in errors}) == len(errors)


def _fca03a_v3_page(content=b"abc", *, page_bytes=2):
    request, inventory = _fca03a_world(content)
    reader = _fca03a_reader(content, inventory)
    cursor = forge.open_forge_content_cursor(
        request, inventory, path="src/main.py", page_bytes=page_bytes
    )
    return forge.read_forge_content_page(reader, request, inventory, cursor)


def test_fca03a_v3_concrete_page_reconstructs_and_projects_detached():
    page = _fca03a_v3_page()
    rebuilt = forge.ForgeContentPage(**_fca03a_page_values(page))

    assert type(rebuilt) is forge.ForgeContentPage
    assert rebuilt.source_cursor is not page.source_cursor
    assert rebuilt.next_cursor is not page.next_cursor
    projection = rebuilt.to_dict()
    assert projection == page.to_dict()
    projection["source_cursor"]["next_offset"] = 99
    projection["next_cursor"]["next_offset"] = 99
    assert rebuilt.to_dict()["source_cursor"]["next_offset"] == 0
    assert rebuilt.to_dict()["next_cursor"]["next_offset"] == 2


def test_fca03a_v3_constructor_and_projection_have_zero_effects():
    source = inspect.getsource(forge.ForgeContentPage.__post_init__) + inspect.getsource(
        forge.ForgeContentPage.to_dict
    )
    for forbidden in (
        "subprocess",
        "pathlib",
        "open(",
        "socket",
        "requests",
        "provider",
        "thread",
        "process",
        "time.",
    ):
        assert forbidden not in source.lower()

    page = _fca03a_v3_page()
    rebuilt = forge.ForgeContentPage(**_fca03a_page_values(page))
    assert rebuilt.to_dict() == page.to_dict()


def test_fca03a_v3_forged_and_post_construction_tampered_pages_reject_at_projection():
    page = _fca03a_v3_page()
    hostile_values = {
        "schema": "bad",
        "source_cursor": object.__new__(forge.ForgeContentCursor),
        "offset": -1,
        "content": b"x",
        "page_content_sha256": "sha256:" + "0" * 64,
        "complete": True,
        "next_cursor": None,
        "page_hash": "sha256:" + "0" * 64,
    }

    forged = object.__new__(forge.ForgeContentPage)
    errors = []
    with pytest.raises(ForgeSnapshotError) as raised:
        forged.to_dict()
    errors.append(raised.value)

    for field, value in hostile_values.items():
        candidate = forge.ForgeContentPage(**_fca03a_page_values(page))
        object.__setattr__(candidate, field, value)
        with pytest.raises(ForgeSnapshotError) as raised:
            candidate.to_dict()
        errors.append(raised.value)

    assert all(error.args == ("content_range_mismatch",) for error in errors)
    assert all(error.__cause__ is None for error in errors)
    assert all(error.__context__ is None for error in errors)
    assert len({id(error) for error in errors}) == len(errors)


def test_fca03a_v3_inherited_cursor_terminal_and_page_hash_matrix_remains_exact():
    request, inventory = _fca03a_world(b"abc")
    reader = _fca03a_reader(b"abc", inventory)
    cursor = forge.open_forge_content_cursor(
        request, inventory, path="src/main.py", page_bytes=2
    )
    first = forge.read_forge_content_page(reader, request, inventory, cursor)
    final = forge.read_forge_content_page(reader, request, inventory, first.next_cursor)

    first_rebuilt = forge.ForgeContentPage(**_fca03a_page_values(first))
    final_rebuilt = forge.ForgeContentPage(**_fca03a_page_values(final))
    assert first_rebuilt.complete is False
    assert first_rebuilt.next_cursor.cursor_hash == first.next_cursor.cursor_hash
    assert first_rebuilt.page_hash == first.page_hash
    assert final_rebuilt.complete is True
    assert final_rebuilt.next_cursor is None
    assert final_rebuilt.page_hash == final.page_hash

    object.__setattr__(first_rebuilt.next_cursor, "next_offset", 3)
    with pytest.raises(ForgeSnapshotError) as raised:
        first_rebuilt.to_dict()
    assert raised.value.args == ("content_range_mismatch",)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_fca03a_v3_valid_field_page_subclass_rejects_without_callbacks():
    page = _fca03a_v3_page()
    calls = []

    class ValidFieldPageSubclass(forge.ForgeContentPage):
        def __eq__(self, other):
            calls.append("eq")
            raise BaseException("PRIVATE_MARKER")

        def __repr__(self):
            calls.append("repr")
            raise BaseException("PRIVATE_MARKER")

    errors = []
    with pytest.raises(ForgeSnapshotError) as raised:
        ValidFieldPageSubclass(**_fca03a_page_values(page))
    errors.append(raised.value)

    forged = object.__new__(ValidFieldPageSubclass)
    for field, value in _fca03a_page_values(page).items():
        object.__setattr__(forged, field, value)
    with pytest.raises(ForgeSnapshotError) as raised:
        forge.ForgeContentPage.to_dict(forged)
    errors.append(raised.value)

    assert calls == []
    assert all(error.args == ("content_range_mismatch",) for error in errors)
    assert all(error.__cause__ is None for error in errors)
    assert all(error.__context__ is None for error in errors)
