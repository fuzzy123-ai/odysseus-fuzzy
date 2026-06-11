import os
import re
import shutil
import base64
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from src.constants import DATA_DIR
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
from .project_planning import (
    ProjectPlanApplyRequest,
    ProjectPlanRequest,
    ProjectPlanValidationError,
    apply_project_plan,
    build_project_plan,
    template_options,
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

# --- Helper Functions ---
def get_vault_path(request: Request) -> str:
    """Get the user-specific vault directory.
    
    Uses multi-user isolation or 'default' if auth is disabled.
    """
    username = require_user(request)
    folder_name = username if username else "default"
    configured_vault = os.getenv("OBSIDIAN_VAULT_DIR", "").strip()
    if configured_vault:
        vault_template = configured_vault.format(owner=folder_name)
        vault_dir = os.path.abspath(os.path.expanduser(vault_template))
    else:
        vault_dir = os.path.abspath(os.path.join(DATA_DIR, "obsidian_vaults", folder_name))
        os.makedirs(vault_dir, exist_ok=True)
    return vault_dir

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

def secure_path(vault_dir: str, relative_path: str) -> str:
    """Resolve and validate a relative path within the user's vault.
    
    Prevents path traversal attacks. Raises HTTPException 400 if invalid.
    """
    cleaned_rel = relative_path.replace("\\", "/").strip("/")
    abs_vault = os.path.abspath(vault_dir)
    abs_target = os.path.abspath(os.path.join(abs_vault, cleaned_rel))
    
    # Ensure target is strictly inside vault_dir using commonpath
    if os.path.commonpath([abs_vault, abs_target]) != abs_vault:
        raise HTTPException(status_code=400, detail="Path traversal attempt detected")
        
    return abs_target

def _read_text_if_exists(path: str) -> Optional[str]:
    if not os.path.exists(path) or os.path.isdir(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

def is_self_or_descendant_move(abs_old: str, abs_new: str) -> bool:
    old_path = os.path.abspath(abs_old)
    new_path = os.path.abspath(abs_new)
    return new_path == old_path or os.path.commonpath([old_path, new_path]) == old_path

def get_file_tree(dir_path: str, base_path: str) -> List[Dict[str, Any]]:
    """Recursively build a sorted tree of directories and files."""
    tree = []
    try:
        for entry in os.scandir(dir_path):
            if entry.name == ".obsidian":
                continue
            rel_path = os.path.relpath(entry.path, base_path).replace("\\", "/")
            if entry.is_dir():
                tree.append({
                    "name": entry.name,
                    "path": rel_path,
                    "is_dir": True,
                    "children": get_file_tree(entry.path, base_path)
                })
            else:
                tree.append({
                    "name": entry.name,
                    "path": rel_path,
                    "is_dir": False
                })
    except Exception:
        pass
    # Sort: folders first, then files alphabetically
    tree.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return tree

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
        tree = get_file_tree(vault_dir, vault_dir)
        return tree
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
    lower_name = abs_path.lower()
    if lower_name.endswith((".md", ".txt", ".json", ".html", ".js", ".css")):
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return {"content": content}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")
    else:
        # Serve binary files (images, PDFs) directly
        return FileResponse(abs_path)

@router.post("/file")
async def create_file(req: FileWriteRequest, request: Request):
    """Create a new file in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    abs_path = secure_path(vault_dir, req.path)
    
    if os.path.exists(abs_path):
        raise HTTPException(status_code=400, detail="File already exists")
        
    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        record_action(
            vault_dir,
            action="create_file",
            owner=current_owner(request),
            tool="obsidian_api",
            paths=[req.path],
            after={"content": req.content},
        )
        return {"success": True, "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create file: {e}")

@router.put("/file")
async def update_file(req: FileWriteRequest, request: Request):
    """Update (autosave) an existing file in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    abs_path = secure_path(vault_dir, req.path)
    
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    if os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail="Specified path is a directory")
        
    try:
        before_content = _read_text_if_exists(abs_path)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        record_action(
            vault_dir,
            action="update_file",
            owner=current_owner(request),
            tool="obsidian_api",
            paths=[req.path],
            before={"content": before_content},
            after={"content": req.content},
        )
        return {"success": True, "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update file: {e}")

@router.delete("/file")
async def delete_file(path: str, request: Request):
    """Delete a file from the vault."""
    vault_dir = get_unlocked_vault_path(request)
    abs_path = secure_path(vault_dir, path)
    
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    if os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail="Specified path is a directory")
        
    try:
        os.remove(abs_path)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")

