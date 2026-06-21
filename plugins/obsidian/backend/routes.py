import os
import shutil
import base64
import json
import asyncio
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from src.auth_helpers import effective_user, require_user

VAULT_READ_SCOPE = "vault:read"
VAULT_WRITE_SCOPE = "vault:write"
VAULT_DELETE_SCOPE = "vault:delete"

def _require_vault_scope(request: Request, required: str) -> str:
    """Return the data owner if the caller has the required vault scope.
    
    Browser sessions already passed the route's normal auth gate and have full access.
    For API tokens, checks the token's scopes and raises 403 if missing.
    """
    state = getattr(request, "state", None)
    if getattr(state, "api_token", False):
        scopes = set(getattr(state, "api_token_scopes", []) or [])
        if required not in scopes:
            raise HTTPException(403, f"API token missing required scope: {required}")
        owner = getattr(state, "api_token_owner", None)
        if not owner:
            raise HTTPException(403, "API token has no owner")
        return owner
    if state is None:
        return "default"
    return effective_user(request) or "default"

from .vault_security import (
    VaultSecurityError,
    lock_vault,
    protection_status,
    remove_password,
    require_unlocked,
    set_password,
    unlock_vault,
)
from .import_export import export_vault, import_vault
from .vault_history import latest_reversible, list_history, mark_undone, record_action
from .vault_model import (
    add_manual_relationship,
    build_vault_index,
    graph_payload,
    load_manual_relationships,
    remove_manual_relationship,
    search_semantic,
    suggest_links,
    suggest_tags,
)
from . import vault_service
from .project_planning import (
    GameDevConceptDraftRequest,
    ProjectPlan,
    ProjectPlanApplyRequest,
    ProjectDescriptionImproveRequest,
    ProjectPlanRequest,
    ProjectPlanValidationError,
    apply_project_plan,
    build_gamedev_concept_draft_with_ai,
    build_project_plan,
    generate_project_plan_content,
    improve_project_description_with_ai,
    normalize_project_target_folder,
    normalize_relative_path,
    prepare_project_plan_for_apply,
    slugify_project,
    template_options,
    validate_gamedev_concept_gate,
    validate_project_plan,
)
from .memory_review import (
    MemoryReviewApplyRequest,
    MemoryReviewPlan,
    MemoryReviewRequest,
    MemoryReviewValidationError,
    apply_memory_review_plan,
    build_memory_review_plan,
    validate_memory_review_plan,
)
from .memory_capture import (
    MemoryCaptureApplyRequest,
    MemoryCaptureRequest,
    MemoryCaptureValidationError,
    apply_memory_capture_plan,
    build_memory_capture_plan,
    validate_memory_capture_plan,
)
from .memory_spark import (
    SparkAnalyzeRequest,
    SparkApplyRequest,
    SparkValidationError,
    analyze_memory_health,
    apply_spark_plan,
    build_spark_plan,
)
from .freshness import audit_knowledge, quarantine_list
from .derived_index import build_derived_index, derived_index_status, retrieve_derived_chunks
from .hybrid_retrieval import raptor_status
from .raptor_cache import bounded_raptor_graph_view
from .raptor_rebuild import rebuild_raptor_artifacts
from .memory_automation import memory_automation_status, run_memory_automation
from .memory_ledger import memory_ledger_status, sync_memory_ledger
from .query_layer import answer_query, answer_query_async, query_layer_status
from .rebuild_proof import rebuild_proof_status, run_rebuild_proof
from .external_upgrade_proof import (
    collect_distribution_layout,
    collect_external_upgrade_proof,
    collect_version_sync,
)
from .memory_status import memory_status
from .memory_tree import analyze_memory_tree, memory_tree_status

OBSIDIAN_API_PREFIX = "/api/plugins/obsidian"
ORCA_API_PREFIX = "/api/plugins/orca"

router = APIRouter(prefix=OBSIDIAN_API_PREFIX)

OBSIDIAN_APP_SHELL_PATH = "/api/plugins/obsidian/app"
OBSIDIAN_APP_SHELL_ALIASES = (
    OBSIDIAN_APP_SHELL_PATH,
    f"{OBSIDIAN_APP_SHELL_PATH}/",
)
OBSIDIAN_WEB_ASSET_PREFIX = "/api/plugins/obsidian/web/"

APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Odysseus ORCA Atlas</title>
  <style>
    :root {
      --bg: #101114;
      --fg: #f2f0e8;
      --panel: #17191f;
      --border: #30343d;
      --accent: #d35f5f;
      --red: #d35f5f;
    }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--fg);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
  </style>
</head>
<body data-obsidian-standalone="true">
  <script type="module" src="__PLUGIN_API_PREFIX__/web/app.js"></script>
