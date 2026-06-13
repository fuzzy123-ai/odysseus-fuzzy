import os
import shutil
import base64
import json
import asyncio
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from src.auth_helpers import require_user

from .vault_security import (
    VaultSecurityError,
    export_vault,
    import_vault,
    lock_vault,
    protection_status,
    remove_password,
    require_unlocked,
    set_password,
    unlock_vault,
)
from .vault_history import latest_reversible, list_history, mark_undone, record_action
from .vault_model import (
    add_manual_relationship,
    build_vault_index,
    graph_payload,
    load_manual_relationships,
    remove_manual_relationship,
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

router = APIRouter(prefix="/api/plugins/obsidian")

APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Odysseus Obsidian</title>
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
  <script type="module">
    window.ODYSSEUS_OBSIDIAN_STANDALONE = true;
    import "/api/plugins/obsidian/web/main.js";
    const openObsidian = () => {
      window.OdysseusObsidian?.openPanel?.();
    };
    if (document.readyState === "loading") {
      window.addEventListener("DOMContentLoaded", openObsidian, { once: true });
    } else {
      openObsidian();
    }
  </script>
</body>
</html>"""

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

# --- Helper Functions ---
def get_vault_path(request: Request) -> str:
    """Get the user-specific vault directory.
    
    Uses multi-user isolation or 'default' if auth is disabled.
    """
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
    return require_user(request) or "default"

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
    plan = validate_project_plan(vault_dir, req.plan, collect_conflicts=True)
    if plan.conflicts:
        raise HTTPException(status_code=409, detail={"message": "Plan has file conflicts", "conflicts": plan.conflicts})
    if not req.confirm:
        raise HTTPException(status_code=409, detail="Confirmation required before creating a project structure")
    result = apply_project_plan(vault_dir, plan)
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
    for relationship in result["relationships"]:
        record_action(
            vault_dir,
            action="relationship_add",
            owner=owner,
            tool="obsidian_project_plan_apply",
            paths=[relationship["source"], relationship["target"]],
            after={"relationship": relationship},
        )
    return result

def is_self_or_descendant_move(abs_old: str, abs_new: str) -> bool:
    return vault_service.is_self_or_descendant_move(abs_old, abs_new)

def get_file_tree(dir_path: str, base_path: str) -> List[Dict[str, Any]]:
    """Recursively build a sorted tree of directories and files."""
    return vault_service.file_tree(base_path, dir_path)

# --- Endpoints ---

@router.get("/app")
async def obsidian_app():
    """Serve a standalone entry page for the plugin manager's Open button."""
    return HTMLResponse(APP_HTML)

@router.get("/status")
async def vault_status(request: Request):
    """Return vault protection status without exposing secrets."""
    return protection_status(get_vault_path(request))

@router.post("/vault/password")
async def set_vault_password(req: VaultPasswordRequest, request: Request):
    """Enable or replace password protection for the vault."""
    try:
        return set_password(get_vault_path(request), req.password)
    except VaultSecurityError as exc:
        raise vault_error(exc)

@router.post("/vault/lock")
async def lock_current_vault(request: Request):
    """Lock a password-protected vault."""
    try:
        return lock_vault(get_vault_path(request))
    except VaultSecurityError as exc:
        raise vault_error(exc)

@router.post("/vault/unlock")
async def unlock_current_vault(req: VaultPasswordRequest, request: Request):
    """Unlock a password-protected vault."""
    try:
        return unlock_vault(get_vault_path(request), req.password)
    except VaultSecurityError as exc:
        raise vault_error(exc)

@router.delete("/vault/password")
async def remove_vault_password(req: VaultPasswordRequest, request: Request):
    """Disable password protection after password verification."""
    try:
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
    try:
        vault_service.create_file(
            vault_dir,
            req.path,
            req.content,
            owner=current_owner(request),
            tool="obsidian_api",
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
    try:
        vault_service.update_file(
            vault_dir,
            req.path,
            req.content,
            owner=current_owner(request),
            tool="obsidian_api",
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
    try:
        vault_service.delete_file(vault_dir, path, owner=current_owner(request), tool="obsidian_api")
        return {"success": True}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except IsADirectoryError:
        raise HTTPException(status_code=400, detail="Specified path is a directory")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")

@router.post("/folder")
async def create_folder(req: FolderCreateRequest, request: Request):
    """Create a new folder in the vault."""
    vault_dir = get_unlocked_vault_path(request)
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

@router.get("/search")
async def search_vault(q: str, request: Request):
    """Perform full-text search inside all markdown notes in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    results = []
    
    if not q.strip():
        return results
    
    try:
        for result in vault_service.search_markdown(vault_dir, q):
            results.append({
                "path": result.path,
                "matches": [{"line": match.line, "text": match.text} for match in result.matches],
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    return results

@router.get("/tags")
async def list_tags(request: Request):
    """Return explicit and implicit vault tags with deterministic colors."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        return build_vault_index(vault_dir)["tags"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build tags: {e}")

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
            ProjectPlanApplyRequest(plan=plan_payload, confirm=req.confirm, confirm_conflicts=req.confirm_conflicts),
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

@router.post("/relationships")
async def create_relationship(req: RelationshipRequest, request: Request):
    """Create a typed manual graph relationship between existing notes."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        relationship = add_manual_relationship(vault_dir, req.dict())
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
    removed = remove_manual_relationship(vault_dir, req.dict())
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