@router.post("/folder")
async def create_folder(req: FolderCreateRequest, request: Request):
    """Create a new folder in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    abs_path = secure_path(vault_dir, req.path)
    
    if os.path.exists(abs_path):
        raise HTTPException(status_code=400, detail="Path already exists")
        
    try:
        os.makedirs(abs_path, exist_ok=True)
        return {"success": True, "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create folder: {e}")

@router.delete("/folder")
async def delete_folder(path: str, request: Request):
    """Recursively delete a folder from the vault."""
    vault_dir = get_unlocked_vault_path(request)
    abs_path = secure_path(vault_dir, path)
    
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Folder not found")
        
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail="Specified path is not a directory")
        
    try:
        shutil.rmtree(abs_path)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete folder: {e}")

@router.post("/rename")
async def rename_item(req: RenameRequest, request: Request):
    """Rename or move a file/folder in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    abs_old = secure_path(vault_dir, req.old_path)
    abs_new = secure_path(vault_dir, req.new_path)
    
    if not os.path.exists(abs_old):
        raise HTTPException(status_code=404, detail="Source not found")
        
    if os.path.exists(abs_new):
        raise HTTPException(status_code=400, detail="Destination already exists")

    if os.path.isdir(abs_old) and is_self_or_descendant_move(abs_old, abs_new):
        raise HTTPException(status_code=400, detail="Cannot move a folder into itself")
        
    try:
        os.makedirs(os.path.dirname(abs_new), exist_ok=True)
        shutil.move(abs_old, abs_new)
        record_action(
            vault_dir,
            action="rename_item",
            owner=current_owner(request),
            tool="obsidian_api",
            paths=[req.old_path, req.new_path],
            before={"path": req.old_path},
            after={"path": req.new_path},
        )
        return {"success": True, "path": req.new_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rename: {e}")

@router.get("/search")
async def search_vault(q: str, request: Request):
    """Perform full-text search inside all markdown notes in the vault."""
    vault_dir = get_unlocked_vault_path(request)
    results = []
    
    if not q.strip():
        return results
        
    query_re = re.compile(re.escape(q), re.IGNORECASE)
    
    try:
        for root, dirs, files in os.walk(vault_dir):
            dirs[:] = [d for d in dirs if d != ".obsidian"]
            for file in files:
                if not file.lower().endswith(".md"):
                    continue
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, vault_dir).replace("\\", "/")
                
                try:
                    matches = []
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        for line_num, line in enumerate(f, 1):
                            if query_re.search(line):
                                matches.append({
                                    "line": line_num,
                                    "text": line.strip()
                                })
                    if matches:
                        results.append({
                            "path": rel_path,
                            "matches": matches
                        })
                except Exception:
                    continue  # skip unreadable files
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

@router.post("/project-plan/preview")
async def project_plan_preview(req: ProjectPlanRequest, request: Request):
    """Build a non-destructive AI project planning preview."""
    vault_dir = get_unlocked_vault_path(request)
    try:
        plan = build_project_plan(vault_dir, req)
        return plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
    except ProjectPlanValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/project-plan/apply")
async def project_plan_apply(req: ProjectPlanApplyRequest, request: Request):
    """Apply a confirmed project plan by writing files and relationships."""
    vault_dir = get_unlocked_vault_path(request)
    try:
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
                owner=current_owner(request),
                tool="obsidian_project_plan_apply",
                paths=[path],
                after={"content": _read_text_if_exists(abs_path)},
            )
        for relationship in result["relationships"]:
            record_action(
                vault_dir,
                action="relationship_add",
                owner=current_owner(request),
                tool="obsidian_project_plan_apply",
                paths=[relationship["source"], relationship["target"]],
                after={"relationship": relationship},
            )
        return result
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