</body>
</html>"""

def app_html_for_request(request: Request) -> str:
    path = request.url.path
    api_prefix = ORCA_API_PREFIX if path.startswith(f"{ORCA_API_PREFIX}/") else OBSIDIAN_API_PREFIX
    return APP_HTML.replace("__PLUGIN_API_PREFIX__", api_prefix)

# --- Request Models ---
class FileWriteRequest(BaseModel):
    path: str
    content: str

class FolderCreateRequest(BaseModel):
    path: str

class RenameRequest(BaseModel):
    old_path: str
    new_path: str

class RelationshipRequest(BaseModel):
    source: str
    target: str
    type: str = "manual"
    reason: str = ""

class FrontmatterMergeRequest(BaseModel):
    path: str
    frontmatter: Dict[str, Any]

class BatchOperationRequest(BaseModel):
    operations: List[Dict[str, Any]]
    dry_run: bool = False

class VaultPasswordRequest(BaseModel):
    password: str

class VaultExportRequest(BaseModel):
    password: Optional[str] = None
    root: str = ""

class VaultImportRequest(BaseModel):
    archive_base64: str
    password: Optional[str] = None

class ProjectPlanSessionCreateRequest(BaseModel):
    request: ProjectPlanRequest

class ProjectPlanSessionPreviewRequest(BaseModel):
    request: Optional[ProjectPlanRequest] = None

class ProjectPlanSessionApplyRequest(BaseModel):
    plan: Optional[ProjectPlan] = None
    confirm: bool = False
    confirm_conflicts: bool = False
    selected_paths: List[str] = Field(default_factory=list)
    overwrite_paths: List[str] = Field(default_factory=list)

# --- Helper Functions ---
def get_vault_path(request: Request) -> str:
    """Get the user-specific vault directory.
    
    Uses multi-user isolation or 'default' if auth is disabled.
    """
    if getattr(request.state, "api_token", False):
        username = _require_vault_scope(request, VAULT_READ_SCOPE)
    else:
        username = require_user(request)
    return vault_service.vault_path_for_owner(username)

def get_unlocked_vault_path(request: Request) -> str:
    vault_dir = get_vault_path(request)
    try:
        require_unlocked(vault_dir)
    except VaultSecurityError as exc:
        raise HTTPException(status_code=423, detail=str(exc))
    return vault_dir

def current_owner(request: Request) -> str:
    if getattr(request.state, "api_token", False) and not getattr(request.state, "api_token_owner", None):
        raise HTTPException(403, "API token has no owner")
    return effective_user(request) or "default"


def vault_actor(request: Request, tool: str = "obsidian_api") -> Dict[str, Any]:
    if not getattr(request.state, "api_token", False):
        return {"source": tool}
    return {
        "source": tool,
        "token_id": str(getattr(request.state, "api_token_id", "") or ""),
        "token_prefix": str(getattr(request.state, "api_token_prefix", "") or ""),
    }

def vault_error(exc: VaultSecurityError) -> HTTPException:
    detail = str(exc)
    status = 400
    if "locked" in detail.lower():
        status = 423
    elif "invalid password" in detail.lower():
        status = 401
    elif "conflict" in detail.lower():
        status = 409
    return HTTPException(status_code=status, detail=detail)


def _vault_watch_signature(vault_dir: str) -> Tuple[int, Tuple[Tuple[str, int, int], ...]]:
    """Return a cheap snapshot for detecting vault file changes."""
    entries: List[Tuple[str, int, int]] = []
    ignored_dirs = {"__pycache__", ".trash", ".snapshots"}
    for root, dirs, files in os.walk(vault_dir):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for filename in files:
            abs_path = os.path.join(root, filename)
            try:
                stat = os.stat(abs_path)
            except OSError:
                continue
            rel_path = os.path.relpath(abs_path, vault_dir).replace("\\", "/")
            entries.append((rel_path, int(stat.st_mtime_ns), int(stat.st_size)))
    entries.sort(key=lambda item: item[0].lower())
    latest = max((entry[1] for entry in entries), default=0)
    return latest, tuple(entries)


def _resolve_obsidian_ai_endpoint(owner: str):
    from src.endpoint_resolver import resolve_endpoint

    url, model, headers = resolve_endpoint("utility", owner=owner)
    role = "utility"
    if not url or not model:
        url, model, headers = resolve_endpoint("default", owner=owner)
        role = "default"
    return role, url, model, headers


def resolve_obsidian_ai_status(owner: str) -> Dict[str, Any]:
    role, url, model, _headers = _resolve_obsidian_ai_endpoint(owner)
    return {
        "available": bool(url and model),
        "role": role,
        "model": model or "",
        "endpoint_url": url or "",
    }


def project_planning_llm_call(owner: str):
    from src.llm_core import llm_call_async

    _role, url, model, headers = _resolve_obsidian_ai_endpoint(owner)
    if not url or not model:
        raise HTTPException(status_code=503, detail="No LLM endpoint configured")

    async def _call(messages, *, max_tokens: int, temperature: float):
        return await llm_call_async(
            url,
            model,
            messages,
            headers=headers,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=120,
            prompt_type="obsidian_project_planning",
        )

    return _call


@router.get("/ai-status")
async def ai_status(request: Request):
    """Return the read-only Obsidian AI model currently resolved for this user."""
    return resolve_obsidian_ai_status(current_owner(request))


def secure_path(vault_dir: str, relative_path: str) -> str:
    """Resolve and validate a relative path within the user's vault.
    
    Prevents path traversal attacks. Raises HTTPException 400 if invalid.
    """
    try:
        return vault_service.secure_path(vault_dir, relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path traversal attempt detected")

def _read_text_if_exists(path: str) -> Optional[str]:
    return vault_service.read_text_if_exists(path)

def _project_plan_stream_event(event: str, payload: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _dump_model(value: Any) -> Dict[str, Any]:
    return value.model_dump() if hasattr(value, "model_dump") else value.dict()

def _project_plan_session_path(vault_dir: str) -> str:
    return os.path.join(vault_dir, ".obsidian", "project_planning_sessions.json")

def _load_project_plan_sessions(vault_dir: str) -> Dict[str, Any]:
    path = _project_plan_session_path(vault_dir)
    if not os.path.exists(path):
        return {"sessions": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        payload = {"sessions": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), list):
        return {"sessions": []}
    return payload

def _save_project_plan_sessions(vault_dir: str, payload: Dict[str, Any]) -> None:
    path = _project_plan_session_path(vault_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"sessions": payload.get("sessions", [])}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)

def _project_plan_request_payload(req: ProjectPlanRequest) -> Dict[str, Any]:
    return _dump_model(req)

def _project_session_target_folder(req: ProjectPlanRequest) -> str:
    return normalize_project_target_folder(req.target_folder, slugify_project(req.title))

def _append_project_debug_event(
    session: Dict[str, Any],
    phase: str,
    message: str,
    *,
    file: str = "",
    error: str = "",
) -> None:
    events = session.setdefault("debug_events", [])
    events.append({
        "ts": _utc_now(),
        "phase": phase,
        "message": message,
        "file": file,
        "error": error,
    })
    del events[:-80]

def _new_project_plan_session(req: ProjectPlanRequest) -> Dict[str, Any]:
    now = _utc_now()
    target_folder = _project_session_target_folder(req)
    session = {
        "id": uuid.uuid4().hex,
        "title": req.title.strip() or "Untitled project",
        "kind": req.kind,
        "target_folder": target_folder,
        "target_preview_path": target_folder,
        "request": _project_plan_request_payload(req),
        "plan": None,
        "status": "draft",
        "progress": {
            "phase": "draft",
            "current_file": "",
            "current_index": 0,
            "total_files": 0,
            "message": "Draft saved",
        },
        "error": "",
        "debug_events": [],
        "created_at": now,
        "updated_at": now,
    }
    _append_project_debug_event(session, "draft", "Session created")
    return session

def _visible_project_plan_sessions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    hidden = {"created", "cancelled"}
    return [
        session for session in payload.get("sessions", [])
        if str(session.get("status", "")).lower() not in hidden
    ]

def _find_project_plan_session(payload: Dict[str, Any], session_id: str) -> Optional[Dict[str, Any]]:
    for session in payload.get("sessions", []):
        if session.get("id") == session_id:
            return session
    return None

def _update_project_plan_session(vault_dir: str, session_id: str, **updates: Any) -> Dict[str, Any]:
    payload = _load_project_plan_sessions(vault_dir)
    session = _find_project_plan_session(payload, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Project planning session not found")
    session.update(updates)
    session["updated_at"] = _utc_now()
    _save_project_plan_sessions(vault_dir, payload)
    return session

def _apply_project_plan_to_vault(vault_dir: str, owner: str, req: ProjectPlanApplyRequest) -> Dict[str, Any]:
    plan = prepare_project_plan_for_apply(
        vault_dir,
        req.plan,
        selected_paths=req.selected_paths,
        overwrite_paths=req.overwrite_paths,
    )
    overwrite_paths = {
        normalize_relative_path(path)
        for path in req.overwrite_paths or []
        if str(path or "").strip()
    }
    blocking_conflicts = [
        item for item in plan.conflicts
        if normalize_relative_path(str(item.get("path", ""))) not in overwrite_paths
    ]
    if blocking_conflicts:
        raise HTTPException(status_code=409, detail={"message": "Plan has file conflicts", "conflicts": plan.conflicts})
    if overwrite_paths and not req.confirm_conflicts:
        raise HTTPException(status_code=409, detail="Confirmation required before overwriting existing project files")
    if not req.confirm:
        raise HTTPException(status_code=409, detail="Confirmation required before creating a project structure")
    result = apply_project_plan(vault_dir, plan, overwrite_paths=req.overwrite_paths)
    for path in result["created_files"]:
        abs_path = secure_path(vault_dir, path)
        record_action(
            vault_dir,
            action="create_file",
            owner=owner,
            tool="obsidian_project_plan_apply",
            paths=[path],
            after={"content": _read_text_if_exists(abs_path)},
        )
    for detail in result.get("overwritten_file_details", []):
        record_action(
            vault_dir,
            action="update_file",
            owner=owner,
            tool="obsidian_project_plan_apply",
            paths=[detail["path"]],
            before={"content": detail["before"]},
            after={"content": detail["after"]},
        )
    for relationship in result["relationships"]:
        record_action(
            vault_dir,
            action="relationship_add",
            owner=owner,
            tool="obsidian_project_plan_apply",
            paths=[relationship["source"], relationship["target"]],
            after={"relationship": relationship},
        )
    result.pop("overwritten_file_details", None)
    return result

def is_self_or_descendant_move(abs_old: str, abs_new: str) -> bool:
    return vault_service.is_self_or_descendant_move(abs_old, abs_new)

def get_file_tree(dir_path: str, base_path: str) -> List[Dict[str, Any]]:
    """Recursively build a sorted tree of directories and files."""
    return vault_service.file_tree(base_path, dir_path)

# --- Endpoints ---

@router.get("/app")
@router.get("/app/")
async def obsidian_app(request: Request):
    """Serve a standalone entry page for the plugin manager's Open button."""
    return HTMLResponse(app_html_for_request(request))

