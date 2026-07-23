"""Persistable project policy for provider-neutral Forge dispatch."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping

from core.atomic_io import atomic_write_json
from src.project_forge_contract import FORGE_PROVIDERS, ProjectForgeContractError
from src.constants import DATA_DIR
from src.project_version_store import owner_key_for, validate_repo_id


PROJECT_FORGE_POLICY_SCHEMA = "odysseus.project_forge_policy.v1"
FORGE_MODES = FORGE_PROVIDERS
BACKUP_PROVIDERS = ("nextcloud",)
NEXTCLOUD_MIRROR_SCOPES = (
    "named_versions_and_releases",
    "named_versions",
    "releases",
    "all_versions",
)


class ProjectForgePolicyError(ProjectForgeContractError):
    """Raised when persisted Forge policy is invalid or unsafe."""


class ProjectForgePolicyStore:
    """Owner-scoped policy persistence with a safe implicit local default."""

    def __init__(self, *, root: str | Path | None = None) -> None:
        configured = Path(root) if root is not None else Path(DATA_DIR) / "project_forge_policies"
        self.root = configured.expanduser().resolve(strict=False)

    def load_policy(self, *, owner_id: Any, repo_id: Any) -> "ProjectForgePolicy":
        path = self._path(owner_id=owner_id, repo_id=repo_id)
        if not path.exists():
            return ProjectForgePolicy(forge_mode="local")
        if not path.is_file():
            raise ProjectForgePolicyError("project Forge policy path is invalid")
        try:
            return ProjectForgePolicy.load_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectForgePolicyError("project Forge policy could not be loaded") from exc

    def save_policy(
        self,
        *,
        owner_id: Any,
        repo_id: Any,
        policy: "ProjectForgePolicy",
    ) -> "ProjectForgePolicy":
        if not isinstance(policy, ProjectForgePolicy):
            raise ProjectForgePolicyError("policy must be a ProjectForgePolicy")
        path = self._path(owner_id=owner_id, repo_id=repo_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        policy.save_json(path)
        return self.load_policy(owner_id=owner_id, repo_id=repo_id)

    def _path(self, *, owner_id: Any, repo_id: Any) -> Path:
        owner = owner_key_for(owner_id)
        repo = validate_repo_id(repo_id)
        path = (self.root / owner / f"{repo}.json").resolve(strict=False)
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ProjectForgePolicyError("project Forge policy path escapes its store") from exc
        return path


@dataclass(frozen=True, slots=True)
class NextcloudForgePolicy:
    """Readable Nextcloud mirror policy; no client-side encryption in v1."""

    mirror_scope: str = "named_versions_and_releases"
    include_readable_tree: bool = True
    include_artifacts: bool = True
    include_git_bundle: bool = True
    client_side_encryption: bool = False

    def __post_init__(self) -> None:
        mirror_scope = _choice(self.mirror_scope, field_name="nextcloud.mirror_scope", choices=NEXTCLOUD_MIRROR_SCOPES)
        include_readable_tree = _bool(self.include_readable_tree, field_name="nextcloud.include_readable_tree")
        include_artifacts = _bool(self.include_artifacts, field_name="nextcloud.include_artifacts")
        include_git_bundle = _bool(self.include_git_bundle, field_name="nextcloud.include_git_bundle")
        client_side_encryption = _bool(self.client_side_encryption, field_name="nextcloud.client_side_encryption")
        if client_side_encryption:
            raise ProjectForgePolicyError("nextcloud.client_side_encryption must remain false for readable v1 mirrors")
        if not include_readable_tree:
            raise ProjectForgePolicyError("nextcloud.include_readable_tree must remain true for a readable Forge mirror")
        object.__setattr__(self, "mirror_scope", mirror_scope)
        object.__setattr__(self, "include_readable_tree", include_readable_tree)
        object.__setattr__(self, "include_artifacts", include_artifacts)
        object.__setattr__(self, "include_git_bundle", include_git_bundle)
        object.__setattr__(self, "client_side_encryption", client_side_encryption)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mirror_scope": self.mirror_scope,
            "include_readable_tree": self.include_readable_tree,
            "include_artifacts": self.include_artifacts,
            "include_git_bundle": self.include_git_bundle,
            "client_side_encryption": self.client_side_encryption,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NextcloudForgePolicy":
        allowed = {
            "mirror_scope",
            "include_readable_tree",
            "include_artifacts",
            "include_git_bundle",
            "client_side_encryption",
        }
        data = _mapping(payload, field_name="nextcloud", allowed=allowed)
        return cls(
            mirror_scope=data.get("mirror_scope", "named_versions_and_releases"),
            include_readable_tree=data.get("include_readable_tree", True),
            include_artifacts=data.get("include_artifacts", True),
            include_git_bundle=data.get("include_git_bundle", True),
            client_side_encryption=data.get("client_side_encryption", False),
        )


@dataclass(frozen=True, slots=True)
class GitHubForgePolicy:
    """Non-force native GitHub branch-sync policy."""

    push_branch: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "push_branch", _bool(self.push_branch, field_name="github.push_branch"))

    def to_dict(self) -> dict[str, Any]:
        return {"push_branch": self.push_branch}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GitHubForgePolicy":
        data = _mapping(payload, field_name="github", allowed={"push_branch"})
        return cls(push_branch=data.get("push_branch", True))


# Conventional spelling for callers that prefer ``Github`` over ``GitHub``.
GithubForgePolicy = GitHubForgePolicy


@dataclass(frozen=True, slots=True)
class ProjectForgePolicy:
    """The sole source of provider selection for project commits."""

    SCHEMA: ClassVar[str] = PROJECT_FORGE_POLICY_SCHEMA

    forge_mode: str = "local"
    sync_on_commit: bool = True
    backup_providers: tuple[str, ...] = ()
    nextcloud: NextcloudForgePolicy = field(default_factory=NextcloudForgePolicy)
    github: GitHubForgePolicy = field(default_factory=GitHubForgePolicy)

    def __post_init__(self) -> None:
        forge_mode = _choice(self.forge_mode, field_name="forge_mode", choices=FORGE_MODES)
        sync_on_commit = _bool(self.sync_on_commit, field_name="sync_on_commit")
        backup_providers = _backup_providers(self.backup_providers)
        if forge_mode != "github" and backup_providers:
            raise ProjectForgePolicyError("backup_providers are only supported when forge_mode is github")
        if not isinstance(self.nextcloud, NextcloudForgePolicy):
            raise ProjectForgePolicyError("nextcloud must be a NextcloudForgePolicy")
        if not isinstance(self.github, GitHubForgePolicy):
            raise ProjectForgePolicyError("github must be a GitHubForgePolicy")
        object.__setattr__(self, "forge_mode", forge_mode)
        object.__setattr__(self, "sync_on_commit", sync_on_commit)
        object.__setattr__(self, "backup_providers", backup_providers)

    @property
    def schema(self) -> str:
        return self.SCHEMA

    @property
    def configured_providers(self) -> tuple[str, ...]:
        """All configured providers, with the canonical local Forge first."""

        providers = ["local"]
        if self.forge_mode != "local":
            providers.append(self.forge_mode)
        providers.extend(provider for provider in self.backup_providers if provider not in providers)
        return tuple(providers)

    @property
    def sync_targets(self) -> tuple[str, ...]:
        """External commit targets selected by this persisted policy."""

        if not self.sync_on_commit or self.forge_mode == "local":
            return ()
        targets = [self.forge_mode]
        targets.extend(provider for provider in self.backup_providers if provider not in targets)
        return tuple(targets)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "forge_mode": self.forge_mode,
            "sync_on_commit": self.sync_on_commit,
            "backup_providers": list(self.backup_providers),
            "nextcloud": self.nextcloud.to_dict(),
            "github": self.github.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProjectForgePolicy":
        allowed = {"schema", "forge_mode", "sync_on_commit", "backup_providers", "nextcloud", "github"}
        data = _mapping(payload, field_name="project Forge policy", allowed=allowed)
        if _required(data, "schema") != cls.SCHEMA:
            raise ProjectForgePolicyError(f"schema must be {cls.SCHEMA}")
        raw_backups = data.get("backup_providers", [])
        if not isinstance(raw_backups, list):
            raise ProjectForgePolicyError("backup_providers must be a list")
        nextcloud_payload = data.get("nextcloud", {})
        github_payload = data.get("github", {})
        return cls(
            forge_mode=data.get("forge_mode", "local"),
            sync_on_commit=data.get("sync_on_commit", True),
            backup_providers=tuple(raw_backups),
            nextcloud=NextcloudForgePolicy.from_dict(nextcloud_payload),
            github=GitHubForgePolicy.from_dict(github_payload),
        )

    def save_json(self, path: str | Path) -> None:
        atomic_write_json(str(path), self.to_dict(), indent=2)

    @classmethod
    def load_json(cls, path: str | Path) -> "ProjectForgePolicy":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def resolve_commit_providers(policy: ProjectForgePolicy) -> tuple[str, ...]:
    """Resolve external commit targets from a loaded policy and nothing else."""

    if not isinstance(policy, ProjectForgePolicy):
        raise ProjectForgePolicyError("policy must be a loaded ProjectForgePolicy")
    return policy.sync_targets


def _backup_providers(values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ProjectForgePolicyError("backup_providers must be a list")
    providers: list[str] = []
    for value in values:
        provider = _choice(value, field_name="backup_provider", choices=BACKUP_PROVIDERS)
        if provider not in providers:
            providers.append(provider)
    return tuple(providers)


def _choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = " ".join(str(value or "").strip().split()).lower().replace("-", "_")
    if text not in choices:
        raise ProjectForgePolicyError(f"unsupported {field_name}: {value!r}")
    return text


def _bool(value: Any, *, field_name: str) -> bool:
    if type(value) is not bool:
        raise ProjectForgePolicyError(f"{field_name} must be a boolean")
    return value


def _mapping(payload: Mapping[str, Any], *, field_name: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProjectForgePolicyError(f"{field_name} must be a mapping")
    data = dict(payload)
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProjectForgePolicyError(f"{field_name} contains unknown fields: {', '.join(unknown)}")
    return data


def _required(payload: Mapping[str, Any], key: str) -> Any:
    if key not in payload:
        raise ProjectForgePolicyError(f"missing required field: {key}")
    return payload[key]
