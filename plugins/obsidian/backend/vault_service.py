import os
import re
import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.constants import DATA_DIR

from .vault_history import record_action
from .vault_security import require_unlocked


TEXT_EXTENSIONS = (".md", ".txt", ".json", ".html", ".js", ".css")


@dataclass(frozen=True)
class SearchMatch:
    line: int
    text: str


@dataclass(frozen=True)
class SearchResult:
    path: str
    matches: List[SearchMatch]


def owner_folder(owner: Optional[str]) -> str:
    return owner if owner else "default"


def vault_path_for_owner(owner: Optional[str]) -> str:
    folder_name = owner_folder(owner)
    configured_vault = os.getenv("OBSIDIAN_VAULT_DIR", "").strip()
    if configured_vault:
        vault_template = configured_vault.format(owner=folder_name)
        return os.path.abspath(os.path.expanduser(vault_template))
    vault_dir = os.path.abspath(os.path.join(DATA_DIR, "obsidian_vaults", folder_name))
    os.makedirs(vault_dir, exist_ok=True)
    return vault_dir


def unlocked_vault_path_for_owner(owner: Optional[str]) -> str:
    vault_dir = vault_path_for_owner(owner)
    require_unlocked(vault_dir)
    return vault_dir


def secure_path(vault_dir: str, relative_path: str) -> str:
    cleaned_rel = str(relative_path or "").replace("\\", "/").strip("/")
    abs_vault = os.path.abspath(vault_dir)
    abs_target = os.path.abspath(os.path.join(abs_vault, cleaned_rel))
    if os.path.commonpath([abs_vault, abs_target]) != abs_vault:
        raise ValueError("Path traversal attempt detected")
    return abs_target


def read_text_if_exists(path: str) -> Optional[str]:
    if not os.path.exists(path) or os.path.isdir(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def is_text_path(path: str) -> bool:
    return path.lower().endswith(TEXT_EXTENSIONS)


def is_self_or_descendant_move(abs_old: str, abs_new: str) -> bool:
    old_path = os.path.abspath(abs_old)
    new_path = os.path.abspath(abs_new)
    return new_path == old_path or os.path.commonpath([old_path, new_path]) == old_path


def file_tree(vault_dir: str, dir_path: Optional[str] = None) -> List[Dict[str, Any]]:
    base_path = vault_dir
    current_dir = dir_path or vault_dir
    tree: List[Dict[str, Any]] = []
    for entry in os.scandir(current_dir):
        if entry.name == ".obsidian":
            continue
        rel_path = os.path.relpath(entry.path, base_path).replace("\\", "/")
        if entry.is_dir():
            tree.append({
                "name": entry.name,
                "path": rel_path,
                "is_dir": True,
                "children": file_tree(vault_dir, entry.path),
            })
        else:
            tree.append({
                "name": entry.name,
                "path": rel_path,
                "is_dir": False,
            })
    tree.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    return tree


def markdown_notes(vault_dir: str) -> List[str]:
    notes: List[str] = []
    for root, dirs, files in os.walk(vault_dir):
        dirs[:] = [directory for directory in dirs if directory != ".obsidian"]
        for filename in files:
            if filename.lower().endswith(".md"):
                abs_path = os.path.join(root, filename)
                notes.append(os.path.relpath(abs_path, vault_dir).replace("\\", "/"))
    return sorted(notes, key=str.lower)


def read_file(vault_dir: str, path: str) -> str:
    abs_path = secure_path(vault_dir, path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    if os.path.isdir(abs_path):
        raise IsADirectoryError(f"Path is a directory: {path}")
    with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def write_file(vault_dir: str, path: str, content: str, *, owner: Optional[str], tool: str) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    exists = os.path.exists(abs_path)
    if exists and os.path.isdir(abs_path):
        raise IsADirectoryError(f"Path is a directory: {path}")
    before_content = read_text_if_exists(abs_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    record_action(
        vault_dir,
        action="update_file" if exists else "create_file",
        owner=owner,
        tool=tool,
        paths=[path],
        before={"content": before_content} if exists else {},
        after={"content": content},
    )
    return {"success": True, "path": path, "created": not exists}


def create_file(vault_dir: str, path: str, content: str, *, owner: Optional[str], tool: str) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    if os.path.exists(abs_path):
        raise FileExistsError(f"File already exists: {path}")
    return write_file(vault_dir, path, content, owner=owner, tool=tool)


def update_file(vault_dir: str, path: str, content: str, *, owner: Optional[str], tool: str) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    return write_file(vault_dir, path, content, owner=owner, tool=tool)


def delete_file(vault_dir: str, path: str, *, owner: Optional[str], tool: str, reversible: bool = False) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    if os.path.isdir(abs_path):
        raise IsADirectoryError(f"Path is a folder, not a file: {path}")
    before_content = read_text_if_exists(abs_path)
    os.remove(abs_path)
    record_action(
        vault_dir,
        action="delete_file",
        owner=owner,
        tool=tool,
        paths=[path],
        before={"content": before_content},
        reversible=reversible,
    )
    return {"success": True, "path": path}


def create_folder(vault_dir: str, path: str, *, owner: Optional[str], tool: str) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    if os.path.exists(abs_path):
        raise FileExistsError(f"Path already exists: {path}")
    os.makedirs(abs_path, exist_ok=False)
    record_action(vault_dir, action="create_folder", owner=owner, tool=tool, paths=[path], reversible=False)
    return {"success": True, "path": path}


def delete_folder(vault_dir: str, path: str, *, owner: Optional[str], tool: str, recursive: bool = False) -> Dict[str, Any]:
    abs_path = secure_path(vault_dir, path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Folder not found: {path}")
    if not os.path.isdir(abs_path):
        raise NotADirectoryError(f"Path is not a folder: {path}")
    if recursive:
        shutil.rmtree(abs_path)
    else:
        os.rmdir(abs_path)
    record_action(vault_dir, action="delete_folder", owner=owner, tool=tool, paths=[path], reversible=False)
    return {"success": True, "path": path}


def rename_item(vault_dir: str, old_path: str, new_path: str, *, owner: Optional[str], tool: str) -> Dict[str, Any]:
    abs_old = secure_path(vault_dir, old_path)
    abs_new = secure_path(vault_dir, new_path)
    if not os.path.exists(abs_old):
        raise FileNotFoundError(f"Source not found: {old_path}")
    if os.path.exists(abs_new):
        raise FileExistsError(f"Destination already exists: {new_path}")
    if os.path.isdir(abs_old) and is_self_or_descendant_move(abs_old, abs_new):
        raise ValueError("Cannot move a folder into itself.")
    os.makedirs(os.path.dirname(abs_new), exist_ok=True)
    os.replace(abs_old, abs_new)
    record_action(
        vault_dir,
        action="rename_item",
        owner=owner,
        tool=tool,
        paths=[old_path, new_path],
        before={"path": old_path},
        after={"path": new_path},
    )
    return {"success": True, "old_path": old_path, "new_path": new_path}


def search_markdown(vault_dir: str, query: str) -> List[SearchResult]:
    query_re = re.compile(re.escape(query), re.IGNORECASE)
    results: List[SearchResult] = []
    for path in markdown_notes(vault_dir):
        abs_path = secure_path(vault_dir, path)
        matches: List[SearchMatch] = []
        with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
            for line_num, line in enumerate(handle, 1):
                if query_re.search(line):
                    matches.append(SearchMatch(line=line_num, text=line.strip()))
        if matches:
            results.append(SearchResult(path=path, matches=matches))
    return results