@router.get("/status")
async def vault_status(request: Request):
    """Return vault protection status without exposing secrets."""
    return protection_status(get_vault_path(request))

@router.post("/vault/password")
async def set_vault_password(req: VaultPasswordRequest, request: Request):
    """Enable or replace password protection for the vault."""
    try:
        _require_vault_scope(request, VAULT_WRITE_SCOPE)
        return set_password(get_vault_path(request), req.password)
    except VaultSecurityError as exc:
        raise vault_error(exc)

@router.post("/vault/lock")
async def lock_current_vault(request: Request):
    """Lock a password-protected vault."""
    try:
        _require_vault_scope(request, VAULT_WRITE_SCOPE)
        return lock_vault(get_vault_path(request))
    except VaultSecurityError as exc:
        raise vault_error(exc)

@router.post("/vault/unlock")
async def unlock_current_vault(req: VaultPasswordRequest, request: Request):
    """Unlock a password-protected vault."""
    try:
        _require_vault_scope(request, VAULT_WRITE_SCOPE)
        return unlock_vault(get_vault_path(request), req.password)
    except VaultSecurityError as exc:
        raise vault_error(exc)

@router.delete("/vault/password")
async def remove_vault_password(req: VaultPasswordRequest, request: Request):
    """Disable password protection after password verification."""
    try:
        _require_vault_scope(request, VAULT_WRITE_SCOPE)
        return remove_password(get_vault_path(request), req.password)
    except VaultSecurityError as exc:
        raise vault_error(exc)

@router.post("/vault/export")
async def export_current_vault(req: VaultExportRequest, request: Request):
    """Export the current vault as plain or password-encrypted ZIP data."""
    try:
        archive = export_vault(get_vault_path(request), password=req.password, root=req.root)
        return {
            "filename": archive.filename,
            "encrypted": archive.encrypted,
            "file_count": archive.file_count,
            "archive_base64": base64.b64encode(archive.data).decode("ascii"),
        }
    except VaultSecurityError as exc:
        raise vault_error(exc)

@router.post("/vault/import")
async def import_current_vault(req: VaultImportRequest, request: Request):
    """Import a plain or password-encrypted ZIP vault archive."""
    try:
        _require_vault_scope(request, VAULT_WRITE_SCOPE)
        archive_data = base64.b64decode(req.archive_base64, validate=True)
        result = import_vault(get_vault_path(request), archive_data, password=req.password)
        return {"success": True, **result}
    except (ValueError, VaultSecurityError) as exc:
        raise vault_error(VaultSecurityError(str(exc)))

@router.get("/files")
async def list_files(request: Request):
    """Get the complete tree structure of the vault."""
    try:
        vault_dir = get_unlocked_vault_path(request)
        return vault_service.file_tree(vault_dir)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vault/events")
async def vault_events(request: Request):
    """Stream vault change notifications so the UI can refresh automatically."""
    vault_dir = get_unlocked_vault_path(request)

    async def event_stream():
        last_signature = None
        while True:
            if await request.is_disconnected():
                break
            try:
                latest, signature = await asyncio.to_thread(_vault_watch_signature, vault_dir)
                if last_signature is None:
                    last_signature = signature
                    yield f"event: ready\ndata: {json.dumps({'latest': latest})}\n\n"
                elif signature != last_signature:
                    last_signature = signature
                    yield f"event: vault_changed\ndata: {json.dumps({'latest': latest})}\n\n"
                else:
                    yield ": keepalive\n\n"
            except Exception as exc:
                payload = json.dumps({"error": str(exc)})
                yield f"event: error\ndata: {payload}\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/files/recent")
async def files_recent(request: Request, since: Optional[str] = None, until: Optional[str] = None):
    """Return files filtered by modification time range (ISO dates)."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        return vault_service.files_recent(vault_dir, since=since, until=until)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/files/changed")
async def files_changed(request: Request, since: str):
    """Return files modified since a date (excludes newly created files)."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        return vault_service.files_changed(vault_dir, since=since)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/file")
