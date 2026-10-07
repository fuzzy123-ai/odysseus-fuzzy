from __future__ import annotations

import subprocess
import stat
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import pytest

from src.project_forge_local import LocalProjectForge
from src.project_version_store import ProjectVersionIntegrityError, StoredProjectVersion


OWNER_ID = "alice"
REPO_ID = "fastpath"
VERSION_ID = "pv_" + ("1" * 32)
ERROR_NOT_COMMIT = "local Forge Git object is not a commit"
ERROR_GIT_FAILED = "local Git command failed"
ERROR_GIT_EXECUTION = "local Git command could not be executed"
ERROR_REF_MISMATCH = "durable version ref does not match manifest commit"


def _command(arguments: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        input=input_text,
        shell=False,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return result


def _checked_command(arguments: list[str], *, input_text: str | None = None) -> str:
    result = _command(arguments, input_text=input_text)
    if result.returncode != 0:
        raise AssertionError("Git fixture command failed")
    return result.stdout.strip()


def _git(repository: Path, *arguments: str, input_text: str | None = None) -> str:
    return _checked_command(["git", "--git-dir", str(repository), *arguments], input_text=input_text)


def _initialize_repository(repository: Path, *, object_format: str | None = None) -> bool:
    repository.parent.mkdir(parents=True)
    arguments = ["git", "init", "--quiet", "--bare"]
    if object_format is not None:
        arguments.append(f"--object-format={object_format}")
    arguments.append(str(repository))
    return _command(arguments).returncode == 0


def _new_forge(tmp_path: Path, *, object_format: str | None = None) -> tuple[LocalProjectForge, Path]:
    forge = LocalProjectForge(root=tmp_path / "forge", source_roots=(tmp_path,))
    repository = forge.store.repository_path(owner_id=OWNER_ID, repo_id=REPO_ID)
    if not _initialize_repository(repository, object_format=object_format):
        if object_format == "sha256":
            pytest.skip("installed Git does not support SHA-256 repositories")
        raise AssertionError("Git fixture repository initialization failed")
    return forge, repository


def _tree(repository: Path) -> str:
    return _git(repository, "mktree", input_text="")


def _commit(repository: Path, *, message: str) -> str:
    return _git(
        repository,
        "-c",
        "user.name=Odysseus Test",
        "-c",
        "user.email=odysseus@example.invalid",
        "commit-tree",
        _tree(repository),
        "-m",
        message,
    )


def _blob(repository: Path, *, content: str = "payload\n") -> str:
    return _git(repository, "hash-object", "-w", "--stdin", input_text=content)


def _annotated_tag(repository: Path, *, name: str, target: str) -> str:
    _git(
        repository,
        "-c",
        "user.name=Odysseus Test",
        "-c",
        "user.email=odysseus@example.invalid",
        "tag",
        "-a",
        name,
        target,
        "-m",
        name,
    )
    return _git(repository, "rev-parse", f"refs/tags/{name}")


def _ref() -> str:
    return f"refs/odysseus/versions/{VERSION_ID}"


def _set_ref(repository: Path, target: str) -> None:
    _git(repository, "update-ref", _ref(), target)


def _corrupt_ref(repository: Path) -> None:
    ref_path = repository.joinpath(*_ref().split("/"))
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text("invalid-object-name\n", encoding="ascii")


def _stored(commit_sha: str) -> StoredProjectVersion:
    return StoredProjectVersion(
        manifest={"commit_sha": commit_sha, "version_id": VERSION_ID},
        manifest_sha256="sha256:" + ("0" * 64),
    )


def _old_two_command_verify(
    forge: LocalProjectForge,
    repository: Path,
    stored: StoredProjectVersion,
) -> None:
    object_type = forge._run_git(
        ("--git-dir", str(repository), "cat-file", "-t", stored.commit_sha),
        store_path=repository,
        integrity_check=True,
    ).stdout.strip()
    if object_type != "commit":
        raise ProjectVersionIntegrityError(ERROR_NOT_COMMIT)
    result = forge._run_git(
        ("--git-dir", str(repository), "rev-parse", "--verify", f"{_ref()}^{{commit}}"),
        store_path=repository,
        integrity_check=True,
    )
    if result.stdout.strip() != stored.commit_sha:
        raise ProjectVersionIntegrityError(ERROR_REF_MISMATCH)


def _new_verify(forge: LocalProjectForge, stored: StoredProjectVersion) -> None:
    forge._verify_git_version(owner_id=OWNER_ID, repo_id=REPO_ID, stored=stored)


def _outcome(operation: Callable[[], None]) -> tuple[str, str]:
    try:
        operation()
    except ProjectVersionIntegrityError as exc:
        return "error", str(exc)
    return "return", ""


def _assert_old_and_new(
    forge: LocalProjectForge,
    repository: Path,
    stored: StoredProjectVersion,
    expected: tuple[str, str],
) -> None:
    assert _outcome(lambda: _old_two_command_verify(forge, repository, stored)) == expected
    assert _outcome(lambda: _new_verify(forge, stored)) == expected


def _tracked_calls(forge: LocalProjectForge) -> tuple[list[tuple[str, ...]], Callable[..., object]]:
    calls: list[tuple[str, ...]] = []
    original = forge._run_git

    def tracked(arguments, **kwargs):
        calls.append(tuple(str(argument) for argument in arguments))
        if "--batch-check=%(objectname) %(objecttype)" in arguments:
            lines = kwargs["input_text"].split("\n")
            assert len(lines) == 3 and lines[-1] == ""
            assert lines[1] == f"{_ref()}^{{commit}}"
            assert len(lines[0]) in (40, 64)
            assert all(character in "0123456789abcdef" for character in lines[0])
        else:
            assert "input_text" not in kwargs
        return original(arguments, **kwargs)

    return calls, tracked


def test_direct_commit_healthy_verification_uses_one_git_process(tmp_path: Path) -> None:
    forge, repository = _new_forge(tmp_path)
    commit_sha = _commit(repository, message="direct")
    _set_ref(repository, commit_sha)
    calls, tracked = _tracked_calls(forge)

    with patch.object(forge, "_run_git", side_effect=tracked):
        _new_verify(forge, _stored(commit_sha))

    assert len(calls) == 1
    assert "--batch-check=%(objectname) %(objecttype)" in calls[0]


def test_annotated_tag_chain_peeling_to_manifest_commit_uses_one_git_process(tmp_path: Path) -> None:
    forge, repository = _new_forge(tmp_path)
    commit_sha = _commit(repository, message="tagged")
    inner_tag = _annotated_tag(repository, name="inner", target=commit_sha)
    outer_tag = _annotated_tag(repository, name="outer", target=inner_tag)
    _set_ref(repository, outer_tag)
    calls, tracked = _tracked_calls(forge)

    with patch.object(forge, "_run_git", side_effect=tracked):
        _new_verify(forge, _stored(commit_sha))

    assert len(calls) == 1
    assert "--batch-check=%(objectname) %(objecttype)" in calls[0]


@pytest.mark.parametrize("object_kind", ["blob", "tree", "tag"], ids=["blob", "tree", "tag"])
@pytest.mark.parametrize("ref_state", ["missing", "corrupt"], ids=["missing-ref", "corrupt-ref"])
def test_noncommit_manifest_object_keeps_old_error_for_bad_ref(
    tmp_path: Path,
    object_kind: str,
    ref_state: str,
) -> None:
    forge, repository = _new_forge(tmp_path)
    commit_sha = _commit(repository, message="tag target")
    if object_kind == "blob":
        manifest_oid = _blob(repository)
    elif object_kind == "tree":
        manifest_oid = _tree(repository)
    else:
        manifest_oid = _annotated_tag(repository, name="manifest-tag", target=commit_sha)
    if ref_state == "corrupt":
        _corrupt_ref(repository)

    _assert_old_and_new(
        forge,
        repository,
        _stored(manifest_oid),
        ("error", ERROR_NOT_COMMIT),
    )


@pytest.mark.parametrize(
    "ref_state",
    ["missing", "corrupt", "noncommit"],
    ids=["missing-ref", "corrupt-ref", "noncommit-ref"],
)
def test_valid_manifest_commit_keeps_old_generic_error_for_bad_ref(tmp_path: Path, ref_state: str) -> None:
    forge, repository = _new_forge(tmp_path)
    commit_sha = _commit(repository, message="manifest")
    if ref_state == "corrupt":
        _corrupt_ref(repository)
    elif ref_state == "noncommit":
        _set_ref(repository, _blob(repository))

    _assert_old_and_new(
        forge,
        repository,
        _stored(commit_sha),
        ("error", ERROR_GIT_FAILED),
    )


@pytest.mark.parametrize("target_kind", ["commit", "tag"], ids=["changed-commit", "changed-tag"])
def test_changed_commit_or_tag_keeps_old_mismatch_error(tmp_path: Path, target_kind: str) -> None:
    forge, repository = _new_forge(tmp_path)
    manifest_commit = _commit(repository, message="manifest")
    changed_commit = _commit(repository, message="changed")
    target = changed_commit
    if target_kind == "tag":
        target = _annotated_tag(repository, name="changed-tag", target=changed_commit)
    _set_ref(repository, target)

    _assert_old_and_new(
        forge,
        repository,
        _stored(manifest_commit),
        ("error", ERROR_REF_MISMATCH),
    )


@pytest.mark.parametrize("object_state", ["missing", "corrupt"], ids=["missing-object", "corrupt-object"])
def test_missing_or_corrupt_manifest_commit_keeps_old_generic_error(tmp_path: Path, object_state: str) -> None:
    forge, repository = _new_forge(tmp_path)
    if object_state == "missing":
        manifest_commit = "f" * 40
    else:
        manifest_commit = _commit(repository, message="corrupt")
        _set_ref(repository, manifest_commit)
        object_path = repository / "objects" / manifest_commit[:2] / manifest_commit[2:]
        # Git creates loose objects read-only on Windows; corrupt only this fixture.
        object_path.chmod(stat.S_IREAD | stat.S_IWRITE)
        object_path.write_bytes(b"corrupt")

    _assert_old_and_new(
        forge,
        repository,
        _stored(manifest_commit),
        ("error", ERROR_GIT_FAILED),
    )


@pytest.mark.parametrize("replacement_kind", ["commit", "blob"], ids=["same-type", "cross-type"])
def test_replace_ref_semantics_match_old_two_command_oracle(tmp_path: Path, replacement_kind: str) -> None:
    forge, repository = _new_forge(tmp_path)
    manifest_commit = _commit(repository, message="manifest")
    _set_ref(repository, manifest_commit)
    replacement = (
        _commit(repository, message="replacement")
        if replacement_kind == "commit"
        else _blob(repository, content="replacement\n")
    )
    _git(repository, "update-ref", f"refs/replace/{manifest_commit}", replacement)
    expected = ("return", "") if replacement_kind == "commit" else ("error", ERROR_NOT_COMMIT)

    _assert_old_and_new(forge, repository, _stored(manifest_commit), expected)


def test_sha256_repository_healthy_verification_uses_one_git_process_when_supported(tmp_path: Path) -> None:
    forge, repository = _new_forge(tmp_path, object_format="sha256")
    commit_sha = _commit(repository, message="sha256")
    assert len(commit_sha) == 64
    _set_ref(repository, commit_sha)
    calls, tracked = _tracked_calls(forge)

    with patch.object(forge, "_run_git", side_effect=tracked):
        _new_verify(forge, _stored(commit_sha))

    assert len(calls) == 1
    assert "--batch-check=%(objectname) %(objecttype)" in calls[0]


@pytest.mark.parametrize("manifest_kind", ["commit", "blob"], ids=["valid-commit", "noncommit-object"])
def test_rev_parse_execution_failure_keeps_old_classification_without_raw_cause(
    tmp_path: Path,
    manifest_kind: str,
) -> None:
    forge, repository = _new_forge(tmp_path)
    manifest_oid = (
        _commit(repository, message="manifest")
        if manifest_kind == "commit"
        else _blob(repository)
    )
    if manifest_kind == "commit":
        _set_ref(repository, manifest_oid)
    original = forge._run_git

    def fail_rev_parse(arguments, **kwargs):
        if "--batch-check=%(objectname) %(objecttype)" in arguments:
            raise ProjectVersionIntegrityError(ERROR_GIT_EXECUTION)
        if "rev-parse" in arguments:
            try:
                raise OSError("private fixture path")
            except OSError as cause:
                raise ProjectVersionIntegrityError(ERROR_GIT_EXECUTION) from cause
        return original(arguments, **kwargs)

    expected_message = ERROR_GIT_EXECUTION if manifest_kind == "commit" else ERROR_NOT_COMMIT
    expected = ("error", expected_message)
    with patch.object(forge, "_run_git", side_effect=fail_rev_parse):
        old_outcome = _outcome(lambda: _old_two_command_verify(forge, repository, _stored(manifest_oid)))
        new_outcome = _outcome(lambda: _new_verify(forge, _stored(manifest_oid)))

    assert old_outcome == expected
    assert new_outcome == expected
    assert "private fixture path" not in new_outcome[1]


def test_repeated_verification_rechecks_git_without_cache(tmp_path: Path) -> None:
    forge, repository = _new_forge(tmp_path)
    manifest_commit = _commit(repository, message="manifest")
    changed_commit = _commit(repository, message="changed")
    _set_ref(repository, manifest_commit)
    calls, tracked = _tracked_calls(forge)

    with patch.object(forge, "_run_git", side_effect=tracked):
        _new_verify(forge, _stored(manifest_commit))
        _set_ref(repository, changed_commit)
        assert _outcome(lambda: _new_verify(forge, _stored(manifest_commit))) == (
            "error",
            ERROR_REF_MISMATCH,
        )

    assert len(calls) == 4
    assert sum("rev-parse" in call for call in calls) == 1
    assert sum("--batch-check=%(objectname) %(objecttype)" in call for call in calls) == 2
    assert sum("-t" in call for call in calls) == 1


@pytest.mark.parametrize(
    "probe_kind",
    [
        "empty", "short", "extra", "noncommit", "malformed", "different-oid",
        "nonzero", "exception", "crlf", "nul", "nonascii", "trailing-space",
        "missing-final-lf", "bytes", "bool-returncode", "string-subclass", "changing-getters",
    ],
)
def test_untrusted_batch_result_uses_original_checks_in_order(tmp_path: Path, probe_kind: str) -> None:
    forge, repository = _new_forge(tmp_path)
    commit_sha = _commit(repository, message="batch fallback")
    _set_ref(repository, commit_sha)
    expected = f"{commit_sha} commit\n{commit_sha} commit\n"

    class SpoofEqual(str):
        def __eq__(self, other):
            raise AssertionError("a non-exact string must not be compared")

    class AlwaysEqual(str):
        def __eq__(self, other):
            return True

    class ChangingResult:
        def __init__(self):
            self.returncode_reads = 0
            self.stdout_reads = 0

        @property
        def returncode(self):
            self.returncode_reads += 1
            return 1 if self.returncode_reads == 1 else False

        @property
        def stdout(self):
            self.stdout_reads += 1
            return "invalid\n" if self.stdout_reads == 1 else AlwaysEqual("invalid")

    changing_result = ChangingResult()

    outputs = {
        "empty": "",
        "short": f"{commit_sha} commit\n",
        "extra": expected + f"{commit_sha} commit\n",
        "noncommit": expected.replace("commit", "blob", 1),
        "malformed": expected.replace(" commit", "  commit", 1),
        "different-oid": ("0" if commit_sha[0] != "0" else "1") + expected[1:],
        "crlf": expected.replace("\n", "\r\n"),
        "nul": expected + "\0",
        "nonascii": expected + "\N{SNOWMAN}",
        "trailing-space": expected.replace("\n", " \n", 1),
        "missing-final-lf": expected[:-1],
        "bytes": expected.encode("ascii"),
        "string-subclass": SpoofEqual(expected),
    }
    calls = []
    original = forge._run_git

    def injected(arguments, **kwargs):
        calls.append(tuple(arguments))
        if "--batch-check=%(objectname) %(objecttype)" in arguments:
            assert kwargs["input_text"] == f"{commit_sha}\n{_ref()}^{{commit}}\n"
            assert kwargs["allow_failure"] is True and kwargs["integrity_check"] is True
            if probe_kind == "exception":
                raise OSError("private diagnostic fixture cause")
            if probe_kind == "changing-getters":
                return changing_result
            return subprocess.CompletedProcess(
                arguments,
                False if probe_kind == "bool-returncode" else 1 if probe_kind == "nonzero" else 0,
                outputs.get(probe_kind, expected),
                "",
            )
        assert "input_text" not in kwargs and kwargs["integrity_check"] is True
        return original(arguments, **kwargs)

    with patch.object(forge, "_run_git", side_effect=injected):
        assert _outcome(lambda: _new_verify(forge, _stored(commit_sha))) == ("return", "")

    assert len(calls) == 3
    assert "--batch-check=%(objectname) %(objecttype)" in calls[0]
    assert calls[1][-3:] == ("cat-file", "-t", commit_sha)
    assert calls[2][-3:] == ("rev-parse", "--verify", f"{_ref()}^{{commit}}")
    if probe_kind == "changing-getters":
        assert changing_result.returncode_reads == changing_result.stdout_reads == 1
