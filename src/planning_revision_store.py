"""Read-only, owner-scoped revision index for Planning Definition v2."""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Iterable, Mapping

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
_REPOSITORY_LOCKS_GUARD = threading.Lock()
_REPOSITORY_LOCKS: dict[str, threading.RLock] = {}


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
]