async def read_file(path: str, request: Request):
    """Read a specific file's content or serve binary assets."""
    vault_dir = get_unlocked_vault_path(request)
    abs_path = secure_path(vault_dir, path)
    
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    if os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail="Specified path is a directory")
        
    # Check if the file is markdown or text to return as JSON
    if vault_service.is_text_path(abs_path):
        try:
            return {"content": vault_service.read_file(vault_dir, path)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")
    else:
        # Serve binary files (images, PDFs) directly
        return FileResponse(abs_path)

@router.post("/file")
async def create_file(req: FileWriteRequest, request: Request):
    """Create a new file in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        vault_service.create_file(
            vault_dir,
            req.path,
            req.content,
            owner=current_owner(request),
            tool="obsidian_api",
            actor=vault_actor(request),
        )
        return {"success": True, "path": req.path}
    except FileExistsError:
        raise HTTPException(status_code=400, detail="File already exists")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create file: {e}")

@router.put("/file")
async def update_file(req: FileWriteRequest, request: Request):
    """Update (autosave) an existing file in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        vault_service.update_file(
            vault_dir,
            req.path,
            req.content,
            owner=current_owner(request),
            tool="obsidian_api",
            actor=vault_actor(request),
        )
        return {"success": True, "path": req.path}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except IsADirectoryError:
        raise HTTPException(status_code=400, detail="Specified path is a directory")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update file: {e}")

@router.delete("/file")
async def delete_file(path: str, request: Request):
    """Delete a file from the vault."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_DELETE_SCOPE)
    try:
        vault_service.delete_file(vault_dir, path, owner=current_owner(request), tool="obsidian_api", actor=vault_actor(request))
        return {"success": True}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except IsADirectoryError:
        raise HTTPException(status_code=400, detail="Specified path is a directory")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")

@router.get("/file/frontmatter")
async def read_frontmatter(path: str, request: Request):
    """Read only the YAML frontmatter of a markdown file (without the body)."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        return {"path": path, "frontmatter": vault_service.read_frontmatter(vault_dir, path)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read frontmatter: {e}")

@router.put("/file/frontmatter")
async def merge_frontmatter(req: FrontmatterMergeRequest, request: Request):
    """Merge frontmatter keys into a markdown file (body unchanged)."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        return vault_service.merge_frontmatter(
            vault_dir,
            req.path,
            req.frontmatter,
            owner=current_owner(request),
            tool="obsidian_api",
            actor=vault_actor(request),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to merge frontmatter: {e}")

@router.post("/folder")
async def create_folder(req: FolderCreateRequest, request: Request):
    """Create a new folder in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        vault_service.create_folder(vault_dir, req.path, owner=current_owner(request), tool="obsidian_api")
        return {"success": True, "path": req.path}
    except FileExistsError:
        raise HTTPException(status_code=400, detail="Path already exists")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create folder: {e}")

@router.delete("/folder")
async def delete_folder(path: str, request: Request):
    """Recursively delete a folder from the vault."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_DELETE_SCOPE)
    try:
        vault_service.delete_folder(vault_dir, path, owner=current_owner(request), tool="obsidian_api", recursive=True)
        return {"success": True}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Folder not found")
    except NotADirectoryError:
        raise HTTPException(status_code=400, detail="Specified path is not a directory")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete folder: {e}")

@router.post("/rename")
async def rename_item(req: RenameRequest, request: Request):
    """Rename or move a file/folder in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        vault_service.rename_item(
            vault_dir,
            req.old_path,
            req.new_path,
            owner=current_owner(request),
            tool="obsidian_api",
        )
        return {"success": True, "path": req.new_path}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except FileExistsError:
        raise HTTPException(status_code=400, detail="Destination already exists")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rename: {e}")

@router.post("/batch")
async def batch_ops(req: BatchOperationRequest, request: Request):
    """Execute multiple vault operations atomically with optional dry-run preview."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        return vault_service.batch_operations(
            vault_dir,
            req.operations,
            owner=current_owner(request),
            tool="obsidian_api",
            dry_run=req.dry_run,
            actor=vault_actor(request),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch operation failed: {e}")

@router.get("/search")
async def search_vault(q: str, request: Request, max_results: Optional[int] = None, tag_filter: Optional[str] = None):
    """Perform full-text search inside all markdown notes in the vault.

    - max_results: Limit the number of returned files (default unlimited).
    - tag_filter:  Only search notes that carry this tag (exact, case-insensitive).
    """
    vault_dir = get_unlocked_vault_path(request)
    results = []
    
    if not q.strip():
        return results
    
    try:
        for result in vault_service.search_markdown(vault_dir, q):
            # Apply tag filter if specified
            if tag_filter:
                try:
                    content = vault_service.read_file(vault_dir, result.path)
                except OSError:
                    continue
                tags = extract_tags(content, result.path)["tags"]
                tag_lower = tag_filter.strip().lower().lstrip("#")
                if not any(t.lower() == tag_lower for t in tags):
                    continue
            results.append({
                "path": result.path,
                "matches": [{"line": match.line, "text": match.text} for match in result.matches],
            })
            if max_results and len(results) >= max_results:
                break
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    return results

@router.get("/search-semantic")
async def semantic_search_vault(q: str, request: Request, top_k: int = 10):
    """Semantic (embedding-based) search across all markdown notes in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        return search_semantic(vault_dir, q, top_k=top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Semantic search failed: {e}")

@router.get("/tags")
async def list_tags(request: Request):
    """Return explicit and implicit vault tags with deterministic colors."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        return build_vault_index(vault_dir)["tags"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build tags: {e}")

@router.get("/tags/suggest")
async def suggest_tags_route(request: Request, prefix: str = ""):
    """Suggest existing tags matching a prefix."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        return suggest_tags(vault_dir, prefix=prefix)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to suggest tags: {e}")

@router.get("/graph")
async def graph_vault(request: Request, focus: Optional[str] = None, tag: Optional[str] = None):
    """Return the Obsidian graph model with edge reasons and tag metadata."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        return graph_payload(vault_dir, focus=focus, tag=tag)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build graph: {e}")

@router.get("/relationships")
async def list_relationships(request: Request):
    """Return manually curated graph relationships."""
    vault_dir = get_unlocked_vault_path(request)
    return {"relationships": load_manual_relationships(vault_dir)}

@router.get("/suggest-links")
async def suggest_links_route(request: Request, path: str, top_k: int = 5):
    """Suggest related notes for a given vault path."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        return suggest_links(vault_dir, path=path, top_k=top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to suggest links: {e}")

@router.get("/project-plan/templates")
async def project_plan_templates(request: Request):
    """Return deterministic project planning templates and schema options."""
    get_unlocked_vault_path(request)
    return template_options()

@router.get("/project-plan/sessions")
async def project_plan_sessions(request: Request):
    """Return active recoverable project planning sessions for this vault."""
    vault_dir = get_unlocked_vault_path(request)
    payload = _load_project_plan_sessions(vault_dir)
    return {"sessions": _visible_project_plan_sessions(payload)}

