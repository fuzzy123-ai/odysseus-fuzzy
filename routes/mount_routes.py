"""Mount point management API for owner-scoped virtual file-tool paths."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from core.mount_manager import (
    delete_mount,
    list_all_mounts,
    list_mounts_for_owner,
    upsert_mount,
    validate_mount_definition,
)
from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user


def setup_mount_routes():
    router = APIRouter(prefix="/api/mounts", tags=["mounts"])

    def _require_admin(owner: str | None) -> None:
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail="Mount management is admin-only")

    @router.get("")
    def list_mounts(request: Request):
        owner = get_current_user(request)
        is_admin = owner_is_admin_or_single_user(owner)
        mounts = list_all_mounts() if is_admin else list_mounts_for_owner(owner)
        return {
            "mounts": [
                mount.public_dict(include_host_path=is_admin)
                for mount in mounts
            ]
        }

    @router.post("")
    def create_or_update_mount(request: Request, payload: dict):
        owner = get_current_user(request)
        _require_admin(owner)
        try:
            mount = upsert_mount(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"mount": mount.public_dict(include_host_path=True)}

    @router.put("/{owner_name}/{mount_name}")
    def update_mount(request: Request, owner_name: str, mount_name: str, payload: dict):
        owner = get_current_user(request)
        _require_admin(owner)
        merged = {**payload, "owner": owner_name, "name": mount_name}
        try:
            mount = upsert_mount(merged)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"mount": mount.public_dict(include_host_path=True)}

    @router.delete("/{owner_name}/{mount_name}")
    def remove_mount(request: Request, owner_name: str, mount_name: str):
        owner = get_current_user(request)
        _require_admin(owner)
        if not delete_mount(owner_name, mount_name):
            raise HTTPException(status_code=404, detail="Mount not found")
        return {"ok": True}

    @router.post("/validate")
    def validate_mount(request: Request, payload: dict):
        owner = get_current_user(request)
        _require_admin(owner)
        try:
            mount = validate_mount_definition(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "mount": mount.public_dict(include_host_path=True)}

    @router.post("/{owner_name}/{mount_name}/validate")
    def validate_existing_mount(request: Request, owner_name: str, mount_name: str):
        owner = get_current_user(request)
        _require_admin(owner)
        raw_mount = None
        for mount in list_all_mounts():
            if mount.owner == owner_name and mount.name == mount_name:
                raw_mount = mount.public_dict(include_host_path=True)
                break
        if raw_mount is None:
            raise HTTPException(status_code=404, detail="Mount not found")
        try:
            mount = validate_mount_definition(raw_mount)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "mount": mount.public_dict(include_host_path=True)}

    @router.post("/reload")
    def reload_mounts(request: Request):
        owner = get_current_user(request)
        _require_admin(owner)
        return {"ok": True, "count": len(list_all_mounts())}

    return router
