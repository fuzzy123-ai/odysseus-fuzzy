"""Read-only, owner-scoped revision index for Planning Definition v2."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from src.planning_definition_projection import (
    ORIGIN_STATES,
    PlanningDefinitionProjectionError,
    PlanningDefinitionProjector,
    origin_metadata,
)


PAGE_SCHEMA_ID = "odysseus.planning.definition_page.v2"
PROJECT_READ_SCHEMA_ID = "odysseus.planning.project_read.v2"
MAX_CURSOR_CHARS = 1_024
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


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


__all__ = [
    "MAX_CURSOR_CHARS",
    "PAGE_SCHEMA_ID",
    "PROJECT_READ_SCHEMA_ID",
    "PlanningRevisionStore",
    "PlanningRevisionStoreError",
]