@router.post("/project-plan/sessions")
async def project_plan_session_create(req: ProjectPlanSessionCreateRequest, request: Request):
    """Create a recoverable project planning session without writing vault files."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        validate_gamedev_concept_gate(req.request)
        session = _new_project_plan_session(req.request)
    except ProjectPlanValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    payload = _load_project_plan_sessions(vault_dir)
    payload.setdefault("sessions", []).append(session)
    _save_project_plan_sessions(vault_dir, payload)
    return session

@router.get("/project-plan/sessions/{session_id}")
async def project_plan_session_get(session_id: str, request: Request):
    """Load one project planning session."""
    vault_dir = get_unlocked_vault_path(request)
    payload = _load_project_plan_sessions(vault_dir)
    session = _find_project_plan_session(payload, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Project planning session not found")
    return session

@router.delete("/project-plan/sessions/{session_id}")
async def project_plan_session_delete(session_id: str, request: Request):
    """Cancel a project planning session without touching vault files."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    payload = _load_project_plan_sessions(vault_dir)
    before = len(payload.get("sessions", []))
    payload["sessions"] = [session for session in payload.get("sessions", []) if session.get("id") != session_id]
    if len(payload["sessions"]) == before:
        raise HTTPException(status_code=404, detail="Project planning session not found")
    _save_project_plan_sessions(vault_dir, payload)
    return {"success": True, "session_id": session_id}

