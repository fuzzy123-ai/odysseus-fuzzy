import os
import json
import re
import sys
import base64
from typing import Optional

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

try:
    from obsidian.backend.routes import _undo_entry, router
    from obsidian.backend.project_planning import (
        GameDevConceptDraftRequest,
        ProjectDescriptionImproveRequest,
        ProjectPlan,
        ProjectPlanApplyRequest,
        ProjectPlanRequest,
        ProjectPlanValidationError,
        apply_project_plan,
        build_gamedev_concept_draft_with_ai,
        build_project_plan,
        generate_project_plan_content,
        improve_project_description_with_ai,
        template_options,
        validate_gamedev_concept_gate,
        validate_project_plan,
    )
    from obsidian.backend.memory_review import (
        MemoryReviewPlan,
        MemoryReviewRequest,
        apply_memory_review_plan,
        build_memory_review_plan,
        validate_memory_review_plan,
    )
    from obsidian.backend.vault_history import latest_reversible, list_history, mark_undone, record_action
    from obsidian.backend.vault_security import (
        export_vault,
        import_vault,
        lock_vault,
        protection_status,
        remove_password,
        require_unlocked,
        set_password,
        unlock_vault,
    )
    from obsidian.backend.vault_model import (
        add_manual_relationship,
        build_vault_index,
        graph_payload,
        load_manual_relationships,
        remove_manual_relationship,
    )
except ModuleNotFoundError:
    from backend.routes import _undo_entry, router
    from backend.project_planning import (
        GameDevConceptDraftRequest,
        ProjectDescriptionImproveRequest,
        ProjectPlan,
        ProjectPlanApplyRequest,
        ProjectPlanRequest,
        ProjectPlanValidationError,
        apply_project_plan,
        build_gamedev_concept_draft_with_ai,
        build_project_plan,
        generate_project_plan_content,
        improve_project_description_with_ai,
        template_options,
        validate_gamedev_concept_gate,
        validate_project_plan,
    )
    from backend.memory_review import (
        MemoryReviewPlan,
        MemoryReviewRequest,
        apply_memory_review_plan,
        build_memory_review_plan,
        validate_memory_review_plan,
    )
    from backend.vault_history import latest_reversible, list_history, mark_undone, record_action
    from backend.vault_security import (
        export_vault,
        import_vault,
        lock_vault,
        protection_status,
        remove_password,
        require_unlocked,
        set_password,
        unlock_vault,
    )
    from backend.vault_model import (
        add_manual_relationship,
        build_vault_index,
        graph_payload,
        load_manual_relationships,
        remove_manual_relationship,
    )

# Metadata manifest required by plugin loader
PLUGIN = {
    "name": "obsidian",
    "version": "0.9.0",
    "description": "Obsidian vault integration for direct editing and AI tool search/updates.",
    "category": "productivity",
    "permissions": ["filesystem"],
    "ui": {
        "open": "/api/plugins/obsidian/app",
        "label": "Open Vault",
        "script": "/api/plugins/obsidian/web/main.js",
    }
}

# --- Vault Path Helpers for Agent Tools ---
def get_vault_path_by_owner(owner: Optional[str]) -> str:
    """Resolve vault path by owner username."""
    from src.constants import DATA_DIR
    folder_name = owner if owner else "default"
    configured_vault = os.getenv("OBSIDIAN_VAULT_DIR", "").strip()
    if configured_vault:
        vault_template = configured_vault.format(owner=folder_name)
        vault_dir = os.path.abspath(os.path.expanduser(vault_template))
    else:
        vault_dir = os.path.abspath(os.path.join(DATA_DIR, "obsidian_vaults", folder_name))
        os.makedirs(vault_dir, exist_ok=True)
    return vault_dir

def secure_path(vault_dir: str, relative_path: str) -> str:
    """Ensure relative path is securely located within vault_dir."""
    cleaned_rel = relative_path.replace("\\", "/").strip("/")
    abs_vault = os.path.abspath(vault_dir)
    abs_target = os.path.abspath(os.path.join(abs_vault, cleaned_rel))
    if os.path.commonpath([abs_vault, abs_target]) != abs_vault:
        raise ValueError("Path traversal attempt detected")
    return abs_target

