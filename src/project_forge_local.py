"""Offline local Forge backed by an owner-scoped bare Git repository."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from src.project_forge_contract import validate_persisted_text
from src.project_version_store import (
    ProjectVersionConflictError,
    ProjectVersionIntegrityError,
    ProjectVersionStore,
    ProjectVersionStoreError,
    StoredProjectVersion,
    validate_commit_sha,
    validate_repo_id,
    validate_version_id,
)


class LocalProjectForgeError(ProjectVersionStoreError):
    """Raised when a local Git source or bare Forge operation is unsafe."""


class LocalProjectForge:
    """Securely retain existing local commits before any provider dispatch."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        source_roots: Iterable[str | Path] | None = None,
        git_executable: str = "git",
        store: ProjectVersionStore | None = None,
    ) -> None:
        if store is not None and (root is not None or clock is not None):
            raise LocalProjectForgeError("root and clock must be configured on the injected store")
        self.store = store or ProjectVersionStore(root=root, clock=clock)
        roots = tuple(source_roots) if source_roots is not None else (Path.cwd(),)
        if not roots:
            raise LocalProjectForgeError("at least one authorized source root is required")
        self.source_roots = tuple(Path(path).expanduser().resolve(strict=False) for path in roots)
        executable = str(git_executable or "").strip()
        if not executable or any(character in executable for character in ("\x00", "\r", "\n")):
            raise LocalProjectForgeError("git_executable is invalid")
        self.git_executable = executable

    def store_commit(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        source_repo: str | Path,
        commit_sha: Any,
        idempotency_key: Any,
        policy_snapshot: Mapping[str, Any] | None = None,
        version_label: Any = "",
        change_notes: Iterable[Any] = (),
        artifacts: Iterable[Mapping[str, Any]] = (),
    ) -> StoredProjectVersion:
        """Store an existing source commit and its immutable local manifest.

        A completed idempotent replay verifies and returns local evidence without
        touching the ephemeral source repository again.
        """

        repo = validate_repo_id(repo_id)
        commit = validate_commit_sha(commit_sha)
        notes = list(change_notes)
        artifact_records = [dict(item) for item in artifacts]
        policy = dict(policy_snapshot or {})
        metadata = self.store.normalize_version_metadata(
            version_label=version_label,
            change_notes=notes,
            policy_snapshot=policy,
            artifacts=artifact_records,
        )
        label = metadata["version_label"]
        notes = metadata["change_notes"]
        policy = metadata["policy_snapshot"]
        artifact_records = metadata["artifacts"]
        request_payload = {
            "commit_sha": commit,
            "version_label": label,
            "change_notes": notes,
            "policy_snapshot": policy,
            "artifacts": artifact_records,
        }
        reservation = self.store.reserve_version(
            owner_id=owner_id,
            repo_id=repo,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
        )
        if reservation.replay:
            stored = self.store.verify_version(
                owner_id=owner_id,
                repo_id=repo,
                version_id=reservation.version_id,
            )
            self._verify_git_version(owner_id=owner_id, repo_id=repo, stored=stored)
            return stored

        try:
            repository = self.store.repository_path(owner_id=owner_id, repo_id=repo)
            if repository.exists():
                self._ensure_bare_repository(repository)
                if self._retained_commit_exists(
                    repository=repository,
                    commit_sha=commit,
                    version_id=reservation.version_id,
                ):
                    stored = self.store.persist_version(
                        reservation=reservation,
                        commit_sha=commit,
                        policy_snapshot=policy,
                        version_label=label,
                        change_notes=notes,
                        artifacts=artifact_records,
                    )
                    self._verify_git_version(owner_id=owner_id, repo_id=repo, stored=stored)
                    return stored
            source = self._resolve_source_repo(source_repo)
            self._verify_source_commit(source=source, commit_sha=commit)
            self._ensure_bare_repository(repository)
            self._retain_commit(
                repository=repository,
                source=source,
                commit_sha=commit,
                version_id=reservation.version_id,
            )
            return self.store.persist_version(
                reservation=reservation,
                commit_sha=commit,
                policy_snapshot=policy,
                version_label=label,
                change_notes=notes,
                artifacts=artifact_records,
            )
        except (ProjectVersionConflictError, ProjectVersionIntegrityError):
            raise
        except Exception:
            self.store.mark_failed(reservation=reservation, failure_code="local_git_store_failed")
            raise

    # Explicit alias used by orchestration code that already created the commit.
    store_existing_commit = store_commit

    def verify_version(self, *, owner_id: Any, repo_id: Any, version_id: Any) -> StoredProjectVersion:
        repo = validate_repo_id(repo_id)
        version = validate_version_id(version_id)
        stored = self.store.verify_version(owner_id=owner_id, repo_id=repo, version_id=version)
        self._verify_git_version(owner_id=owner_id, repo_id=repo, stored=stored)
        return stored

    def _resolve_source_repo(self, value: str | Path) -> Path:
        raw = str(value or "").strip()
        if not raw or "\x00" in raw or "://" in raw or re.match(r"^[^/\\\s]+@[^:\s]+:", raw):
            raise LocalProjectForgeError("source_repo must be a local filesystem path")
        source = Path(value).expanduser().resolve(strict=True)
        if not source.is_dir():
            raise LocalProjectForgeError("source_repo must be a local directory")
        self._assert_authorized_source(source)
        self._validate_git_directory_pointer(source)
        return source

    def _validate_git_directory_pointer(self, source: Path) -> None:
        dot_git = source / ".git"
        if dot_git.is_dir():
            self._assert_authorized_source(dot_git.resolve(strict=True))
            return
        if dot_git.is_file():
            if dot_git.stat().st_size > 4096:
                raise LocalProjectForgeError("source Git directory pointer is invalid")
            first_line = dot_git.read_text(encoding="utf-8").splitlines()[0].strip()
            if not first_line.lower().startswith("gitdir:"):
                raise LocalProjectForgeError("source Git directory pointer is invalid")
            target_text = first_line.split(":", 1)[1].strip()
            target = Path(target_text)
            if not target.is_absolute():
                target = dot_git.parent / target
            target = target.resolve(strict=True)
            if not target.is_dir():
                raise LocalProjectForgeError("source Git directory pointer is invalid")
            self._assert_authorized_source(target)
            return
        if (source / "HEAD").is_file() and (source / "objects").is_dir():
            # A local bare repository is a valid source as long as it is scoped.
            return
        raise LocalProjectForgeError("source_repo must be a local Git repository")

    def _verify_source_commit(self, *, source: Path, commit_sha: str) -> None:
        result = self._run_git(
            ("-C", str(source), "rev-parse", "--verify", f"{commit_sha}^{{commit}}"),
            source_path=source,
        )
        if result.stdout.strip() != commit_sha:
            raise LocalProjectForgeError("source commit does not resolve to the requested object id")
        object_type = self._run_git(
            ("-C", str(source), "cat-file", "-t", commit_sha),
            source_path=source,
        ).stdout.strip()
        if object_type != "commit":
            raise LocalProjectForgeError("source object is not a commit")

    def _ensure_bare_repository(self, repository: Path) -> None:
        repository = self._assert_store_path(repository)
        repository.parent.mkdir(parents=True, exist_ok=True)
        self._assert_store_path(repository.parent)
        if not repository.exists():
            self._run_git(("init", "--bare", str(repository)), store_path=repository)
        if not repository.is_dir():
            raise LocalProjectForgeError("local Forge repository path is not a directory")
        result = self._run_git(
            ("--git-dir", str(repository), "rev-parse", "--is-bare-repository"),
            store_path=repository,
        )
        if result.stdout.strip() != "true":
            raise LocalProjectForgeError("local Forge repository is not bare")

    def _retain_commit(self, *, repository: Path, source: Path, commit_sha: str, version_id: str) -> None:
        repository = self._assert_store_path(repository)
        self._assert_authorized_source(source)
        version = validate_version_id(version_id)
        if self._retained_commit_exists(repository=repository, commit_sha=commit_sha, version_id=version):
            return
        ref = f"refs/odysseus/versions/{version}"

        self._run_git(
            (
                "--git-dir",
                str(repository),
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                str(source),
                commit_sha,
            ),
            source_path=source,
            store_path=repository,
        )
        object_type = self._run_git(
            ("--git-dir", str(repository), "cat-file", "-t", commit_sha),
            store_path=repository,
        ).stdout.strip()
        if object_type != "commit":
            raise LocalProjectForgeError("fetched object is not a commit")
        zero_object_id = "0" * len(commit_sha)
        self._run_git(
            ("--git-dir", str(repository), "update-ref", ref, commit_sha, zero_object_id),
            store_path=repository,
        )

    def _retained_commit_exists(self, *, repository: Path, commit_sha: str, version_id: str) -> bool:
        repository = self._assert_store_path(repository)
        commit = validate_commit_sha(commit_sha)
        version = validate_version_id(version_id)
        ref = f"refs/odysseus/versions/{version}"
        existing = self._run_git(
            ("--git-dir", str(repository), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"),
            store_path=repository,
            allow_failure=True,
        )
        if existing.returncode != 0:
            return False
        if existing.stdout.strip() != commit:
            raise ProjectVersionConflictError("durable version ref already targets another commit")
        object_type = self._run_git(
            ("--git-dir", str(repository), "cat-file", "-t", commit),
            store_path=repository,
        ).stdout.strip()
        if object_type != "commit":
            raise ProjectVersionIntegrityError("durable version ref does not target a commit")
        return True

    def _verify_git_version(
        self,
        *,
        owner_id: Any,
        repo_id: str,
        stored: StoredProjectVersion,
    ) -> None:
        repository = self.store.repository_path(owner_id=owner_id, repo_id=repo_id)
        repository = self._assert_store_path(repository)
        if not repository.is_dir():
            raise ProjectVersionIntegrityError("local Forge bare repository is missing")
        commit_sha = validate_commit_sha(stored.commit_sha)
        object_type = self._run_git(
            ("--git-dir", str(repository), "cat-file", "-t", commit_sha),
            store_path=repository,
            integrity_check=True,
        ).stdout.strip()
        if object_type != "commit":
            raise ProjectVersionIntegrityError("local Forge Git object is not a commit")
        ref = f"refs/odysseus/versions/{validate_version_id(stored.version_id)}"
        result = self._run_git(
            ("--git-dir", str(repository), "rev-parse", "--verify", f"{ref}^{{commit}}"),
            store_path=repository,
            integrity_check=True,
        )
        if result.stdout.strip() != commit_sha:
            raise ProjectVersionIntegrityError("durable version ref does not match manifest commit")

    def _run_git(
        self,
        arguments: Sequence[str],
        *,
        source_path: Path | None = None,
        store_path: Path | None = None,
        allow_failure: bool = False,
        integrity_check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if source_path is not None:
            self._assert_authorized_source(source_path.resolve(strict=True))
        if store_path is not None:
            self._assert_store_path(store_path)
        command = [
            self.git_executable,
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.file.allow=always",
            *[str(argument) for argument in arguments],
        ]
        try:
            result = subprocess.run(
                command,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_git_environment(),
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            error_type = ProjectVersionIntegrityError if integrity_check else LocalProjectForgeError
            raise error_type("local Git command could not be executed") from exc
        if result.returncode != 0 and not allow_failure:
            error_type = ProjectVersionIntegrityError if integrity_check else LocalProjectForgeError
            raise error_type("local Git command failed")
        return result

    def _assert_authorized_source(self, path: Path) -> Path:
        resolved = path.expanduser().resolve(strict=False)
        for root in self.source_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise LocalProjectForgeError("source_repo is outside authorized local roots")

    def _assert_store_path(self, path: Path) -> Path:
        resolved = path.expanduser().resolve(strict=False)
        try:
            resolved.relative_to(self.store.root)
        except ValueError as exc:
            raise LocalProjectForgeError("local Forge path escapes configured storage root") from exc
        return resolved


# Conventional alias for callers that name the provider before the noun.
ProjectForgeLocal = LocalProjectForge


def _git_environment() -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "COMSPEC", "HOME", "USERPROFILE", "TMP", "TEMP"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_ASKPASS"] = ""
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    return environment