@router.post("/project-plan/sessions/{session_id}/preview-stream")
async def project_plan_session_preview_stream(session_id: str, req: ProjectPlanSessionPreviewRequest, request: Request):
    """Stream and persist a recoverable project planning preview for one session."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    owner = current_owner(request)
    payload = _load_project_plan_sessions(vault_dir)
    session = _find_project_plan_session(payload, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Project planning session not found")

    request_payload = req.request or ProjectPlanRequest(**session.get("request", {}))
    session.update({
        "title": request_payload.title.strip() or session.get("title") or "Untitled project",
        "kind": request_payload.kind,
        "target_folder": _project_session_target_folder(request_payload),
        "target_preview_path": _project_session_target_folder(request_payload),
        "request": _project_plan_request_payload(request_payload),
        "status": "generating",
        "progress": {
            "phase": "preparing",
            "current_file": "",
            "current_index": 0,
            "total_files": 0,
            "message": "Preparing project plan",
        },
        "error": "",
        "updated_at": _utc_now(),
    })
    _append_project_debug_event(session, "preparing", "Preview stream requested")
    _save_project_plan_sessions(vault_dir, payload)

    async def _stream():
        try:
            _append_project_debug_event(session, "preparing", "Validating request and building deterministic plan")
            _save_project_plan_sessions(vault_dir, payload)
            validate_gamedev_concept_gate(request_payload)
            plan = build_project_plan(vault_dir, request_payload)
            plan_payload = _dump_model(plan)
            session.update({
                "plan": plan_payload,
                "status": "generating",
                "progress": {
                    "phase": "generating",
                    "current_file": "",
                    "current_index": 0,
                    "total_files": len(plan.files),
                    "message": "Generating project files",
                },
                "updated_at": _utc_now(),
            })
            _append_project_debug_event(session, "generating", "Plan built; starting AI content generation")
            _save_project_plan_sessions(vault_dir, payload)
            yield _project_plan_stream_event("session_updated", {"session": session})
            yield _project_plan_stream_event("plan_started", {"plan": plan_payload})

            event_queue: asyncio.Queue[str] = asyncio.Queue()

            async def _progress(event: Dict[str, Any]) -> None:
                event_type = str(event.get("type", "progress"))
                progress_payload = dict(event)
                progress_payload.pop("type", None)
                if event_type == "file_started":
                    session["status"] = "generating"
                    current_file = progress_payload.get("path", "")
                    session["progress"] = {
                        "phase": "generating",
                        "current_file": current_file,
                        "current_index": int(progress_payload.get("index", 0)) + 1,
                        "total_files": int(progress_payload.get("total", len(plan.files))),
                        "message": f"Writing {current_file or 'file'}",
                    }
                    _append_project_debug_event(session, "file_started", "File generation started", file=current_file)
                elif event_type == "file_done":
                    current_plan = session.get("plan") or plan_payload
                    files = current_plan.setdefault("files", [])
                    index = int(progress_payload.get("index", -1))
                    if 0 <= index < len(files) and progress_payload.get("file"):
                        files[index] = progress_payload["file"]
                    session["plan"] = current_plan
                    session["progress"] = {
                        "phase": "generating",
                        "current_file": progress_payload.get("file", {}).get("path", ""),
                        "current_index": int(progress_payload.get("index", 0)) + 1,
                        "total_files": int(progress_payload.get("total", len(plan.files))),
                        "message": "Content ready",
                    }
                    _append_project_debug_event(
                        session,
                        "file_done",
                        "File generation finished",
                        file=progress_payload.get("file", {}).get("path", ""),
                    )
                elif event_type == "warning":
                    warning = progress_payload.get("message", "Generation warning")
                    session["progress"] = {
                        **session.get("progress", {}),
                        "message": warning,
                    }
                    _append_project_debug_event(session, "warning", warning)
                session["updated_at"] = _utc_now()
                _save_project_plan_sessions(vault_dir, payload)
                await event_queue.put(_project_plan_stream_event(event_type, progress_payload))
                await event_queue.put(_project_plan_stream_event("session_updated", {"session": session}))

            generation_task = asyncio.create_task(generate_project_plan_content(
                plan,
                llm_call=project_planning_llm_call(owner),
                progress_callback=_progress,
            ))
            try:
                while not generation_task.done() or not event_queue.empty():
                    try:
                        yield await asyncio.wait_for(event_queue.get(), timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
                plan = await generation_task
            finally:
                if not generation_task.done():
                    generation_task.cancel()
            plan = validate_project_plan(vault_dir, plan, collect_conflicts=True)
            final_payload = _dump_model(plan)
            session.update({
                "plan": final_payload,
                "status": "ready",
                "progress": {
                    "phase": "ready",
                    "current_file": "",
                    "current_index": len(plan.files),
                    "total_files": len(plan.files),
                    "message": "Preview ready",
                },
                "error": "",
                "updated_at": _utc_now(),
            })
            _append_project_debug_event(session, "ready", "Preview ready")
            _save_project_plan_sessions(vault_dir, payload)
            yield _project_plan_stream_event("session_updated", {"session": session})
            yield _project_plan_stream_event("plan_done", {"plan": final_payload})
        except ProjectPlanValidationError as exc:
            session.update({
                "status": "error",
                "progress": {**session.get("progress", {}), "phase": "error", "message": str(exc)},
                "error": str(exc),
                "updated_at": _utc_now(),
            })
            _append_project_debug_event(session, "error", str(exc), error=str(exc))
            _save_project_plan_sessions(vault_dir, payload)
            yield _project_plan_stream_event("session_updated", {"session": session})
            yield _project_plan_stream_event("error", {"detail": str(exc)})
        except Exception:
            session.update({
                "status": "error",
                "progress": {**session.get("progress", {}), "phase": "error", "message": "Project preview failed"},
                "error": "Project preview failed",
                "updated_at": _utc_now(),
            })
            _append_project_debug_event(session, "error", "Project preview failed", error="Project preview failed")
            _save_project_plan_sessions(vault_dir, payload)
            yield _project_plan_stream_event("session_updated", {"session": session})
            yield _project_plan_stream_event("error", {"detail": "Project preview failed"})

    return StreamingResponse(_stream(), media_type="text/event-stream")

@router.post("/project-plan/sessions/{session_id}/apply")
async def project_plan_session_apply(session_id: str, req: ProjectPlanSessionApplyRequest, request: Request):
    """Apply a confirmed project planning session and mark it created."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    owner = current_owner(request)
    payload = _load_project_plan_sessions(vault_dir)
    session = _find_project_plan_session(payload, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Project planning session not found")
    plan_payload = req.plan or (ProjectPlan(**session["plan"]) if session.get("plan") else None)
    if not plan_payload:
        raise HTTPException(status_code=400, detail="Project planning session has no plan")
    session.update({
        "status": "applying",
        "progress": {**session.get("progress", {}), "phase": "applying", "message": "Creating vault files"},
        "updated_at": _utc_now(),
    })
    _append_project_debug_event(session, "applying", "Apply requested")
    _save_project_plan_sessions(vault_dir, payload)
    try:
        result = _apply_project_plan_to_vault(
            vault_dir,
            owner,
            ProjectPlanApplyRequest(
                plan=plan_payload,
                confirm=req.confirm,
                confirm_conflicts=req.confirm_conflicts,
                selected_paths=req.selected_paths,
                overwrite_paths=req.overwrite_paths,
            ),
        )
        session.update({
            "status": "created",
            "progress": {**session.get("progress", {}), "phase": "created", "message": "Project files created"},
            "error": "",
            "updated_at": _utc_now(),
        })
        _append_project_debug_event(session, "created", "Project files created")
        _save_project_plan_sessions(vault_dir, payload)
        return {**result, "session": session}
    except Exception as exc:
        session.update({
            "status": "error",
            "progress": {**session.get("progress", {}), "phase": "error", "message": str(exc)},
            "error": str(exc),
            "updated_at": _utc_now(),
        })
        _append_project_debug_event(session, "error", str(exc), error=str(exc))
        _save_project_plan_sessions(vault_dir, payload)
        raise

@router.post("/project-plan/improve-description")
async def project_plan_improve_description(req: ProjectDescriptionImproveRequest, request: Request):
    """Improve the project planning prompt without writing to the vault."""
    get_unlocked_vault_path(request)
    try:
        description = await improve_project_description_with_ai(
            req,
            llm_call=project_planning_llm_call(current_owner(request)),
        )
        return {"description": description}
    except HTTPException:
        raise
    except ProjectPlanValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/project-plan/gamedev-draft")
async def project_plan_gamedev_draft(req: GameDevConceptDraftRequest, request: Request):
    """Build an editable GameDev concept draft before creating the plan preview."""
    get_unlocked_vault_path(request)
    try:
        return await build_gamedev_concept_draft_with_ai(
            req,
            llm_call=project_planning_llm_call(current_owner(request)),
        )
    except HTTPException:
        raise
    except ProjectPlanValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/project-plan/preview")
async def project_plan_preview(req: ProjectPlanRequest, request: Request):
    """Build a non-destructive AI project planning preview."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        validate_gamedev_concept_gate(req)
        plan = build_project_plan(vault_dir, req)
        if req.generate_content:
            plan = await generate_project_plan_content(
                plan,
                llm_call=project_planning_llm_call(current_owner(request)),
            )
            plan = validate_project_plan(vault_dir, plan, collect_conflicts=True)
        return plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
    except ProjectPlanValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/project-plan/preview-stream")
async def project_plan_preview_stream(req: ProjectPlanRequest, request: Request):
    """Stream a non-destructive AI project planning preview as each file is generated."""
    vault_dir = get_unlocked_vault_path(request)
    owner = current_owner(request)

    async def _stream():
        try:
            validate_gamedev_concept_gate(req)
            plan = build_project_plan(vault_dir, req)
            plan_payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
            yield _project_plan_stream_event("plan_started", {"plan": plan_payload})

            event_queue: asyncio.Queue[str] = asyncio.Queue()

            async def _progress(event: Dict[str, Any]) -> None:
                event_type = str(event.pop("type", "progress"))
                await event_queue.put(_project_plan_stream_event(event_type, event))

            generation_task = asyncio.create_task(generate_project_plan_content(
                plan,
                llm_call=project_planning_llm_call(owner),
                progress_callback=_progress,
            ))
            try:
                while not generation_task.done() or not event_queue.empty():
                    try:
                        yield await asyncio.wait_for(event_queue.get(), timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
                plan = await generation_task
            finally:
                if not generation_task.done():
                    generation_task.cancel()
            plan = validate_project_plan(vault_dir, plan, collect_conflicts=True)
            final_payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
            yield _project_plan_stream_event("plan_done", {"plan": final_payload})
        except ProjectPlanValidationError as exc:
            yield _project_plan_stream_event("error", {"detail": str(exc)})
        except Exception:
            yield _project_plan_stream_event("error", {"detail": "Project preview failed"})

    return StreamingResponse(_stream(), media_type="text/event-stream")

@router.post("/project-plan/apply")
async def project_plan_apply(req: ProjectPlanApplyRequest, request: Request):
    """Apply a confirmed project plan by writing files and relationships."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        return _apply_project_plan_to_vault(vault_dir, current_owner(request), req)
    except HTTPException:
        raise
    except ProjectPlanValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/memory-review/preview")
async def memory_review_preview(req: MemoryReviewRequest, request: Request):
    """Build a non-destructive memory review Save-to-Obsidian preview."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        plan = build_memory_review_plan(vault_dir, req)
        return plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
    except MemoryReviewValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/memory-review/apply")
async def memory_review_apply(req: MemoryReviewApplyRequest, request: Request):
    """Apply a confirmed memory review plan by writing or updating notes."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        plan = validate_memory_review_plan(vault_dir, req.plan, collect_conflicts=True)
        if plan.conflicts:
            raise HTTPException(status_code=409, detail={"message": "Memory review plan has file conflicts", "conflicts": plan.conflicts})
        if plan.action not in {"memory_only", "discard"}:
            _require_vault_scope(request, VAULT_WRITE_SCOPE)
        if plan.action not in {"memory_only", "discard"} and not req.confirm:
            raise HTTPException(status_code=409, detail="Confirmation required before changing Obsidian notes")
        result = apply_memory_review_plan(vault_dir, plan)
        for path in result.get("created_files", []):
            abs_path = secure_path(vault_dir, path)
            record_action(
                vault_dir,
                action="create_file",
                owner=current_owner(request),
                tool="obsidian_memory_review_apply",
                paths=[path],
                after={"content": _read_text_if_exists(abs_path)},
            )
        for detail in result.get("updated_file_details", []):
            record_action(
                vault_dir,
                action="update_file",
                owner=current_owner(request),
                tool="obsidian_memory_review_apply",
                paths=[detail["path"]],
                before={"content": detail["before"]},
                after={"content": detail["after"]},
            )
        for relationship in result.get("relationships", []):
            record_action(
                vault_dir,
                action="relationship_add",
                owner=current_owner(request),
                tool="obsidian_memory_review_apply",
                paths=[relationship["source"], relationship["target"]],
                after={"relationship": relationship},
            )
        result.pop("updated_file_details", None)
        return result
    except HTTPException:
        raise
    except MemoryReviewValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/memory-capture/preview")
async def memory_capture_preview(req: MemoryCaptureRequest, request: Request):
    """Build a normalized, non-destructive memory capture plan."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        plan = build_memory_capture_plan(vault_dir, req)
        return plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
    except MemoryCaptureValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/memory-capture/apply")
async def memory_capture_apply(req: MemoryCaptureApplyRequest, request: Request):
    """Apply a confirmed normalized memory capture plan."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        plan = validate_memory_capture_plan(vault_dir, req.plan)
        if plan.confirm_required and not req.confirm:
            raise HTTPException(status_code=409, detail="Confirmation required before changing Obsidian notes")
        return apply_memory_capture_plan(
            vault_dir,
            plan,
            owner=current_owner(request),
            actor=vault_actor(request),
        )
    except HTTPException:
        raise
    except MemoryCaptureValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/spark/analyze")
async def spark_analyze(req: SparkAnalyzeRequest, request: Request):
    """Analyze long-term memory health without changing the vault."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        health = analyze_memory_health(vault_dir, req)
        return health.model_dump() if hasattr(health, "model_dump") else health.dict()
    except SparkValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/spark/plan")
async def spark_plan(req: SparkAnalyzeRequest, request: Request):
    """Build a non-destructive Spark cleanup and canonicalization plan."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        plan = build_spark_plan(vault_dir, req)
        return plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
    except SparkValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/spark/apply")
async def spark_apply(req: SparkApplyRequest, request: Request):
    """Apply selected low/medium-risk Spark actions with confirmation."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        return apply_spark_plan(
            vault_dir,
            req,
            owner=current_owner(request),
            actor=vault_actor(request),
        )
    except SparkValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/memory-tree")
async def memory_tree(request: Request):
    """Return read-only SOMT health and branch summary."""
    vault_dir = get_unlocked_vault_path(request)
    return memory_tree_status(vault_dir)


@router.get("/memory/status")
async def memory_status_route(request: Request):
    """Return read-only status across SOMT, Freshness Gate, quarantine, and RAPTOR."""
    vault_dir = get_unlocked_vault_path(request)
    return memory_status(vault_dir)


@router.get("/memory/ledger")
async def memory_ledger_route(request: Request):
    """Return SQLite source/index ledger status for the derived memory pipeline."""
    vault_dir = get_unlocked_vault_path(request)
    return memory_ledger_status(vault_dir)


@router.post("/memory/ledger/sync")
async def memory_ledger_sync_route(request: Request):
    """Synchronize the derived source ledger from current vault files."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    return sync_memory_ledger(vault_dir)


@router.get("/memory/index")
async def derived_index_route(request: Request):
    """Return derived chunk/index status for the memory pipeline."""
    vault_dir = get_unlocked_vault_path(request)
    return derived_index_status(vault_dir)


@router.post("/memory/index/rebuild")
async def derived_index_rebuild_route(request: Request):
    """Build or rebuild the derived chunk index from current vault sources."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    return build_derived_index(vault_dir)


@router.get("/memory/index/retrieve")
async def derived_index_retrieve_route(request: Request, q: str, top_k: int = 5, path_prefix: str = ""):
    """Run lightweight retrieval against the derived chunk index."""
    vault_dir = get_unlocked_vault_path(request)
    return retrieve_derived_chunks(vault_dir, q, top_k=top_k, path_prefix=path_prefix)


@router.get("/memory/query/status")
async def query_layer_status_route(request: Request):
    """Return read-only status for the lightweight query layer."""
    vault_dir = get_unlocked_vault_path(request)
    return query_layer_status(vault_dir, owner=current_owner(request))


@router.get("/memory/query")
async def query_layer_route(request: Request, q: str, top_k: int = 5, path_prefix: str = "", answer_mode: str = "auto"):
    """Answer a query from the derived index with citations and confidence."""
    vault_dir = get_unlocked_vault_path(request)
    return await answer_query_async(
        vault_dir,
        q,
        top_k=top_k,
        path_prefix=path_prefix,
        owner=current_owner(request),
        answer_mode=answer_mode,
    )


@router.get("/memory/automation/status")
async def memory_automation_status_route(request: Request):
    """Return status for low-risk background memory automation."""
    vault_dir = get_unlocked_vault_path(request)
    return memory_automation_status(vault_dir)


@router.post("/memory/automation/run")
async def memory_automation_run_route(request: Request, force: bool = False):
    """Run one low-risk memory automation pass for derived data maintenance."""
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    get_unlocked_vault_path(request)
    return run_memory_automation(current_owner(request), trigger="manual", context={}, force=force)


@router.get("/memory/rebuild-proof")
async def rebuild_proof_status_route(request: Request):
    """Return the latest derived memory rebuild proof report status."""
    vault_dir = get_unlocked_vault_path(request)
    return rebuild_proof_status(vault_dir)


@router.post("/memory/rebuild-proof/run")
async def rebuild_proof_run_route(request: Request, q: Optional[str] = None, top_k: int = 5):
    """Run a full ledger/index/query rebuild proof and persist its report."""
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    vault_dir = get_unlocked_vault_path(request)
    return await asyncio.to_thread(run_rebuild_proof, vault_dir, query=q, top_k=top_k)


@router.get("/memory/external-upgrade-proof")
async def external_upgrade_proof_status_route(request: Request):
    """Return release-evidence status for external plugin distribution and version sync."""
    get_unlocked_vault_path(request)
    plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return {
        "plugin_root": plugin_dir,
        "version_sync": collect_version_sync(plugin_dir),
        "distribution_layout": collect_distribution_layout(plugin_dir, ignore_runtime_artifacts=True),
    }


@router.post("/memory/external-upgrade-proof/run")
async def external_upgrade_proof_run_route(
    request: Request,
    q: Optional[str] = None,
    top_k: int = 5,
    path_prefix: str = "",
    export_password: str = "external-upgrade-proof",
):
    """Run export/import/rebuild release evidence for external distribution upgrades."""
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    vault_dir = get_unlocked_vault_path(request)
    plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return await asyncio.to_thread(
        collect_external_upgrade_proof,
        plugin_dir,
        vault_dir,
        query=q,
        top_k=top_k,
        path_prefix=path_prefix,
        export_password=export_password,
    )


@router.post("/memory-tree/analyze")
async def memory_tree_analyze(request: Request, limit: Optional[int] = None):
    """Analyze the vault into a read-only SOMT candidate report."""
    vault_dir = get_unlocked_vault_path(request)
    return analyze_memory_tree(vault_dir, limit=limit)


@router.get("/knowledge-audit")
async def knowledge_audit(request: Request):
    """Run the read-only Freshness Gate audit."""
    vault_dir = get_unlocked_vault_path(request)
    return audit_knowledge(vault_dir)


@router.get("/quarantine")
async def quarantine(request: Request):
    """List knowledge isolated from default retrieval without deleting it."""
    vault_dir = get_unlocked_vault_path(request)
    return quarantine_list(vault_dir)


@router.get("/raptor/status")
async def raptor_status_route(request: Request):
    """Return RAPTOR index status and write-gate readiness."""
    vault_dir = get_unlocked_vault_path(request)
    return raptor_status(vault_dir)


@router.get("/raptor/graph")
async def raptor_graph_route(request: Request, edge_offset: int = 0, limit: int = 500):
    """Return a bounded cached RAPTOR graph edge view."""
    vault_dir = get_unlocked_vault_path(request)
    return bounded_raptor_graph_view(vault_dir, edge_offset=edge_offset, limit=limit)


@router.post("/raptor/rebuild")
async def raptor_rebuild_route(request: Request, max_sources: int = 2000, max_edges: int = 5000):
    """Rebuild derived RAPTOR artifacts when explicit write gates allow it."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    return await asyncio.to_thread(
        rebuild_raptor_artifacts,
        vault_dir,
        max_sources=max(1, min(int(max_sources), 100_000)),
        max_edges=max(0, min(int(max_edges), 500_000)),
    )


