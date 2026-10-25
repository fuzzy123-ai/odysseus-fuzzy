"""Planning Definition v2 read models and canonical durable revision authority."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Mapping
import weakref

from src.harbour_authority import (
    AuthenticatedPrincipalIssuer,
    HarbourAuthorityError,
    PrincipalRef,
    TrustedPrincipal,
    authenticate_principal,
    require_owner,
)

from src.planning_definition_projection import (
    ORIGIN_STATES,
    PlanningDefinitionProjectionError,
    PlanningDefinitionProjector,
    origin_metadata,
)
from src.planning_definition_contract import (
    PlanningDefinitionContractError,
    compute_roadmap_content_hash,
    validate_planning_definition,
)


PAGE_SCHEMA_ID = "odysseus.planning.definition_page.v2"
PROJECT_READ_SCHEMA_ID = "odysseus.planning.project_read.v2"
MAX_CURSOR_CHARS = 1_024
TEMPORARY_REPOSITORY_MARKER = ".odysseus-planning-temporary-repository"
TEMPORARY_REPOSITORY_SCHEMA = "odysseus.planning.temporary_repository.v1"
DRAFT_STATE_SCHEMA = "odysseus.planning.draft_state.v1"
OWNER_SOURCE_SNAPSHOT_SCHEMA_ID = "odysseus.planning.owner_source_snapshot.v1"
OWNER_SOURCE_SELECTION_MODE = "latest_approved"
MAX_OWNER_SOURCE_PROJECTS = 100
MAX_OWNER_SOURCE_REVISIONS = 1_000
MAX_OWNER_SOURCE_ROADMAPS = 100
MAX_OWNER_SOURCE_METADATA_BYTES = 65_536
_CHANGE_FIELDS = frozenset(
    {
        "title",
        "objective",
        "assumptions",
        "constraints",
        "nodes",
        "edges",
        "gates",
        "done_contract",
        "source_refs",
    }
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_IMMUTABLE_ADMIN_SUBJECT_RE = re.compile(r"^owner_[0-9a-f]{32}$")
_REPOSITORY_LOCKS_GUARD = threading.Lock()
_REPOSITORY_LOCKS: dict[str, threading.RLock] = {}
_PLANNING_VALIDATION_PAIR_MAX_ENTRIES = 1_024
_PLANNING_VALIDATION_PAIR_MAX_BYTES = 64 * 1024 * 1024


class PlanningRevisionStoreError(ValueError):
    def __init__(self, code: str, detail: str, *, origin_state: str = "error") -> None:
        self.code = code
        self.detail = detail
        self.origin_state = origin_state if origin_state in ORIGIN_STATES else "error"
        super().__init__(f"{code}: {detail}")


class PlanningRevisionStore:
    """Immutable in-process index built from validated definition snapshots."""

    def __init__(
        self,
        records: Iterable[tuple[str, Mapping[str, Any], str]] = (),
        *,
        projector: PlanningDefinitionProjector | None = None,
        cursor_secret: bytes | str = b"planning-definition-read-model-v2",
        origin_state: str = "live",
        origin_reason: str = "definition_snapshot_loaded",
        trusted_head_bindings: Iterable[Mapping[str, Any]] | None = None,
    ) -> None:
        self._projector = projector or PlanningDefinitionProjector()
        self._cursor_secret = _cursor_secret(cursor_secret)
        if origin_state not in ORIGIN_STATES:
            raise PlanningRevisionStoreError("invalid_origin_state", "origin state is not supported")
        self._origin_state = origin_state
        self._origin_reason = _bounded_reason(origin_reason)
        self._projects: dict[str, dict[str, dict[str, Any]]] = {}
        self._revisions: dict[str, dict[tuple[str, str, int], dict[str, Any]]] = {}
        self._sources: dict[str, dict[tuple[str, str, int], str]] = {}
        for owner, payload, source_ref in records:
            self._ingest(owner, payload, source_ref=source_ref)
        if trusted_head_bindings is not None:
            self._apply_trusted_head_bindings(trusted_head_bindings)
        self._verify_merged_references()

    @classmethod
    def from_directory(
        cls,
        root: str | Path,
        *,
        owner: str,
        cursor_secret: bytes | str = b"planning-definition-read-model-v2",
    ) -> "PlanningRevisionStore":
        directory = Path(root).expanduser().resolve(strict=False)
        if not directory.is_dir():
            return cls(
                (),
                cursor_secret=cursor_secret,
                origin_state="unavailable",
                origin_reason="definition_directory_unavailable",
            )
        records: list[tuple[str, Mapping[str, Any], str]] = []
        for path in sorted(directory.glob("*.json"), key=lambda item: item.name.lower()):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, Mapping):
                records.append((owner, payload, path.name))
        state = "live" if records else "unavailable"
        reason = "definition_snapshot_loaded" if records else "definition_directory_empty"
        return cls(
            records,
            cursor_secret=cursor_secret,
            origin_state=state,
            origin_reason=reason,
        )

    def list_projects(
        self,
        owner: str,
        *,
        cursor: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        owner_key = _owner(owner)
        projects = self._projects.get(owner_key, {})
        items = [self._project_summary(owner_key, projects[key]) for key in sorted(projects)]
        return self._page(owner_key, "projects", items, cursor=cursor, limit=limit)

    def snapshot_owner_sources(self, owner: str) -> dict[str, Any]:
        """Return the bounded approved source bindings for one known owner."""

        owner_key = _snapshot_owner(owner)
        if (
            owner_key not in self._projects
            or owner_key not in self._revisions
            or owner_key not in self._sources
        ):
            raise PlanningRevisionStoreError(
                "owner_not_found",
                "Planning owner was not found",
            )
        projects = self._projects[owner_key]
        revisions = self._revisions[owner_key]
        sources = self._sources[owner_key]
        try:
            project_count = len(projects)
            revision_count = len(revisions)
            source_count = len(sources)
        except Exception as exc:
            raise PlanningRevisionStoreError(
                "owner_source_unavailable",
                "Planning owner source metadata is unavailable",
            ) from exc
        if (
            project_count > MAX_OWNER_SOURCE_PROJECTS
            or revision_count > MAX_OWNER_SOURCE_REVISIONS
            or source_count > MAX_OWNER_SOURCE_REVISIONS
        ):
            raise PlanningRevisionStoreError(
                "owner_source_limit_exceeded",
                "Planning owner source metadata exceeds its limit",
            )
        if source_count != revision_count:
            raise PlanningRevisionStoreError(
                "owner_source_unavailable",
                "Planning owner source metadata is unavailable",
            )

        project_rows: list[tuple[str, list[Any], dict[Any, Any]]] = []
        declared_count = 0
        try:
            for stored_project_id, project in projects.items():
                if type(project) is not dict:
                    _raise_owner_source_integrity()
                project_id = _snapshot_identifier(stored_project_id)
                if project.get("project_id") != project_id:
                    _raise_owner_source_integrity()
                roadmap_refs = project.get("roadmap_refs")
                approved = project.get("latest_approved_revision")
                if type(roadmap_refs) is not list or type(approved) is not dict:
                    _raise_owner_source_integrity()
                roadmap_count = len(roadmap_refs)
                approved_count = len(approved)
                if (
                    roadmap_count > MAX_OWNER_SOURCE_ROADMAPS
                    or approved_count > roadmap_count
                ):
                    raise PlanningRevisionStoreError(
                        "owner_source_limit_exceeded",
                        "Planning owner source metadata exceeds its limit",
                    )
                declared_count += roadmap_count
                if declared_count > MAX_OWNER_SOURCE_ROADMAPS:
                    raise PlanningRevisionStoreError(
                        "owner_source_limit_exceeded",
                        "Planning owner source metadata exceeds its limit",
                    )
                project_rows.append((project_id, roadmap_refs, approved))
        except PlanningRevisionStoreError:
            raise
        except Exception as exc:
            raise PlanningRevisionStoreError(
                "owner_source_unavailable",
                "Planning owner source metadata is unavailable",
            ) from exc

        eligible: list[dict[str, Any]] = []
        ineligible: list[dict[str, Any]] = []
        try:
            for project_id, roadmap_refs, approved in project_rows:
                seen: set[str] = set()
                matched_pointers = 0
                for raw_roadmap_id in roadmap_refs:
                    roadmap_id = _snapshot_identifier(raw_roadmap_id)
                    if roadmap_id in seen:
                        _raise_owner_source_integrity()
                    seen.add(roadmap_id)
                    missing_pointer = object()
                    pointer = approved.get(roadmap_id, missing_pointer)
                    if pointer is missing_pointer:
                        ineligible.append(
                            {
                                "project_id": project_id,
                                "roadmap_id": roadmap_id,
                                "eligibility": "ineligible_no_approved_revision",
                                "revision": None,
                                "revision_state": "",
                                "content_hash": "",
                                "updated_at": "",
                                "source_ref": "",
                            }
                        )
                        continue
                    matched_pointers += 1
                    if type(pointer) is not dict:
                        _raise_owner_source_integrity()
                    revision = _snapshot_revision(pointer.get("revision"))
                    content_hash = _snapshot_content_hash(pointer.get("content_hash"))
                    key = (project_id, roadmap_id, revision)
                    roadmap = revisions.get(key)
                    source_ref = sources.get(key)
                    if type(roadmap) is not dict:
                        _raise_owner_source_integrity()
                    if (
                        roadmap.get("project_id") != project_id
                        or roadmap.get("roadmap_id") != roadmap_id
                        or _snapshot_revision(roadmap.get("revision")) != revision
                        or roadmap.get("revision_state") != "approved"
                        or _snapshot_content_hash(roadmap.get("content_hash")) != content_hash
                    ):
                        _raise_owner_source_integrity()
                    eligible.append(
                        {
                            "project_id": project_id,
                            "roadmap_id": roadmap_id,
                            "eligibility": "eligible_approved",
                            "revision": revision,
                            "revision_state": "approved",
                            "content_hash": content_hash,
                            "updated_at": _snapshot_timestamp(roadmap.get("updated_at")),
                            "source_ref": _snapshot_source_ref(source_ref),
                        }
                    )
                if matched_pointers != len(approved):
                    _raise_owner_source_integrity()
        except PlanningRevisionStoreError:
            raise
        except Exception as exc:
            raise PlanningRevisionStoreError(
                "owner_source_unavailable",
                "Planning owner source metadata is unavailable",
            ) from exc

        eligible.sort(
            key=lambda item: (
                item["project_id"],
                item["roadmap_id"],
                item["revision"],
            )
        )
        ineligible.sort(key=lambda item: (item["project_id"], item["roadmap_id"]))
        manifest = {
            "schema_id": OWNER_SOURCE_SNAPSHOT_SCHEMA_ID,
            "owner": owner_key,
            "selection_mode": OWNER_SOURCE_SELECTION_MODE,
            "eligible": eligible,
            "ineligible": ineligible,
        }
        try:
            canonical = json.dumps(
                manifest,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise PlanningRevisionStoreError(
                "owner_source_unavailable",
                "Planning owner source metadata is unavailable",
            ) from exc
        if len(canonical) > MAX_OWNER_SOURCE_METADATA_BYTES:
            raise PlanningRevisionStoreError(
                "owner_source_limit_exceeded",
                "Planning owner source metadata exceeds its limit",
            )
        return {
            **manifest,
            "eligible_count": len(eligible),
            "ineligible_count": len(ineligible),
            "snapshot_digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        }

    def get_project(self, owner: str, project_id: str) -> dict[str, Any]:
        owner_key = _owner(owner)
        project = self._project(owner_key, project_id)
        roadmap_summaries = self._roadmap_summaries(owner_key, project_id)
        return {
            "schema": PROJECT_READ_SCHEMA_ID,
            "project": deepcopy(project),
            "roadmaps": roadmap_summaries,
            "origin": self._origin(as_of=_project_as_of(owner_key, project_id, self._revisions)),
            "read_only": True,
            "launch_authorized": False,
        }

    def list_roadmaps(
        self,
        owner: str,
        project_id: str,
        *,
        cursor: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        owner_key = _owner(owner)
        self._project(owner_key, project_id)
        project_key = _identifier(project_id, "project_id")
        items = self._roadmap_summaries(owner_key, project_key)
        return self._page(
            owner_key,
            f"project:{project_key}:roadmaps",
            items,
            cursor=cursor,
            limit=limit,
        )

    def get_roadmap(
        self,
        owner: str,
        project_id: str,
        roadmap_id: str,
        *,
        revision: str | int = "latest_approved",
    ) -> dict[str, Any]:
        owner_key = _owner(owner)
        project = self._project(owner_key, project_id)
        project_key = str(project["project_id"])
        roadmap_key = _identifier(roadmap_id, "roadmap_id")
        selected_revision = self._resolve_revision(project, roadmap_key, revision)
        key = (project_key, roadmap_key, selected_revision)
        roadmap = self._revisions.get(owner_key, {}).get(key)
        if roadmap is None:
            raise PlanningRevisionStoreError("revision_not_found", "Planning revision was not found")
        return self._projector.project_revision(
            project=project,
            roadmap=roadmap,
            origin_state=self._origin_state,
            origin_reason=self._origin_reason,
        )

    def list_revisions(
        self,
        owner: str,
        project_id: str,
        roadmap_id: str,
        *,
        cursor: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        owner_key = _owner(owner)
        project = self._project(owner_key, project_id)
        project_key = str(project["project_id"])
        roadmap_key = _identifier(roadmap_id, "roadmap_id")
        items = [
            _revision_summary(roadmap)
            for (candidate_project, candidate_roadmap, _revision), roadmap in sorted(
                self._revisions.get(owner_key, {}).items(),
                key=lambda item: item[0],
            )
            if candidate_project == project_key and candidate_roadmap == roadmap_key
        ]
        if not items:
            raise PlanningRevisionStoreError("roadmap_not_found", "Planning roadmap was not found")
        return self._page(
            owner_key,
            f"project:{project_key}:roadmap:{roadmap_key}:revisions",
            items,
            cursor=cursor,
            limit=limit,
        )

    def _ingest(self, owner: str, payload: Mapping[str, Any], *, source_ref: str) -> None:
        owner_key = _owner(owner)
        try:
            document = self._projector.normalize_document(payload, source_ref=source_ref)
        except PlanningDefinitionProjectionError as exc:
            raise PlanningRevisionStoreError(exc.code, exc.detail) from exc
        project = document["project"]
        project_id = project["project_id"]
        owner_revisions = self._revisions.setdefault(owner_key, {})
        owner_sources = self._sources.setdefault(owner_key, {})
        for roadmap in document["roadmaps"]:
            key = (project_id, roadmap["roadmap_id"], roadmap["revision"])
            existing = owner_revisions.get(key)
            if existing is not None and self._projector.canonical_bytes(existing) != self._projector.canonical_bytes(roadmap):
                raise PlanningRevisionStoreError(
                    "revision_conflict", "one owner has conflicting content for a Planning revision"
                )
        owner_projects = self._projects.setdefault(owner_key, {})
        if project_id in owner_projects:
            owner_projects[project_id] = _merge_project(owner_projects[project_id], project)
        else:
            owner_projects[project_id] = deepcopy(project)
        for roadmap in document["roadmaps"]:
            key = (project_id, roadmap["roadmap_id"], roadmap["revision"])
            owner_revisions[key] = deepcopy(roadmap)
            owner_sources[key] = _safe_source_ref(source_ref)

    def _verify_merged_references(self) -> None:
        for owner, projects in self._projects.items():
            revisions = self._revisions.get(owner, {})
            for project_id, project in projects.items():
                available_ids = {
                    roadmap_id
                    for candidate_project, roadmap_id, _revision in revisions
                    if candidate_project == project_id
                }
                if set(project["roadmap_refs"]) != available_ids:
                    raise PlanningRevisionStoreError(
                        "project_reference_conflict",
                        "project roadmap references do not match the available revisions",
                    )
                for roadmap_id, reference in project["latest_approved_revision"].items():
                    target = revisions.get((project_id, roadmap_id, reference["revision"]))
                    if target is None or target["content_hash"] != reference["content_hash"]:
                        raise PlanningRevisionStoreError(
                            "project_reference_conflict",
                            "latest approved reference does not resolve",
                        )

    def _apply_trusted_head_bindings(
        self, raw_bindings: Iterable[Mapping[str, Any]]
    ) -> None:
        """Apply detached durable head metadata to this read-only projection."""
        legacy_fields = {
            "owner_id", "project_id", "roadmap_id",
            "accepted_revision_id", "accepted_revision", "accepted_content_sha256",
            "current_revision_id", "current_revision", "current_content_sha256",
            "approved_revision_id", "approved_revision", "approved_content_sha256",
        }
        augmented_fields = legacy_fields | {"generation"}
        seen: set[tuple[str, str, str]] = set()
        try:
            if type(raw_bindings) not in (list, tuple):
                raise ValueError
            if len(raw_bindings) > MAX_OWNER_SOURCE_ROADMAPS:
                raise ValueError
            bindings = tuple(raw_bindings)
            for binding in bindings:
                if type(binding) is not dict or set(binding) not in (
                    legacy_fields, augmented_fields,
                ):
                    raise ValueError
                owner = _snapshot_owner(binding["owner_id"])
                project_id = _snapshot_identifier(binding["project_id"])
                roadmap_id = _snapshot_identifier(binding["roadmap_id"])
                key = (owner, project_id, roadmap_id)
                if key in seen:
                    raise ValueError
                seen.add(key)
                accepted_id = _snapshot_source_revision_id(binding["accepted_revision_id"])
                current_id = _snapshot_source_revision_id(binding["current_revision_id"])
                approved_id = _snapshot_source_revision_id(binding["approved_revision_id"])
                accepted_revision = _snapshot_revision(binding["accepted_revision"])
                current_revision = _snapshot_revision(binding["current_revision"])
                approved_revision = _snapshot_revision(binding["approved_revision"])
                if set(binding) == augmented_fields:
                    _snapshot_revision(binding["generation"])
                accepted_hash = _snapshot_content_hash(binding["accepted_content_sha256"])
                current_hash = _snapshot_content_hash(binding["current_content_sha256"])
                approved_hash = _snapshot_content_hash(binding["approved_content_sha256"])
                revisions = self._revisions.get(owner, {})
                sources = self._sources.get(owner, {})
                indexed = {
                    revision: roadmap
                    for (candidate_project, candidate_roadmap, revision), roadmap in revisions.items()
                    if candidate_project == project_id and candidate_roadmap == roadmap_id
                }
                if not indexed:
                    raise ValueError
                ordered = sorted(indexed)
                if (
                    len(ordered) > MAX_OWNER_SOURCE_REVISIONS
                    or accepted_revision != 1
                    or current_revision != len(ordered)
                    or any(revision != index + 1 for index, revision in enumerate(ordered))
                ):
                    raise ValueError
                by_source = {
                    _snapshot_durable_revision_ref(sources[(project_id, roadmap_id, revision)]): revision
                    for revision in ordered
                }
                if (
                    current_revision < accepted_revision
                    or by_source.get(accepted_id) != accepted_revision
                    or by_source.get(current_id) != current_revision
                    or by_source.get(approved_id) != approved_revision
                    or not accepted_revision <= approved_revision <= current_revision
                ):
                    raise ValueError
                accepted = indexed[accepted_revision]
                current = indexed[current_revision]
                approved = indexed[approved_revision]
                if (
                    accepted.get("content_hash") != accepted_hash
                    or current.get("content_hash") != current_hash
                    or approved.get("content_hash") != approved_hash
                    or approved.get("revision_state") != "approved"
                ):
                    raise ValueError
                project = self._projects[owner][project_id]
                if roadmap_id not in project["roadmap_refs"]:
                    raise ValueError
                project["latest_approved_revision"][roadmap_id] = {
                    "revision": approved_revision,
                    "content_hash": approved_hash,
                }
            indexed_subjects = {
                (owner, project_id, roadmap_id)
                for owner, projects in self._projects.items()
                for project_id, project in projects.items()
                for roadmap_id in project["roadmap_refs"]
                if any(
                    source.startswith("durable:")
                    for (candidate_project, candidate_roadmap, _revision), source
                    in self._sources.get(owner, {}).items()
                    if candidate_project == project_id and candidate_roadmap == roadmap_id
                )
            }
            if seen != indexed_subjects:
                raise ValueError
        except PlanningRevisionStoreError:
            raise
        except Exception:
            raise PlanningRevisionStoreError(
                "durable_head_binding_invalid",
                "durable Planning head bindings are invalid",
            ) from None

    def _project(self, owner_key: str, project_id: str) -> dict[str, Any]:
        key = _identifier(project_id, "project_id")
        project = self._projects.get(owner_key, {}).get(key)
        if project is None:
            raise PlanningRevisionStoreError("project_not_found", "Planning project was not found")
        return project

    def _project_summary(self, owner: str, project: Mapping[str, Any]) -> dict[str, Any]:
        project_id = str(project["project_id"])
        roadmaps = self._roadmap_summaries(owner, project_id)
        return {
            "project_id": project_id,
            "title": project["title"],
            "roadmap_count": len(roadmaps),
            "revision_count": sum(item["revision_count"] for item in roadmaps),
            "latest_updated_at": max((item["updated_at"] for item in roadmaps), default="1970-01-01T00:00:00Z"),
        }

    def _roadmap_summaries(self, owner: str, project_id: str) -> list[dict[str, Any]]:
        project = self._projects[owner][project_id]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for (candidate_project, roadmap_id, _revision), roadmap in self._revisions.get(owner, {}).items():
            if candidate_project == project_id:
                grouped.setdefault(roadmap_id, []).append(roadmap)
        items: list[dict[str, Any]] = []
        for roadmap_id in sorted(grouped):
            revisions = sorted(grouped[roadmap_id], key=lambda item: item["revision"])
            latest = project["latest_approved_revision"].get(roadmap_id)
            newest = revisions[-1]
            items.append(
                {
                    "project_id": project_id,
                    "roadmap_id": roadmap_id,
                    "title": newest["title"],
                    "revision_count": len(revisions),
                    "newest_revision": newest["revision"],
                    "newest_revision_state": newest["revision_state"],
                    "latest_approved_revision": latest["revision"] if latest else None,
                    "latest_approved_hash": latest["content_hash"] if latest else "",
                    "updated_at": newest["updated_at"],
                }
            )
        return items

    def _resolve_revision(
        self,
        project: Mapping[str, Any],
        roadmap_id: str,
        revision: str | int,
    ) -> int:
        if revision == "latest_approved":
            reference = project["latest_approved_revision"].get(roadmap_id)
            if reference is None:
                raise PlanningRevisionStoreError(
                    "approved_revision_not_found", "Roadmap has no approved revision"
                )
            return int(reference["revision"])
        if isinstance(revision, str) and revision.isdigit():
            revision = int(revision)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise PlanningRevisionStoreError(
                "invalid_revision", "revision must be latest_approved or a positive integer"
            )
        return revision

    def _page(
        self,
        owner: str,
        collection: str,
        items: list[dict[str, Any]],
        *,
        cursor: str,
        limit: int,
    ) -> dict[str, Any]:
        bounded_limit = _limit(limit)
        offset = self._decode_cursor(
            cursor,
            owner=owner,
            collection=collection,
            limit=bounded_limit,
        ) if cursor else 0
        page_items = deepcopy(items[offset : offset + bounded_limit])
        next_offset = offset + len(page_items)
        has_more = next_offset < len(items)
        return {
            "schema": PAGE_SCHEMA_ID,
            "items": page_items,
            "limit": bounded_limit,
            "has_more": has_more,
            "next_cursor": self._encode_cursor(
                owner=owner,
                collection=collection,
                offset=next_offset,
                limit=bounded_limit,
            ) if has_more else "",
            "origin": self._origin(as_of=_page_as_of(page_items)),
            "read_only": True,
            "raw_private_content_visible": False,
        }

    def _encode_cursor(self, *, owner: str, collection: str, offset: int, limit: int) -> str:
        body = {
            "v": 1,
            "owner": hashlib.sha256(owner.encode("utf-8")).hexdigest(),
            "collection": collection,
            "offset": offset,
            "limit": limit,
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        signature = hmac.new(self._cursor_secret, raw, hashlib.sha256).hexdigest()
        return f"v1.{token}.{signature}"

    def _decode_cursor(self, cursor: str, *, owner: str, collection: str, limit: int) -> int:
        if len(cursor) > MAX_CURSOR_CHARS or not re.fullmatch(
            r"v1\.[A-Za-z0-9_-]+\.[0-9a-f]{64}", cursor
        ):
            raise PlanningRevisionStoreError("invalid_cursor", "cursor is invalid")
        _version, token, signature = cursor.split(".")
        try:
            raw = base64.urlsafe_b64decode(token + ("=" * (-len(token) % 4)))
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise PlanningRevisionStoreError("invalid_cursor", "cursor is invalid") from exc
        expected = hmac.new(self._cursor_secret, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or not isinstance(body, Mapping):
            raise PlanningRevisionStoreError("invalid_cursor", "cursor is invalid")
        expected_body = {
            "v": 1,
            "owner": hashlib.sha256(owner.encode("utf-8")).hexdigest(),
            "collection": collection,
            "offset": body.get("offset"),
            "limit": limit,
        }
        if dict(body) != expected_body or isinstance(body.get("offset"), bool) or not isinstance(body.get("offset"), int) or body["offset"] < 0:
            raise PlanningRevisionStoreError("invalid_cursor", "cursor scope is invalid")
        return int(body["offset"])

    def _origin(self, *, as_of: str) -> dict[str, Any]:
        return origin_metadata(
            self._origin_state,
            source="planning_revision_store",
            reason=self._origin_reason,
            as_of=as_of,
        )


class PlanningRevisionRepositoryError(PlanningRevisionStoreError):
    def __init__(self, code: str, detail: str, *, origin_state: str = "live") -> None:
        super().__init__(code, detail, origin_state=origin_state)


class PlanningRevisionRepository:
    """Explicitly temporary write boundary for draft/apply acceptance tests.

    Production composition does not construct this class. The repository root
    must carry the exact temporary marker, and every source and sidecar write
    remains confined below that resolved root.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        owner: str,
        definition_file: str = "definition.json",
        cursor_secret: bytes | str = b"planning-definition-read-model-v2",
    ) -> None:
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise PlanningRevisionRepositoryError(
                "temporary_repository_required", "temporary repository root is invalid"
            )
        marker = self.root / TEMPORARY_REPOSITORY_MARKER
        try:
            marker_value = marker.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise PlanningRevisionRepositoryError(
                "planning_write_gate_required",
                "temporary repository marker is required",
            ) from exc
        if marker_value != TEMPORARY_REPOSITORY_SCHEMA:
            raise PlanningRevisionRepositoryError(
                "planning_write_gate_required", "temporary repository marker is invalid"
            )
        if Path(definition_file).name != definition_file or not definition_file.endswith(".json"):
            raise PlanningRevisionRepositoryError(
                "invalid_definition_file", "definition file must be one JSON file in the root"
            )
        self.owner = _owner(owner)
        self.definition_path = _confined(self.root, self.root / definition_file)
        if not self.definition_path.is_file():
            raise PlanningRevisionRepositoryError(
                "definition_source_unavailable", "definition source is unavailable", origin_state="unavailable"
            )
        self.state_path = _confined(self.root, self.root / ".planning-draft-state.json")
        self._projector = PlanningDefinitionProjector()
        self._cursor_secret = _cursor_secret(cursor_secret)
        self._lock = _repository_lock(self.root)
        self._document()

    def snapshot_store(self) -> PlanningRevisionStore:
        with self._lock:
            document = self._document()
            state = self._state()
            draft_refs = {
                item["draft_id"]: deepcopy(item)
                for item in document["project"].get("draft_refs", [])
            }
            for draft in state["drafts"].values():
                if not isinstance(draft, Mapping) or draft.get("status") not in {"open", "validated"}:
                    continue
                reference = {
                    "draft_id": draft["draft_id"],
                    "roadmap_id": draft["roadmap_id"],
                    "base_revision": draft["base_revision"],
                    "base_hash": draft["base_hash"],
                }
                prior = draft_refs.get(reference["draft_id"])
                if prior is not None and prior != reference:
                    raise PlanningRevisionRepositoryError(
                        "draft_reference_conflict", "draft reference conflicts with the definition"
                    )
                draft_refs[reference["draft_id"]] = reference
            document["project"]["draft_refs"] = [
                draft_refs[key] for key in sorted(draft_refs)
            ]
            validate_planning_definition(document)
            return PlanningRevisionStore(
                [(self.owner, document, self.definition_path.name)],
                cursor_secret=self._cursor_secret,
                origin_state="live",
                origin_reason="temporary_definition_readback",
            )

    def create_draft(
        self,
        owner: str,
        project_id: str,
        roadmap_id: str,
        *,
        base_revision: int,
        base_hash: str,
        idempotency_key: str,
        changes: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            self._require_owner(owner)
            project_key = _identifier(project_id, "project_id")
            roadmap_key = _identifier(roadmap_id, "roadmap_id")
            revision = _positive_revision(base_revision)
            content_hash = _definition_hash(base_hash)
            idem = _idempotency_key(idempotency_key)
            normalized_changes = _changes(changes)
            request = {
                "project_id": project_key,
                "roadmap_id": roadmap_key,
                "base_revision": revision,
                "base_hash": content_hash,
                "idempotency_key": idem,
                "changes": normalized_changes,
            }
            fingerprint = _payload_hash(request)
            state = self._state()
            replay = _idempotency_replay(state, f"create:{idem}", fingerprint)
            if replay is not None:
                return replay
            document = self._document()
            self._assert_base(document, project_key, roadmap_key, revision, content_hash)
            draft_id = "pd_" + hashlib.sha256(
                f"{self.owner}\0{project_key}\0{roadmap_key}\0{idem}".encode("utf-8")
            ).hexdigest()[:32]
            timestamp = _now()
            receipt = {
                "schema": "odysseus.planning.draft_receipt.v1",
                "status": "draft_created",
                "project_id": project_key,
                "roadmap_id": roadmap_key,
                "draft_id": draft_id,
                "draft_version": 1,
                "base_revision": revision,
                "base_hash": content_hash,
                "operation": normalized_changes["operation"],
                "changed_fields": sorted(normalized_changes["set"]),
                "source_mutated": False,
            }
            state["drafts"][draft_id] = {
                "draft_id": draft_id,
                "project_id": project_key,
                "roadmap_id": roadmap_key,
                "base_revision": revision,
                "base_hash": content_hash,
                "draft_version": 1,
                "status": "open",
                "changes": normalized_changes,
                "candidate_updated_at": timestamp,
                "validation": None,
            }
            _remember_idempotency(state, f"create:{idem}", fingerprint, receipt)
            self._save_state(state)
            return deepcopy(receipt)

    def validate_draft(
        self,
        owner: str,
        project_id: str,
        roadmap_id: str,
        draft_id: str,
        *,
        expected_draft_version: int,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_owner(owner)
            project_key = _identifier(project_id, "project_id")
            roadmap_key = _identifier(roadmap_id, "roadmap_id")
            expected = _positive_revision(expected_draft_version, field="expected_draft_version")
            state = self._state()
            draft = _draft(state, draft_id, project_key, roadmap_key)
            if draft["status"] == "validated" and expected == draft["draft_version"]:
                return deepcopy(draft["validation"]["receipt"])
            self._assert_draft_version(draft, expected)
            if draft["status"] != "open":
                raise PlanningRevisionRepositoryError(
                    "draft_not_open", "draft cannot be validated in its current state"
                )
            document = self._document()
            self._assert_base(
                document,
                project_key,
                roadmap_key,
                draft["base_revision"],
                draft["base_hash"],
            )
            candidate = self._candidate(document, draft)
            receipt = {
                "schema": "odysseus.planning.draft_validation.v1",
                "status": "valid",
                "project_id": project_key,
                "roadmap_id": roadmap_key,
                "draft_id": draft["draft_id"],
                "draft_version": draft["draft_version"] + 1,
                "operation": draft["changes"]["operation"],
                "candidate_revision": candidate["roadmap"]["revision"],
                "candidate_hash": candidate["roadmap"]["content_hash"],
                "changed_fields": candidate["changed_fields"],
                "source_mutated": False,
            }
            draft["draft_version"] += 1
            draft["status"] = "validated"
            draft["validation"] = {
                "candidate_hash": candidate["roadmap"]["content_hash"],
                "candidate_revision": candidate["roadmap"]["revision"],
                "receipt": receipt,
            }
            self._save_state(state)
            return deepcopy(receipt)

    def act_on_draft(
        self,
        owner: str,
        project_id: str,
        roadmap_id: str,
        draft_id: str,
        *,
        action: str,
        expected_draft_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_owner(owner)
            project_key = _identifier(project_id, "project_id")
            roadmap_key = _identifier(roadmap_id, "roadmap_id")
            draft_key = _draft_id(draft_id)
            action_key = str(action or "").strip().lower()
            if action_key not in {"accept", "discard"}:
                raise PlanningRevisionRepositoryError(
                    "invalid_draft_action", "action must be accept or discard"
                )
            expected = _positive_revision(expected_draft_version, field="expected_draft_version")
            idem = _idempotency_key(idempotency_key)
            request = {
                "project_id": project_key,
                "roadmap_id": roadmap_key,
                "draft_id": draft_key,
                "action": action_key,
                "expected_draft_version": expected,
                "idempotency_key": idem,
            }
            fingerprint = _payload_hash(request)
            state = self._state()
            replay = _idempotency_replay(state, f"action:{idem}", fingerprint)
            if replay is not None:
                return replay
            draft = _draft(state, draft_key, project_key, roadmap_key)
            self._assert_draft_version(draft, expected)
            if action_key == "discard":
                if draft["status"] not in {"open", "validated"}:
                    raise PlanningRevisionRepositoryError(
                        "draft_not_actionable", "draft cannot be discarded in its current state"
                    )
                draft["draft_version"] += 1
                draft["status"] = "discarded"
                receipt = {
                    "schema": "odysseus.planning.draft_action_receipt.v1",
                    "status": "discarded",
                    "project_id": project_key,
                    "roadmap_id": roadmap_key,
                    "draft_id": draft_key,
                    "draft_version": draft["draft_version"],
                    "source_mutated": False,
                }
                _remember_idempotency(state, f"action:{idem}", fingerprint, receipt)
                self._save_state(state)
                return deepcopy(receipt)
            if draft["status"] != "validated" or not isinstance(draft.get("validation"), Mapping):
                raise PlanningRevisionRepositoryError(
                    "draft_not_validated", "draft must be validated before acceptance"
                )
            document = self._document()
            self._assert_base(
                document,
                project_key,
                roadmap_key,
                draft["base_revision"],
                draft["base_hash"],
            )
            candidate = self._candidate(document, draft)
            if (
                candidate["roadmap"]["content_hash"] != draft["validation"]["candidate_hash"]
                or candidate["roadmap"]["revision"] != draft["validation"]["candidate_revision"]
            ):
                raise PlanningRevisionRepositoryError(
                    "draft_candidate_conflict", "validated candidate changed before acceptance"
                )
            receipt = self._apply_candidate(
                document,
                state,
                draft,
                candidate,
                action_idempotency=(f"action:{idem}", fingerprint),
            )
            return receipt

    def undo_apply(
        self,
        owner: str,
        undo_id: str,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_owner(owner)
            undo_key = _undo_id(undo_id)
            idem = _idempotency_key(idempotency_key)
            fingerprint = _payload_hash({"undo_id": undo_key, "idempotency_key": idem})
            state = self._state()
            replay = _idempotency_replay(state, f"undo:{idem}", fingerprint)
            if replay is not None:
                return replay
            undo = state["undo"].get(undo_key)
            if not isinstance(undo, Mapping):
                raise PlanningRevisionRepositoryError("undo_not_found", "undo receipt was not found")
            if undo.get("status") != "available":
                raise PlanningRevisionRepositoryError("undo_not_available", "undo is not available")
            document = self._document()
            project = document["project"]
            roadmaps = [
                item
                for item in document["roadmaps"]
                if item["roadmap_id"] == undo["roadmap_id"]
            ]
            newest = max(roadmaps, key=lambda item: item["revision"])
            if (
                newest["revision"] != undo["accepted_revision"]
                or newest["content_hash"] != undo["accepted_hash"]
            ):
                raise PlanningRevisionRepositoryError(
                    "undo_conflict", "a later revision prevents this undo"
                )
            previous = undo.get("previous_latest")
            if isinstance(previous, Mapping):
                project["latest_approved_revision"][undo["roadmap_id"]] = deepcopy(previous)
                restored_revision = previous["revision"]
                restored_hash = previous["content_hash"]
            else:
                project["latest_approved_revision"].pop(undo["roadmap_id"], None)
                restored_revision = None
                restored_hash = ""
            validate_planning_definition(document)
            before_bytes = self.definition_path.read_bytes()
            self._write_document_with_readback(document, expected_before=before_bytes)
            receipt = {
                "schema": "odysseus.planning.undo_receipt.v1",
                "status": "undone",
                "undo_id": undo_key,
                "project_id": undo["project_id"],
                "roadmap_id": undo["roadmap_id"],
                "accepted_revision_retained": undo["accepted_revision"],
                "accepted_hash_retained": undo["accepted_hash"],
                "restored_revision": restored_revision,
                "restored_hash": restored_hash,
            }
            undo["status"] = "consumed"
            undo["receipt"] = receipt
            _remember_idempotency(state, f"undo:{idem}", fingerprint, receipt)
            try:
                self._save_state(state)
            except Exception:
                _atomic_write_bytes(self.definition_path, before_bytes)
                raise
            return deepcopy(receipt)

    def _require_owner(self, owner: str) -> None:
        if not hmac.compare_digest(_owner(owner), self.owner):
            raise PlanningRevisionRepositoryError(
                "project_not_found", "Planning project was not found"
            )

    def _document(self) -> dict[str, Any]:
        try:
            raw = self.definition_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PlanningRevisionRepositoryError(
                "definition_source_unavailable",
                "definition source cannot be read",
                origin_state="unavailable",
            ) from exc
        try:
            return self._projector.normalize_document(payload, source_ref=self.definition_path.name)
        except PlanningDefinitionProjectionError as exc:
            raise PlanningRevisionRepositoryError(exc.code, exc.detail) from exc

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "schema": DRAFT_STATE_SCHEMA,
                "drafts": {},
                "idempotency": {},
                "undo": {},
            }
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PlanningRevisionRepositoryError(
                "draft_state_corrupt", "draft state cannot be read"
            ) from exc
        if not isinstance(value, Mapping) or set(value) != {"schema", "drafts", "idempotency", "undo"}:
            raise PlanningRevisionRepositoryError("draft_state_corrupt", "draft state is invalid")
        if value.get("schema") != DRAFT_STATE_SCHEMA or not all(
            isinstance(value.get(key), Mapping) for key in ("drafts", "idempotency", "undo")
        ):
            raise PlanningRevisionRepositoryError("draft_state_corrupt", "draft state is invalid")
        return deepcopy(dict(value))

    def _save_state(self, state: Mapping[str, Any]) -> None:
        _atomic_write_json(self.state_path, state)

    def _assert_base(
        self,
        document: Mapping[str, Any],
        project_id: str,
        roadmap_id: str,
        base_revision: int,
        base_hash: str,
    ) -> None:
        project = document["project"]
        if project["project_id"] != project_id or roadmap_id not in project["roadmap_refs"]:
            raise PlanningRevisionRepositoryError("roadmap_not_found", "Planning roadmap was not found")
        base = _current_base(document, roadmap_id)
        if base["revision"] != base_revision or base["content_hash"] != base_hash:
            raise PlanningRevisionRepositoryError(
                "base_revision_conflict", "base revision or hash no longer matches"
            )

    def _assert_draft_version(self, draft: Mapping[str, Any], expected: int) -> None:
        if draft.get("draft_version") != expected:
            raise PlanningRevisionRepositoryError(
                "draft_version_conflict", "expected draft version does not match"
            )

    def _candidate(
        self,
        document: Mapping[str, Any],
        draft: Mapping[str, Any],
    ) -> dict[str, Any]:
        roadmaps = [
            item for item in document["roadmaps"] if item["roadmap_id"] == draft["roadmap_id"]
        ]
        base = next(
            (
                item
                for item in roadmaps
                if item["revision"] == draft["base_revision"]
                and item["content_hash"] == draft["base_hash"]
            ),
            None,
        )
        if base is None:
            raise PlanningRevisionRepositoryError(
                "base_revision_conflict", "draft base revision no longer exists"
            )
        operation = draft["changes"]["operation"]
        if operation == "restore":
            target_revision = draft["changes"]["restore_revision"]
            source = next(
                (
                    item
                    for item in roadmaps
                    if item["revision"] == target_revision and item["revision_state"] == "approved"
                ),
                None,
            )
            if source is None:
                raise PlanningRevisionRepositoryError(
                    "restore_revision_invalid", "restore revision must name an approved revision"
                )
        else:
            source = base
        candidate = deepcopy(source)
        candidate["revision"] = max(item["revision"] for item in roadmaps) + 1
        candidate["created_at"] = draft["candidate_updated_at"]
        candidate["updated_at"] = draft["candidate_updated_at"]
        candidate["revision_state"] = "tombstoned" if operation == "tombstone" else "approved"
        for field, value in draft["changes"]["set"].items():
            candidate[field] = deepcopy(value)
        candidate["content_hash"] = compute_roadmap_content_hash(candidate)
        candidate_document = deepcopy(dict(document))
        candidate_document["roadmaps"].append(candidate)
        latest = candidate_document["project"]["latest_approved_revision"]
        previous_latest = deepcopy(latest.get(draft["roadmap_id"]))
        if operation == "tombstone":
            latest.pop(draft["roadmap_id"], None)
        else:
            latest[draft["roadmap_id"]] = {
                "revision": candidate["revision"],
                "content_hash": candidate["content_hash"],
            }
        try:
            validate_planning_definition(candidate_document)
        except PlanningDefinitionContractError as exc:
            raise PlanningRevisionRepositoryError(exc.reason_code, exc.detail) from exc
        return {
            "document": candidate_document,
            "roadmap": candidate,
            "previous_latest": previous_latest,
            "changed_fields": sorted(
                set(draft["changes"]["set"])
                | ({"revision_state"} if operation in {"tombstone", "restore"} else set())
            ),
        }

    def _apply_candidate(
        self,
        document: Mapping[str, Any],
        state: dict[str, Any],
        draft: dict[str, Any],
        candidate: Mapping[str, Any],
        *,
        action_idempotency: tuple[str, str],
    ) -> dict[str, Any]:
        before_bytes = self.definition_path.read_bytes()
        before_document_hash = _payload_hash(document)
        self._write_document_with_readback(candidate["document"], expected_before=before_bytes)
        accepted = candidate["roadmap"]
        undo_id = "pu_" + hashlib.sha256(
            f"{draft['draft_id']}\0{accepted['content_hash']}".encode("utf-8")
        ).hexdigest()[:32]
        receipt = {
            "schema": "odysseus.planning.draft_action_receipt.v1",
            "status": "accepted",
            "project_id": draft["project_id"],
            "roadmap_id": draft["roadmap_id"],
            "draft_id": draft["draft_id"],
            "draft_version": draft["draft_version"] + 1,
            "operation": draft["changes"]["operation"],
            "accepted_revision": accepted["revision"],
            "accepted_hash": accepted["content_hash"],
            "previous_latest_revision": candidate["previous_latest"]["revision"] if candidate["previous_latest"] else None,
            "previous_latest_hash": candidate["previous_latest"]["content_hash"] if candidate["previous_latest"] else "",
            "before_document_hash": before_document_hash,
            "after_document_hash": _payload_hash(candidate["document"]),
            "undo_id": undo_id,
            "readback_verified": True,
        }
        draft["draft_version"] += 1
        draft["status"] = "accepted"
        draft["acceptance"] = receipt
        state["undo"][undo_id] = {
            "status": "available",
            "project_id": draft["project_id"],
            "roadmap_id": draft["roadmap_id"],
            "accepted_revision": accepted["revision"],
            "accepted_hash": accepted["content_hash"],
            "previous_latest": deepcopy(candidate["previous_latest"]),
        }
        _remember_idempotency(state, action_idempotency[0], action_idempotency[1], receipt)
        try:
            self._save_state(state)
        except Exception:
            _atomic_write_bytes(self.definition_path, before_bytes)
            raise
        return deepcopy(receipt)

    def _write_document_with_readback(
        self,
        document: Mapping[str, Any],
        *,
        expected_before: bytes,
    ) -> None:
        current = self.definition_path.read_bytes()
        if not hmac.compare_digest(current, expected_before):
            raise PlanningRevisionRepositoryError(
                "source_write_conflict", "definition source changed before atomic apply"
            )
        encoded = (
            json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        _atomic_write_bytes(self.definition_path, encoded)
        try:
            readback = self._document()
            if _payload_hash(readback) != _payload_hash(document):
                raise PlanningRevisionRepositoryError(
                    "post_write_readback_failed", "definition readback differs from applied content"
                )
        except Exception:
            _atomic_write_bytes(self.definition_path, expected_before)
            raise


def _merge_project(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    immutable_fields = ("project_id", "title", "objective", "scope", "constraints")
    if any(existing.get(field) != incoming.get(field) for field in immutable_fields):
        raise PlanningRevisionStoreError(
            "project_conflict", "project metadata differs across revision documents"
        )
    merged = deepcopy(dict(existing))
    merged["roadmap_refs"] = sorted(set(existing["roadmap_refs"]) | set(incoming["roadmap_refs"]))
    latest = {**deepcopy(existing["latest_approved_revision"])}
    for roadmap_id, reference in incoming["latest_approved_revision"].items():
        prior = latest.get(roadmap_id)
        if prior is None or reference["revision"] > prior["revision"]:
            latest[roadmap_id] = deepcopy(reference)
        elif reference["revision"] == prior["revision"] and reference["content_hash"] != prior["content_hash"]:
            raise PlanningRevisionStoreError(
                "project_conflict", "approved revision hash differs across documents"
            )
    merged["latest_approved_revision"] = latest
    drafts = {
        item["draft_id"]: deepcopy(item)
        for item in [*existing["draft_refs"], *incoming["draft_refs"]]
    }
    merged["draft_refs"] = [drafts[key] for key in sorted(drafts)]
    return merged


def _owner(value: Any) -> str:
    owner = str(value or "").strip()
    if not owner or len(owner) > 320 or any(ord(character) < 32 for character in owner):
        raise PlanningRevisionStoreError("owner_required", "authenticated owner is required")
    return owner


def _identifier(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
        raise PlanningRevisionStoreError("invalid_identifier", f"{field} is invalid")
    return text


def _limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise PlanningRevisionStoreError("invalid_limit", "limit must be between 1 and 100")
    return value


def _cursor_secret(value: bytes | str) -> bytes:
    secret = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(secret, bytes) or len(secret) < 16:
        raise PlanningRevisionStoreError("invalid_cursor_secret", "cursor secret must be at least 16 bytes")
    return secret


def _bounded_reason(value: Any) -> str:
    reason = str(value or "").strip()
    if not reason or len(reason) > 160:
        raise PlanningRevisionStoreError("invalid_origin_reason", "origin reason is invalid")
    return reason


def _safe_source_ref(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text) or ".." in text.split("/"):
        return ""
    return text[:240]


def _snapshot_owner(value: Any) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 128
        or any(ord(character) < 32 for character in value)
    ):
        raise PlanningRevisionStoreError(
            "owner_source_invalid_request",
            "Planning owner source request is invalid",
        )
    return value


def _snapshot_identifier(value: Any) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _raise_owner_source_integrity()
    return value


def _snapshot_revision(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= (2**63 - 1):
        _raise_owner_source_integrity()
    return value


def _snapshot_source_revision_id(value: Any) -> str:
    if (
        type(value) is not str
        or not value.startswith("planning-revision:")
        or len(value) > 256
        or not value.removeprefix("planning-revision:")
        or any(ord(character) < 33 for character in value)
    ):
        _raise_owner_source_integrity()
    return value


def _snapshot_durable_revision_ref(value: Any) -> str:
    if type(value) is not str or not value.startswith("durable:"):
        _raise_owner_source_integrity()
    return _snapshot_source_revision_id(value.removeprefix("durable:"))


def _snapshot_content_hash(value: Any) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        _raise_owner_source_integrity()
    return value


def _snapshot_timestamp(value: Any) -> str:
    if type(value) is not str or not value or len(value) > 64 or value != value.strip():
        _raise_owner_source_integrity()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _raise_owner_source_integrity()
    if parsed.tzinfo is None:
        _raise_owner_source_integrity()
    return value


def _snapshot_source_ref(value: Any) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 240
        or value != value.strip()
        or "\\" in value
        or value.startswith(("/", "~"))
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(character) < 32 for character in value)
    ):
        _raise_owner_source_integrity()
    return value


def _raise_owner_source_integrity() -> None:
    raise PlanningRevisionStoreError(
        "owner_source_integrity_error",
        "Planning owner source metadata failed integrity validation",
    )


def _revision_summary(roadmap: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "project_id": roadmap["project_id"],
        "roadmap_id": roadmap["roadmap_id"],
        "revision": roadmap["revision"],
        "content_hash": roadmap["content_hash"],
        "revision_state": roadmap["revision_state"],
        "title": roadmap["title"],
        "created_at": roadmap["created_at"],
        "updated_at": roadmap["updated_at"],
    }


def _project_as_of(
    owner: str,
    project_id: str,
    revisions: Mapping[str, Mapping[tuple[str, str, int], Mapping[str, Any]]],
) -> str:
    return max(
        (
            roadmap["updated_at"]
            for (candidate_project, _roadmap, _revision), roadmap in revisions.get(owner, {}).items()
            if candidate_project == project_id
        ),
        default="1970-01-01T00:00:00Z",
    )


def _page_as_of(items: list[Mapping[str, Any]]) -> str:
    return max(
        (str(item.get("latest_updated_at") or item.get("updated_at") or "") for item in items),
        default="1970-01-01T00:00:00Z",
    ) or "1970-01-01T00:00:00Z"


def _changes(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanningRevisionRepositoryError("invalid_changes", "changes must be an object")
    operation = str(value.get("operation") or "").strip().lower()
    if operation not in {"update", "tombstone", "restore"}:
        raise PlanningRevisionRepositoryError(
            "invalid_changes", "changes.operation must be update, tombstone or restore"
        )
    allowed = {"operation", "set"} | ({"restore_revision"} if operation == "restore" else set())
    if set(value) != allowed:
        raise PlanningRevisionRepositoryError(
            "invalid_changes", "changes fields do not match the selected operation"
        )
    fields = value.get("set")
    if not isinstance(fields, Mapping) or not set(fields) <= _CHANGE_FIELDS:
        raise PlanningRevisionRepositoryError(
            "invalid_changes", "changes.set contains an unsupported definition field"
        )
    if operation == "update" and not fields:
        raise PlanningRevisionRepositoryError(
            "invalid_changes", "update requires at least one changed definition field"
        )
    if operation in {"tombstone", "restore"} and fields:
        raise PlanningRevisionRepositoryError(
            "invalid_changes", "tombstone and restore cannot set definition fields"
        )
    normalized: dict[str, Any] = {
        "operation": operation,
        "set": deepcopy(dict(fields)),
    }
    if operation == "restore":
        normalized["restore_revision"] = _positive_revision(
            value.get("restore_revision"), field="restore_revision"
        )
    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlanningRevisionRepositoryError(
            "invalid_changes", "changes contain a non-JSON value"
        ) from exc
    if len(encoded) > 256_000:
        raise PlanningRevisionRepositoryError(
            "changes_too_large", "changes exceed the 256000-byte limit"
        )
    return normalized


def _positive_revision(value: Any, *, field: str = "base_revision") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PlanningRevisionRepositoryError(
            "invalid_revision", f"{field} must be a positive integer"
        )
    return value


def _definition_hash(value: Any) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise PlanningRevisionRepositoryError(
            "invalid_content_hash", "base_hash must be sha256 lowercase hexadecimal"
        )
    return text


def _idempotency_key(value: Any) -> str:
    text = str(value or "").strip()
    if not _IDEMPOTENCY_RE.fullmatch(text):
        raise PlanningRevisionRepositoryError(
            "invalid_idempotency_key", "idempotency key is invalid or too short"
        )
    return text


def _draft_id(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"pd_[0-9a-f]{32}", text):
        raise PlanningRevisionRepositoryError("invalid_draft_id", "draft id is invalid")
    return text


def _undo_id(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"pu_[0-9a-f]{32}", text):
        raise PlanningRevisionRepositoryError("invalid_undo_id", "undo id is invalid")
    return text


def _draft(
    state: Mapping[str, Any],
    draft_id: str,
    project_id: str,
    roadmap_id: str,
) -> dict[str, Any]:
    key = _draft_id(draft_id)
    record = state["drafts"].get(key)
    if not isinstance(record, dict) or record.get("project_id") != project_id or record.get("roadmap_id") != roadmap_id:
        raise PlanningRevisionRepositoryError("draft_not_found", "draft was not found")
    return record


def _current_base(document: Mapping[str, Any], roadmap_id: str) -> Mapping[str, Any]:
    roadmaps = [item for item in document["roadmaps"] if item["roadmap_id"] == roadmap_id]
    if not roadmaps:
        raise PlanningRevisionRepositoryError("roadmap_not_found", "Planning roadmap was not found")
    latest = document["project"]["latest_approved_revision"].get(roadmap_id)
    if latest is not None:
        target = next(
            (
                item
                for item in roadmaps
                if item["revision"] == latest["revision"]
                and item["content_hash"] == latest["content_hash"]
            ),
            None,
        )
        if target is None:
            raise PlanningRevisionRepositoryError(
                "project_reference_conflict", "latest approved revision does not resolve"
            )
        return target
    return max(roadmaps, key=lambda item: item["revision"])


def _idempotency_replay(
    state: Mapping[str, Any],
    key: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    prior = state["idempotency"].get(key)
    if prior is None:
        return None
    if not isinstance(prior, Mapping) or prior.get("fingerprint") != fingerprint:
        raise PlanningRevisionRepositoryError(
            "idempotency_conflict", "idempotency key was already used for another request"
        )
    receipt = prior.get("receipt")
    if not isinstance(receipt, Mapping):
        raise PlanningRevisionRepositoryError(
            "draft_state_corrupt", "idempotency receipt is invalid"
        )
    return deepcopy(dict(receipt))


def _remember_idempotency(
    state: dict[str, Any],
    key: str,
    fingerprint: str,
    receipt: Mapping[str, Any],
) -> None:
    state["idempotency"][key] = {
        "fingerprint": fingerprint,
        "receipt": deepcopy(dict(receipt)),
    }


def _payload_hash(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlanningRevisionRepositoryError(
            "non_canonical_value", "value cannot be canonicalized"
        ) from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _confined(root: Path, candidate: Path) -> Path:
    resolved = candidate.expanduser().resolve(strict=False)
    try:
        if os.path.commonpath((str(root), str(resolved))) != str(root):
            raise ValueError("outside root")
    except ValueError as exc:
        raise PlanningRevisionRepositoryError(
            "path_confinement_failed", "repository path escapes the temporary root"
        ) from exc
    return resolved


def _repository_lock(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root))
    with _REPOSITORY_LOCKS_GUARD:
        lock = _REPOSITORY_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REPOSITORY_LOCKS[key] = lock
        return lock


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, encoded)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


# The legacy repository above remains an explicitly temporary editor fixture.
# This separate store is the durable canonical Planning writer.
HARBOUR_PLANNING_STATE_SCHEMA = "odysseus.harbour.planning_store.v2"
_HARBOUR_PLANNING_STATE_SCHEMA_V1 = "odysseus.harbour.planning_store.v1"
HARBOUR_PLANNING_PAYLOAD_SCHEMA = "odysseus.planning.definition.v2"
_HARBOUR_STATE_FILE = "harbour-planning-state.v1.json"
_HARBOUR_LOCK_FILE = ".harbour-planning-state.v1.lock"


class DurablePlanningStoreError(ValueError):
    """Content-free public error raised by the canonical Planning writer."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _raise_durable_planning_error(code: str) -> None:
    error = DurablePlanningStoreError(code)
    try:
        raise error from None
    finally:
        # Raising inside a caller's except block otherwise retains its context.
        error.__context__ = None
        error.__cause__ = None


def _require_unambiguous_component(value: object) -> None:
    # Keep historical record validators tolerant; this is a new-input boundary.
    if type(value) is str and ":" in value:
        _raise_durable_planning_error("validation_failed")


@dataclass(slots=True)
class _PlanningRuntime:
    lock: Any
    terminal_code: str | None = None

    def publish(self, code: str) -> None:
        # Called under the root lock; one assignment publishes the whole result.
        if self.terminal_code is None:
            self.terminal_code = code


_PLANNING_RUNTIMES: dict[str, _PlanningRuntime] = {}


def _planning_runtime(root: Path) -> _PlanningRuntime:
    lock = _repository_lock(root)
    with _REPOSITORY_LOCKS_GUARD:
        return _PLANNING_RUNTIMES.setdefault(os.path.normcase(str(root)), _PlanningRuntime(lock))


def _durable_operation(method):
    """Detach public failures, preserving a concurrently published terminal."""
    @wraps(method)
    def operation(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except DurablePlanningStoreError as error:
            code = error.code
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            code = "validation_failed"
        self._ensure_available()
        _raise_durable_planning_error(code)
    return operation


@dataclass(frozen=True, slots=True, repr=False)
class PlanningHead:
    owner_id: str
    project_id: str
    roadmap_id: str
    current_revision_id: str
    current_content_sha256: str
    approved_revision_id: str | None
    generation: int

    def __repr__(self) -> str:
        return "PlanningHead(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningRevision:
    revision_id: str
    owner_id: str
    project_id: str
    roadmap_id: str
    revision: int
    parent_revision_id: str | None
    payload_schema_id: str
    payload_sha256: str
    content_sha256: str
    immutable_locator: str
    revision_state: str
    created_at: str
    principal_ref: PrincipalRef

    def __repr__(self) -> str:
        return "PlanningRevision(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningContextRevisionMember:
    revision: PlanningRevision
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.revision) is not PlanningRevision or type(self.payload) is not bytes:
            raise ValueError("invalid Planning context revision member")
        try:
            snapshot = _revision_from_dict(_revision_dict(self.revision, self.payload))
        except Exception:
            raise ValueError("invalid Planning context revision member") from None
        if (
            snapshot != self.revision
            or _bytes_digest(self.payload) != self.revision.payload_sha256
        ):
            raise ValueError("invalid Planning context revision member")

    def __repr__(self) -> str:
        return "PlanningContextRevisionMember(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningAcceptedHeadBinding:
    owner_id: str
    project_id: str
    roadmap_id: str
    accepted_revision_id: str
    accepted_revision: int
    accepted_content_sha256: str
    current_revision_id: str
    current_revision: int
    current_content_sha256: str
    approved_revision_id: str
    approved_revision: int
    approved_content_sha256: str
    generation: int

    def __post_init__(self) -> None:
        identifiers = (
            self.owner_id, self.project_id, self.roadmap_id,
            self.accepted_revision_id, self.current_revision_id,
            self.approved_revision_id,
        )
        revisions = (
            self.accepted_revision, self.current_revision,
            self.approved_revision, self.generation,
        )
        hashes = (
            self.accepted_content_sha256, self.current_content_sha256,
            self.approved_content_sha256,
        )
        if (
            any(type(value) is not str or _ID_RE.fullmatch(value) is None for value in identifiers)
            or any(type(value) is not int or isinstance(value, bool) or not 1 <= value <= 2**63 - 1 for value in revisions)
            or any(type(value) is not str or _HASH_RE.fullmatch(value) is None for value in hashes)
            or not self.accepted_revision <= self.approved_revision <= self.current_revision
        ):
            raise ValueError("invalid accepted Planning head binding")

    def __repr__(self) -> str:
        return "PlanningAcceptedHeadBinding(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningAcceptedMigrationBundle:
    owner_id: str
    project_id: str
    batch_bytes: bytes = field(repr=False)
    heads: tuple[PlanningAcceptedHeadBinding, ...] = field(repr=False)
    revisions: tuple[PlanningContextRevisionMember, ...] = field(repr=False)
    state_digest: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.owner_id) is not str or _ID_RE.fullmatch(self.owner_id) is None
            or type(self.project_id) is not str or _ID_RE.fullmatch(self.project_id) is None
            or type(self.batch_bytes) is not bytes
            or type(self.heads) is not tuple or not self.heads
            or any(type(item) is not PlanningAcceptedHeadBinding for item in self.heads)
            or type(self.revisions) is not tuple or not self.revisions
            or any(type(item) is not PlanningContextRevisionMember for item in self.revisions)
            or type(self.state_digest) is not str or _HASH_RE.fullmatch(self.state_digest) is None
        ):
            raise ValueError("invalid accepted Planning migration bundle")
        try:
            batch = _strict_json_object(self.batch_bytes)
            canonical_batch = _canonical_planning_bytes(batch)
        except Exception:
            raise ValueError("invalid accepted Planning migration bundle") from None
        if (
            canonical_batch != self.batch_bytes
            or batch.get("state") != "accepted"
            or batch.get("owner_id") != self.owner_id
            or batch.get("project_id") != self.project_id
            or type(batch.get("source_items")) is not list
            or not batch["source_items"]
        ):
            raise ValueError("invalid accepted Planning migration bundle")
        head_order = tuple((item.project_id, item.roadmap_id) for item in self.heads)
        revision_order = tuple(
            (item.revision.project_id, item.revision.roadmap_id,
             item.revision.revision, item.revision.revision_id)
            for item in self.revisions
        )
        if head_order != tuple(sorted(head_order)) or len(set(head_order)) != len(head_order):
            raise ValueError("invalid accepted Planning migration bundle")
        if revision_order != tuple(sorted(revision_order)):
            raise ValueError("invalid accepted Planning migration bundle")
        revisions = {item.revision.revision_id: item.revision for item in self.revisions}
        if len(revisions) != len(self.revisions):
            raise ValueError("invalid accepted Planning migration bundle")
        source_roadmaps = tuple(
            item.get("roadmap_id")
            for item in batch["source_items"]
            if type(item) is dict
        )
        if (
            len(source_roadmaps) != len(batch["source_items"])
            or any(
                type(roadmap_id) is not str or _ID_RE.fullmatch(roadmap_id) is None
                for roadmap_id in source_roadmaps
            )
            or len(set(source_roadmaps)) != len(source_roadmaps)
            or set(source_roadmaps) != {item.roadmap_id for item in self.heads}
        ):
            raise ValueError("invalid accepted Planning migration bundle")
        source_items = {
            item["roadmap_id"]: item for item in batch["source_items"]
        }
        if any(
            item.revision.owner_id != self.owner_id
            or item.revision.project_id != self.project_id
            or item.revision.roadmap_id not in source_items
            for item in self.revisions
        ):
            raise ValueError("invalid accepted Planning migration bundle")
        for head in self.heads:
            if head.owner_id != self.owner_id or head.project_id != self.project_id:
                raise ValueError("invalid accepted Planning migration bundle")
            accepted = revisions.get(head.accepted_revision_id)
            current = revisions.get(head.current_revision_id)
            approved = revisions.get(head.approved_revision_id)
            if (
                accepted is None or current is None or approved is None
                or accepted.revision != 1
                or accepted.revision != head.accepted_revision
                or accepted.content_sha256 != head.accepted_content_sha256
                or current.revision != head.current_revision
                or current.content_sha256 != head.current_content_sha256
                or approved.revision != head.approved_revision
                or approved.content_sha256 != head.approved_content_sha256
                or approved.revision_state != "approved"
                or accepted.parent_revision_id is not None
                or source_items[head.roadmap_id].get("content_hash") != head.accepted_content_sha256
                or any(
                    revision.owner_id != self.owner_id
                    or revision.project_id != self.project_id
                    or revision.roadmap_id != head.roadmap_id
                    for revision in (accepted, current, approved)
                )
            ):
                raise ValueError("invalid accepted Planning migration bundle")
            cursor = current
            seen: set[str] = set()
            while True:
                if cursor.revision_id in seen:
                    raise ValueError("invalid accepted Planning migration bundle")
                seen.add(cursor.revision_id)
                if cursor.revision_id == accepted.revision_id:
                    break
                if cursor.parent_revision_id is None:
                    raise ValueError("invalid accepted Planning migration bundle")
                next_revision = revisions.get(cursor.parent_revision_id)
                if (
                    next_revision is None
                    or next_revision.owner_id != self.owner_id
                    or next_revision.project_id != self.project_id
                    or next_revision.roadmap_id != head.roadmap_id
                    or next_revision.revision != cursor.revision - 1
                ):
                    raise ValueError("invalid accepted Planning migration bundle")
                cursor = next_revision
            expected = {
                item.revision.revision_id for item in self.revisions
                if item.revision.roadmap_id == head.roadmap_id
            }
            if seen != expected:
                raise ValueError("invalid accepted Planning migration bundle")

    def __repr__(self) -> str:
        return "PlanningAcceptedMigrationBundle(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class _AcceptedMigrationMaterialization:
    batch: dict[str, Any] = field(repr=False)
    records: tuple[tuple[str, dict[str, Any], str], ...] = field(repr=False)
    heads: tuple[PlanningAcceptedHeadBinding, ...] = field(repr=False)
    revisions: tuple[PlanningContextRevisionMember, ...] = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class PlanningReceipt:
    receipt_id: str
    command_kind: str
    subject_id: str
    input_digest: str
    output_digest: str
    before_head: PlanningHead | None
    after_head: PlanningHead
    new_revision_id: str | None
    restored_revision_id: str | None
    producer: str
    occurred_at: str

    def __repr__(self) -> str:
        return "PlanningReceipt(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningSubjectBoundReceipt:
    receipt_handle: str
    current_binding_digest: str
    operation: str
    producer: str
    occurred_at: str
    verified: bool
    authorizes_mutation: bool

    def __post_init__(self) -> None:
        if (
            type(self.receipt_handle) is not str or _HASH_RE.fullmatch(self.receipt_handle) is None
            or type(self.current_binding_digest) is not str or _HASH_RE.fullmatch(self.current_binding_digest) is None
            or self.operation not in {"append_revision", "approve_revision", "revert_head"}
            or self.producer != "harbour_planning_store"
            or type(self.occurred_at) is not str or not self.occurred_at.endswith("Z")
            or type(self.verified) is not bool or self.verified is not True
            or type(self.authorizes_mutation) is not bool or self.authorizes_mutation is not False
        ):
            raise ValueError("invalid subject-bound Planning receipt")

    def __repr__(self) -> str:
        return "PlanningSubjectBoundReceipt(<content-free>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningMigrationReceipt:
    receipt_type: str
    receipt_id: str
    batch_id: str
    phase: str
    input_digest: str
    output_digest: str
    before_state: str
    after_state: str
    before_subjects: tuple[dict[str, Any], ...]
    after_subjects: tuple[dict[str, Any], ...]
    new_revision_ids: tuple[str, ...]
    producer: str
    occurred_at: str

    def __repr__(self) -> str:
        return "PlanningMigrationReceipt(<content-free>)"


class DurablePlanningRevisionStore:
    """Append-only, owner-scoped Planning authority with restart replay.

    One atomically replaced state image is the transaction boundary.  It
    contains immutable payload bytes, transitions, receipts, idempotency
    bindings and the materialized heads.  Startup independently replays and
    checks the complete image; any mismatch leaves this instance unavailable
    and permanently write-disabled.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        trusted_issuers: Iterable[AuthenticatedPrincipalIssuer],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self._path = self.root / _HARBOUR_STATE_FILE
        self._trusted_issuers = tuple(trusted_issuers)
        if not self._trusted_issuers or any(
            type(item) is not AuthenticatedPrincipalIssuer
            for item in self._trusted_issuers
        ):
            raise ValueError("trusted_issuers must contain exact issuer objects")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._runtime = _planning_runtime(self.root)
        self._lock = self._runtime.lock
        self._state = _new_harbour_state()
        self._planning_validation_pair_local = threading.local()
        with self._lock:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                self._refresh_locked()
            except OSError:
                self._runtime.publish("validation_failed")
            except DurablePlanningStoreError:
                pass

    @property
    def available(self) -> bool:
        return self._runtime.terminal_code is None

    @contextmanager
    def _planning_payload_validation_pair(self):
        """Enter one thread-bound, two-read lexical validation scope."""
        with _planning_payload_validation_pair_scope(self):
            yield None

    @contextmanager
    def _activate_planning_payload_validation_pair_read(self):
        """Bind one fresh bundle replay to the current lexical pair, if any."""
        with _planning_payload_validation_pair_read(self):
            yield None

    @_durable_operation
    def create_project(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        project_id: str,
        roadmap_id: str,
        payload_bytes: bytes,
        idempotency_key: str,
        expected_generation: int = 0,
    ) -> PlanningReceipt:
        principal_ref, owner, project, roadmap, key, occurred = self._command_prelude(
            principal, owner_id, project_id, roadmap_id, idempotency_key
        )
        if type(expected_generation) is not int or expected_generation != 0:
            raise DurablePlanningStoreError("validation_failed")
        payload, document, roadmap_record = _validated_planning_payload(
            payload_bytes, project, roadmap
        )
        if roadmap_record["revision"] != 1:
            raise DurablePlanningStoreError("validation_failed")
        command = {
            "kind": "create_project",
            "owner_id": owner,
            "project_id": project,
            "roadmap_id": roadmap,
            "expected_generation": 0,
            "payload_sha256": _bytes_digest(payload),
            "principal_identity": _command_principal_identity(principal_ref),
        }
        return self._commit_revision_command(
            command=command,
            idempotency_key=key,
            principal_ref=principal_ref,
            payload=payload,
            document=document,
            roadmap_record=roadmap_record,
            expected_head=None,
            restored_revision_id=None,
            occurred_at=occurred,
        )

    @_durable_operation
    def append_revision(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        project_id: str,
        roadmap_id: str,
        payload_bytes: bytes,
        expected_head: PlanningHead,
        idempotency_key: str,
    ) -> PlanningReceipt:
        principal_ref, owner, project, roadmap, key, occurred = self._command_prelude(
            principal, owner_id, project_id, roadmap_id, idempotency_key
        )
        expected = _exact_head(expected_head, owner, project, roadmap)
        with _append_planning_payload_validation_scope(self) as validation_run:
            payload, document, roadmap_record = _validated_planning_payload(
                payload_bytes, project, roadmap, _validation_run=validation_run,
            )
            _commit_append_planning_payload_validation(
                validation_run, self, payload, project, roadmap,
            )
            command = {
                "kind": "append_revision",
                "owner_id": owner,
                "project_id": project,
                "roadmap_id": roadmap,
                "expected_head": _head_dict(expected),
                "payload_sha256": _bytes_digest(payload),
                "principal_identity": _command_principal_identity(principal_ref),
            }
            return self._commit_revision_command(
                command=command,
                idempotency_key=key,
                principal_ref=principal_ref,
                payload=payload,
                document=document,
                roadmap_record=roadmap_record,
                expected_head=expected,
                restored_revision_id=None,
                occurred_at=occurred,
                _append_validation_run=validation_run,
            )

    @_durable_operation
    def replay_append_revision(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        project_id: str,
        roadmap_id: str,
        payload_bytes: bytes,
        expected_head: PlanningHead,
        idempotency_key: str,
    ) -> PlanningReceipt | None:
        """Return only an exact, already committed append receipt.

        This read-only lookup is deliberately performed under the same
        authoritative lock as the eventual write.  Process callers use it
        before their stale-head normalization so an uncertain completed write
        can be recovered by its exact idempotency key without replaying it.
        """
        principal_ref, owner, project, roadmap, key, _occurred = self._command_prelude(
            principal, owner_id, project_id, roadmap_id, idempotency_key
        )
        expected = _exact_head(expected_head, owner, project, roadmap)
        payload, _document, _roadmap_record = _validated_planning_payload(
            payload_bytes, project, roadmap
        )
        command = {
            "kind": "append_revision",
            "owner_id": owner,
            "project_id": project,
            "roadmap_id": roadmap,
            "expected_head": _head_dict(expected),
            "payload_sha256": _bytes_digest(payload),
            "principal_identity": _command_principal_identity(principal_ref),
        }
        digest = _canonical_digest(command)
        with self._lock:
            with _interprocess_repository_lock(self.root):
                self._refresh_locked()
                return self._idempotency_result(owner, key, digest)

    @_durable_operation
    def approve_revision(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        project_id: str,
        roadmap_id: str,
        revision_id: str,
        expected_head: PlanningHead,
        idempotency_key: str,
    ) -> PlanningReceipt:
        principal_ref, owner, project, roadmap, key, occurred = self._command_prelude(
            principal, owner_id, project_id, roadmap_id, idempotency_key
        )
        expected = _exact_head(expected_head, owner, project, roadmap)
        target_id = _harbour_identifier(revision_id)
        target = self._revision(target_id)
        if (
            target.owner_id != owner
            or target.project_id != project
            or target.roadmap_id != roadmap
        ):
            raise DurablePlanningStoreError("not_found")
        if target.revision_state != "approved":
            raise DurablePlanningStoreError("validation_failed")
        command = {
            "kind": "approve_revision",
            "owner_id": owner,
            "project_id": project,
            "roadmap_id": roadmap,
            "expected_head": _head_dict(expected),
            "revision_id": target_id,
            "principal_identity": _command_principal_identity(principal_ref),
        }
        return self._commit_head_command(
            command=command,
            idempotency_key=key,
            expected_head=expected,
            approved_revision_id=target_id,
            occurred_at=occurred,
        )

    @_durable_operation
    def revert_head(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        project_id: str,
        roadmap_id: str,
        ancestor_revision_id: str,
        expected_head: PlanningHead,
        idempotency_key: str,
    ) -> PlanningReceipt:
        principal_ref, owner, project, roadmap, key, occurred = self._command_prelude(
            principal, owner_id, project_id, roadmap_id, idempotency_key
        )
        expected = _exact_head(expected_head, owner, project, roadmap)
        ancestor_id = _harbour_identifier(ancestor_revision_id)
        ancestor = self._revision(ancestor_id)
        if (
            ancestor.owner_id != owner
            or ancestor.project_id != project
            or ancestor.roadmap_id != roadmap
            or ancestor.revision_state != "approved"
        ):
            raise DurablePlanningStoreError("not_found")
        current = self._revision_for_head(expected)
        document = _strict_json_object(self._payload_for_revision(ancestor_id))
        roadmap_record = document["roadmaps"][0]
        roadmap_record["revision"] = current.revision + 1
        roadmap_record["content_hash"] = compute_roadmap_content_hash(roadmap_record)
        document["project"]["latest_approved_revision"] = {
            roadmap: {
                "revision": roadmap_record["revision"],
                "content_hash": roadmap_record["content_hash"],
            }
        }
        document["project"]["draft_refs"] = []
        payload = _canonical_planning_bytes(document)
        _validated_planning_payload(payload, project, roadmap)
        command = {
            "kind": "revert_head",
            "owner_id": owner,
            "project_id": project,
            "roadmap_id": roadmap,
            "expected_head": _head_dict(expected),
            "ancestor_revision_id": ancestor_id,
            "payload_sha256": _bytes_digest(payload),
            "principal_identity": _command_principal_identity(principal_ref),
        }
        return self._commit_revision_command(
            command=command,
            idempotency_key=key,
            principal_ref=principal_ref,
            payload=payload,
            document=document,
            roadmap_record=roadmap_record,
            expected_head=expected,
            restored_revision_id=ancestor_id,
            occurred_at=occurred,
        )

    @_durable_operation
    def prepare_migration(
        self,
        principal: TrustedPrincipal,
        *,
        manifest: Mapping[str, Any],
    ) -> PlanningMigrationReceipt:
        """Persist one head-free, six-subject migration preparation."""
        self._ensure_available()
        body = _validated_migration_manifest(manifest)
        ref, occurred = self._authenticate(principal)
        try:
            owner = require_owner(ref, body["owner_id"])
        except HarbourAuthorityError as exc:
            raise DurablePlanningStoreError(exc.code) from None
        batch_id = body["batch_id"]
        expected = [
            _migration_subject(owner, body["project_id"], item["roadmap_id"], None, 0)
            for item in body["source_items"]
        ]
        phase_input = _migration_phase_input(
            "prepare", batch_id, body["manifest_digest"], "absent", expected,
            _command_principal_identity(ref),
        )
        digest = _migration_digest(phase_input)
        key = _migration_idempotency_key("prepare", digest)
        with self._lock:
            with _interprocess_repository_lock(self.root):
                self._refresh_locked()
                replay = self._migration_idempotency_result(owner, key, digest)
                if replay is not None:
                    return replay
                existing = self._state["migration_batches"].get(batch_id)
                if existing is not None:
                    raise DurablePlanningStoreError("idempotency_conflict")
                target_prefix = f"{owner}:{body['project_id']}:"
                if any(key.startswith(target_prefix) for key in self._state["heads"]):
                    raise DurablePlanningStoreError("target_conflict")
                if any(
                    key.startswith(target_prefix) and fence != 0
                    for key, fence in self._state["head_fences"].items()
                ):
                    raise DurablePlanningStoreError("target_conflict")
                for subject in expected:
                    if self._state["heads"].get(subject["subject_key"]) is not None:
                        raise DurablePlanningStoreError("target_conflict")
                    if self._state["head_fences"].get(subject["subject_key"], 0) != 0:
                        raise DurablePlanningStoreError("target_conflict")
                batch = {
                    "batch_id": batch_id,
                    "manifest_digest": body["manifest_digest"],
                    "owner_id": owner,
                    "project_id": body["project_id"],
                    "state": "prepared",
                    "source_items": deepcopy(body["source_items"]),
                    "supporting_relations": deepcopy(body["supporting_relations"]),
                    "structural_census": deepcopy(body["structural_census"]),
                    # This is a read-only census observation. It is deliberately
                    # outside the manifest digest and command/idempotency key.
                    "census_observation": deepcopy(body["supporting_observation"]),
                    "principal_identity": _command_principal_identity(ref),
                    "manifest_identity": _migration_manifest_identity(body),
                    "expected_subjects": deepcopy(expected),
                    "accepted_subjects": [],
                    "normalized_payloads": deepcopy(body["normalized_payloads"]),
                    "prepared_receipt_id": "",
                    "accepted_receipt_id": "",
                    "terminal_receipt_id": "",
                }
                receipt, transition = _new_migration_receipt(
                    phase="prepare", batch_id=batch_id, command_digest=digest,
                    idempotency_key=key, before_state="absent", after_state="prepared",
                    before_subjects=expected, after_subjects=expected,
                    new_revision_ids=[], occurred_at=occurred,
                )
                batch["prepared_receipt_id"] = receipt.receipt_id
                candidate = deepcopy(self._state)
                candidate["migration_batches"][batch_id] = batch
                _append_migration_event(candidate, owner, key, digest, transition, receipt)
                self._persist_migration(candidate)
                return receipt

    @_durable_operation
    def accept_migration(
        self,
        principal: TrustedPrincipal,
        *,
        batch_id: str,
        current_manifest: Callable[[], Mapping[str, Any]],
    ) -> PlanningMigrationReceipt:
        return self._finish_migration_phase(
            principal, batch_id=batch_id, phase="accept", current_manifest=current_manifest,
        )

    @_durable_operation
    def abort_migration(
        self, principal: TrustedPrincipal, *, batch_id: str
    ) -> PlanningMigrationReceipt:
        return self._finish_migration_phase(principal, batch_id=batch_id, phase="abort")

    @_durable_operation
    def rollback_migration(
        self, principal: TrustedPrincipal, *, batch_id: str
    ) -> PlanningMigrationReceipt:
        return self._finish_migration_phase(principal, batch_id=batch_id, phase="rollback")

    @_durable_operation
    def get_migration_batch(
        self, principal: TrustedPrincipal, *, owner_id: str, batch_id: str
    ) -> dict[str, Any]:
        self._ensure_available()
        ref, occurred = self._authenticate(principal)
        try:
            owner = require_owner(ref, owner_id)
            target = _harbour_identifier(batch_id)
        except HarbourAuthorityError as exc:
            raise DurablePlanningStoreError(exc.code) from None
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock:
            self._refresh_locked()
            batch = self._state["migration_batches"].get(target)
            if batch is None or batch["owner_id"] != owner:
                raise DurablePlanningStoreError("not_found")
            return deepcopy(batch)

    @_durable_operation
    def find_accepted_migration_batch(
        self, principal: TrustedPrincipal, *, owner_id: str, project_id: str
    ) -> dict[str, Any] | None:
        """Find the one accepted migration for this authenticated owner/project."""
        self._ensure_available()
        ref, _occurred = self._authenticate(principal)
        try:
            owner = require_owner(ref, owner_id)
            project = _harbour_identifier(project_id)
        except HarbourAuthorityError as exc:
            raise DurablePlanningStoreError(exc.code) from None
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock:
            self._refresh_locked()
            matches = [
                value for value in self._state["migration_batches"].values()
                if value["owner_id"] == owner and value["project_id"] == project
                and value["state"] == "accepted"
            ]
            if len(matches) > 1:
                raise DurablePlanningStoreError("validation_failed")
            return deepcopy(matches[0]) if matches else None

    @_durable_operation
    def list_migration_batches(
        self, principal: TrustedPrincipal, *, owner_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        self._ensure_available()
        ref, _occurred = self._authenticate(principal)
        try:
            owner = require_owner(ref, owner_id)
            project = _harbour_identifier(project_id)
        except HarbourAuthorityError as exc:
            raise DurablePlanningStoreError(exc.code) from None
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock:
            self._refresh_locked()
            return [
                deepcopy(batch) for batch in sorted(
                    self._state["migration_batches"].values(), key=lambda item: item["batch_id"]
                ) if batch["owner_id"] == owner and batch["project_id"] == project
            ]

    @_durable_operation
    def read_migration_receipt(
        self, principal: TrustedPrincipal, *, owner_id: str, receipt_id: str
    ) -> dict[str, Any]:
        """Read a validated, detached migration receipt scoped to its owning batch."""
        self._ensure_available()
        ref, _occurred = self._authenticate(principal)
        try:
            owner = require_owner(ref, owner_id)
            target = _harbour_identifier(receipt_id)
        except HarbourAuthorityError as exc:
            raise DurablePlanningStoreError(exc.code) from None
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock:
            self._refresh_locked()
            record = self._state["receipts"].get(target)
            if record is None or record.get("receipt_type") != "planning_migration":
                raise DurablePlanningStoreError("not_found")
            try:
                receipt = _migration_receipt_from_dict(record)
            except (ValueError, TypeError, KeyError):
                raise DurablePlanningStoreError("validation_failed") from None
            batch = self._state["migration_batches"].get(receipt.batch_id)
            if batch is None or batch["owner_id"] != owner:
                raise DurablePlanningStoreError("not_found")
            return _migration_receipt_dict(receipt)

    @_durable_operation
    def read_accepted_migration_records(
        self, principal: TrustedPrincipal, *, owner_id: str, project_id: str
    ) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any], str]], list[dict[str, Any]]]:
        """Return accepted ancestry, canonical revisions, and detached head bindings."""
        batch = self.find_accepted_migration_batch(
            principal, owner_id=owner_id, project_id=project_id
        )
        if batch is None:
            raise DurablePlanningStoreError("not_found")
        ref, _occurred = self._authenticate(principal)
        owner = require_owner(ref, owner_id)
        return self._read_accepted_migration_records_for_batch(
            principal, batch=batch, owner=owner, project_id=project_id,
        )

    @_durable_operation
    def read_subject_bound_accepted_migration_records(
        self, principal: TrustedPrincipal, *, project_id: str,
    ) -> tuple[str, dict[str, Any], list[tuple[str, dict[str, Any], str]], list[dict[str, Any]]]:
        """Read one accepted migration by authenticated immutable subject and issuer."""
        self._ensure_available()
        ref, _occurred = self._authenticate(principal)
        try:
            project = _harbour_identifier(project_id)
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock, _interprocess_repository_lock(self.root):
            self._refresh_locked()
            stored_batch = self._subject_bound_accepted_batch_locked(ref, project)
            owner = stored_batch["owner_id"]
            materialized = self._materialize_accepted_migration_locked(
                batch=stored_batch, owner=owner, project_id=project,
            )
            return owner, *self._legacy_accepted_projection(
                materialized, include_head_generation=True,
            )

    @_durable_operation
    def read_subject_bound_accepted_migration_bundle(
        self, principal: TrustedPrincipal, *, project_id: str,
    ) -> PlanningAcceptedMigrationBundle:
        """Return one immutable accepted source image under a single read fence."""
        self._ensure_available()
        ref, _occurred = self._authenticate(principal)
        try:
            project = _harbour_identifier(project_id)
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock, _interprocess_repository_lock(self.root):
            validation_capture = _PlanningPayloadValidationCapture()
            with self._activate_planning_payload_validation_pair_read():
                entry_bytes = self._refresh_locked(_validation_run_capture=validation_capture)
            if type(entry_bytes) is not bytes:
                raise DurablePlanningStoreError("validation_failed")
            entry_digest = _canonical_digest(self._state)
            stored_batch = self._subject_bound_accepted_batch_locked(ref, project)
            owner = stored_batch["owner_id"]
            validation_run = validation_capture.take(entry_bytes, entry_digest)
            materialized = self._materialize_accepted_migration_locked(
                batch=stored_batch, owner=owner, project_id=project,
                _validation_run=validation_run,
                _retained_state_bytes=entry_bytes,
                _retained_state_digest=entry_digest,
            )
            bundle = PlanningAcceptedMigrationBundle(
                owner_id=owner,
                project_id=project,
                batch_bytes=_canonical_planning_bytes(materialized.batch),
                heads=tuple(
                    sorted(
                        materialized.heads,
                        key=lambda item: (item.project_id, item.roadmap_id),
                    )
                ),
                revisions=materialized.revisions,
                state_digest=entry_digest,
            )
            self._assert_retained_image_locked(entry_bytes, entry_digest)
            return bundle

    @_durable_operation
    def _read_dual_subject_bound_accepted_migration_bundle(
        self,
        cookie_principal: TrustedPrincipal,
        mcp_principal: TrustedPrincipal,
        *,
        project_id: str,
    ) -> PlanningAcceptedMigrationBundle:
        """Authenticate two recipient principals and materialize one source image."""
        self._ensure_available()
        if cookie_principal is mcp_principal:
            raise DurablePlanningStoreError("validation_failed")
        cookie_ref, _cookie_occurred = self._authenticate(cookie_principal)
        mcp_ref, _mcp_occurred = self._authenticate(mcp_principal)
        if (
            (cookie_ref.issuer_id, cookie_ref.subject_id, cookie_ref.owner_id)
            != (mcp_ref.issuer_id, mcp_ref.subject_id, mcp_ref.owner_id)
            or cookie_ref.auth_context_digest == mcp_ref.auth_context_digest
            or cookie_ref.principal_digest == mcp_ref.principal_digest
        ):
            raise DurablePlanningStoreError("validation_failed")
        try:
            project = _harbour_identifier(project_id)
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock, _interprocess_repository_lock(self.root):
            validation_capture = _PlanningPayloadValidationCapture()
            with self._activate_planning_payload_validation_pair_read():
                entry_bytes = self._refresh_locked(
                    _validation_run_capture=validation_capture,
                )
            if type(entry_bytes) is not bytes:
                raise DurablePlanningStoreError("validation_failed")
            entry_digest = _canonical_digest(self._state)
            cookie_batch = self._subject_bound_accepted_batch_locked(cookie_ref, project)
            mcp_batch = self._subject_bound_accepted_batch_locked(mcp_ref, project)
            if cookie_batch is not mcp_batch and cookie_batch != mcp_batch:
                raise DurablePlanningStoreError("validation_failed")
            owner = cookie_batch["owner_id"]
            if owner != cookie_ref.owner_id or owner != mcp_ref.owner_id:
                raise DurablePlanningStoreError("validation_failed")
            validation_run = validation_capture.take(entry_bytes, entry_digest)
            materialized = self._materialize_accepted_migration_locked(
                batch=cookie_batch,
                owner=owner,
                project_id=project,
                _validation_run=validation_run,
                _retained_state_bytes=entry_bytes,
                _retained_state_digest=entry_digest,
            )
            bundle = PlanningAcceptedMigrationBundle(
                owner_id=owner,
                project_id=project,
                batch_bytes=_canonical_planning_bytes(materialized.batch),
                heads=tuple(sorted(
                    materialized.heads,
                    key=lambda item: (item.project_id, item.roadmap_id),
                )),
                revisions=materialized.revisions,
                state_digest=entry_digest,
            )
            self._assert_retained_image_locked(entry_bytes, entry_digest)
            return bundle

    @_durable_operation
    def read_subject_bound_current_receipt(
        self,
        principal: TrustedPrincipal,
        *,
        project_id: str,
        roadmap_id: str,
        expected_head: PlanningHead,
    ) -> PlanningSubjectBoundReceipt | None:
        """Resolve the unique ordinary receipt that produced the bound head."""
        self._ensure_available()
        ref, _occurred = self._authenticate(principal)
        try:
            project = _harbour_identifier(project_id)
            roadmap = _harbour_identifier(roadmap_id)
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock, _interprocess_repository_lock(self.root):
            entry_bytes = self._refresh_locked()
            if type(entry_bytes) is not bytes:
                raise DurablePlanningStoreError("stale_authority")
            entry_digest = _canonical_digest(self._state)
            stored_batch = self._subject_bound_accepted_batch_locked(ref, project)
            owner = stored_batch["owner_id"]
            materialized = self._materialize_accepted_migration_locked(
                batch=stored_batch, owner=owner, project_id=project,
            )
            selected = [item for item in materialized.heads if item.roadmap_id == roadmap]
            if len(selected) != 1:
                raise DurablePlanningStoreError("not_found" if not selected else "validation_failed")
            accepted = selected[0]
            current = PlanningHead(
                owner_id=accepted.owner_id,
                project_id=accepted.project_id,
                roadmap_id=accepted.roadmap_id,
                current_revision_id=accepted.current_revision_id,
                current_content_sha256=accepted.current_content_sha256,
                approved_revision_id=accepted.approved_revision_id,
                generation=accepted.generation,
            )
            expected = _exact_head(expected_head, owner, project, roadmap)
            if expected != current:
                raise DurablePlanningStoreError("stale_authority")
            current_dict = _head_dict(current)
            matches: list[tuple[dict[str, Any], PlanningReceipt]] = []
            for transition in self._state["transitions"]:
                if "batch_id" in transition or transition.get("after_head") != current_dict:
                    continue
                record = self._state["receipts"].get(transition.get("receipt_id"))
                try:
                    receipt = _receipt_from_dict(record)
                except (ValueError, TypeError, KeyError):
                    raise DurablePlanningStoreError("validation_failed") from None
                if (
                    receipt.command_kind not in {"append_revision", "approve_revision", "revert_head"}
                    or transition.get("kind") != receipt.command_kind
                    or receipt.after_head != current
                ):
                    raise DurablePlanningStoreError("validation_failed")
                matches.append((transition, receipt))
            if len(matches) > 1:
                raise DurablePlanningStoreError("validation_failed")
            if not matches:
                self._assert_retained_image_locked(entry_bytes, entry_digest)
                return None
            _transition, receipt = matches[0]
            try:
                parsed_occurred = datetime.fromisoformat(receipt.occurred_at.replace("Z", "+00:00"))
                canonical_occurred = parsed_occurred.astimezone(timezone.utc).isoformat(
                    timespec="microseconds"
                ).replace("+00:00", "Z")
            except (TypeError, ValueError, OverflowError):
                raise DurablePlanningStoreError("validation_failed") from None
            if (
                parsed_occurred.tzinfo is None
                or parsed_occurred.utcoffset() != timezone.utc.utcoffset(parsed_occurred)
                or canonical_occurred != receipt.occurred_at
            ):
                raise DurablePlanningStoreError("validation_failed")
            binding_digest = _canonical_digest({
                "schema": "odysseus.planning.subject_bound_current_receipt_binding.v1",
                "issuer_id": ref.issuer_id,
                "subject_id": ref.subject_id,
                "owner_id": owner,
                "project_id": project,
                "roadmap_id": roadmap,
                "head": current_dict,
            })
            handle = _canonical_digest({
                "schema": "odysseus.planning.subject_bound_current_receipt_handle.v1",
                "receipt_id": receipt.receipt_id,
                "binding_digest": binding_digest,
                "operation": receipt.command_kind,
            })
            result = PlanningSubjectBoundReceipt(
                receipt_handle=handle,
                current_binding_digest=binding_digest,
                operation=receipt.command_kind,
                producer=receipt.producer,
                occurred_at=receipt.occurred_at,
                verified=True,
                authorizes_mutation=False,
            )
            self._assert_retained_image_locked(entry_bytes, entry_digest)
            return result

    def _subject_bound_accepted_batch_locked(
        self, ref: PrincipalRef, project: str,
    ) -> dict[str, Any]:
        accepted_candidates = [
            candidate for candidate in self._state["migration_batches"].values()
            if candidate.get("state") == "accepted" and candidate.get("project_id") == project
        ]
        matches = []
        for candidate in accepted_candidates:
            binding = candidate.get("accepted_subject_binding")
            original = candidate.get("principal_identity")
            identity_binding = (
                type(original) is dict
                and original.get("owner_id") == candidate.get("owner_id")
                and type(original.get("subject_id")) is str
                and original["subject_id"] != candidate.get("owner_id")
                and _IMMUTABLE_ADMIN_SUBJECT_RE.fullmatch(original["subject_id"]) is not None
                and original.get("issuer_id") == ref.issuer_id
                and original.get("subject_id") == ref.subject_id
            )
            explicit_binding = (
                type(binding) is dict
                and type(binding.get("subject_id")) is str
                and binding.get("subject_id") != candidate.get("owner_id")
                and _IMMUTABLE_ADMIN_SUBJECT_RE.fullmatch(binding["subject_id"]) is not None
                and (binding.get("issuer_id"), binding.get("subject_id"))
                == (ref.issuer_id, ref.subject_id)
            )
            if identity_binding or explicit_binding:
                matches.append(candidate)
        if len(matches) != 1:
            if len(matches) > 1:
                raise DurablePlanningStoreError("validation_failed")
            raise DurablePlanningStoreError(
                "migration_required" if accepted_candidates else "not_found"
            )
        return matches[0]

    def _read_accepted_migration_records_for_batch(
        self, principal: TrustedPrincipal, *, batch: dict[str, Any], owner: str, project_id: str,
        refresh: bool = True, include_head_generation: bool = False,
    ) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any], str]], list[dict[str, Any]]]:
        self._ensure_available()
        self._authenticate(principal)
        project = _harbour_identifier(project_id)
        with self._lock, _interprocess_repository_lock(self.root):
            self._refresh_locked()
            materialized = self._materialize_accepted_migration_locked(
                batch=batch, owner=owner, project_id=project,
            )
            return self._legacy_accepted_projection(
                materialized, include_head_generation=include_head_generation,
            )

    def _materialize_accepted_migration_locked(
        self, *, batch: dict[str, Any], owner: str, project_id: str,
        _validation_run: _PlanningPayloadValidationRun | None = None,
        _retained_state_bytes: bytes | None = None,
        _retained_state_digest: str | None = None,
    ) -> _AcceptedMigrationMaterialization:
        if _validation_run is None:
            if _retained_state_bytes is not None or _retained_state_digest is not None:
                raise DurablePlanningStoreError("validation_failed")
        else:
            if type(_validation_run) is not _PlanningPayloadValidationRun:
                raise DurablePlanningStoreError("validation_failed")
            if _canonical_digest(self._state) != _retained_state_digest:
                raise DurablePlanningStoreError("validation_failed")
            _validation_run.begin_materialization(
                _retained_state_bytes, _retained_state_digest,
            )
        stored_batch = self._state["migration_batches"].get(batch["batch_id"])
        if stored_batch is None or stored_batch != batch:
            raise DurablePlanningStoreError("validation_failed")
        receipt_id = stored_batch["accepted_receipt_id"]
        receipt_record = self._state["receipts"].get(receipt_id)
        try:
            receipt = _migration_receipt_from_dict(receipt_record)
        except (ValueError, TypeError, KeyError):
            raise DurablePlanningStoreError("validation_failed") from None
        if (
            receipt.phase != "accept" or receipt.batch_id != stored_batch["batch_id"]
            or receipt.after_state != "accepted"
            or "structural_census" not in stored_batch
            or "manifest_identity" not in stored_batch
            or receipt.new_revision_ids == ()
            or len(receipt.new_revision_ids) != len(stored_batch["source_items"])
        ):
            raise DurablePlanningStoreError("validation_failed")
        records: list[tuple[str, dict[str, Any], str]] = []
        head_bindings: list[PlanningAcceptedHeadBinding] = []
        revision_members: list[PlanningContextRevisionMember] = []
        seen_revisions: set[str] = set()
        for item, accepted_revision_id in zip(stored_batch["source_items"], receipt.new_revision_ids):
            roadmap_id = item["roadmap_id"]
            subject_key = _planning_key(owner, project_id, roadmap_id)
            raw_head = self._state["heads"].get(subject_key)
            if raw_head is None:
                raise DurablePlanningStoreError("validation_failed")
            head = _head_from_dict(raw_head)
            if (head.owner_id, head.project_id, head.roadmap_id) != (owner, project_id, roadmap_id):
                raise DurablePlanningStoreError("validation_failed")
            cursor = head.current_revision_id
            chain: list[PlanningRevision] = []
            seen: set[str] = set()
            while cursor is not None:
                if cursor in seen:
                    raise DurablePlanningStoreError("validation_failed")
                seen.add(cursor)
                raw_revision = self._state["revisions"].get(cursor)
                if raw_revision is None:
                    raise DurablePlanningStoreError("validation_failed")
                revision = _revision_from_dict(raw_revision)
                if (revision.owner_id, revision.project_id, revision.roadmap_id) != (owner, project_id, roadmap_id):
                    raise DurablePlanningStoreError("validation_failed")
                chain.append(revision)
                if cursor == accepted_revision_id:
                    break
                cursor = revision.parent_revision_id
            if not chain or chain[-1].revision_id != accepted_revision_id:
                raise DurablePlanningStoreError("validation_failed")
            if chain[-1].revision != 1 or chain[-1].parent_revision_id is not None:
                raise DurablePlanningStoreError("validation_failed")
            chain_by_id = {revision.revision_id: revision for revision in chain}
            approved = chain_by_id.get(head.approved_revision_id)
            current = chain[0]
            accepted = chain[-1]
            if (
                approved is None
                or approved.revision_state != "approved"
                or current.revision_id != head.current_revision_id
                or current.content_sha256 != head.current_content_sha256
            ):
                raise DurablePlanningStoreError("validation_failed")
            roadmap_by_id: dict[str, dict[str, Any]] = {}
            for revision in reversed(chain):
                if revision.revision_id in seen_revisions:
                    raise DurablePlanningStoreError("validation_failed")
                seen_revisions.add(revision.revision_id)
                payload = self._payload_for_revision(revision.revision_id, refresh=False)
                _payload, _normalized, roadmap_record = _validated_planning_payload(
                    payload, project_id, roadmap_id, _validation_run=_validation_run,
                )
                if (
                    _canonical_planning_bytes(_normalized) != payload
                    or _payload != payload
                    or _bytes_digest(payload) != revision.payload_sha256
                    or roadmap_record["content_hash"] != revision.content_sha256
                    or roadmap_record["revision"] != revision.revision
                    or roadmap_record["revision_state"] != revision.revision_state
                ):
                    raise DurablePlanningStoreError("validation_failed")
                roadmap_by_id[revision.revision_id] = roadmap_record
                records.append((owner, _normalized, "durable:" + revision.revision_id))
                revision_members.append(PlanningContextRevisionMember(revision, bytes(payload)))
            accepted_record = roadmap_by_id.get(accepted.revision_id)
            current_record = roadmap_by_id.get(current.revision_id)
            approved_record = roadmap_by_id.get(approved.revision_id)
            if (
                accepted_record is None or current_record is None or approved_record is None
                or approved_record["revision_state"] != "approved"
            ):
                raise DurablePlanningStoreError("validation_failed")
            head_bindings.append(PlanningAcceptedHeadBinding(
                owner, project_id, roadmap_id,
                accepted.revision_id, accepted.revision, accepted.content_sha256,
                current.revision_id, current.revision, current.content_sha256,
                approved.revision_id, approved.revision, approved.content_sha256,
                head.generation,
            ))
        if len(head_bindings) != len(stored_batch["source_items"]):
            raise DurablePlanningStoreError("validation_failed")
        head_tuple = tuple(head_bindings)
        revision_tuple = tuple(sorted(
            revision_members,
            key=lambda item: (
                item.revision.project_id, item.revision.roadmap_id,
                item.revision.revision, item.revision.revision_id,
            ),
        ))
        return _AcceptedMigrationMaterialization(
            deepcopy(stored_batch), tuple(records), head_tuple, revision_tuple,
        )

    @staticmethod
    def _legacy_accepted_projection(
        materialized: _AcceptedMigrationMaterialization, *, include_head_generation: bool,
    ) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any], str]], list[dict[str, Any]]]:
        heads = []
        for item in materialized.heads:
            binding = {
                "owner_id": item.owner_id,
                "project_id": item.project_id,
                "roadmap_id": item.roadmap_id,
                "accepted_revision_id": item.accepted_revision_id,
                "accepted_revision": item.accepted_revision,
                "accepted_content_sha256": item.accepted_content_sha256,
                "current_revision_id": item.current_revision_id,
                "current_revision": item.current_revision,
                "current_content_sha256": item.current_content_sha256,
                "approved_revision_id": item.approved_revision_id,
                "approved_revision": item.approved_revision,
                "approved_content_sha256": item.approved_content_sha256,
            }
            if include_head_generation:
                binding["generation"] = item.generation
            heads.append(binding)
        return (
            deepcopy(materialized.batch),
            deepcopy(list(materialized.records)),
            heads,
        )

    def _assert_retained_image_locked(self, entry_bytes: bytes, entry_digest: str) -> None:
        self._ensure_available()
        try:
            final_bytes = self._path.read_bytes()
            final_digest = _canonical_digest(self._state)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise DurablePlanningStoreError("stale_authority") from None
        if final_bytes != entry_bytes or final_digest != entry_digest:
            raise DurablePlanningStoreError("stale_authority")
        self._ensure_available()

    @_durable_operation
    def bind_accepted_migration_subject(
        self, principal: TrustedPrincipal, *, batch_id: str, owner_id: str,
        project_id: str, manifest_digest: str, accepted_receipt_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Append an internal, explicit subject binding for a legacy accepted batch."""
        self._ensure_available()
        ref, occurred = self._authenticate(principal)
        try:
            owner = require_owner(ref, owner_id)
            batch_target = _harbour_identifier(batch_id)
            project = _harbour_identifier(project_id)
            manifest = _digest_value(manifest_digest)
            accepted_receipt = _harbour_identifier(accepted_receipt_id)
            key = _harbour_idempotency_key(idempotency_key)
        except HarbourAuthorityError as exc:
            raise DurablePlanningStoreError(exc.code) from None
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        principal_identity = _command_principal_identity(ref)
        command = {
            "schema": "odysseus.harbour.accepted_subject_binding.v1",
            "batch_id": batch_target, "owner_id": owner, "project_id": project,
            "manifest_digest": manifest, "accepted_receipt_id": accepted_receipt,
            "principal_identity": principal_identity, "idempotency_key": key,
        }
        digest = _canonical_digest(command)
        with self._lock:
            with _interprocess_repository_lock(self.root):
                self._refresh_locked()
                batch = self._state["migration_batches"].get(batch_target)
                if batch is None:
                    raise DurablePlanningStoreError("not_found")
                if (
                    batch["state"] != "accepted"
                    or batch["owner_id"] != owner
                    or batch["project_id"] != project
                    or batch["manifest_digest"] != manifest
                    or batch["accepted_receipt_id"] != accepted_receipt
                    or batch.get("principal_identity", {}).get("issuer_id") != ref.issuer_id
                ):
                    raise DurablePlanningStoreError("idempotency_conflict")
                try:
                    self._materialize_accepted_migration_locked(
                        batch=batch, owner=owner, project_id=project,
                    )
                except DurablePlanningStoreError:
                    raise DurablePlanningStoreError("validation_failed") from None
                prior = self._state["idempotency"].get(_idempotency_scope(owner, key))
                if prior is not None:
                    if prior.get("command_digest") != digest:
                        raise DurablePlanningStoreError("idempotency_conflict")
                    record = self._state["receipts"].get(prior.get("receipt_id"))
                    try:
                        parsed = _subject_binding_receipt_from_dict(record)
                    except (ValueError, TypeError, KeyError):
                        raise DurablePlanningStoreError("validation_failed") from None
                    if (
                        parsed["batch_id"] != batch_target
                        or parsed["binding"] != {
                            "issuer_id": principal_identity["issuer_id"],
                            "subject_id": principal_identity["subject_id"],
                            "owner_id": owner,
                        }
                        or parsed["principal_identity"] != principal_identity
                    ):
                        raise DurablePlanningStoreError("idempotency_conflict")
                    return deepcopy(record)
                if "accepted_subject_binding" in batch:
                    raise DurablePlanningStoreError("idempotency_conflict")
                original_identity = batch.get("principal_identity", {})
                if (
                    original_identity.get("subject_id") != owner
                    or original_identity.get("owner_id") != owner
                    or original_identity.get("issuer_id") != ref.issuer_id
                    or type(ref.subject_id) is not str
                    or ref.subject_id == owner
                    or _IMMUTABLE_ADMIN_SUBJECT_RE.fullmatch(ref.subject_id) is None
                ):
                    raise DurablePlanningStoreError("idempotency_conflict")
                if any(
                    other_id != batch_target
                    and other.get("state") == "accepted"
                    and other.get("project_id") == project
                    and _accepted_batch_binds_identity(other, ref.issuer_id, ref.subject_id)
                    for other_id, other in self._state["migration_batches"].items()
                ):
                    raise DurablePlanningStoreError("idempotency_conflict")
                binding = {
                    "issuer_id": principal_identity["issuer_id"],
                    "subject_id": principal_identity["subject_id"],
                    "owner_id": owner,
                }
                receipt_id = "planning-subject-binding-receipt:" + hashlib.sha256(
                    (digest + ":receipt").encode("ascii")
                ).hexdigest()
                receipt = _subject_binding_receipt(
                    receipt_id=receipt_id, batch=batch, binding=binding,
                    principal_identity=principal_identity, input_digest=digest,
                    idempotency_key=key, occurred_at=occurred,
                )
                transition = {
                    "transition_id": "planning-subject-binding-transition:" + hashlib.sha256(
                        (digest + ":transition").encode("ascii")
                    ).hexdigest(),
                    "kind": "migration_bind_subject", "batch_id": batch_target,
                    "command_digest": digest, "idempotency_key": key,
                    "receipt_id": receipt_id,
                }
                candidate = deepcopy(self._state)
                changed = candidate["migration_batches"][batch_target]
                changed["accepted_subject_binding"] = binding
                changed["subject_binding_receipt_id"] = receipt_id
                candidate["transitions"].append(transition)
                candidate["receipts"][receipt_id] = receipt
                candidate["idempotency"][_idempotency_scope(owner, key)] = {
                    "command_digest": digest, "receipt_id": receipt_id,
                }
                self._persist_migration(candidate)
                return deepcopy(receipt)

    def _finish_migration_phase(
        self,
        principal: TrustedPrincipal,
        *,
        batch_id: object,
        phase: str,
        current_manifest: Callable[[], Mapping[str, Any]] | None = None,
    ) -> PlanningMigrationReceipt:
        self._ensure_available()
        ref, occurred = self._authenticate(principal)
        try:
            target = _harbour_identifier(batch_id)
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock:
            with _interprocess_repository_lock(self.root):
                self._refresh_locked()
                batch = self._state["migration_batches"].get(target)
                if batch is None:
                    raise DurablePlanningStoreError("not_found")
                try:
                    owner = require_owner(ref, batch["owner_id"])
                except HarbourAuthorityError as exc:
                    raise DurablePlanningStoreError(exc.code) from None
                expected_state = "prepared" if phase in {"accept", "abort"} else "accepted"
                before_subjects = batch["expected_subjects"] if phase in {"accept", "abort"} else batch["accepted_subjects"]
                phase_input = _migration_phase_input(
                    phase, target, batch["manifest_digest"], expected_state,
                    before_subjects, _command_principal_identity(ref),
                )
                digest = _migration_digest(phase_input)
                key = _migration_idempotency_key(phase, digest)
                replay = self._migration_idempotency_result(owner, key, digest)
                if replay is not None:
                    return replay
                if "manifest_identity" not in batch:
                    # Historical batches without a persisted manifest snapshot
                    # remain inspectable, but cannot acquire new authority from
                    # today's repository files or constants.
                    raise DurablePlanningStoreError("migration_required")
                if batch.get("principal_identity") != _command_principal_identity(ref):
                    raise DurablePlanningStoreError("idempotency_conflict")
                if batch["state"] != expected_state:
                    raise DurablePlanningStoreError("idempotency_conflict")
                _assert_migration_subjects(self._state, before_subjects)
                candidate = deepcopy(self._state)
                changed = candidate["migration_batches"][target]
                new_revision_ids: list[str] = []
                if phase == "accept":
                    if any(
                        batch_id != target
                        and prior.get("state") == "accepted"
                        and prior.get("project_id") == batch["project_id"]
                        and _accepted_batch_binds_identity(prior, ref.issuer_id, ref.subject_id)
                        for batch_id, prior in candidate["migration_batches"].items()
                    ):
                        raise DurablePlanningStoreError("idempotency_conflict")
                    accepted: list[dict[str, Any]] = []
                    for item in batch["source_items"]:
                        roadmap_id = item["roadmap_id"]
                        payload = batch["normalized_payloads"][roadmap_id].encode("utf-8")
                        canonical = _canonical_planning_bytes(_strict_json_object(payload))
                        if canonical != payload:
                            raise DurablePlanningStoreError("validation_failed")
                        _payload, _document, roadmap = _validated_planning_payload(
                            payload, batch["project_id"], roadmap_id
                        )
                        if roadmap["revision"] != 1 or roadmap["revision_state"] != "approved":
                            raise DurablePlanningStoreError("validation_failed")
                        revision_id = "planning-revision:" + hashlib.sha256(
                            (digest + ":" + roadmap_id).encode("utf-8")
                        ).hexdigest()
                        revision = PlanningRevision(
                            revision_id, owner, batch["project_id"], roadmap_id, 1,
                            None, HARBOUR_PLANNING_PAYLOAD_SCHEMA, _bytes_digest(payload),
                            roadmap["content_hash"], "harbour-state:" + revision_id,
                            "approved", occurred, ref,
                        )
                        candidate["revisions"][revision_id] = _revision_dict(revision, payload)
                        head = PlanningHead(owner, batch["project_id"], roadmap_id,
                                           revision_id, roadmap["content_hash"], revision_id, 1)
                        subject_key = _planning_key(owner, batch["project_id"], roadmap_id)
                        candidate["heads"][subject_key] = _head_dict(head)
                        candidate["head_fences"][subject_key] = 1
                        accepted.append(_migration_subject(owner, batch["project_id"], roadmap_id, head, 1))
                        new_revision_ids.append(revision_id)
                    after_state, after_subjects = "accepted", accepted
                    changed["state"] = after_state
                    changed["accepted_subjects"] = deepcopy(accepted)
                elif phase == "abort":
                    after_state, after_subjects = "aborted", deepcopy(batch["expected_subjects"])
                    changed["state"] = after_state
                elif phase == "rollback":
                    after_subjects = []
                    for subject in batch["accepted_subjects"]:
                        subject_key = subject["subject_key"]
                        candidate["heads"].pop(subject_key, None)
                        candidate["head_fences"][subject_key] = 2
                        after_subjects.append(_migration_subject(
                            owner, batch["project_id"], subject["roadmap_id"], None, 2
                        ))
                    after_state = "rolled_back"
                    changed["state"] = after_state
                else:
                    raise DurablePlanningStoreError("validation_failed")
                receipt, transition = _new_migration_receipt(
                    phase=phase, batch_id=target, command_digest=digest,
                    idempotency_key=key, before_state=expected_state,
                    after_state=after_state, before_subjects=before_subjects,
                    after_subjects=after_subjects, new_revision_ids=new_revision_ids,
                    occurred_at=occurred,
                )
                if phase == "accept":
                    changed["accepted_receipt_id"] = receipt.receipt_id
                else:
                    changed["terminal_receipt_id"] = receipt.receipt_id
                _append_migration_event(candidate, owner, key, digest, transition, receipt)
                if phase == "accept":
                    if not callable(current_manifest):
                        raise DurablePlanningStoreError("migration_required")
                    try:
                        current = _validated_migration_manifest(current_manifest())
                    except DurablePlanningStoreError:
                        raise
                    except Exception:
                        raise DurablePlanningStoreError("stale_authority") from None
                    current_identity = _migration_manifest_identity(current)
                    if (
                        current_identity != batch["manifest_identity"]
                        or current["manifest_digest"] != batch["manifest_digest"]
                        or current["batch_id"] != target
                        or current["structural_census"] != batch["structural_census"]
                    ):
                        raise DurablePlanningStoreError("stale_authority")
                self._persist_migration(candidate)
                return receipt

    @_durable_operation
    def get_head(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        project_id: str,
        roadmap_id: str,
    ) -> PlanningHead:
        _ref, owner, project, roadmap, _occurred = self._read_prelude(
            principal, owner_id, project_id, roadmap_id
        )
        with self._lock:
            self._refresh_locked()
            head = self._state["heads"].get(_planning_key(owner, project, roadmap))
            if head is None:
                raise DurablePlanningStoreError("not_found")
            return _head_from_dict(head)

    @_durable_operation
    def read_revision(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        revision_id: str,
    ) -> PlanningRevision:
        self._ensure_available()
        principal_ref, occurred = self._authenticate(principal)
        try:
            owner = require_owner(principal_ref, owner_id)
            target_id = _harbour_identifier(revision_id)
        except HarbourAuthorityError as exc:
            raise DurablePlanningStoreError(exc.code) from None
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock:
            self._refresh_locked()
            revision = self._revision(target_id)
            if revision.owner_id != owner:
                raise DurablePlanningStoreError("not_found")
            return revision

    @_durable_operation
    def read_revision_bundle(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        revision_id: str,
    ) -> PlanningContextRevisionMember:
        self._ensure_available()
        principal_ref, _occurred = self._authenticate(principal)
        try:
            owner = require_owner(principal_ref, owner_id)
            target_id = _harbour_identifier(revision_id)
        except HarbourAuthorityError as exc:
            raise DurablePlanningStoreError(exc.code) from None
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        with self._lock:
            self._refresh_locked(_read_validation_only=True)
            record = self._state["revisions"].get(target_id)
            if record is None:
                raise DurablePlanningStoreError("not_found")
            try:
                revision = _revision_from_dict(record)
                payload = bytes(base64.b64decode(record["payload_b64"], validate=True))
            except (ValueError, TypeError, KeyError):
                raise DurablePlanningStoreError("validation_failed") from None
            if revision.revision_id != target_id:
                raise DurablePlanningStoreError("validation_failed")
            if revision.owner_id != owner:
                raise DurablePlanningStoreError("not_found")
            return PlanningContextRevisionMember(revision=revision, payload=payload)

    @_durable_operation
    def read_payload(
        self,
        principal: TrustedPrincipal,
        *,
        owner_id: str,
        revision_id: str,
    ) -> bytes:
        revision = self.read_revision(
            principal, owner_id=owner_id, revision_id=revision_id
        )
        return self._payload_for_revision(revision.revision_id)

    def _payload_for_revision(self, revision_id: str, *, refresh: bool = True) -> bytes:
        with self._lock:
            if refresh:
                self._refresh_locked()
            record = self._state["revisions"][revision_id]
            try:
                return bytes(base64.b64decode(record["payload_b64"], validate=True))
            except (ValueError, TypeError):
                raise DurablePlanningStoreError("validation_failed") from None

    def _command_prelude(
        self,
        principal: TrustedPrincipal,
        owner_id: object,
        project_id: object,
        roadmap_id: object,
        idempotency_key: object,
    ) -> tuple[PrincipalRef, str, str, str, str, str]:
        self._ensure_available()
        for component in (owner_id, project_id, roadmap_id, idempotency_key):
            _require_unambiguous_component(component)
        try:
            key = _harbour_idempotency_key(idempotency_key)
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        ref, owner, project, roadmap, occurred = self._read_prelude(
            principal, owner_id, project_id, roadmap_id
        )
        return ref, owner, project, roadmap, key, occurred

    def _read_prelude(
        self,
        principal: TrustedPrincipal,
        owner_id: object,
        project_id: object,
        roadmap_id: object,
    ) -> tuple[PrincipalRef, str, str, str, str]:
        self._ensure_available()
        for component in (owner_id, project_id, roadmap_id):
            _require_unambiguous_component(component)
        ref, occurred = self._authenticate(principal)
        try:
            owner = require_owner(ref, owner_id)
        except HarbourAuthorityError as exc:
            raise DurablePlanningStoreError(exc.code) from None
        try:
            project = _harbour_identifier(project_id)
            roadmap = _harbour_identifier(roadmap_id)
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None
        return ref, owner, project, roadmap, occurred

    def _authenticate(self, principal: TrustedPrincipal) -> tuple[PrincipalRef, str]:
        # Fully normalize timezone behavior before entering any root lock.
        # A callback can run another command; the later refresh observes it.
        try:
            occurred = _harbour_now(self._clock)
            now = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
            ref = authenticate_principal(
                principal,
                trusted_issuers=self._trusted_issuers,
                now=now,
            )
        except HarbourAuthorityError as exc:
            raise DurablePlanningStoreError(exc.code) from None
        except Exception:
            raise DurablePlanningStoreError("unauthenticated") from None
        self._ensure_available()
        return _exact_principal_ref(ref), occurred

    def _ensure_available(self) -> None:
        code = self._runtime.terminal_code
        if code is not None:
            _raise_durable_planning_error(code)

    def _revision(self, revision_id: str) -> PlanningRevision:
        with self._lock:
            self._refresh_locked()
            record = self._state["revisions"].get(revision_id)
            if record is None:
                raise DurablePlanningStoreError("not_found")
            return _revision_from_dict(record)

    def _revision_for_head(self, head: PlanningHead) -> PlanningRevision:
        with self._lock:
            revision = self._revision(head.current_revision_id)
            if revision.content_sha256 != head.current_content_sha256:
                raise DurablePlanningStoreError("validation_failed")
            return revision

    def _commit_revision_command(
        self,
        *,
        command: dict[str, Any],
        idempotency_key: str,
        principal_ref: PrincipalRef,
        payload: bytes,
        document: dict[str, Any],
        roadmap_record: dict[str, Any],
        expected_head: PlanningHead | None,
        restored_revision_id: str | None,
        occurred_at: str,
        _append_validation_run: _PlanningPayloadValidationRun | None = None,
    ) -> PlanningReceipt:
        is_append = command.get("kind") == "append_revision"
        if is_append != (type(_append_validation_run) is _PlanningPayloadValidationRun):
            raise DurablePlanningStoreError("validation_failed")
        digest = _canonical_digest(command)
        with self._lock:
            with _interprocess_repository_lock(self.root):
                if is_append:
                    self._refresh_locked(
                        _append_validation_run=_append_validation_run,
                    )
                else:
                    self._refresh_locked()
                replay = self._idempotency_result(
                    command["owner_id"], idempotency_key, digest
                )
                if replay is not None:
                    return replay
                actual = self._head_or_none(
                    command["owner_id"], command["project_id"], command["roadmap_id"]
                )
                if actual != expected_head:
                    raise DurablePlanningStoreError("stale_authority")
                if command["kind"] == "append_revision":
                    if actual is None:
                        raise DurablePlanningStoreError("stale_authority")
                    current_record = self._state["revisions"].get(actual.current_revision_id)
                    if current_record is None:
                        raise DurablePlanningStoreError("validation_failed")
                    try:
                        current_revision = _revision_from_dict(current_record)
                    except ValueError:
                        raise DurablePlanningStoreError("validation_failed") from None
                    if (
                        current_revision.revision_id != actual.current_revision_id
                        or current_revision.owner_id != actual.owner_id
                        or current_revision.project_id != actual.project_id
                        or current_revision.roadmap_id != actual.roadmap_id
                        or current_revision.content_sha256 != actual.current_content_sha256
                        or roadmap_record["revision"] != current_revision.revision + 1
                    ):
                        raise DurablePlanningStoreError("validation_failed")
                subject_key = _planning_key(
                    command["owner_id"], command["project_id"], command["roadmap_id"]
                )
                if actual is None and self._state["head_fences"].get(subject_key, 0) != 0:
                    raise DurablePlanningStoreError("stale_authority")
                revision_number = int(roadmap_record["revision"])
                revision_id = "planning-revision:" + hashlib.sha256(
                    (digest + ":" + str(revision_number)).encode("ascii")
                ).hexdigest()
                if revision_id in self._state["revisions"]:
                    raise DurablePlanningStoreError("validation_failed")
                occurred = occurred_at
                revision = PlanningRevision(
                    revision_id=revision_id,
                    owner_id=command["owner_id"],
                    project_id=command["project_id"],
                    roadmap_id=command["roadmap_id"],
                    revision=revision_number,
                    parent_revision_id=actual.current_revision_id if actual else None,
                    payload_schema_id=HARBOUR_PLANNING_PAYLOAD_SCHEMA,
                    payload_sha256=_bytes_digest(payload),
                    content_sha256=roadmap_record["content_hash"],
                    immutable_locator="harbour-state:" + revision_id,
                    revision_state=roadmap_record["revision_state"],
                    created_at=occurred,
                    principal_ref=principal_ref,
                )
                approved = actual.approved_revision_id if actual else None
                if restored_revision_id is not None and revision.revision_state == "approved":
                    approved = revision.revision_id
                after = PlanningHead(
                    revision.owner_id,
                    revision.project_id,
                    revision.roadmap_id,
                    revision.revision_id,
                    revision.content_sha256,
                    approved,
                    (actual.generation if actual else 0) + 1,
                )
                return self._persist_transaction(
                    command=command,
                    command_digest=digest,
                    idempotency_key=idempotency_key,
                    before=actual,
                    after=after,
                    revision=revision,
                    payload=payload,
                    restored_revision_id=restored_revision_id,
                    occurred_at=occurred,
                    _append_validation_run=_append_validation_run,
                )

    def _commit_head_command(
        self,
        *,
        command: dict[str, Any],
        idempotency_key: str,
        expected_head: PlanningHead,
        approved_revision_id: str,
        occurred_at: str,
    ) -> PlanningReceipt:
        digest = _canonical_digest(command)
        with self._lock:
            with _interprocess_repository_lock(self.root):
                self._refresh_locked()
                replay = self._idempotency_result(
                    command["owner_id"], idempotency_key, digest
                )
                if replay is not None:
                    return replay
                actual = self._head_or_none(
                    command["owner_id"], command["project_id"], command["roadmap_id"]
                )
                if actual != expected_head:
                    raise DurablePlanningStoreError("stale_authority")
                after = PlanningHead(
                    actual.owner_id,
                    actual.project_id,
                    actual.roadmap_id,
                    actual.current_revision_id,
                    actual.current_content_sha256,
                    approved_revision_id,
                    actual.generation + 1,
                )
                return self._persist_transaction(
                    command=command,
                    command_digest=digest,
                    idempotency_key=idempotency_key,
                    before=actual,
                    after=after,
                    revision=None,
                    payload=None,
                    restored_revision_id=None,
                    occurred_at=occurred_at,
                )

    def _persist_transaction(
        self,
        *,
        command: dict[str, Any],
        command_digest: str,
        idempotency_key: str,
        before: PlanningHead | None,
        after: PlanningHead,
        revision: PlanningRevision | None,
        payload: bytes | None,
        restored_revision_id: str | None,
        occurred_at: str,
        _append_validation_run: _PlanningPayloadValidationRun | None = None,
    ) -> PlanningReceipt:
        transition_id = "planning-transition:" + hashlib.sha256(
            (command_digest + ":transition").encode("ascii")
        ).hexdigest()
        receipt_id = "planning-receipt:" + hashlib.sha256(
            (command_digest + ":receipt").encode("ascii")
        ).hexdigest()
        output = {
            "after_head": _head_dict(after),
            "new_revision_id": revision.revision_id if revision else None,
            "restored_revision_id": restored_revision_id,
        }
        receipt = PlanningReceipt(
            receipt_id=receipt_id,
            command_kind=command["kind"],
            subject_id=_planning_subject_id(
                after.owner_id, after.project_id, after.roadmap_id
            ),
            input_digest=command_digest,
            output_digest=_canonical_digest(output),
            before_head=before,
            after_head=after,
            new_revision_id=revision.revision_id if revision else None,
            restored_revision_id=restored_revision_id,
            producer="harbour_planning_store",
            occurred_at=occurred_at,
        )
        candidate = deepcopy(self._state)
        if revision is not None and payload is not None:
            candidate["revisions"][revision.revision_id] = _revision_dict(revision, payload)
        transition = {
            "transition_id": transition_id,
            "kind": command["kind"],
            "before_head": _head_dict(before) if before else None,
            "after_head": _head_dict(after),
            "new_revision_id": revision.revision_id if revision else None,
            "command_digest": command_digest,
            "idempotency_key": idempotency_key,
            "receipt_id": receipt_id,
        }
        candidate["transitions"].append(transition)
        candidate["receipts"][receipt_id] = _receipt_dict(receipt)
        candidate["heads"][_planning_key(
            after.owner_id, after.project_id, after.roadmap_id
        )] = _head_dict(after)
        candidate["head_fences"][_planning_key(
            after.owner_id, after.project_id, after.roadmap_id
        )] = after.generation
        candidate["idempotency"][_idempotency_scope(after.owner_id, idempotency_key)] = {
            "command_digest": command_digest,
            "receipt_id": receipt_id,
        }
        try:
            if command["kind"] == "append_revision":
                if type(_append_validation_run) is not _PlanningPayloadValidationRun:
                    raise DurablePlanningStoreError("validation_failed")
                with _append_planning_payload_validation_verification(
                    self, _append_validation_run, "candidate",
                ):
                    _verify_harbour_state(candidate)
                _append_validation_run.seal()
            else:
                if _append_validation_run is not None:
                    raise DurablePlanningStoreError("validation_failed")
                _verify_harbour_state(candidate)
            self._ensure_available()
            _atomic_write_json(self._path, candidate)
        except DurablePlanningStoreError:
            raise
        except (OSError, ValueError, TypeError, KeyError, PlanningDefinitionContractError):
            raise DurablePlanningStoreError("validation_failed") from None
        self._state = candidate
        return receipt

    def _persist_migration(self, candidate: dict[str, Any]) -> None:
        try:
            _verify_harbour_state(candidate)
            self._ensure_available()
            _atomic_write_json(self._path, candidate)
        except DurablePlanningStoreError:
            raise
        except (OSError, ValueError, TypeError, KeyError, PlanningDefinitionContractError):
            raise DurablePlanningStoreError("validation_failed") from None
        self._state = candidate

    def _migration_idempotency_result(
        self, owner_id: str, key: str, command_digest: str
    ) -> PlanningMigrationReceipt | None:
        prior = self._state["idempotency"].get(_idempotency_scope(owner_id, key))
        if prior is None:
            return None
        if prior.get("command_digest") != command_digest:
            raise DurablePlanningStoreError("idempotency_conflict")
        record = self._state["receipts"].get(prior.get("receipt_id"))
        if record is None:
            raise DurablePlanningStoreError("validation_failed")
        try:
            return _migration_receipt_from_dict(record)
        except ValueError:
            raise DurablePlanningStoreError("validation_failed") from None

    def _idempotency_result(
        self, owner_id: str, key: str, command_digest: str
    ) -> PlanningReceipt | None:
        prior = self._state["idempotency"].get(_idempotency_scope(owner_id, key))
        if prior is None:
            return None
        if prior.get("command_digest") != command_digest:
            raise DurablePlanningStoreError("idempotency_conflict")
        receipt = self._state["receipts"].get(prior.get("receipt_id"))
        if receipt is None:
            raise DurablePlanningStoreError("validation_failed")
        return _receipt_from_dict(receipt)

    def _head_or_none(
        self, owner_id: str, project_id: str, roadmap_id: str
    ) -> PlanningHead | None:
        value = self._state["heads"].get(_planning_key(owner_id, project_id, roadmap_id))
        return _head_from_dict(value) if value is not None else None

    def _refresh_locked(
        self, *,
        _validation_run_capture: _PlanningPayloadValidationCapture | None = None,
        _append_validation_run: _PlanningPayloadValidationRun | None = None,
        _read_validation_only: bool = False,
    ) -> bytes | None:
        # Callers hold the repository lock. Preserve the first unavailable
        # classification, including for operations that passed an earlier check.
        if (
            type(_read_validation_only) is not bool
            or _read_validation_only and (
                _validation_run_capture is not None
                or _append_validation_run is not None
            )
        ):
            raise DurablePlanningStoreError("validation_failed")
        if _read_validation_only:
            _require_planning_payload_read_validation_only_mode()
        self._ensure_available()
        raw_state = None
        try:
            if self._path.exists():
                raw_state = self._path.read_bytes()
                state = _strict_json_object(raw_state)
                state = _upgrade_harbour_state(state)
                if _read_validation_only:
                    validation_run = _verify_harbour_state(
                        state, _read_validation_only=True,
                    )
                elif _append_validation_run is None:
                    validation_run = _verify_harbour_state(state)
                else:
                    with _append_planning_payload_validation_verification(
                        self, _append_validation_run, "refresh",
                    ):
                        validation_run = _verify_harbour_state(state)
                if _legacy_key_quarantine_required(state):
                    self._runtime.publish("migration_required")
                    self._state = _new_harbour_state()
                else:
                    self._state = state
                    if _validation_run_capture is not None:
                        if type(_validation_run_capture) is not _PlanningPayloadValidationCapture:
                            raise TypeError("invalid validation capture")
                        _validation_run_capture.capture(
                            validation_run, raw_state, _canonical_digest(state),
                        )
            elif self._state != _new_harbour_state():
                raise ValueError("durable state disappeared")
        except (
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
            PlanningDefinitionContractError,
            DurablePlanningStoreError,
        ):
            self._runtime.publish("validation_failed")
            self._state = _new_harbour_state()
        self._ensure_available()
        return raw_state


@contextmanager
def _interprocess_repository_lock(root: Path):
    """Hold one crash-released OS lock across refresh, CAS and replace."""

    lock_path = root / _HARBOUR_LOCK_FILE
    handle = None
    locked = False
    try:
        handle = lock_path.open("a+b", buffering=0)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + 30.0
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise DurablePlanningStoreError("validation_failed") from None
                    time.sleep(0.01)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            locked = True
        yield
    except DurablePlanningStoreError:
        raise
    except Exception:
        raise DurablePlanningStoreError("validation_failed") from None
    finally:
        if handle is not None:
            if locked:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()


def _new_harbour_state() -> dict[str, Any]:
    return {
        "schema": HARBOUR_PLANNING_STATE_SCHEMA,
        "revisions": {},
        "transitions": [],
        "receipts": {},
        "idempotency": {},
        "heads": {},
        "head_fences": {},
        "migration_batches": {},
    }


def _upgrade_harbour_state(state: dict[str, Any]) -> dict[str, Any]:
    """Upgrade v1 only in memory; callers decide whether a later command writes."""
    if type(state) is not dict or state.get("schema") != _HARBOUR_PLANNING_STATE_SCHEMA_V1:
        return state
    if set(state) != {"schema", "revisions", "transitions", "receipts", "idempotency", "heads"}:
        raise ValueError("v1 state shape")
    if type(state["heads"]) is not dict:
        raise ValueError("v1 heads mapping")
    upgraded = deepcopy(state)
    upgraded["schema"] = HARBOUR_PLANNING_STATE_SCHEMA
    upgraded["head_fences"] = {
        key: _head_from_dict(value).generation for key, value in upgraded["heads"].items()
    }
    upgraded["migration_batches"] = {}
    return upgraded


def _legacy_key_quarantine_required(state: dict[str, Any]) -> bool:
    """Classify raw keys only after complete structural replay verification."""
    for name, delimiters in (("heads", 2), ("head_fences", 2), ("idempotency", 1)):
        if any(key.count(":") > delimiters for key in state[name]):
            return True
    for batch in state["migration_batches"].values():
        for name in ("expected_subjects", "accepted_subjects"):
            if any(subject["subject_key"].count(":") > 2 for subject in batch[name]):
                return True
    for receipt in state["receipts"].values():
        if receipt.get("receipt_type") == "planning_migration":
            for name in ("before_subjects", "after_subjects"):
                if any(subject["subject_key"].count(":") > 2 for subject in receipt[name]):
                    return True
    return False


def _planning_payload_validation_dependencies() -> tuple[object, ...]:
    return (
        _validated_planning_payload,
        _strict_json_object,
        validate_planning_definition,
        _canonical_planning_bytes,
        deepcopy,
    )


class _PlanningPayloadValidationRun:
    """Opaque identity for one replay-local validation run."""

    __slots__ = ("__weakref__",)

    def __init__(
        self, *, _append_store: object | None = None,
        _append_invocation: object | None = None,
    ) -> None:
        _register_planning_payload_validation_run(
            self,
            append_store=_append_store,
            append_invocation=_append_invocation,
        )

    def __init_subclass__(cls, **kwargs) -> None:
        raise TypeError("planning validation runs cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("planning validation runs are immutable")

    def __repr__(self) -> str:
        return "_PlanningPayloadValidationRun(<opaque>)"

    def checkout(
        self, value: object, project_id: str, roadmap_id: str,
    ) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
        return _checkout_planning_payload_validation_run(
            self, value, project_id, roadmap_id,
        )

    def seal(self) -> None:
        _seal_planning_payload_validation_run(self)

    def bind_image(self, state_bytes: bytes, state_digest: str) -> None:
        _bind_planning_payload_validation_run(self, state_bytes, state_digest)

    def begin_materialization(self, state_bytes: object, state_digest: object) -> None:
        _begin_planning_payload_validation_materialization(
            self, state_bytes, state_digest,
        )


def _build_planning_payload_validation_registry():
    """Keep all reusable validation masters outside caller-reachable objects."""
    pair_rows: dict[int, dict[str, Any]] = {}
    run_rows: dict[int, dict[str, Any]] = {}
    guard = threading.RLock()
    dependency_reader = _planning_payload_validation_dependencies
    current_thread = threading.current_thread
    get_ident = threading.get_ident
    getpid = os.getpid
    ref = weakref.ref
    active_pair_local = threading.local()
    active_read_local = threading.local()
    active_append_local = threading.local()
    active_append_verification_local = threading.local()

    class PairHandle:
        __slots__ = ("__weakref__",)

        def __setattr__(self, name: str, value: object) -> None:
            raise AttributeError("planning validation pair handles are immutable")

        def __repr__(self) -> str:
            return "_PlanningPayloadValidationPair(<opaque>)"

    class AppendInvocation:
        __slots__ = ("__weakref__",)

        def __setattr__(self, name: str, value: object) -> None:
            raise AttributeError("append validation invocations are immutable")

    def same_dependencies(actual: object, expected: object) -> bool:
        if type(actual) is not tuple or type(expected) is not tuple:
            return False
        if len(actual) != len(expected):
            return False
        for index in range(len(expected)):
            if actual[index] is not expected[index]:
                return False
        return True

    def context_identity() -> tuple[threading.Thread, int, int]:
        return current_thread(), get_ident(), getpid()

    def fail() -> None:
        raise DurablePlanningStoreError("validation_failed")

    def require_read_validation_only_mode() -> None:
        if (
            getattr(active_read_local, "scope", None) is not None
            or getattr(active_append_local, "scope", None) is not None
            or getattr(active_append_verification_local, "scope", None) is not None
        ):
            fail()

    def active_pairs() -> dict[int, object]:
        rows = getattr(active_pair_local, "rows", None)
        if rows is None:
            rows = {}
            active_pair_local.rows = rows
        if type(rows) is not dict:
            fail()
        return rows

    def validate_pair_locked(
        handle: object, store: object | None = None,
    ) -> dict[str, Any]:
        if type(handle) is not PairHandle:
            fail()
        row = pair_rows.get(id(handle))
        thread, thread_id, process_id = context_identity()
        try:
            bound_store = row["store"]()
            valid = (
                type(row) is dict
                and row["handle"]() is handle
                and bound_store is not None
                and (store is None or bound_store is store)
                and row["thread"]() is thread
                and row["thread_id"] == thread_id
                and row["process_id"] == process_id
                and active_pairs().get(id(bound_store)) is handle
                and getattr(bound_store._planning_validation_pair_local, "scope", None) is handle
                and type(row["memo"]) is dict
                and type(row["memo_bytes"]) is int
                and row["memo_bytes"] >= 0
                and type(row["read_count"]) is int
                and 0 <= row["read_count"] <= 2
                and type(row["read_open"]) is bool
                and same_dependencies(dependency_reader(), row["dependencies"])
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            fail()
        return row

    def validate_run_locked(run: object) -> dict[str, Any]:
        if type(run) is not _PlanningPayloadValidationRun:
            fail()
        row = run_rows.get(id(run))
        thread, thread_id, process_id = context_identity()
        try:
            valid = (
                type(row) is dict
                and row["run"]() is run
                and row["thread"]() is thread
                and row["thread_id"] == thread_id
                and row["process_id"] == process_id
                and type(row["memo"]) is dict
                and type(row["sealed"]) is bool
                and type(row["materialized"]) is bool
                and same_dependencies(dependency_reader(), row["dependencies"])
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            fail()
        kind = row.get("kind")
        if kind == "append":
            active = getattr(active_append_local, "scope", None)
            try:
                bound_store = row["append_store"]()
                invocation = row["append_invocation"]()
                valid = (
                    type(active) is tuple
                    and len(active) == 2
                    and active[0] is invocation
                    and active[1] is bound_store
                    and bound_store is not None
                    and type(invocation) is AppendInvocation
                    and row["pair"] is None
                    and row["phase"] in {
                        "new_payload", "payload_committed", "refresh_verifying",
                        "refresh_verified", "candidate_verifying", "candidate_verified",
                        "sealed",
                    }
                    and type(row["initial_checkout_count"]) is int
                    and 0 <= row["initial_checkout_count"] <= 1
                    and (
                        row["initial_key"] is None
                        or type(row["initial_key"]) is tuple
                    )
                    and type(row["verification_claimed"]) is bool
                    and type(row["verification_completed"]) is bool
                )
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                fail()
        elif kind != "ordinary" or any(
            row.get(name) is not None
            for name in ("append_store", "append_invocation", "phase", "initial_key")
        ):
            fail()
        pair = row["pair"]
        if pair is not None:
            pair_row = validate_pair_locked(pair)
            if pair_row["read_open"] and pair_row["active_run"]() is not run:
                fail()
        return row

    def clear_run(row: object) -> None:
        if type(row) is not dict:
            return
        memo = row.get("memo")
        try:
            if type(memo) is dict:
                dict.clear(memo)
            row["bound_state_bytes"] = None
            row["bound_state_digest"] = None
            row["append_store"] = None
            row["append_invocation"] = None
            row["initial_key"] = None
        except BaseException:
            pass

    def discard_run(run: object) -> None:
        if type(run) is not _PlanningPayloadValidationRun:
            return
        row = None
        try:
            with guard:
                candidate = run_rows.get(id(run))
                if type(candidate) is dict and candidate.get("run")() is run:
                    row = run_rows.pop(id(run), None)
        except BaseException:
            pass
        clear_run(row)

    def run_collected(identity: int):
        def collected(dead_ref: object) -> None:
            with guard:
                row = run_rows.pop(identity, None)
            clear_run(row)
        return collected

    @contextmanager
    def pair_scope(store: object):
        if type(store) is not DurablePlanningRevisionStore:
            fail()
        local = store._planning_validation_pair_local
        rows = active_pairs()
        store_key = id(store)
        if rows or getattr(local, "scope", None) is not None:
            fail()
        handle = PairHandle()
        identity = id(handle)
        thread, thread_id, process_id = context_identity()
        dependencies = dependency_reader()
        if type(dependencies) is not tuple:
            fail()
        row = {
            "handle": ref(handle),
            "store": ref(store),
            "thread": ref(thread),
            "thread_id": thread_id,
            "process_id": process_id,
            "dependencies": dependencies,
            "memo": {},
            "memo_bytes": 0,
            "read_count": 0,
            "read_open": False,
            "active_run": lambda: None,
        }
        with guard:
            if identity in pair_rows:
                fail()
            pair_rows[identity] = row
            rows[store_key] = handle
        try:
            local.scope = handle
            yield None
            with guard:
                current = validate_pair_locked(handle, store)
                if (
                    current["read_count"] != 2
                    or current["read_open"]
                    or current["active_run"]() is not None
                ):
                    fail()
        finally:
            with guard:
                removed = pair_rows.pop(identity, None)
            try:
                active = getattr(active_pair_local, "rows", None)
                if type(active) is dict:
                    active.pop(store_key, None)
            except BaseException:
                pass
            try:
                if hasattr(local, "scope"):
                    del local.scope
            except BaseException:
                pass
            try:
                memo = removed.get("memo") if type(removed) is dict else None
                if type(memo) is dict:
                    dict.clear(memo)
            except BaseException:
                pass

    @contextmanager
    def pair_read(store: object):
        if type(store) is not DurablePlanningRevisionStore:
            fail()
        local = store._planning_validation_pair_local
        rows = active_pairs()
        handle = rows.get(id(store))
        mirror = getattr(local, "scope", None)
        if handle is None and mirror is None:
            yield None
            return
        if handle is None or mirror is not handle:
            fail()
        if getattr(active_read_local, "scope", None) is not None:
            fail()
        with guard:
            row = validate_pair_locked(handle, store)
            if row["read_count"] >= 2 or row["read_open"] or row["active_run"]() is not None:
                fail()
            row["read_count"] += 1
            row["read_open"] = True
        try:
            active_read_local.scope = handle
            yield None
        finally:
            try:
                if hasattr(active_read_local, "scope"):
                    del active_read_local.scope
            except BaseException:
                pass
            with guard:
                current = pair_rows.get(id(handle))
                if type(current) is dict and current.get("handle")() is handle:
                    current["read_open"] = False
                    current["active_run"] = lambda: None

    def register_run(
        run: object, *, append_store: object | None = None,
        append_invocation: object | None = None,
    ) -> None:
        if type(run) is not _PlanningPayloadValidationRun:
            fail()
        identity = id(run)
        thread, thread_id, process_id = context_identity()
        dependencies = dependency_reader()
        if type(dependencies) is not tuple:
            fail()
        append = append_store is not None or append_invocation is not None
        if append:
            active = getattr(active_append_local, "scope", None)
            if (
                type(append_store) is not DurablePlanningRevisionStore
                or type(append_invocation) is not AppendInvocation
                or type(active) is not tuple
                or len(active) != 2
                or active[0] is not append_invocation
                or active[1] is not append_store
                or getattr(active_read_local, "scope", None) is not None
            ):
                fail()
            pair = None
        else:
            if append_store is not None or append_invocation is not None:
                fail()
            pair = getattr(active_read_local, "scope", None)
        with guard:
            if identity in run_rows:
                fail()
            if pair is not None:
                pair_row = validate_pair_locked(pair)
                if not pair_row["read_open"] or pair_row["active_run"]() is not None:
                    pair = None
                elif not same_dependencies(dependencies, pair_row["dependencies"]):
                    fail()
            run_ref = ref(run, run_collected(identity))
            run_rows[identity] = {
                "run": run_ref,
                "thread": ref(thread),
                "thread_id": thread_id,
                "process_id": process_id,
                "dependencies": dependencies,
                "pair": pair,
                "kind": "append" if append else "ordinary",
                "append_store": ref(append_store) if append else None,
                "append_invocation": ref(append_invocation) if append else None,
                "phase": "new_payload" if append else None,
                "initial_key": None,
                "initial_checkout_count": 0,
                "verification_claimed": False,
                "verification_completed": False,
                "memo": {},
                "sealed": False,
                "bound_state_bytes": None,
                "bound_state_digest": None,
                "materialized": False,
            }
            if pair is not None:
                pair_row["active_run"] = run_ref

    @contextmanager
    def append_scope(store: object):
        if type(store) is not DurablePlanningRevisionStore:
            fail()
        if (
            getattr(active_append_local, "scope", None) is not None
            or getattr(active_append_verification_local, "scope", None) is not None
            or getattr(active_read_local, "scope", None) is not None
        ):
            fail()
        invocation = AppendInvocation()
        active_append_local.scope = (invocation, store)
        run = None
        try:
            run = _PlanningPayloadValidationRun(
                _append_store=store,
                _append_invocation=invocation,
            )
            yield run
        finally:
            discard_run(run)
            try:
                current = getattr(active_append_local, "scope", None)
                if type(current) is tuple and current[0] is invocation:
                    del active_append_local.scope
            except BaseException:
                pass

    def commit_append_payload(
        run: object, store: object, payload: object,
        project_id: object, roadmap_id: object,
    ) -> None:
        if (
            type(payload) is not bytes
            or type(project_id) is not str
            or type(roadmap_id) is not str
        ):
            fail()
        key = (bytes(payload), project_id, roadmap_id)
        with guard:
            row = validate_run_locked(run)
            bound_store = row["append_store"]() if row["kind"] == "append" else None
            memo = row["memo"]
            if (
                row["kind"] != "append"
                or bound_store is not store
                or row["phase"] != "new_payload"
                or row["initial_checkout_count"] != 1
                or row["initial_key"] != key
                or len(memo) > 1
                or (memo and key not in memo)
            ):
                fail()
            master = memo.get(key)
        if master is not None and type(master) is not dict:
            fail()
        with guard:
            row = validate_run_locked(run)
            if row["phase"] != "new_payload" or row["initial_key"] != key:
                fail()
            row["phase"] = "payload_committed"

    @contextmanager
    def append_verification(store: object, run: object, phase: object):
        if phase not in {"refresh", "candidate"}:
            fail()
        expected = "payload_committed" if phase == "refresh" else "refresh_verified"
        verifying = phase + "_verifying"
        completed = phase + "_verified"
        with guard:
            row = validate_run_locked(run)
            bound_store = row["append_store"]() if row["kind"] == "append" else None
            invocation = row["append_invocation"]() if row["kind"] == "append" else None
            if (
                bound_store is not store
                or row["phase"] != expected
                or getattr(active_append_verification_local, "scope", None) is not None
            ):
                fail()
            row["phase"] = verifying
            row["verification_claimed"] = False
            row["verification_completed"] = False
            active_append_verification_local.scope = (run, store, invocation, phase)
        succeeded = False
        try:
            yield None
            succeeded = True
        finally:
            try:
                if hasattr(active_append_verification_local, "scope"):
                    del active_append_verification_local.scope
            except BaseException:
                pass
        if succeeded:
            with guard:
                row = validate_run_locked(run)
                if (
                    row["phase"] != verifying
                    or not row["verification_claimed"]
                    or not row["verification_completed"]
                ):
                    fail()
                row["phase"] = completed
                row["verification_claimed"] = False
                row["verification_completed"] = False

    def active_append_verification_run() -> _PlanningPayloadValidationRun | None:
        scope = getattr(active_append_verification_local, "scope", None)
        if scope is None:
            return None
        if type(scope) is not tuple or len(scope) != 4:
            fail()
        run, store, invocation, phase = scope
        with guard:
            row = validate_run_locked(run)
            if (
                row["kind"] != "append"
                or row["append_store"]() is not store
                or row["append_invocation"]() is not invocation
                or row["phase"] != phase + "_verifying"
                or row["verification_claimed"]
                or row["verification_completed"]
            ):
                fail()
            row["verification_claimed"] = True
        return run

    def complete_append_verification(run: object) -> None:
        scope = getattr(active_append_verification_local, "scope", None)
        if type(scope) is not tuple or len(scope) != 4 or scope[0] is not run:
            fail()
        with guard:
            row = validate_run_locked(run)
            if not row["verification_claimed"] or row["verification_completed"]:
                fail()
            row["verification_completed"] = True

    def pair_checkout(
        pair: object, key: tuple[bytes, str, str], dependencies: tuple[object, ...],
    ) -> dict[str, Any] | None:
        with guard:
            row = validate_pair_locked(pair)
            if row["read_count"] != 2 or not same_dependencies(dependencies, row["dependencies"]):
                return None
            master = row["memo"].get(key)
        if master is None:
            return None
        if type(master) is not dict or dependencies[3](master) != key[0]:
            raise ValueError("paired validation memo changed")
        return master

    def pair_remember(
        pair: object,
        key: tuple[bytes, str, str],
        master: dict[str, Any],
        dependencies: tuple[object, ...],
    ) -> None:
        with guard:
            row = validate_pair_locked(pair)
            if row["read_count"] != 1 or not same_dependencies(dependencies, row["dependencies"]):
                return
            if key in row["memo"]:
                return
            payload_bytes = len(key[0])
            if (
                len(row["memo"]) >= _PLANNING_VALIDATION_PAIR_MAX_ENTRIES
                or payload_bytes > _PLANNING_VALIDATION_PAIR_MAX_BYTES - row["memo_bytes"]
            ):
                return
        if type(master) is not dict or dependencies[3](master) != key[0]:
            raise ValueError("paired validation memo was not canonical")
        with guard:
            row = validate_pair_locked(pair)
            if (
                row["read_count"] == 1
                and same_dependencies(dependencies, row["dependencies"])
                and key not in row["memo"]
                and len(row["memo"]) < _PLANNING_VALIDATION_PAIR_MAX_ENTRIES
                and payload_bytes <= _PLANNING_VALIDATION_PAIR_MAX_BYTES - row["memo_bytes"]
            ):
                row["memo"][key] = master
                row["memo_bytes"] += payload_bytes

    def checkout_run(
        run: object, value: object, project_id: str, roadmap_id: str,
    ) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
        with guard:
            row = validate_run_locked(run)
            dependencies = row["dependencies"]
            pair = row["pair"]
            sealed = row["sealed"]
            kind = row["kind"]
            phase = row["phase"]
            if kind == "append" and not (
                (phase == "new_payload" and row["initial_checkout_count"] == 0)
                or (
                    phase in {"refresh_verifying", "candidate_verifying"}
                    and not row["verification_completed"]
                )
            ):
                fail()
        if (
            type(value) is not bytes
            or type(project_id) is not str
            or type(roadmap_id) is not str
            or len(value) < 1
        ):
            fail()
        payload = bytes(value)
        key = (payload, project_id, roadmap_id)
        try:
            with guard:
                row = validate_run_locked(run)
                master = row["memo"].get(key)
            if master is not None:
                # Append masters are transaction-local, registry-owned copies;
                # callers receive only detached documents. Ordinary/read-pair
                # runs retain their defensive canonical recheck.
                if (
                    type(master) is not dict
                    or (kind != "append" and dependencies[3](master) != payload)
                ):
                    raise ValueError("validation memo changed")
                detached = dependencies[4](master)
            else:
                paired = (
                    pair_checkout(pair, key, dependencies)
                    if pair is not None and not sealed else None
                )
                if paired is not None:
                    with guard:
                        row = validate_run_locked(run)
                        if row["sealed"]:
                            raise ValueError("sealed validation memo miss")
                        row["memo"][key] = paired
                    detached = dependencies[4](paired)
                else:
                    document = dependencies[1](payload)
                    dependencies[2](document)
                    if document["project"]["project_id"] != project_id:
                        raise ValueError
                    if document["project"]["roadmap_refs"] != [roadmap_id]:
                        raise ValueError
                    if len(document["roadmaps"]) != 1:
                        raise ValueError
                    roadmap = document["roadmaps"][0]
                    if roadmap["project_id"] != project_id or roadmap["roadmap_id"] != roadmap_id:
                        raise ValueError
                    if dependencies[3](document) == payload:
                        if sealed:
                            raise ValueError("sealed validation memo miss")
                        master = dependencies[4](document)
                        if type(master) is not dict or master is document:
                            raise ValueError("validation memo master did not detach")
                        with guard:
                            row = validate_run_locked(run)
                            if row["sealed"]:
                                raise ValueError("sealed validation memo miss")
                            row["memo"][key] = master
                        if pair is not None:
                            pair_remember(pair, key, master, dependencies)
                        detached = dependencies[4](master)
                    else:
                        detached = dependencies[4](document)
            with guard:
                row = validate_run_locked(run)
                private_master = row["memo"].get(key)
            if type(detached) is not dict or detached is private_master:
                raise ValueError("validation memo did not detach")
            if kind == "append" and phase == "new_payload":
                with guard:
                    row = validate_run_locked(run)
                    if row["phase"] != "new_payload" or row["initial_checkout_count"] != 0:
                        fail()
                    row["initial_checkout_count"] = 1
                    row["initial_key"] = key
            roadmap = detached["roadmaps"][0]
            return payload, detached, roadmap
        except (
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
            PlanningDefinitionContractError,
        ):
            raise DurablePlanningStoreError("validation_failed") from None

    def seal_run(run: object) -> None:
        with guard:
            row = validate_run_locked(run)
            if row["sealed"]:
                fail()
            if row["kind"] == "append" and row["phase"] != "candidate_verified":
                fail()
            row["sealed"] = True
            if row["kind"] == "append":
                row["phase"] = "sealed"

    def bind_run(run: object, state_bytes: bytes, state_digest: str) -> None:
        with guard:
            row = validate_run_locked(run)
            if (
                row["kind"] != "ordinary"
                or not row["sealed"]
                or row["bound_state_bytes"] is not None
                or row["bound_state_digest"] is not None
                or type(state_bytes) is not bytes
                or type(state_digest) is not str
            ):
                fail()
        try:
            _digest_value(state_digest)
        except (TypeError, ValueError):
            raise DurablePlanningStoreError("validation_failed") from None
        with guard:
            row = validate_run_locked(run)
            if row["bound_state_bytes"] is not None or row["bound_state_digest"] is not None:
                fail()
            row["bound_state_bytes"] = bytes(state_bytes)
            row["bound_state_digest"] = state_digest

    def bound_matches(run: object, state_bytes: object, state_digest: object) -> bool:
        with guard:
            row = validate_run_locked(run)
            if row["kind"] != "ordinary":
                fail()
            return (
                type(state_bytes) is bytes
                and type(state_digest) is str
                and row["bound_state_bytes"] == state_bytes
                and row["bound_state_digest"] == state_digest
            )

    def begin_materialization(
        run: object, state_bytes: object, state_digest: object,
    ) -> None:
        with guard:
            row = validate_run_locked(run)
            if (
                row["kind"] != "ordinary"
                or not row["sealed"]
                or row["materialized"]
                or type(state_bytes) is not bytes
                or type(state_digest) is not str
                or row["bound_state_bytes"] != state_bytes
                or row["bound_state_digest"] != state_digest
            ):
                fail()
            row["materialized"] = True

    return (
        pair_scope,
        pair_read,
        require_read_validation_only_mode,
        append_scope,
        register_run,
        commit_append_payload,
        append_verification,
        active_append_verification_run,
        complete_append_verification,
        discard_run,
        checkout_run,
        seal_run,
        bind_run,
        bound_matches,
        begin_materialization,
    )


(
    _planning_payload_validation_pair_scope,
    _planning_payload_validation_pair_read,
    _require_planning_payload_read_validation_only_mode,
    _append_planning_payload_validation_scope,
    _register_planning_payload_validation_run,
    _commit_append_planning_payload_validation,
    _append_planning_payload_validation_verification,
    _active_append_planning_payload_validation_run,
    _complete_append_planning_payload_validation_verification,
    _discard_planning_payload_validation_run,
    _checkout_planning_payload_validation_run,
    _seal_planning_payload_validation_run,
    _bind_planning_payload_validation_run,
    _planning_payload_validation_run_bound_matches,
    _begin_planning_payload_validation_materialization,
) = _build_planning_payload_validation_registry()
del _build_planning_payload_validation_registry


class _PlanningPayloadValidationCapture:
    """Lexical handoff from one successful refresh to its bundle materialization."""

    __slots__ = ("_run", "_taken")

    def __init__(self) -> None:
        self._run: _PlanningPayloadValidationRun | None = None
        self._taken = False

    def capture(
        self, run: object, state_bytes: bytes, state_digest: str,
    ) -> None:
        if type(run) is not _PlanningPayloadValidationRun or self._run is not None:
            raise DurablePlanningStoreError("validation_failed")
        run.bind_image(state_bytes, state_digest)
        self._run = run

    def take(self, state_bytes: bytes, state_digest: str) -> _PlanningPayloadValidationRun:
        run = self._run
        if self._taken or type(run) is not _PlanningPayloadValidationRun:
            raise DurablePlanningStoreError("validation_failed")
        if not _planning_payload_validation_run_bound_matches(
            run, state_bytes, state_digest,
        ):
            raise DurablePlanningStoreError("validation_failed")
        self._taken = True
        return run


def _validated_planning_payload(
    value: object,
    project_id: str,
    roadmap_id: str,
    *,
    _validation_run: _PlanningPayloadValidationRun | None = None,
) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    if _validation_run is not None:
        if type(_validation_run) is not _PlanningPayloadValidationRun:
            raise DurablePlanningStoreError("validation_failed")
        return _validation_run.checkout(value, project_id, roadmap_id)
    if type(value) is not bytes or len(value) < 1:
        raise DurablePlanningStoreError("validation_failed")
    payload = bytes(value)
    try:
        document = _strict_json_object(payload)
        validate_planning_definition(document)
        if document["project"]["project_id"] != project_id:
            raise ValueError
        if document["project"]["roadmap_refs"] != [roadmap_id]:
            raise ValueError
        if len(document["roadmaps"]) != 1:
            raise ValueError
        roadmap = document["roadmaps"][0]
        if roadmap["project_id"] != project_id or roadmap["roadmap_id"] != roadmap_id:
            raise ValueError
        return payload, document, roadmap
    except (UnicodeError, ValueError, TypeError, KeyError, RecursionError, PlanningDefinitionContractError):
        raise DurablePlanningStoreError("validation_failed") from None


def _strict_json_object(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise ValueError("invalid bytes")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if type(key) is not str or key in result:
                raise ValueError("duplicate or invalid key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")),
        )
    except RecursionError:
        # Small byte strings can still exceed the decoder's nesting budget.
        # Replay must classify this exactly like any other malformed image.
        raise ValueError("JSON nesting exceeds decoder budget") from None
    if type(value) is not dict:
        raise ValueError("root is not object")
    return value


def _verify_harbour_state(
    state: dict[str, Any], *, _read_validation_only: bool = False,
) -> _PlanningPayloadValidationRun | None:
    if type(_read_validation_only) is not bool:
        raise ValueError("invalid read validation mode")
    if _read_validation_only:
        _require_planning_payload_read_validation_only_mode()
        validation_run = None
        append_validation = False
    else:
        validation_run = _active_append_planning_payload_validation_run()
        append_validation = validation_run is not None
        if validation_run is None:
            validation_run = _PlanningPayloadValidationRun()
    if type(state) is not dict or set(state) != {
        "schema", "revisions", "transitions", "receipts", "idempotency", "heads",
        "head_fences", "migration_batches"
    }:
        raise ValueError("state shape")
    if state["schema"] != HARBOUR_PLANNING_STATE_SCHEMA:
        raise ValueError("state schema")
    if any(type(state[name]) is not dict for name in (
        "revisions", "receipts", "idempotency", "heads", "head_fences", "migration_batches"
    )):
        raise ValueError("state mappings")
    if type(state["transitions"]) is not list:
        raise ValueError("state transitions")
    revisions: dict[str, PlanningRevision] = {}
    for revision_id, record in state["revisions"].items():
        revision = _revision_from_dict(record)
        if revision_id != revision.revision_id:
            raise ValueError("revision key")
        payload = base64.b64decode(record["payload_b64"], validate=True)
        if _bytes_digest(payload) != revision.payload_sha256:
            raise ValueError("payload digest")
        if _read_validation_only:
            _payload, _document, roadmap = _validated_planning_payload(
                bytes(payload), revision.project_id, revision.roadmap_id,
            )
        else:
            _payload, _document, roadmap = _validated_planning_payload(
                bytes(payload), revision.project_id, revision.roadmap_id,
                _validation_run=validation_run,
            )
        if (
            _payload != payload
            or roadmap["revision"] != revision.revision
            or roadmap["content_hash"] != revision.content_sha256
            or roadmap["revision_state"] != revision.revision_state
            or revision.principal_ref.owner_id != revision.owner_id
        ):
            raise ValueError("revision payload binding")
        revisions[revision_id] = revision
    replay: dict[str, dict[str, Any]] = {}
    replay_fences: dict[str, int] = {}
    seen_transitions: set[str] = set()
    seen_receipts: set[str] = set()
    referenced_revisions: set[str] = set()
    for transition in state["transitions"]:
        if type(transition) is dict and set(transition) == {
            "transition_id", "kind", "batch_id", "command_digest",
            "idempotency_key", "receipt_id"
        }:
            transition_id = _harbour_identifier(transition["transition_id"])
            if transition_id in seen_transitions:
                raise ValueError("duplicate transition")
            seen_transitions.add(transition_id)
            batch_id = _harbour_identifier(transition["batch_id"])
            batch = state["migration_batches"].get(batch_id)
            if batch is None:
                raise ValueError("migration batch missing")
            digest = _digest_value(transition["command_digest"])
            _harbour_idempotency_key(transition["idempotency_key"])
            receipt_id = _harbour_identifier(transition["receipt_id"])
            if receipt_id in seen_receipts or receipt_id not in state["receipts"]:
                raise ValueError("migration receipt")
            receipt_value = state["receipts"][receipt_id]
            if type(receipt_value) is dict and receipt_value.get("receipt_type") == "planning_subject_binding":
                binding_receipt = _subject_binding_receipt_from_dict(receipt_value)
                command = {
                    "schema": "odysseus.harbour.accepted_subject_binding.v1",
                    "batch_id": binding_receipt["batch_id"],
                    "owner_id": binding_receipt["owner_id"],
                    "project_id": binding_receipt["project_id"],
                    "manifest_digest": binding_receipt["manifest_digest"],
                    "accepted_receipt_id": binding_receipt["accepted_receipt_id"],
                    "principal_identity": binding_receipt["principal_identity"],
                    "idempotency_key": binding_receipt["idempotency_key"],
                }
                batch = state["migration_batches"].get(batch_id)
                if (
                    batch is None or batch.get("state") not in {"accepted", "rolled_back"}
                    or binding_receipt["receipt_id"] != receipt_id
                    or batch.get("subject_binding_receipt_id") != receipt_id
                    or batch.get("accepted_subject_binding") != binding_receipt["binding"]
                    or binding_receipt["batch_id"] != batch_id
                    or binding_receipt["owner_id"] != batch["owner_id"]
                    or binding_receipt["project_id"] != batch["project_id"]
                    or binding_receipt["manifest_digest"] != batch["manifest_digest"]
                    or binding_receipt["accepted_receipt_id"] != batch["accepted_receipt_id"]
                    or transition["kind"] != "migration_bind_subject"
                    or binding_receipt["input_digest"] != digest
                    or _canonical_digest(command) != digest
                    or binding_receipt["receipt_id"] != "planning-subject-binding-receipt:" + hashlib.sha256(
                        (digest + ":receipt").encode("ascii")
                    ).hexdigest()
                    or transition_id != "planning-subject-binding-transition:" + hashlib.sha256(
                        (digest + ":transition").encode("ascii")
                    ).hexdigest()
                    or transition["idempotency_key"] != binding_receipt["idempotency_key"]
                ):
                    raise ValueError("subject binding transition binding")
                seen_receipts.add(receipt_id)
                continue
            receipt = _migration_receipt_from_dict(receipt_value)
            if (
                receipt.receipt_id != receipt_id
                or receipt.batch_id != batch_id
                or receipt.input_digest != digest
                or transition["kind"] != "migration_" + receipt.phase
            ):
                raise ValueError("migration transition binding")
            _assert_replay_subjects(replay, replay_fences, receipt.before_subjects)
            for subject in receipt.after_subjects:
                parsed = _migration_subject_from_dict(subject)
                key = parsed["subject_key"]
                head = parsed["head"]
                fence = parsed["fence"]
                if head is None:
                    replay.pop(key, None)
                else:
                    revision = revisions.get(head.current_revision_id)
                    if revision is None or (
                        revision.owner_id, revision.project_id, revision.roadmap_id,
                        revision.content_sha256
                    ) != (head.owner_id, head.project_id, head.roadmap_id, head.current_content_sha256):
                        raise ValueError("migration head revision")
                    replay[key] = _head_dict(head)
                if fence:
                    replay_fences[key] = fence
            if receipt.output_digest != _migration_digest({
                "after_state": receipt.after_state,
                "after_subjects": list(receipt.after_subjects),
                "new_revision_ids": list(receipt.new_revision_ids),
            }):
                raise ValueError("migration output digest")
            if receipt.phase == "accept":
                if len(receipt.new_revision_ids) != 6 or any(item not in revisions for item in receipt.new_revision_ids):
                    raise ValueError("migration revision set")
                referenced_revisions.update(receipt.new_revision_ids)
            elif receipt.new_revision_ids:
                raise ValueError("unexpected migration revisions")
            seen_receipts.add(receipt_id)
            continue
        if type(transition) is not dict or set(transition) != {
            "transition_id", "kind", "before_head", "after_head", "new_revision_id",
            "command_digest", "idempotency_key", "receipt_id"
        }:
            raise ValueError("transition shape")
        transition_id = _harbour_identifier(transition["transition_id"])
        if transition_id in seen_transitions:
            raise ValueError("duplicate transition")
        seen_transitions.add(transition_id)
        _digest_value(transition["command_digest"])
        _harbour_idempotency_key(transition["idempotency_key"])
        after = _head_from_dict(transition["after_head"])
        before = _head_from_dict(transition["before_head"]) if transition["before_head"] is not None else None
        subject = _planning_key(after.owner_id, after.project_id, after.roadmap_id)
        current = _head_from_dict(replay[subject]) if subject in replay else None
        if before != current or after.generation != (before.generation if before else 0) + 1:
            raise ValueError("transition chain")
        if before is not None and (
            before.owner_id, before.project_id, before.roadmap_id
        ) != (after.owner_id, after.project_id, after.roadmap_id):
            raise ValueError("transition subject")
        current_revision = revisions.get(after.current_revision_id)
        if current_revision is None or (
            current_revision.owner_id,
            current_revision.project_id,
            current_revision.roadmap_id,
            current_revision.content_sha256,
        ) != (
            after.owner_id,
            after.project_id,
            after.roadmap_id,
            after.current_content_sha256,
        ):
            raise ValueError("head revision binding")
        if after.approved_revision_id is not None:
            approved_revision = revisions.get(after.approved_revision_id)
            if approved_revision is None or (
                approved_revision.owner_id,
                approved_revision.project_id,
                approved_revision.roadmap_id,
                approved_revision.revision_state,
            ) != (after.owner_id, after.project_id, after.roadmap_id, "approved"):
                raise ValueError("approved revision binding")
        new_id = transition["new_revision_id"]
        if new_id is not None:
            revision = revisions.get(new_id)
            if revision is None or revision.revision_id != after.current_revision_id:
                raise ValueError("transition revision")
            if revision.parent_revision_id != (before.current_revision_id if before else None):
                raise ValueError("revision parent")
            referenced_revisions.add(new_id)
        elif before is None or after.current_revision_id != before.current_revision_id:
            raise ValueError("head-only transition")
        receipt_id = _harbour_identifier(transition["receipt_id"])
        receipt_record = state["receipts"].get(receipt_id)
        if receipt_record is None or receipt_id in seen_receipts:
            raise ValueError("transition receipt")
        receipt = _receipt_from_dict(receipt_record)
        if (
            receipt.input_digest != transition["command_digest"]
            or receipt.before_head != before
            or receipt.after_head != after
            or receipt.new_revision_id != new_id
            or receipt.subject_id != _planning_subject_id(
                after.owner_id, after.project_id, after.roadmap_id
            )
            or receipt.command_kind != transition["kind"]
            or receipt.output_digest != _canonical_digest(
                {
                    "after_head": _head_dict(after),
                    "new_revision_id": new_id,
                    "restored_revision_id": receipt.restored_revision_id,
                }
            )
        ):
            raise ValueError("receipt binding")
        if receipt.command_kind == "revert_head":
            restored = revisions.get(receipt.restored_revision_id or "")
            if restored is None or restored.revision >= current_revision.revision:
                raise ValueError("revert source")
        elif receipt.restored_revision_id is not None:
            raise ValueError("unexpected restore source")
        seen_receipts.add(receipt_id)
        replay[subject] = _head_dict(after)
        replay_fences[subject] = after.generation
    if (
        set(state["receipts"]) != seen_receipts
        or state["heads"] != replay
        or state["head_fences"] != replay_fences
    ):
        raise ValueError("orphan receipt or head mismatch")
    if set(revisions) != referenced_revisions:
        raise ValueError("orphan revision")
    expected_idempotency: dict[str, dict[str, str]] = {}
    for transition in state["transitions"]:
        if "batch_id" in transition:
            owner_id = state["migration_batches"][transition["batch_id"]]["owner_id"]
        else:
            owner_id = _head_from_dict(transition["after_head"]).owner_id
        scope = _idempotency_scope(owner_id, transition["idempotency_key"])
        if scope in expected_idempotency:
            raise ValueError("reused idempotency key")
        expected_idempotency[scope] = {
            "command_digest": transition["command_digest"],
            "receipt_id": transition["receipt_id"],
        }
    if state["idempotency"] != expected_idempotency:
        raise ValueError("idempotency index mismatch")
    for scope, record in state["idempotency"].items():
        if type(scope) is not str or type(record) is not dict or set(record) != {"command_digest", "receipt_id"}:
            raise ValueError("idempotency shape")
        _digest_value(record["command_digest"])
        receipt = state["receipts"].get(record["receipt_id"])
        if receipt is None or receipt["input_digest"] != record["command_digest"]:
            raise ValueError("idempotency binding")
    _verify_migration_batches(state, _validation_run=validation_run)
    if append_validation:
        _complete_append_planning_payload_validation_verification(validation_run)
    elif validation_run is not None:
        validation_run.seal()
    return validation_run


def _validated_migration_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    value = _exact_json_snapshot(value)
    v1_fields = {
        "schema", "owner_id", "project_id", "source_items", "supporting_relations",
        "normalized_payloads", "authority_pins", "structural_census",
        "supporting_observation", "manifest_digest", "batch_id"
    }
    v2_fields = v1_fields | {"authority_map_id"}
    if type(value) is not dict or set(value) not in (v1_fields, v2_fields):
        raise DurablePlanningStoreError("validation_failed")
    try:
        body = _strict_json_object(_canonical_planning_bytes(value))
        if (
            body.get("schema") == "odysseus.harbour.planning_migration_manifest.v1"
            and set(body) != v1_fields
        ) or (
            body.get("schema") == "odysseus.harbour.planning_migration_manifest.v2"
            and set(body) != v2_fields
        ):
            raise ValueError
        from src.planning_roadmap_migration import (
            FROZEN_MEMBERS, HISTORICAL_AUTHORITY_PINS, SUPPORTING_PATH, SUPPORTING_PIN,
            resolve_migration_manifest_identity,
        )
        identity = _migration_manifest_identity(body)
        _map_id, expected_authority_pins = resolve_migration_manifest_identity(identity)
        if body["schema"] not in {
            "odysseus.harbour.planning_migration_manifest.v1",
            "odysseus.harbour.planning_migration_manifest.v2",
        }:
            raise ValueError
        _require_unambiguous_component(body["owner_id"])
        _require_unambiguous_component(body["project_id"])
        owner = _harbour_identifier(body["owner_id"])
        project = _harbour_identifier(body["project_id"])
        if owner != "fuzzy" or project != "harbour-one":
            raise ValueError
        if type(body["source_items"]) is not list or len(body["source_items"]) != 6:
            raise ValueError
        if type(body["supporting_relations"]) is not list or len(body["supporting_relations"]) != 1:
            raise ValueError
        if type(body["normalized_payloads"]) is not dict:
            raise ValueError
        if (
            type(body["authority_pins"]) is not dict
            or len(body["authority_pins"]) != len(expected_authority_pins)
        ):
            raise ValueError
        expected_primary = {
            path: (adapter, roadmap_id, blob, sha)
            for path, adapter, roadmap_id, blob, sha in FROZEN_MEMBERS
        }
        census = body["structural_census"]
        if type(census) is not list or not census:
            raise ValueError
        census_keys: list[tuple[str, str, str, str, str]] = []
        census_paths: set[str] = set()
        for entry in census:
            if type(entry) is not dict or set(entry) != {
                "source_id", "path", "kind", "extension", "structural_role"
            } or any(type(entry[field]) is not str or not entry[field] for field in entry):
                raise ValueError
            key = (entry["source_id"], entry["path"], entry["kind"], entry["extension"], entry["structural_role"])
            if entry["path"] in census_paths:
                raise ValueError
            expected_id = "repo-plan:" + hashlib.sha256(entry["path"].lower().encode("utf-8")).hexdigest()[:16]
            expected_extension = Path(entry["path"]).suffix.lower().lstrip(".")
            expected_kind = (
                "roadmap_json" if entry["path"].startswith("specs/roadmaps/") and expected_extension == "json"
                else "roadmap_doc" if entry["path"].startswith("specs/roadmaps/")
                else "planning_doc_json" if expected_extension == "json"
                else "planning_doc"
            )
            expected_role = (
                "primary_import_candidate" if entry["path"] in set(expected_primary)
                else "supporting_relation" if entry["path"] == SUPPORTING_PATH
                else "historical_evidence" if expected_extension in {"md", "markdown"}
                else "control_plane_authority" if entry["path"] in HISTORICAL_AUTHORITY_PINS
                else "primary_import_candidate" if expected_kind.startswith("roadmap_") and expected_extension == "json"
                else "supporting_plan_source"
            )
            if (
                entry["source_id"] != expected_id or entry["kind"] != expected_kind
                or entry["extension"] != expected_extension or entry["structural_role"] != expected_role
            ):
                raise ValueError
            census_paths.add(entry["path"])
            census_keys.append(key)
        if census_keys != sorted(census_keys):
            raise ValueError
        if body["authority_pins"] != expected_authority_pins:
            raise ValueError
        census_by_path = {entry["path"]: entry for entry in census}
        spec_json = {
            path for path, entry in census_by_path.items()
            if path.startswith("specs/roadmaps/") and entry["extension"] == "json"
        }
        if spec_json != set(expected_primary) | {SUPPORTING_PATH}:
            raise ValueError
        if any(census_by_path[path]["structural_role"] != "primary_import_candidate" for path in expected_primary):
            raise ValueError
        if census_by_path[SUPPORTING_PATH]["structural_role"] != "supporting_relation":
            raise ValueError
        if any(
            entry["structural_role"] == "primary_import_candidate"
            and entry["path"] not in set(expected_primary) | {SUPPORTING_PATH}
            for entry in census
        ):
            raise ValueError
        if {item["path"] for item in body["source_items"]} != set(expected_primary):
            raise ValueError
        for item in body["source_items"]:
            if (
                item["adapter"], item["roadmap_id"], item["blob"], item["sha256"]
            ) != expected_primary[item["path"]]:
                raise ValueError
        if any(census_by_path[item["path"]]["structural_role"] != "primary_import_candidate" for item in body["source_items"]):
            raise ValueError
        relation = body["supporting_relations"][0]
        if (
            relation["path"], relation["target_roadmap_id"], relation["sha256"]
        ) != (SUPPORTING_PATH, "abc-roadmap-reconciliation-import", SUPPORTING_PIN[1]):
            raise ValueError
        observation = body["supporting_observation"]
        if type(observation) is not dict or set(observation) != {"count", "digest"}:
            raise ValueError
        if type(observation["count"]) is not int or observation["count"] < 0:
            raise ValueError
        _digest_value(observation["digest"])
        for path, blob in body["authority_pins"].items():
            if type(path) is not str or type(blob) is not str or not re.fullmatch(r"[0-9a-f]{40}", blob):
                raise ValueError
        roadmap_ids: list[str] = []
        for item in body["source_items"]:
            if type(item) is not dict or set(item) != {
                "path", "role", "adapter", "roadmap_id", "blob", "sha256",
                "payload_sha256", "content_hash"
            }:
                raise ValueError
            _require_unambiguous_component(item["roadmap_id"])
            roadmap_id = _harbour_identifier(item["roadmap_id"])
            if roadmap_id in roadmap_ids or item["role"] != "primary":
                raise ValueError
            if item["adapter"] not in {"legacy_slices_v1", "legacy_graph_v1"}:
                raise ValueError
            if type(item["path"]) is not str or type(item["blob"]) is not str or not re.fullmatch(r"[0-9a-f]{40}", item["blob"]):
                raise ValueError
            if type(item["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                raise ValueError
            _digest_value(item["payload_sha256"]); _digest_value(item["content_hash"])
            roadmap_ids.append(roadmap_id)
        if set(body["normalized_payloads"]) != set(roadmap_ids):
            raise ValueError
        for roadmap_id, text in body["normalized_payloads"].items():
            if type(text) is not str:
                raise ValueError
            payload = text.encode("utf-8")
            document = _strict_json_object(payload)
            if _canonical_planning_bytes(document) != payload:
                raise ValueError
            _payload, _document, roadmap = _validated_planning_payload(payload, project, roadmap_id)
            item = next(record for record in body["source_items"] if record["roadmap_id"] == roadmap_id)
            if _bytes_digest(payload) != item["payload_sha256"] or roadmap["content_hash"] != item["content_hash"]:
                raise ValueError
        relation = body["supporting_relations"][0]
        if type(relation) is not dict or set(relation) != {
            "path", "role", "target_roadmap_id", "sha256"
        } or relation["role"] != "supporting_relation_only":
            raise ValueError
        if relation["target_roadmap_id"] not in roadmap_ids:
            raise ValueError
        digest = _migration_digest(_migration_manifest_identity(body))
        if body["manifest_digest"] != digest:
            raise ValueError
        if body["batch_id"] != "planning-migration-batch:" + digest[7:]:
            raise ValueError
        _harbour_identifier(body["batch_id"])
        return body
    except DurablePlanningStoreError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, PlanningDefinitionContractError):
        raise DurablePlanningStoreError("validation_failed") from None


def _migration_subject(
    owner: str, project: str, roadmap: str, head: PlanningHead | None, fence: int
) -> dict[str, Any]:
    return {
        "subject_key": _planning_key(owner, project, roadmap),
        "owner_id": owner,
        "project_id": project,
        "roadmap_id": roadmap,
        "head": _head_dict(head) if head else None,
        "fence": fence,
    }


def _migration_subject_from_dict(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "subject_key", "owner_id", "project_id", "roadmap_id", "head", "fence"
    }:
        raise ValueError("migration subject shape")
    owner = _harbour_identifier(value["owner_id"])
    project = _harbour_identifier(value["project_id"])
    roadmap = _harbour_identifier(value["roadmap_id"])
    if value["subject_key"] != _planning_key(owner, project, roadmap):
        raise ValueError("migration subject key")
    fence = value["fence"]
    if type(fence) is not int or not 0 <= fence <= 9_223_372_036_854_775_807:
        raise ValueError("migration fence")
    head = _head_from_dict(value["head"]) if value["head"] is not None else None
    if head is not None and (
        head.owner_id, head.project_id, head.roadmap_id, head.generation
    ) != (owner, project, roadmap, fence):
        raise ValueError("migration subject head")
    return {**value, "head": head}


def _migration_phase_input(
    phase: str, batch_id: str, manifest_digest: str, expected_state: str,
    before_subjects: list[dict[str, Any]], principal_identity: Mapping[str, str],
) -> dict[str, Any]:
    if phase not in {"prepare", "accept", "abort", "rollback"}:
        raise DurablePlanningStoreError("validation_failed")
    return {
        "schema": "odysseus.harbour.planning_migration_phase.v1",
        "phase": phase,
        "batch_id": batch_id,
        "manifest_digest": manifest_digest,
        "expected_batch_state": expected_state,
        "before_subjects": deepcopy(before_subjects),
        "principal_identity": {
            "issuer_id": principal_identity["issuer_id"],
            "subject_id": principal_identity["subject_id"],
            "owner_id": principal_identity["owner_id"],
            "auth_context_digest": principal_identity["auth_context_digest"],
        },
    }


_MIGRATION_MANIFEST_IDENTITY_FIELDS_V1 = (
    "schema", "owner_id", "project_id", "source_items", "supporting_relations",
    "normalized_payloads", "authority_pins", "structural_census",
)
_MIGRATION_MANIFEST_IDENTITY_FIELDS_V2 = _MIGRATION_MANIFEST_IDENTITY_FIELDS_V1 + ("authority_map_id",)


def _migration_manifest_identity(body: Mapping[str, Any]) -> dict[str, Any]:
    """Snapshot the exact canonical authority material; omit informational observation."""
    schema = body.get("schema")
    if schema == "odysseus.harbour.planning_migration_manifest.v1":
        fields = _MIGRATION_MANIFEST_IDENTITY_FIELDS_V1
        if "authority_map_id" in body:
            raise DurablePlanningStoreError("validation_failed")
    elif schema == "odysseus.harbour.planning_migration_manifest.v2":
        fields = _MIGRATION_MANIFEST_IDENTITY_FIELDS_V2
        if "authority_map_id" not in body:
            raise DurablePlanningStoreError("validation_failed")
    else:
        raise DurablePlanningStoreError("validation_failed")
    return {field: deepcopy(body[field]) for field in fields}


def _migration_idempotency_key(phase: str, digest: str) -> str:
    _digest_value(digest)
    return f"h1r03.{phase}.{digest[7:55]}"


def _new_migration_receipt(
    *, phase: str, batch_id: str, command_digest: str, idempotency_key: str,
    before_state: str, after_state: str, before_subjects: list[dict[str, Any]],
    after_subjects: list[dict[str, Any]], new_revision_ids: list[str], occurred_at: str,
) -> tuple[PlanningMigrationReceipt, dict[str, Any]]:
    receipt_id = "planning-migration-receipt:" + hashlib.sha256(
        (command_digest + ":receipt").encode("ascii")
    ).hexdigest()
    output_digest = _migration_digest({
        "after_state": after_state,
        "after_subjects": after_subjects,
        "new_revision_ids": new_revision_ids,
    })
    receipt = PlanningMigrationReceipt(
        "planning_migration", receipt_id, batch_id, phase, command_digest,
        output_digest, before_state, after_state,
        tuple(deepcopy(before_subjects)), tuple(deepcopy(after_subjects)),
        tuple(new_revision_ids), "harbour_planning_store", occurred_at,
    )
    transition = {
        "transition_id": "planning-migration-transition:" + hashlib.sha256(
            (command_digest + ":transition").encode("ascii")
        ).hexdigest(),
        "kind": "migration_" + phase,
        "batch_id": batch_id,
        "command_digest": command_digest,
        "idempotency_key": idempotency_key,
        "receipt_id": receipt_id,
    }
    return receipt, transition


def _migration_receipt_dict(value: PlanningMigrationReceipt) -> dict[str, Any]:
    return {
        "receipt_type": value.receipt_type, "receipt_id": value.receipt_id,
        "batch_id": value.batch_id, "phase": value.phase,
        "input_digest": value.input_digest, "output_digest": value.output_digest,
        "before_state": value.before_state, "after_state": value.after_state,
        "before_subjects": [deepcopy(item) for item in value.before_subjects],
        "after_subjects": [deepcopy(item) for item in value.after_subjects],
        "new_revision_ids": list(value.new_revision_ids), "producer": value.producer,
        "occurred_at": value.occurred_at,
    }


def _subject_binding_receipt(
    *, receipt_id: str, batch: Mapping[str, Any], binding: Mapping[str, str],
    principal_identity: Mapping[str, str], input_digest: str,
    idempotency_key: str, occurred_at: str,
) -> dict[str, Any]:
    output = {
        "batch_id": batch["batch_id"], "owner_id": batch["owner_id"],
        "project_id": batch["project_id"], "manifest_digest": batch["manifest_digest"],
        "accepted_receipt_id": batch["accepted_receipt_id"],
        "binding": dict(binding), "principal_identity": dict(principal_identity),
    }
    return {
        "receipt_type": "planning_subject_binding", "receipt_id": receipt_id,
        **output, "input_digest": input_digest,
        "output_digest": _canonical_digest(output),
        "idempotency_key": idempotency_key, "occurred_at": occurred_at,
    }


def _subject_binding_receipt_from_dict(value: object) -> dict[str, Any]:
    fields = {
        "receipt_type", "receipt_id", "batch_id", "owner_id", "project_id",
        "manifest_digest", "accepted_receipt_id", "binding", "principal_identity", "input_digest",
        "output_digest", "idempotency_key", "occurred_at",
    }
    if type(value) is not dict or set(value) != fields or value["receipt_type"] != "planning_subject_binding":
        raise ValueError("subject binding receipt shape")
    for key in ("receipt_id", "batch_id", "owner_id", "project_id", "accepted_receipt_id"):
        _harbour_identifier(value[key])
    _digest_value(value["manifest_digest"])
    _digest_value(value["input_digest"])
    _digest_value(value["output_digest"])
    _harbour_idempotency_key(value["idempotency_key"])
    if type(value["occurred_at"]) is not str:
        raise ValueError("subject binding receipt time")
    binding = value["binding"]
    if type(binding) is not dict or set(binding) != {"issuer_id", "subject_id", "owner_id"}:
        raise ValueError("subject binding identity")
    for key in binding:
        _harbour_identifier(binding[key])
    if binding["owner_id"] != value["owner_id"]:
        raise ValueError("subject binding owner")
    principal_identity = value["principal_identity"]
    if type(principal_identity) is not dict or set(principal_identity) != {
        "issuer_id", "subject_id", "owner_id", "auth_context_digest",
    }:
        raise ValueError("subject binding principal identity")
    for key in ("issuer_id", "subject_id", "owner_id"):
        _harbour_identifier(principal_identity[key])
    _digest_value(principal_identity["auth_context_digest"])
    if (
        principal_identity["issuer_id"] != binding["issuer_id"]
        or principal_identity["subject_id"] != binding["subject_id"]
        or principal_identity["owner_id"] != value["owner_id"]
        or principal_identity["subject_id"] == value["owner_id"]
        or _IMMUTABLE_ADMIN_SUBJECT_RE.fullmatch(principal_identity["subject_id"]) is None
    ):
        raise ValueError("subject binding principal relationship")
    output = {key: value[key] for key in (
        "batch_id", "owner_id", "project_id", "manifest_digest", "accepted_receipt_id",
        "binding", "principal_identity",
    )}
    if value["output_digest"] != _canonical_digest(output):
        raise ValueError("subject binding output digest")
    return deepcopy(value)


def _migration_receipt_from_dict(value: object) -> PlanningMigrationReceipt:
    fields = {
        "receipt_type", "receipt_id", "batch_id", "phase", "input_digest",
        "output_digest", "before_state", "after_state", "before_subjects",
        "after_subjects", "new_revision_ids", "producer", "occurred_at"
    }
    if type(value) is not dict or set(value) != fields or value["receipt_type"] != "planning_migration":
        raise ValueError("migration receipt shape")
    phase = value["phase"]
    edges = {
        "prepare": ("absent", "prepared"), "accept": ("prepared", "accepted"),
        "abort": ("prepared", "aborted"), "rollback": ("accepted", "rolled_back"),
    }
    if phase not in edges or (value["before_state"], value["after_state"]) != edges[phase]:
        raise ValueError("migration phase")
    if type(value["before_subjects"]) is not list or type(value["after_subjects"]) is not list:
        raise ValueError("migration subjects")
    for item in value["before_subjects"] + value["after_subjects"]:
        _migration_subject_from_dict(item)
    if type(value["new_revision_ids"]) is not list:
        raise ValueError("migration revisions")
    revisions = tuple(_harbour_identifier(item) for item in value["new_revision_ids"])
    if value["producer"] != "harbour_planning_store" or type(value["occurred_at"]) is not str:
        raise ValueError("migration producer")
    return PlanningMigrationReceipt(
        value["receipt_type"], _harbour_identifier(value["receipt_id"]),
        _harbour_identifier(value["batch_id"]), phase,
        _digest_value(value["input_digest"]), _digest_value(value["output_digest"]),
        value["before_state"], value["after_state"],
        tuple(deepcopy(value["before_subjects"])), tuple(deepcopy(value["after_subjects"])),
        revisions, value["producer"], value["occurred_at"],
    )


def _append_migration_event(
    candidate: dict[str, Any], owner: str, key: str, digest: str,
    transition: dict[str, Any], receipt: PlanningMigrationReceipt,
) -> None:
    candidate["transitions"].append(transition)
    candidate["receipts"][receipt.receipt_id] = _migration_receipt_dict(receipt)
    candidate["idempotency"][_idempotency_scope(owner, key)] = {
        "command_digest": digest, "receipt_id": receipt.receipt_id,
    }


def _assert_migration_subjects(state: dict[str, Any], subjects: list[dict[str, Any]]) -> None:
    for value in subjects:
        subject = _migration_subject_from_dict(value)
        current = state["heads"].get(subject["subject_key"])
        expected = _head_dict(subject["head"]) if subject["head"] else None
        if current != expected or state["head_fences"].get(subject["subject_key"], 0) != subject["fence"]:
            raise DurablePlanningStoreError("stale_authority")


def _assert_replay_subjects(
    heads: dict[str, dict[str, Any]], fences: dict[str, int], subjects: tuple[dict[str, Any], ...]
) -> None:
    for value in subjects:
        subject = _migration_subject_from_dict(value)
        expected = _head_dict(subject["head"]) if subject["head"] else None
        if heads.get(subject["subject_key"]) != expected or fences.get(subject["subject_key"], 0) != subject["fence"]:
            raise ValueError("migration subject CAS chain")


def _verify_migration_manifest_identity(batch: Mapping[str, Any]) -> None:
    identity = batch.get("manifest_identity")
    from src.planning_roadmap_migration import resolve_migration_manifest_identity
    try:
        resolve_migration_manifest_identity(identity)
    except ValueError:
        raise ValueError("migration manifest identity shape or authority map") from None
    _harbour_identifier(identity["owner_id"])
    _harbour_identifier(identity["project_id"])
    if identity["owner_id"] != "fuzzy" or identity["project_id"] != "harbour-one":
        raise ValueError("migration manifest target")
    if type(identity["source_items"]) is not list or type(identity["supporting_relations"]) is not list:
        raise ValueError("migration manifest evidence shape")
    if type(identity["normalized_payloads"]) is not dict or type(identity["authority_pins"]) is not dict:
        raise ValueError("migration manifest mapping shape")
    if type(identity["structural_census"]) is not list:
        raise ValueError("migration manifest census shape")
    if any(
        type(path) is not str or not path
        or type(blob) is not str or not re.fullmatch(r"[0-9a-f]{40}", blob)
        for path, blob in identity["authority_pins"].items()
    ):
        raise ValueError("migration manifest authority pins")
    if (
        batch["owner_id"] != identity["owner_id"]
        or batch["project_id"] != identity["project_id"]
        or batch["source_items"] != identity["source_items"]
        or batch["supporting_relations"] != identity["supporting_relations"]
        or batch["normalized_payloads"] != identity["normalized_payloads"]
        or batch["structural_census"] != identity["structural_census"]
    ):
        raise ValueError("migration manifest projection mismatch")
    digest = _migration_digest(identity)
    if (
        batch["manifest_digest"] != digest
        or batch["batch_id"] != "planning-migration-batch:" + digest[7:]
    ):
        raise ValueError("migration manifest digest binding")


def _verify_migration_phase_records(state: Mapping[str, Any], batch: Mapping[str, Any]) -> None:
    batch_id = batch["batch_id"]
    identity = batch["principal_identity"]
    if batch["state"] == "prepared":
        phases = {"prepare": ("absent", batch["expected_subjects"], batch["prepared_receipt_id"])}
    elif batch["state"] == "accepted":
        phases = {
            "prepare": ("absent", batch["expected_subjects"], batch["prepared_receipt_id"]),
            "accept": ("prepared", batch["expected_subjects"], batch["accepted_receipt_id"]),
        }
    elif batch["state"] == "aborted":
        phases = {
            "prepare": ("absent", batch["expected_subjects"], batch["prepared_receipt_id"]),
            "abort": ("prepared", batch["expected_subjects"], batch["terminal_receipt_id"]),
        }
    else:
        phases = {
            "prepare": ("absent", batch["expected_subjects"], batch["prepared_receipt_id"]),
            "accept": ("prepared", batch["expected_subjects"], batch["accepted_receipt_id"]),
            "rollback": ("accepted", batch["accepted_subjects"], batch["terminal_receipt_id"]),
        }
    transitions = [
        item for item in state["transitions"]
        if type(item) is dict and item.get("batch_id") == batch_id
        and item.get("kind") != "migration_bind_subject"
    ]
    if len(transitions) != len(phases):
        raise ValueError("migration phase transition count")
    transition_by_receipt = {item["receipt_id"]: item for item in transitions}
    if len(transition_by_receipt) != len(transitions):
        raise ValueError("duplicate migration phase receipt")
    for phase, (expected_state, before_subjects, receipt_id) in phases.items():
        if type(receipt_id) is not str or not receipt_id:
            raise ValueError("migration phase receipt reference")
        receipt_value = state["receipts"].get(receipt_id)
        receipt = _migration_receipt_from_dict(receipt_value)
        phase_input = _migration_phase_input(
            phase, batch_id, batch["manifest_digest"], expected_state,
            before_subjects, identity,
        )
        digest = _migration_digest(phase_input)
        key = _migration_idempotency_key(phase, digest)
        expected_receipt_id = "planning-migration-receipt:" + hashlib.sha256(
            (digest + ":receipt").encode("ascii")
        ).hexdigest()
        expected_transition_id = "planning-migration-transition:" + hashlib.sha256(
            (digest + ":transition").encode("ascii")
        ).hexdigest()
        transition = transition_by_receipt.get(receipt_id)
        expected_after_state = {
            "prepare": "prepared", "accept": "accepted",
            "abort": "aborted", "rollback": "rolled_back",
        }[phase]
        output_digest = _migration_digest({
            "after_state": receipt.after_state,
            "after_subjects": list(receipt.after_subjects),
            "new_revision_ids": list(receipt.new_revision_ids),
        })
        if (
            receipt.phase != phase or receipt.batch_id != batch_id
            or receipt.before_state != expected_state
            or receipt.after_state != expected_after_state
            or receipt.input_digest != digest or receipt.output_digest != output_digest
            or receipt.receipt_id != expected_receipt_id
            or transition is None
            or transition["transition_id"] != expected_transition_id
            or transition["kind"] != "migration_" + phase
            or transition["command_digest"] != digest
            or transition["idempotency_key"] != key
            or transition["receipt_id"] != receipt_id
        ):
            raise ValueError("migration phase identity binding")


def _verify_migration_batches(
    state: dict[str, Any], *,
    _validation_run: _PlanningPayloadValidationRun | None = None,
) -> None:
    fields = {
        "batch_id", "manifest_digest", "owner_id", "project_id", "state",
        "source_items", "supporting_relations", "expected_subjects", "accepted_subjects",
        "normalized_payloads", "prepared_receipt_id", "accepted_receipt_id", "terminal_receipt_id"
    }
    for batch_id, batch in state["migration_batches"].items():
        prior_current_fields = fields | {"census_observation", "structural_census", "principal_identity"}
        snapshot_fields = prior_current_fields | {"manifest_identity"}
        binding_fields = {"accepted_subject_binding", "subject_binding_receipt_id"}
        allowed_shapes = (
            fields, prior_current_fields, snapshot_fields,
            fields | binding_fields, prior_current_fields | binding_fields,
            snapshot_fields | binding_fields,
        )
        if type(batch) is not dict or set(batch) not in allowed_shapes or batch_id != batch["batch_id"]:
            raise ValueError("migration batch shape")
        has_manifest_identity = "manifest_identity" in batch
        if "census_observation" in batch:
            observation = batch["census_observation"]
            if type(observation) is not dict or set(observation) != {"count", "digest"}:
                raise ValueError("migration census observation")
            if type(observation["count"]) is not int or observation["count"] < 0:
                raise ValueError("migration census count")
            _digest_value(observation["digest"])
            identity = batch["principal_identity"]
            if type(identity) is not dict or set(identity) != {"issuer_id", "subject_id", "owner_id", "auth_context_digest"}:
                raise ValueError("migration principal identity")
            _harbour_identifier(identity["issuer_id"])
            _harbour_identifier(identity["subject_id"])
            _harbour_identifier(identity["owner_id"])
            _digest_value(identity["auth_context_digest"])
            if "accepted_subject_binding" in batch:
                binding = batch["accepted_subject_binding"]
                if type(binding) is not dict or set(binding) != {"issuer_id", "subject_id", "owner_id"}:
                    raise ValueError("accepted subject binding shape")
                for key in binding:
                    _harbour_identifier(binding[key])
                if (
                    binding["owner_id"] != batch["owner_id"]
                    or binding["subject_id"] == batch["owner_id"]
                    or _IMMUTABLE_ADMIN_SUBJECT_RE.fullmatch(binding["subject_id"]) is None
                    or batch["state"] not in {"accepted", "rolled_back"}
                ):
                    raise ValueError("accepted subject binding owner or state")
                binding_receipt_id = batch["subject_binding_receipt_id"]
                if type(binding_receipt_id) is not str or not binding_receipt_id:
                    raise ValueError("accepted subject binding receipt id")
                _harbour_identifier(binding_receipt_id)
                record = state["receipts"].get(binding_receipt_id)
                parsed = _subject_binding_receipt_from_dict(record)
                if (
                    parsed["receipt_id"] != binding_receipt_id
                    or parsed["batch_id"] != batch_id
                    or parsed["owner_id"] != batch["owner_id"]
                    or parsed["project_id"] != batch["project_id"]
                    or parsed["manifest_digest"] != batch["manifest_digest"]
                    or parsed["accepted_receipt_id"] != batch["accepted_receipt_id"]
                    or parsed["binding"] != binding
                    or binding["issuer_id"] != identity["issuer_id"]
                    or identity["subject_id"] != batch["owner_id"]
                    or parsed["principal_identity"]["owner_id"] != batch["owner_id"]
                ):
                    raise ValueError("accepted subject binding receipt")
            census = batch["structural_census"]
            if type(census) is not list or not census:
                raise ValueError("migration structural census")
            census_keys = []
            census_paths = set()
            for entry in census:
                if type(entry) is not dict or set(entry) != {"source_id", "path", "kind", "extension", "structural_role"}:
                    raise ValueError("migration structural row")
                if any(type(entry[key]) is not str or not entry[key] for key in entry):
                    raise ValueError("migration structural field")
                expected_id = "repo-plan:" + hashlib.sha256(entry["path"].lower().encode("utf-8")).hexdigest()[:16]
                if entry["source_id"] != expected_id:
                    raise ValueError("migration structural identity")
                if entry["path"] in census_paths:
                    raise ValueError("migration structural duplicate")
                census_paths.add(entry["path"])
                census_keys.append((entry["source_id"], entry["path"], entry["kind"], entry["extension"], entry["structural_role"]))
            if census_keys != sorted(census_keys):
                raise ValueError("migration structural order")
        elif "structural_census" in batch:
            raise ValueError("incomplete migration census schema")
        _harbour_identifier(batch_id); _digest_value(batch["manifest_digest"])
        _harbour_identifier(batch["owner_id"]); _harbour_identifier(batch["project_id"])
        if batch["state"] not in {"prepared", "accepted", "aborted", "rolled_back"}:
            raise ValueError("migration batch state")
        if type(batch["source_items"]) is not list or len(batch["source_items"]) != 6:
            raise ValueError("migration source items")
        if type(batch["normalized_payloads"]) is not dict or type(batch["supporting_relations"]) is not list or len(batch["supporting_relations"]) != 1:
            raise ValueError("migration batch evidence")
        roadmap_ids: list[str] = []
        for item in batch["source_items"]:
            if type(item) is not dict or set(item) != {
                "path", "role", "adapter", "roadmap_id", "blob", "sha256",
                "payload_sha256", "content_hash"
            } or item["role"] != "primary":
                raise ValueError("migration source item shape")
            roadmap_id = _harbour_identifier(item["roadmap_id"])
            if roadmap_id in roadmap_ids:
                raise ValueError("duplicate migration roadmap")
            roadmap_ids.append(roadmap_id)
            payload_text = batch["normalized_payloads"].get(roadmap_id)
            if type(payload_text) is not str:
                raise ValueError("migration payload missing")
            payload = payload_text.encode("utf-8")
            _raw, document, roadmap = _validated_planning_payload(
                payload, batch["project_id"], roadmap_id,
                _validation_run=_validation_run,
            )
            if (
                _raw != payload
                or _canonical_planning_bytes(document) != payload
                or _bytes_digest(payload) != item["payload_sha256"]
                or roadmap["content_hash"] != item["content_hash"]
            ):
                raise ValueError("migration payload evidence")
        if set(batch["normalized_payloads"]) != set(roadmap_ids):
            raise ValueError("migration payload set")
        if has_manifest_identity:
            _verify_migration_manifest_identity(batch)
        relation = batch["supporting_relations"][0]
        if type(relation) is not dict or set(relation) != {
            "path", "role", "target_roadmap_id", "sha256"
        } or relation["role"] != "supporting_relation_only" or relation["target_roadmap_id"] not in roadmap_ids:
            raise ValueError("migration supporting relation")
        if type(batch["expected_subjects"]) is not list or len(batch["expected_subjects"]) != 6:
            raise ValueError("migration expected subjects")
        expected_parsed = [
            _migration_subject_from_dict(item) for item in batch["expected_subjects"]
        ]
        for index, parsed in enumerate(expected_parsed):
            if (
                parsed["owner_id"] != batch["owner_id"]
                or parsed["project_id"] != batch["project_id"]
                or parsed["roadmap_id"] != roadmap_ids[index]
                or parsed["head"] is not None
                or parsed["fence"] != 0
            ):
                raise ValueError("migration initial condition")
        accepted_parsed: list[dict[str, Any]] = []
        if batch["state"] in {"accepted", "rolled_back"}:
            if type(batch["accepted_subjects"]) is not list or len(batch["accepted_subjects"]) != 6:
                raise ValueError("migration accepted subjects")
            accepted_parsed = [
                _migration_subject_from_dict(item) for item in batch["accepted_subjects"]
            ]
            for index, parsed in enumerate(accepted_parsed):
                head = parsed["head"]
                if (
                    parsed["owner_id"] != batch["owner_id"]
                    or parsed["project_id"] != batch["project_id"]
                    or parsed["roadmap_id"] != roadmap_ids[index]
                    or parsed["fence"] != 1
                    or head is None
                    or head.generation != 1
                    or head.owner_id != batch["owner_id"]
                    or head.project_id != batch["project_id"]
                    or head.roadmap_id != roadmap_ids[index]
                    or head.approved_revision_id != head.current_revision_id
                    or head.current_content_sha256 != batch["source_items"][index]["content_hash"]
                ):
                    raise ValueError("migration accepted subject binding")
        elif batch["accepted_subjects"] != []:
            raise ValueError("unexpected accepted subjects")
        prepared = state["receipts"].get(batch["prepared_receipt_id"])
        prepared_receipt = _migration_receipt_from_dict(prepared) if prepared is not None else None
        if prepared_receipt is None or prepared_receipt.phase != "prepare" or prepared_receipt.batch_id != batch_id:
            raise ValueError("migration prepared receipt")
        if list(prepared_receipt.before_subjects) != batch["expected_subjects"] or list(prepared_receipt.after_subjects) != batch["expected_subjects"]:
            raise ValueError("migration prepared subjects")
        if batch["state"] in {"accepted", "rolled_back"}:
            accepted = state["receipts"].get(batch["accepted_receipt_id"])
            accepted_receipt = _migration_receipt_from_dict(accepted) if accepted is not None else None
            if accepted_receipt is None or accepted_receipt.phase != "accept" or accepted_receipt.batch_id != batch_id:
                raise ValueError("migration accepted receipt")
            if list(accepted_receipt.before_subjects) != batch["expected_subjects"] or list(accepted_receipt.after_subjects) != batch["accepted_subjects"]:
                raise ValueError("migration accepted subjects binding")
            if len(set(accepted_receipt.new_revision_ids)) != 6:
                raise ValueError("migration accepted revision membership")
            for index, parsed in enumerate(accepted_parsed):
                head = parsed["head"]
                revision_id = accepted_receipt.new_revision_ids[index]
                revision = state["revisions"].get(revision_id)
                if head is None or head.current_revision_id != revision_id or revision is None:
                    raise ValueError("migration accepted revision order")
                stored_revision = _revision_from_dict(revision)
                if (
                    stored_revision.owner_id != batch["owner_id"]
                    or stored_revision.project_id != batch["project_id"]
                    or stored_revision.roadmap_id != roadmap_ids[index]
                    or stored_revision.revision != 1
                    or stored_revision.parent_revision_id is not None
                    or stored_revision.revision_state != "approved"
                    or stored_revision.content_sha256 != batch["source_items"][index]["content_hash"]
                ):
                    raise ValueError("migration accepted revision binding")
        elif batch["accepted_receipt_id"] != "":
            raise ValueError("unexpected accepted receipt")
        if batch["state"] in {"aborted", "rolled_back"}:
            terminal = state["receipts"].get(batch["terminal_receipt_id"])
            expected_phase = "abort" if batch["state"] == "aborted" else "rollback"
            terminal_receipt = _migration_receipt_from_dict(terminal) if terminal is not None else None
            if terminal_receipt is None or terminal_receipt.phase != expected_phase or terminal_receipt.batch_id != batch_id:
                raise ValueError("migration terminal receipt")
            expected_before = batch["expected_subjects"] if expected_phase == "abort" else batch["accepted_subjects"]
            if list(terminal_receipt.before_subjects) != expected_before:
                raise ValueError("migration terminal subjects binding")
            if expected_phase == "abort":
                expected_after = batch["expected_subjects"]
            else:
                expected_after = [
                    _migration_subject(
                        batch["owner_id"], batch["project_id"], roadmap_id, None, 2
                    )
                    for roadmap_id in roadmap_ids
                ]
            if list(terminal_receipt.after_subjects) != expected_after:
                raise ValueError("migration terminal after subjects binding")
        elif batch["terminal_receipt_id"] != "":
            raise ValueError("unexpected terminal receipt")
        if has_manifest_identity:
            _verify_migration_phase_records(state, batch)


def _exact_head(value: object, owner: str, project: str, roadmap: str) -> PlanningHead:
    if type(value) is not PlanningHead:
        raise DurablePlanningStoreError("validation_failed")
    # Read slots once, reject scalar subclasses before invoking comparisons,
    # and use only this detached copy in CAS and serialization.
    snapshot = _head_from_dict(_head_dict(value))
    if (snapshot.owner_id, snapshot.project_id, snapshot.roadmap_id) != (owner, project, roadmap):
        raise DurablePlanningStoreError("stale_authority")
    return snapshot


def _exact_principal_ref(value: object) -> PrincipalRef:
    if type(value) is not PrincipalRef:
        raise DurablePlanningStoreError("validation_failed")
    return PrincipalRef(
        _harbour_identifier(value.issuer_id),
        _harbour_identifier(value.subject_id),
        _harbour_identifier(value.owner_id),
        _digest_value(value.auth_context_digest),
        _digest_value(value.principal_digest),
    )


def _command_principal_identity(value: PrincipalRef) -> dict[str, str]:
    # Only called after exact sealed-principal authentication and owner checks.
    # A fresh attestation for the same authenticated session must replay the
    # original receipt; migrations deliberately keep their existing identity.
    return {
        "issuer_id": value.issuer_id,
        "subject_id": value.subject_id,
        "owner_id": value.owner_id,
        "auth_context_digest": value.auth_context_digest,
    }


def _accepted_batch_binds_identity(batch: Mapping[str, Any], issuer_id: str, subject_id: str) -> bool:
    binding = batch.get("accepted_subject_binding")
    if type(binding) is dict:
        return (
            binding.get("issuer_id") == issuer_id
            and binding.get("subject_id") == subject_id
            and subject_id != batch.get("owner_id")
            and type(subject_id) is str
            and _IMMUTABLE_ADMIN_SUBJECT_RE.fullmatch(subject_id) is not None
        )
    original = batch.get("principal_identity")
    return (
        type(original) is dict
        and original.get("owner_id") == batch.get("owner_id")
        and original.get("issuer_id") == issuer_id
        and original.get("subject_id") == subject_id
        and subject_id != batch.get("owner_id")
        and type(subject_id) is str
        and _IMMUTABLE_ADMIN_SUBJECT_RE.fullmatch(subject_id) is not None
    )


def _exact_json_snapshot(value: object, depth: int = 0) -> Any:
    """Copy JSON inputs without invoking subclass/container protocols."""
    if depth > 100:
        raise DurablePlanningStoreError("validation_failed")
    kind = type(value)
    if value is None or kind is str or kind is bool or kind is int or kind is float:
        return value
    try:
        if kind is list:
            return [_exact_json_snapshot(item, depth + 1) for item in value]
        if kind is dict:
            result = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise DurablePlanningStoreError("validation_failed")
                result[key] = _exact_json_snapshot(item, depth + 1)
            return result
    except RuntimeError:
        _raise_durable_planning_error("validation_failed")
    raise DurablePlanningStoreError("validation_failed")


def _head_dict(value: PlanningHead) -> dict[str, Any]:
    return {
        "owner_id": value.owner_id,
        "project_id": value.project_id,
        "roadmap_id": value.roadmap_id,
        "current_revision_id": value.current_revision_id,
        "current_content_sha256": value.current_content_sha256,
        "approved_revision_id": value.approved_revision_id,
        "generation": value.generation,
    }


def _head_from_dict(value: object) -> PlanningHead:
    if type(value) is not dict or set(value) != {
        "owner_id", "project_id", "roadmap_id", "current_revision_id",
        "current_content_sha256", "approved_revision_id", "generation"
    }:
        raise ValueError("head shape")
    owner = _harbour_identifier(value["owner_id"])
    project = _harbour_identifier(value["project_id"])
    roadmap = _harbour_identifier(value["roadmap_id"])
    revision_id = _harbour_identifier(value["current_revision_id"])
    content = _digest_value(value["current_content_sha256"])
    approved = value["approved_revision_id"]
    if approved is not None:
        approved = _harbour_identifier(approved)
    generation = value["generation"]
    if type(generation) is not int or not 1 <= generation <= 9_223_372_036_854_775_807:
        raise ValueError("head generation")
    return PlanningHead(owner, project, roadmap, revision_id, content, approved, generation)


def _revision_dict(value: PlanningRevision, payload: bytes) -> dict[str, Any]:
    return {
        "revision_id": value.revision_id,
        "owner_id": value.owner_id,
        "project_id": value.project_id,
        "roadmap_id": value.roadmap_id,
        "revision": value.revision,
        "parent_revision_id": value.parent_revision_id,
        "payload_schema_id": value.payload_schema_id,
        "payload_sha256": value.payload_sha256,
        "content_sha256": value.content_sha256,
        "immutable_locator": value.immutable_locator,
        "revision_state": value.revision_state,
        "created_at": value.created_at,
        "principal_ref": {
            "issuer_id": value.principal_ref.issuer_id,
            "subject_id": value.principal_ref.subject_id,
            "owner_id": value.principal_ref.owner_id,
            "auth_context_digest": value.principal_ref.auth_context_digest,
            "principal_digest": value.principal_ref.principal_digest,
        },
        "payload_b64": base64.b64encode(payload).decode("ascii"),
    }


def _revision_from_dict(value: object) -> PlanningRevision:
    fields = {
        "revision_id", "owner_id", "project_id", "roadmap_id", "revision",
        "parent_revision_id", "payload_schema_id", "payload_sha256", "content_sha256",
        "immutable_locator", "revision_state", "created_at", "principal_ref", "payload_b64"
    }
    if type(value) is not dict or set(value) != fields:
        raise ValueError("revision shape")
    principal = value["principal_ref"]
    if type(principal) is not dict or set(principal) != {
        "issuer_id", "subject_id", "owner_id", "auth_context_digest", "principal_digest"
    }:
        raise ValueError("principal ref")
    ref = PrincipalRef(
        _harbour_identifier(principal["issuer_id"]),
        _harbour_identifier(principal["subject_id"]),
        _harbour_identifier(principal["owner_id"]),
        _digest_value(principal["auth_context_digest"]),
        _digest_value(principal["principal_digest"]),
    )
    parent = value["parent_revision_id"]
    if parent is not None:
        parent = _harbour_identifier(parent)
    revision = value["revision"]
    if type(revision) is not int or not 1 <= revision <= 9_223_372_036_854_775_807:
        raise ValueError("revision number")
    if value["payload_schema_id"] != HARBOUR_PLANNING_PAYLOAD_SCHEMA:
        raise ValueError("payload schema")
    if value["revision_state"] not in {"draft", "in_review", "approved", "superseded", "archived", "tombstoned"}:
        raise ValueError("revision state")
    if type(value["created_at"]) is not str or not value["created_at"].endswith("Z"):
        raise ValueError("created at")
    if type(value["payload_b64"]) is not str:
        raise ValueError("payload encoding")
    return PlanningRevision(
        _harbour_identifier(value["revision_id"]),
        _harbour_identifier(value["owner_id"]),
        _harbour_identifier(value["project_id"]),
        _harbour_identifier(value["roadmap_id"]),
        revision,
        parent,
        value["payload_schema_id"],
        _digest_value(value["payload_sha256"]),
        _digest_value(value["content_sha256"]),
        _harbour_identifier(value["immutable_locator"]),
        value["revision_state"],
        value["created_at"],
        ref,
    )


def _receipt_dict(value: PlanningReceipt) -> dict[str, Any]:
    return {
        "receipt_id": value.receipt_id,
        "command_kind": value.command_kind,
        "subject_id": value.subject_id,
        "input_digest": value.input_digest,
        "output_digest": value.output_digest,
        "before_head": _head_dict(value.before_head) if value.before_head else None,
        "after_head": _head_dict(value.after_head),
        "new_revision_id": value.new_revision_id,
        "restored_revision_id": value.restored_revision_id,
        "producer": value.producer,
        "occurred_at": value.occurred_at,
    }


def _receipt_from_dict(value: object) -> PlanningReceipt:
    if type(value) is not dict or set(value) != {
        "receipt_id", "command_kind", "subject_id", "input_digest", "output_digest",
        "before_head", "after_head", "new_revision_id", "restored_revision_id",
        "producer", "occurred_at"
    }:
        raise ValueError("receipt shape")
    before = _head_from_dict(value["before_head"]) if value["before_head"] is not None else None
    new_id = value["new_revision_id"]
    restored_id = value["restored_revision_id"]
    if new_id is not None:
        new_id = _harbour_identifier(new_id)
    if restored_id is not None:
        restored_id = _harbour_identifier(restored_id)
    if value["command_kind"] not in {"create_project", "append_revision", "approve_revision", "revert_head"}:
        raise ValueError("receipt command")
    if value["producer"] != "harbour_planning_store" or type(value["occurred_at"]) is not str:
        raise ValueError("receipt producer")
    return PlanningReceipt(
        _harbour_identifier(value["receipt_id"]),
        value["command_kind"],
        _harbour_identifier(value["subject_id"]),
        _digest_value(value["input_digest"]),
        _digest_value(value["output_digest"]),
        before,
        _head_from_dict(value["after_head"]),
        new_id,
        restored_id,
        value["producer"],
        value["occurred_at"],
    )


def _canonical_planning_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _migration_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _planning_key(owner: str, project: str, roadmap: str) -> str:
    return f"{owner}:{project}:{roadmap}"


def _planning_subject_id(owner: str, project: str, roadmap: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            (owner, project, roadmap), ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return "planning-head:" + digest


def _idempotency_scope(owner: str, key: str) -> str:
    return f"{owner}:{key}"


def _harbour_identifier(value: object) -> str:
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def _harbour_idempotency_key(value: object) -> str:
    if type(value) is not str or not _IDEMPOTENCY_RE.fullmatch(value):
        raise ValueError("invalid idempotency key")
    return value


def _digest_value(value: object) -> str:
    if type(value) is not str or not _HASH_RE.fullmatch(value):
        raise ValueError("invalid digest")
    return value


def _harbour_now(clock: Callable[[], datetime]) -> str:
    try:
        value = clock()
        if type(value) is not datetime or value.tzinfo is None:
            raise ValueError("invalid clock")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except Exception:
        raise DurablePlanningStoreError("validation_failed") from None


__all__ = [
    "DRAFT_STATE_SCHEMA",
    "MAX_CURSOR_CHARS",
    "PAGE_SCHEMA_ID",
    "PROJECT_READ_SCHEMA_ID",
    "TEMPORARY_REPOSITORY_MARKER",
    "TEMPORARY_REPOSITORY_SCHEMA",
    "PlanningRevisionRepository",
    "PlanningRevisionRepositoryError",
    "PlanningRevisionStore",
    "PlanningRevisionStoreError",
    "HARBOUR_PLANNING_PAYLOAD_SCHEMA",
    "HARBOUR_PLANNING_STATE_SCHEMA",
    "DurablePlanningRevisionStore",
    "DurablePlanningStoreError",
    "PlanningHead",
    "PlanningAcceptedHeadBinding",
    "PlanningAcceptedMigrationBundle",
    "PlanningContextRevisionMember",
    "PlanningMigrationReceipt",
    "PlanningReceipt",
    "PlanningSubjectBoundReceipt",
    "PlanningRevision",
]
