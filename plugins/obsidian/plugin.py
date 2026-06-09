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
    from obsidian.backend.routes import router
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
except ModuleNotFoundError:
    from backend.routes import router
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

# Metadata manifest required by plugin loader
PLUGIN = {
    "name": "obsidian",
    "version": "1.0.0",
    "description": "Obsidian vault integration for direct editing and AI tool search/updates.",
    "category": "productivity",
    "permissions": ["filesystem"],
    "ui": {
        "open": "/api/plugins/obsidian/app",
        "label": "Open Vault"
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

def get_unlocked_vault_path_by_owner(owner: Optional[str]) -> str:
    vault_dir = get_vault_path_by_owner(owner)
    require_unlocked(vault_dir)
    return vault_dir

# --- Tool Handlers ---

def _parse_params(content: str, fallback_key: str) -> dict:
    raw = (content or "").strip()
    if raw.startswith("{"):
        return json.loads(raw)
    return {fallback_key: raw}

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
        
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(note_content)
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
        os.makedirs(os.path.dirname(abs_new), exist_ok=True)
        os.replace(abs_old, abs_new)
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
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        abs_path = secure_path(vault_dir, path)
        if not os.path.exists(abs_path):
            return {"error": f"File not found: {path}", "exit_code": 1}
        if os.path.isdir(abs_path):
            return {"error": f"Path is a folder, not a file: {path}", "exit_code": 1}
        os.remove(abs_path)
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
        vault_dir = get_unlocked_vault_path_by_owner(owner)
        abs_path = secure_path(vault_dir, path)
        if not os.path.exists(abs_path):
            return {"error": f"Folder not found: {path}", "exit_code": 1}
        if not os.path.isdir(abs_path):
            return {"error": f"Path is not a folder: {path}", "exit_code": 1}
        os.rmdir(abs_path)
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
        vault_dir = get_vault_path_by_owner(owner)
        status = remove_password(vault_dir, params.get("password", ""))
        return {"output": json.dumps(status, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to remove vault password: {e}", "exit_code": 1}

async def handle_vault_export(content: str, owner: Optional[str] = None, **kwargs) -> dict:
    """Exports a vault archive as base64 ZIP data."""
    try:
        params = json.loads((content or "{}").strip() or "{}")
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
        archive_data = base64.b64decode(params.get("archive_base64", ""), validate=True)
        vault_dir = get_vault_path_by_owner(owner)
        result = import_vault(vault_dir, archive_data, password=params.get("password"))
        return {"output": json.dumps({"success": True, **result}, ensure_ascii=False), "exit_code": 0}
    except Exception as e:
        return {"error": f"Failed to import vault: {e}", "exit_code": 1}

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
        }, ["path", "content"], handle_write_note),
        _tool_spec("obsidian_search_notes", "Search for notes containing a text query in the user's Obsidian vault.", {
            "query": {"type": "string", "description": "Search keyword or text query."},
        }, ["query"], handle_search_notes),
        _tool_spec("obsidian_create_folder", "Create a folder in the user's Obsidian vault.", {
            "path": {"type": "string", "description": "The relative folder path to create."},
        }, ["path"], handle_create_folder),
        _tool_spec("obsidian_rename_item", "Rename or move a note or folder inside the user's Obsidian vault.", {
            "old_path": {"type": "string", "description": "The current relative path."},
            "new_path": {"type": "string", "description": "The new relative path."},
        }, ["old_path", "new_path"], handle_rename_item),
        _tool_spec("obsidian_delete_note", "Delete a single file inside the user's Obsidian vault.", {
            "path": {"type": "string", "description": "The relative file path to delete."},
        }, ["path"], handle_delete_note),
        _tool_spec("obsidian_delete_folder", "Delete an empty folder inside the user's Obsidian vault.", {
            "path": {"type": "string", "description": "The relative empty folder path to delete."},
        }, ["path"], handle_delete_folder),
        _tool_spec("obsidian_vault_status", "Return Obsidian vault password-protection and lock status.", {}, [], handle_vault_status),
        _tool_spec("obsidian_vault_set_password", "Enable or replace password protection for the Obsidian vault.", {
            "password": {"type": "string", "description": "The vault password. Must not be logged or reused in URLs."},
        }, ["password"], handle_vault_set_password),
        _tool_spec("obsidian_vault_lock", "Lock the password-protected Obsidian vault.", {}, [], handle_vault_lock),
        _tool_spec("obsidian_vault_unlock", "Unlock the Obsidian vault with its password.", {
            "password": {"type": "string", "description": "The vault password. Must not be logged or reused in URLs."},
        }, ["password"], handle_vault_unlock),
        _tool_spec("obsidian_vault_remove_password", "Remove Obsidian vault password protection after password verification.", {
            "password": {"type": "string", "description": "The current vault password."},
        }, ["password"], handle_vault_remove_password),
        _tool_spec("obsidian_vault_export", "Export the Obsidian vault as base64 ZIP data, optionally encrypted with a password.", {
            "password": {"type": "string", "description": "Optional export password."},
            "root": {"type": "string", "description": "Optional relative file or folder root to export."},
        }, [], handle_vault_export),
        _tool_spec("obsidian_vault_import", "Import base64 ZIP vault data, including password-encrypted Odysseus vault exports.", {
            "archive_base64": {"type": "string", "description": "Base64-encoded ZIP archive data."},
            "password": {"type": "string", "description": "Optional password for encrypted archives."},
        }, ["archive_base64"], handle_vault_import),
    ]
    for spec in tools:
        _register_tool(ctx, spec)