@router.post("/relationships")
async def create_relationship(req: RelationshipRequest, request: Request):
    """Create a typed manual graph relationship between existing notes."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    try:
        relationship = add_manual_relationship(vault_dir, _dump_model(req))
        record_action(
            vault_dir,
            action="relationship_add",
            owner=current_owner(request),
            tool="obsidian_api",
            paths=[relationship["source"], relationship["target"]],
            after={"relationship": relationship},
        )
        return {"success": True, "relationship": relationship}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.delete("/relationships")
async def delete_relationship(req: RelationshipRequest, request: Request):
    """Delete one typed manual graph relationship."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_DELETE_SCOPE)
    removed = remove_manual_relationship(vault_dir, _dump_model(req))
    if removed is None:
        raise HTTPException(status_code=404, detail="Relationship not found")
    record_action(
        vault_dir,
        action="relationship_delete",
        owner=current_owner(request),
        tool="obsidian_api",
        paths=[removed["source"], removed["target"]],
        before={"relationship": removed},
    )
    return {"success": True, "relationship": removed}

@router.get("/history")
async def history(request: Request, limit: int = 50):
    """Return recent Obsidian vault actions without exposing secrets."""
    vault_dir = get_unlocked_vault_path(request)
    return {"history": list_history(vault_dir, limit=limit)}