def is_self_or_descendant_move(abs_old: str, abs_new: str) -> bool:
    old_path = os.path.abspath(abs_old)
    new_path = os.path.abspath(abs_new)
    return new_path == old_path or os.path.commonpath([old_path, new_path]) == old_path

def get_unlocked_vault_path_by_owner(owner: Optional[str]) -> str:
    vault_dir = get_vault_path_by_owner(owner)
    require_unlocked(vault_dir)
    return vault_dir

def _read_text_if_exists(path: str) -> Optional[str]:
    if not os.path.exists(path) or os.path.isdir(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def project_planning_llm_call(owner: Optional[str] = None):
    from src.endpoint_resolver import resolve_endpoint
    from src.llm_core import llm_call_async

    url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        url, model, headers = resolve_endpoint("default", owner=owner)
    if not url or not model:
        raise RuntimeError("No LLM endpoint configured")

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

# --- Tool Handlers ---

def _parse_params(content: str, fallback_key: str) -> dict:
    raw = (content or "").strip()
    if raw.startswith("{"):
        return json.loads(raw)
    return {fallback_key: raw}

def _is_confirmed(params: dict) -> bool:
    return params.get("confirm") is True or params.get("confirmed") is True

def _confirmation_required(action: str) -> dict:
    return {
        "error": f"Confirmation required before {action}. Re-run with confirm set to true after the user confirms.",
        "exit_code": 1,
    }

def _note_tree(dir_path: str, base_path: Optional[str] = None) -> list[dict]:
    if base_path is None:
        base_path = dir_path
    tree = []
    for entry in os.scandir(dir_path):
        if entry.name == ".obsidian":
            continue
        rel_path = os.path.relpath(entry.path, base_path).replace("\\", "/")
        if entry.is_dir():
            tree.append({
                "name": entry.name,
                "path": rel_path,
                "is_dir": True,
                "children": _note_tree(entry.path, base_path),
            })
        else:
            tree.append({
                "name": entry.name,
                "path": rel_path,
                "is_dir": False,
            })
    tree.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    return tree

async def handle_list_notes(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Lists all notes in the user's Obsidian vault."""
    try:
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        notes = []
        for root, dirs, files in os.walk(vault_dir):
            dirs[:] = [d for d in dirs if d != ".obsidian"]
            for file in files:
                if file.lower().endswith(".md"):
                    abs_path = os.path.join(root, file)
                    rel_path = os.path.relpath(abs_path, vault_dir).replace("\\", "/")
                    notes.append(rel_path)
        notes.sort()
        if not notes:
            return {"output": "No notes found in the Obsidian vault.", "exit_code": 0}
        return {"output": "\n".join(notes), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to list notes: {e}", "exit_code": 1}

async def handle_read_note(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Reads the content of a specific note from the vault."""
    try:
        params = _parse_params(content, "path")
        
        path = params.get("path", "").strip()
        if not path:
            return {"error": "Path parameter is required.", "exit_code": 1}
            
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        abs_path = secure_path(vault_dir, path)
        
        if not os.path.exists(abs_path):
            return {"error": f"Note not found: {path}", "exit_code": 1}
        if os.path.isdir(abs_path):
            return {"error": f"Path is a directory: {path}", "exit_code": 1}
            
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            note_content = f.read()
        return {"output": note_content, "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to read note: {e}", "exit_code": 1}

async def handle_write_note(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Creates a new note or updates an existing one in the vault."""
    try:
        if content.strip().startswith("{"):
            params = json.loads(content)
        else:
            lines = content.strip().split("\n", 1)
            params = {
                "path": lines[0].strip(),
                "content": lines[1] if len(lines) > 1 else ""
            }
        
        path = params.get("path", "").strip()
        note_content = params.get("content", "")
        
        if not path:
            return {"error": "Path parameter is required.", "exit_code": 1}
            
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        abs_path = secure_path(vault_dir, path)

        exists = os.path.exists(abs_path)
        if exists and not _is_confirmed(params):
            return _confirmation_required(f"overwriting {path}")
        before_content = _read_text_if_exists(abs_path)
        
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(note_content)
        record_action(
            vault_dir,
            action="update_file" if exists else "create_file",
            owner=owner,
            tool="obsidian_write_note",
            paths=[path],
            before={"content": before_content} if exists else {},
            after={"content": note_content},
        )
        return {"output": f"Successfully wrote note to {path}.", "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to write note: {e}", "exit_code": 1}

async def handle_search_notes(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Performs full-text search inside markdown notes."""
    try:
        params = _parse_params(content, "query")
            
        query = params.get("query", "").strip()
        if not query:
            return {"error": "Query parameter is required.", "exit_code": 1}
            
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        query_re = re.compile(re.escape(query), re.IGNORECASE)
        results = []
        
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
                                matches.append(f"Line {line_num}: {line.strip()}")
                    if matches:
                        results.append(f"--- {rel_path} ---\n" + "\n".join(matches))
                except Exception:
                    continue
                    
        if not results:
            return {"output": f"No matches found for query: {query}", "exit_code": 0}
        return {"output": "\n\n".join(results), "exit_code": 0}
    except Exception as e:
        return {"error": f"Search failed: {e}", "exit_code": 1}

async def handle_tree(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Returns the vault tree with folders and files."""
    try:
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        return {"output": json.dumps(_note_tree(vault_dir), ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to list vault tree: {e}", "exit_code": 1}

async def handle_list_tags(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Returns explicit and implicit vault tags."""
    try:
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        tags = build_vault_index(vault_dir)["tags"]
        return {"output": json.dumps(tags, ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to list vault tags: {e}", "exit_code": 1}

async def handle_graph(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Returns the vault graph with relationship reasons."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        result = graph_payload(
            vault_dir,
            focus=params.get("focus"),
            tag=params.get("tag"),
        )
        return {"output": json.dumps(result, ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to build vault graph: {e}", "exit_code": 1}

async def handle_create_folder(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Creates a folder inside the vault."""
    try:
        params = _parse_params(content, "path")
        path = params.get("path", "").strip()
        if not path:
            return {"error": "Path parameter is required.", "exit_code": 1}
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        abs_path = secure_path(vault_dir, path)
        if os.path.exists(abs_path):
            return {"error": f"Path already exists: {path}", "exit_code": 1}
        os.makedirs(abs_path, exist_ok=False)
        record_action(
            vault_dir,
            action="create_folder",
            owner=owner,
            tool="obsidian_create_folder",
            paths=[path],
            reversible=False,
        )
        return {"output": f"Successfully created folder {path}.", "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to create folder: {e}", "exit_code": 1}

async def handle_rename_item(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Renames or moves a vault file or folder."""
    try:
        params = json.loads((content or "").strip())
        old_path = params.get("old_path", "").strip()
        new_path = params.get("new_path", "").strip()
        if not old_path or not new_path:
            return {"error": "old_path and new_path parameters are required.", "exit_code": 1}
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        abs_old = secure_path(vault_dir, old_path)
        abs_new = secure_path(vault_dir, new_path)
        if not os.path.exists(abs_old):
            return {"error": f"Source not found: {old_path}", "exit_code": 1}
        if os.path.exists(abs_new):
            return {"error": f"Destination already exists: {new_path}", "exit_code": 1}
        if os.path.isdir(abs_old) and is_self_or_descendant_move(abs_old, abs_new):
            return {"error": "Cannot move a folder into itself.", "exit_code": 1}
        os.makedirs(os.path.dirname(abs_new), exist_ok=True)
        os.replace(abs_old, abs_new)
        record_action(
            vault_dir,
            action="rename_item",
            owner=owner,
            tool="obsidian_rename_item",
            paths=[old_path, new_path],
            before={"path": old_path},
            after={"path": new_path},
        )
        return {"output": f"Successfully renamed {old_path} to {new_path}.", "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to rename item: {e}", "exit_code": 1}

async def handle_delete_note(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Deletes a single file inside the vault."""
    try:
        params = _parse_params(content, "path")
        path = params.get("path", "").strip()
        if not path:
            return {"error": "Path parameter is required.", "exit_code": 1}
        if not _is_confirmed(params):
            return _confirmation_required(f"deleting {path}")
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        abs_path = secure_path(vault_dir, path)
        if not os.path.exists(abs_path):
            return {"error": f"File not found: {path}", "exit_code": 1}
        if os.path.isdir(abs_path):
            return {"error": f"Path is a folder, not a file: {path}", "exit_code": 1}
        before_content = _read_text_if_exists(abs_path)
        os.remove(abs_path)
        record_action(
            vault_dir,
            action="delete_file",
            owner=owner,
            tool="obsidian_delete_note",
            paths=[path],
            before={"content": before_content},
            reversible=False,
        )
        return {"output": f"Successfully deleted note {path}.", "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to delete note: {e}", "exit_code": 1}

async def handle_delete_folder(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Deletes an empty folder inside the vault."""
    try:
        params = _parse_params(content, "path")
        path = params.get("path", "").strip()
        if not path:
            return {"error": "Path parameter is required.", "exit_code": 1}
        if not _is_confirmed(params):
            return _confirmation_required(f"deleting folder {path}")
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        abs_path = secure_path(vault_dir, path)
        if not os.path.exists(abs_path):
            return {"error": f"Folder not found: {path}", "exit_code": 1}
        if not os.path.isdir(abs_path):
            return {"error": f"Path is not a folder: {path}", "exit_code": 1}
        os.rmdir(abs_path)
        record_action(
            vault_dir,
            action="delete_folder",
            owner=owner,
            tool="obsidian_delete_folder",
            paths=[path],
            reversible=False,
        )
        return {"output": f"Successfully deleted empty folder {path}.", "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to delete folder: {e}", "exit_code": 1}

async def handle_vault_status(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Returns vault protection status without exposing secrets."""
    try:
        vault_dir = get_vault_path_by_owner(owner)
        return {"output": json.dumps(protection_status(vault_dir), ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to read vault status: {e}", "exit_code": 1}

async def handle_vault_set_password(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Enables or replaces vault password protection."""
    try:
        params = _parse_params(content, "password")
        if not _is_confirmed(params):
            return _confirmation_required("changing Obsidian vault password protection")
        vault_dir = get_vault_path_by_owner(owner)
        status = set_password(vault_dir, params.get("password", ""))
        return {"output": json.dumps(status, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to set vault password: {e}", "exit_code": 1}

async def handle_vault_lock(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Locks a password-protected vault."""
    try:
        vault_dir = get_vault_path_by_owner(owner)
        status = lock_vault(vault_dir)
        return {"output": json.dumps(status, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to lock vault: {e}", "exit_code": 1}

async def handle_vault_unlock(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Unlocks a password-protected vault."""
    try:
        params = _parse_params(content, "password")
        vault_dir = get_vault_path_by_owner(owner)
        status = unlock_vault(vault_dir, params.get("password", ""))
        return {"output": json.dumps(status, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to unlock vault: {e}", "exit_code": 1}

async def handle_vault_remove_password(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Removes vault password protection after verification."""
    try:
        params = _parse_params(content, "password")
        if not _is_confirmed(params):
            return _confirmation_required("removing Obsidian vault password protection")
        vault_dir = get_vault_path_by_owner(owner)
        status = remove_password(vault_dir, params.get("password", ""))
        return {"output": json.dumps(status, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to remove vault password: {e}", "exit_code": 1}

async def handle_vault_export(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Exports a vault archive as base64 ZIP data."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        if params.get("password") and not _is_confirmed(params):
            return _confirmation_required("exporting an encrypted Obsidian vault archive")
        vault_dir = get_vault_path_by_owner(owner)
        archive = export_vault(
            vault_dir,
            password=params.get("password"),
            root=params.get("root", ""),
        )
        result = {
            "filename": archive.filename,
            "encrypted": archive.encrypted,
            "file_count": archive.file_count,
            "archive_base64": base64.b64encode(archive.data).decode("ascii"),
        }
        return {"output": json.dumps(result, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to export vault: {e}", "exit_code": 1}

async def handle_vault_import(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Imports plain or encrypted base64 ZIP vault data."""
    try:
        params = json.loads((content or "").strip())
        if not _is_confirmed(params):
            return _confirmation_required("importing an Obsidian vault archive")
        archive_data = base64.b64decode(params.get("archive_base64", ""), validate=True)
        vault_dir = get_vault_path_by_owner(owner)
        result = import_vault(vault_dir, archive_data, password=params.get("password"))
        return {"output": json.dumps({"success": True, **result}, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to import vault: {e}", "exit_code": 1}

async def handle_list_relationships(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Lists manually curated graph relationships."""
    try:
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        relationships = load_manual_relationships(vault_dir)
        return {"output": json.dumps(relationships, ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to list relationships: {e}", "exit_code": 1}

async def handle_add_relationship(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Adds a typed manual relationship between two existing notes."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        relationship = add_manual_relationship(vault_dir, params)
        record_action(
            vault_dir,
            action="relationship_add",
            owner=owner,
            tool="obsidian_add_relationship",
            paths=[relationship["source"], relationship["target"]],
            after={"relationship": relationship},
        )
        return {"output": json.dumps(relationship, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to add relationship: {e}", "exit_code": 1}

async def handle_delete_relationship(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Deletes a typed manual relationship."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        removed = remove_manual_relationship(vault_dir, params)
        if removed is None:
            return {"error": "Relationship not found.", "exit_code": 1}
        record_action(
            vault_dir,
            action="relationship_delete",
            owner=owner,
            tool="obsidian_delete_relationship",
            paths=[removed["source"], removed["target"]],
            before={"relationship": removed},
        )
        return {"output": json.dumps(removed, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to delete relationship: {e}", "exit_code": 1}

async def handle_history(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Returns recent vault action history."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        return {
            "output": json.dumps(list_history(vault_dir, limit=int(params.get("limit", 50))), ensure_ascii=False, indent=2),
            "exit_code": 0,
        }
    except Exception as e:
        return {"error": f"Failed to read history: {e}", "exit_code": 1}

async def handle_undo(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Undoes the latest safe reversible vault action."""
    try:
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        entry = latest_reversible(vault_dir, owner=owner)
        if not entry:
            return {"error": "No reversible Obsidian action to undo.", "exit_code": 1}
        _undo_entry(vault_dir, entry)
        mark_undone(vault_dir, entry["id"])
        return {"output": json.dumps({"undone": entry}, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to undo latest action: {e}", "exit_code": 1}

async def handle_project_plan_templates(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Lists supported AI project planning templates and schema options."""
    try:
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        _ = vault_dir
        return {"output": json.dumps(template_options(), ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to read project plan templates: {e}", "exit_code": 1}

async def handle_project_plan_improve_description(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Improves a project planning prompt without changing the vault."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        _ = vault_dir
        description = await improve_project_description_with_ai(
            ProjectDescriptionImproveRequest(**params),
            llm_call=project_planning_llm_call(owner),
        )
        return {"output": json.dumps({"description": description}, ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to improve project description: {e}", "exit_code": 1}

async def handle_project_plan_gamedev_draft(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Creates an editable GameDev concept draft without changing the vault."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        _ = vault_dir
        draft = await build_gamedev_concept_draft_with_ai(
            GameDevConceptDraftRequest(**params),
            llm_call=project_planning_llm_call(owner),
        )
        return {"output": json.dumps(draft, ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to create GameDev concept draft: {e}", "exit_code": 1}

async def handle_project_plan_preview(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Creates a non-destructive project plan preview for a vault folder."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        request = ProjectPlanRequest(**params)
        validate_gamedev_concept_gate(request)
        plan = build_project_plan(vault_dir, request)
        if request.generate_content:
            plan = await generate_project_plan_content(
                plan,
                llm_call=project_planning_llm_call(owner),
            )
            plan = validate_project_plan(vault_dir, plan, collect_conflicts=True)
        payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        return {"output": json.dumps(payload, ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to preview project plan: {e}", "exit_code": 1}

async def handle_project_plan_apply(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Applies a confirmed project plan by creating files and graph relationships."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        if not _is_confirmed(params):
            return _confirmation_required("creating an Obsidian project structure")
        raw_plan = params.get("plan")
        if not isinstance(raw_plan, dict):
            return {"error": "plan parameter is required.", "exit_code": 1}
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        plan = validate_project_plan(vault_dir, ProjectPlan(**raw_plan), collect_conflicts=True)
        if plan.conflicts:
            return {
                "error": "Project plan has file conflicts and cannot be applied without a future merge flow.",
                "output": json.dumps({"conflicts": plan.conflicts}, ensure_ascii=False),
                "exit_code": 1,
            }
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
        return {"output": json.dumps(result, ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to apply project plan: {e}", "exit_code": 1}

async def handle_memory_review_preview(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Creates a non-destructive memory review Save-to-Obsidian preview."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        plan = build_memory_review_plan(vault_dir, MemoryReviewRequest(**params))
        payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        return {"output": json.dumps(payload, ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to preview memory review: {e}", "exit_code": 1}

async def handle_memory_review_apply(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Applies a confirmed memory review by creating or updating vault notes."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
        raw_plan = params.get("plan")
        if not isinstance(raw_plan, dict):
            return {"error": "plan parameter is required.", "exit_code": 1}
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        plan = validate_memory_review_plan(vault_dir, MemoryReviewPlan(**raw_plan), collect_conflicts=True)
        if plan.conflicts:
            return {
                "error": "Memory review plan has file conflicts and cannot be applied without a future merge flow.",
                "output": json.dumps({"conflicts": plan.conflicts}, ensure_ascii=False),
                "exit_code": 1,
            }
        if plan.action not in {"memory_only", "discard"} and not _is_confirmed(params):
            return _confirmation_required("changing Obsidian notes from memory review")
        result = apply_memory_review_plan(vault_dir, plan)
        for path in result.get("created_files", []):
            abs_path = secure_path(vault_dir, path)
            record_action(
                vault_dir,
                action="create_file",
                owner=owner,
                tool="obsidian_memory_review_apply",
                paths=[path],
                after={"content": _read_text_if_exists(abs_path)},
            )
        for detail in result.get("updated_file_details", []):
            record_action(
                vault_dir,
                action="update_file",
                owner=owner,
                tool="obsidian_memory_review_apply",
                paths=[detail["path"]],
                before={"content": detail["before"]},
                after={"content": detail["after"]},
            )
        for relationship in result.get("relationships", []):
            record_action(
                vault_dir,
                action="relationship_add",
                owner=owner,
                tool="obsidian_memory_review_apply",
                paths=[relationship["source"], relationship["target"]],
                after={"relationship": relationship},
            )
        result.pop("updated_file_details", None)
        return {"output": json.dumps(result, ensure_ascii=False, indent=2), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to apply memory review: {e}", "exit_code": 1}

def _tool_spec(name: str, description: str, properties: dict, required: list[str], handler):
    return {
        "name": name,
        "tool_tag": name,
        "schema": {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        },
        "handler": handler,
    }

def _register_tool(ctx, spec: dict) -> None:
    register = getattr(ctx, "register_tool", None)
    if not callable(register):
        ctx.logger.warning("Tool registration unavailable for %s", spec["name"])
        return
    try:
        register(spec)
    except TypeError:
        register(
            tool_tag=spec["tool_tag"],
            tool_schema=spec["schema"],
            tool_handler=spec["handler"],
        )


def setup(ctx):
    """Setup hook to register endpoints and agent tools."""
    
    # 1. Register routes in FastAPI app
    ctx.add_router(router)

    tools = [
        _tool_spec("obsidian_list_notes", "List all markdown notes in the user's Obsidian vault.", {}, [], handle_list_notes),
        _tool_spec("obsidian_tree", "List the full Obsidian vault tree with folders and files.", {}, [], handle_tree),
        _tool_spec("obsidian_read_note", "Read the contents of a markdown note from the user's Obsidian vault.", {
            "path": {"type": "string", "description": "The relative path of the note to read."},
        }, ["path"], handle_read_note),
        _tool_spec("obsidian_write_note", "Create a new note or update an existing note in the user's Obsidian vault.", {
            "path": {"type": "string", "description": "The relative path of the note."},
            "content": {"type": "string", "description": "The markdown content to write."},
            "confirm": {"type": "boolean", "description": "Required when overwriting an existing note."},
        }, ["path", "content"], handle_write_note),
        _tool_spec("obsidian_search_notes", "Search for notes containing a text query in the user's Obsidian vault.", {
            "query": {"type": "string", "description": "Search keyword or text query."},
        }, ["query"], handle_search_notes),
        _tool_spec("obsidian_list_tags", "List explicit hashtags and implicit filename tags in the user's Obsidian vault.", {}, [], handle_list_tags),
        _tool_spec("obsidian_graph", "Return the Obsidian vault graph with markdown links, filename mentions, shared tags, and edge reasons.", {
            "focus": {"type": "string", "description": "Optional note path to return only the local graph around that note."},
            "tag": {"type": "string", "description": "Optional tag filter."},
        }, [], handle_graph),
        _tool_spec("obsidian_list_relationships", "List manually curated Obsidian graph relationships.", {}, [], handle_list_relationships),
        _tool_spec("obsidian_add_relationship", "Add a typed manual graph relationship between two existing Obsidian notes.", {
            "source": {"type": "string", "description": "Source note path."},
            "target": {"type": "string", "description": "Target note path."},
            "type": {"type": "string", "description": "Relationship type: manual, relates_to, depends_on, blocks, or supports."},
            "reason": {"type": "string", "description": "Short reason shown in the graph."},
        }, ["source", "target"], handle_add_relationship),
        _tool_spec("obsidian_delete_relationship", "Delete a typed manual graph relationship between two Obsidian notes.", {
            "source": {"type": "string", "description": "Source note path."},
            "target": {"type": "string", "description": "Target note path."},
            "type": {"type": "string", "description": "Relationship type to delete."},
        }, ["source", "target"], handle_delete_relationship),
        _tool_spec("obsidian_history", "List recent Obsidian vault actions with owner, tool, paths, and undo status.", {
            "limit": {"type": "integer", "description": "Maximum number of recent actions to return."},
        }, [], handle_history),
        _tool_spec("obsidian_undo", "Undo the latest safe reversible Obsidian vault action for the current user.", {}, [], handle_undo),
        _tool_spec("obsidian_project_plan_templates", "List supported Obsidian AI project planning templates and document schema options.", {}, [], handle_project_plan_templates),
        _tool_spec("obsidian_project_plan_improve_description", "Improve a project planning description before creating an Obsidian project plan preview.", {
            "title": {"type": "string", "description": "Project title."},
            "description": {"type": "string", "description": "Project goal, scope, constraints, or other planning context to improve."},
            "kind": {"type": "string", "description": "Project kind: software, research, writing, sec_ops, generic, teaching, or game_dev."},
        }, ["description"], handle_project_plan_improve_description),
        _tool_spec("obsidian_project_plan_gamedev_draft", "Create an editable GameDev concept draft before generating a full project plan.", {
            "title": {"type": "string", "description": "Game project title."},
            "description": {"type": "string", "description": "Game idea, genre, engine, 2D/3D, constraints, scope hints, and target platform."},
            "kind": {"type": "string", "description": "Usually game_dev or GameDev."},
        }, ["description"], handle_project_plan_gamedev_draft),
        _tool_spec("obsidian_project_plan_preview", "Preview a non-destructive AI project plan for a target Obsidian vault folder.", {
            "target_folder": {"type": "string", "description": "Relative vault folder where the project structure should be planned."},
            "title": {"type": "string", "description": "Project title."},
            "description": {"type": "string", "description": "Project goal, scope, constraints, or other planning context."},
            "kind": {"type": "string", "description": "Project kind: software, research, writing, sec_ops, generic, teaching, or game_dev. Legacy ops is accepted as an alias for sec_ops."},
            "generate_content": {"type": "boolean", "description": "When true, fill each planned file sequentially with AI-generated Markdown content before returning the preview."},
            "approved_concept": {"type": "string", "description": "Approved editable concept draft. Required for GameDev content generation."},
            "concept_approved": {"type": "boolean", "description": "Must be true for GameDev content generation."},
        }, ["target_folder", "title"], handle_project_plan_preview),
        _tool_spec("obsidian_project_plan_apply", "Create files and relationships from a confirmed Obsidian project plan preview.", {
            "plan": {"type": "object", "description": "Project plan returned by obsidian_project_plan_preview."},
            "confirm": {"type": "boolean", "description": "Must be true after the user confirms creating multiple project files."},
        }, ["plan"], handle_project_plan_apply),
        _tool_spec("obsidian_memory_review_preview", "Preview a memory review decision, including Save-to-Obsidian note content, reused tags, links, and graph relationships.", {
            "candidate": {"type": "object", "description": "Memory candidate with title, content, source, source_ref, and risk."},
            "action": {"type": "string", "description": "memory_only, save_to_obsidian, append_to_note, or discard."},
            "target_folder": {"type": "string", "description": "Relative vault folder for a new memory note."},
            "target_note": {"type": "string", "description": "Existing note path when appending."},
            "note_type": {"type": "string", "description": "Schema type such as memory, decision, idea, reference, resource, meeting, or project."},
            "status": {"type": "string", "description": "Review status tag, usually review, draft, active, or archived."},
            "project": {"type": "string", "description": "Optional project slug or name."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Requested tags. Existing vault tags are preferred when possible."},
            "link_paths": {"type": "array", "items": {"type": "string"}, "description": "Existing notes to link directly."},
        }, ["candidate"], handle_memory_review_preview),
        _tool_spec("obsidian_memory_review_apply", "Apply a confirmed memory review preview by creating or appending Obsidian notes and graph relationships.", {
            "plan": {"type": "object", "description": "Memory review plan returned by obsidian_memory_review_preview."},
            "confirm": {"type": "boolean", "description": "Must be true before changing Obsidian notes."},
        }, ["plan"], handle_memory_review_apply),
        _tool_spec("obsidian_create_folder", "Create a folder in the user's Obsidian vault.", {
            "path": {"type": "string", "description": "The relative folder path to create."},
        }, ["path"], handle_create_folder),
        _tool_spec("obsidian_rename_item", "Rename or move a note or folder inside the user's Obsidian vault.", {
            "old_path": {"type": "string", "description": "The current relative path."},
            "new_path": {"type": "string", "description": "The new relative path."},
        }, ["old_path", "new_path"], handle_rename_item),
        _tool_spec("obsidian_delete_note", "Delete a single file inside the user's Obsidian vault.", {
            "path": {"type": "string", "description": "The relative file path to delete."},
            "confirm": {"type": "boolean", "description": "Must be true after the user confirms deletion."},
        }, ["path"], handle_delete_note),
        _tool_spec("obsidian_delete_folder", "Delete an empty folder inside the user's Obsidian vault.", {
            "path": {"type": "string", "description": "The relative empty folder path to delete."},
            "confirm": {"type": "boolean", "description": "Must be true after the user confirms deletion."},
        }, ["path"], handle_delete_folder),
        _tool_spec("obsidian_vault_status", "Return Obsidian vault password-protection and lock status.", {}, [], handle_vault_status),
        _tool_spec("obsidian_vault_set_password", "Enable or replace password protection for the Obsidian vault.", {
            "password": {"type": "string", "description": "The vault password. Must not be logged or reused in URLs."},
            "confirm": {"type": "boolean", "description": "Must be true after the user confirms changing password protection."},
        }, ["password"], handle_vault_set_password),
        _tool_spec("obsidian_vault_lock", "Lock the password-protected Obsidian vault.", {}, [], handle_vault_lock),
        _tool_spec("obsidian_vault_unlock", "Unlock the Obsidian vault with its password.", {
            "password": {"type": "string", "description": "The vault password. Must not be logged or reused in URLs."},
        }, ["password"], handle_vault_unlock),
        _tool_spec("obsidian_vault_remove_password", "Remove Obsidian vault password protection after password verification.", {
            "password": {"type": "string", "description": "The current vault password."},
            "confirm": {"type": "boolean", "description": "Must be true after the user confirms removing password protection."},
        }, ["password"], handle_vault_remove_password),
        _tool_spec("obsidian_vault_export", "Export the Obsidian vault as base64 ZIP data, optionally encrypted with a password.", {
            "password": {"type": "string", "description": "Optional export password."},
            "root": {"type": "string", "description": "Optional relative file or folder root to export."},
            "confirm": {"type": "boolean", "description": "Required when exporting with a password."},
        }, [], handle_vault_export),
        _tool_spec("obsidian_vault_import", "Import base64 ZIP vault data, including password-encrypted Odysseus vault exports.", {
            "archive_base64": {"type": "string", "description": "Base64-encoded ZIP archive data."},
            "password": {"type": "string", "description": "Optional password for encrypted archives."},
            "confirm": {"type": "boolean", "description": "Must be true after the user confirms importing into the vault."},
        }, ["archive_base64"], handle_vault_import),
    ]
    for spec in tools:
        _register_tool(ctx, spec)
