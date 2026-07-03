"""Read-only legacy chat feature contract routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.legacy_chat_contracts import build_legacy_chat_contracts


def setup_legacy_chat_contract_routes() -> APIRouter:
    router = APIRouter(tags=["legacy-chat"])

    @router.get("/api/legacy-chat/contracts")
    def legacy_chat_contracts(request: Request):
        require_admin(request)
        return build_legacy_chat_contracts()

    return router