@router.post("/history/undo")
async def undo_latest(request: Request):
    """Undo the latest safe reversible vault action for the current user."""
    vault_dir = get_unlocked_vault_path(request)
    _require_vault_scope(request, VAULT_WRITE_SCOPE)
    entry = latest_reversible(vault_dir, owner=current_owner(request))
    if not entry:
        raise HTTPException(status_code=404, detail="No reversible action to undo")
    try:
        _undo_entry(vault_dir, entry)
        mark_undone(vault_dir, entry["id"])
        return {"success": True, "undone": entry}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

def _undo_entry(vault_dir: str, entry: Dict[str, Any]) -> None:
    action = entry.get("action")
    before = entry.get("before") or {}
    after = entry.get("after") or {}
    paths = entry.get("paths") or []

    if action == "create_file":
        path = paths[0]
        abs_path = secure_path(vault_dir, path)
        if _read_text_if_exists(abs_path) != after.get("content"):
            raise ValueError("File changed after creation; refusing unsafe undo")
        os.remove(abs_path)
        return

    if action == "update_file":
        path = paths[0]
        abs_path = secure_path(vault_dir, path)
        if _read_text_if_exists(abs_path) != after.get("content"):
            raise ValueError("File changed after update; refusing unsafe undo")
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(before.get("content") or "")
        return

    if action == "rename_item":
        old_path = before.get("path")
        new_path = after.get("path")
        abs_old = secure_path(vault_dir, old_path)
        abs_new = secure_path(vault_dir, new_path)
        if not os.path.exists(abs_new) or os.path.exists(abs_old):
            raise ValueError("Rename can no longer be safely undone")
        os.makedirs(os.path.dirname(abs_old), exist_ok=True)
        shutil.move(abs_new, abs_old)
        return

    if action == "relationship_add":
        remove_manual_relationship(vault_dir, after.get("relationship") or {})
        return

    if action == "relationship_delete":
        add_manual_relationship(vault_dir, before.get("relationship") or {})
        return

    raise ValueError(f"Action is not undoable: {action}")

@router.get("/web/{filename:path}")
async def serve_web_assets(filename: str):
    """Serve static web assets for the plugin's frontend."""
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static_dir = os.path.abspath(os.path.join(current_dir, "frontend"))
    target_path = os.path.abspath(os.path.join(static_dir, filename))
    
    # Path traversal protection
    if os.path.commonpath([static_dir, target_path]) != static_dir:
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    media_type = None
    if target_path.endswith(".css"):
        media_type = "text/css"
    elif target_path.endswith(".js"):
        media_type = "application/javascript"
    elif target_path.endswith(".svg"):
        media_type = "image/svg+xml"
        
    return FileResponse(target_path, media_type=media_type)


def _build_orca_alias_router() -> APIRouter:
    alias_router = APIRouter()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith(OBSIDIAN_API_PREFIX):
            continue
        alias_path = route.path.replace(OBSIDIAN_API_PREFIX, ORCA_API_PREFIX, 1)
        alias_router.add_api_route(
            alias_path,
            route.endpoint,
            methods=route.methods,
            name=f"orca_{route.name}",
            response_class=route.response_class,
            status_code=route.status_code,
            tags=route.tags,
            dependencies=route.dependencies,
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            deprecated=route.deprecated,
            operation_id=f"orca_{route.operation_id}" if route.operation_id else None,
        )
    return alias_router


orca_router = _build_orca_alias_router()
