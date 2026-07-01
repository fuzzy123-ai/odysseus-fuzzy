"""Shared Memory/Inbox triage contract for local and API model outputs.

The contract keeps model-facing labels small and stable so weak local models
cannot drift into free-form taxonomy values that downstream policy gates do not
understand.
"""

from __future__ import annotations

import re
from typing import Any


CLASSIFICATION_VALUES = ("public", "private", "sensitive", "secret")
DOCUMENT_TYPE_VALUES = ("project", "invoice", "worksheet", "transient", "reference")
MEMORY_WRITE_INTENT_STATUS_VALUES = ("ready", "review", "blocked", "skipped")

_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def memory_triage_enum_instruction() -> str:
    return (
        "Do not invent labels. Use exactly these enum values: "
        "classification is one of public, private, sensitive, secret; "
        "document_type is one of project, invoice, worksheet, transient, reference; "
        "memory_write_intent_status is one of ready, review, blocked, skipped. "
        "local_only_required means policy-required local-only, not merely that "
        "processing happens on a local model. api_escalation_allowed is false "
        "only when DSGVO/sensitive/secret policy requires local-only processing."
    )


def normalize_memory_classification(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    token = _safe_token(text, "")
    if token in CLASSIFICATION_VALUES:
        return token
    if any(hint in text for hint in ("secret", "credential", "password", "token", "api key", "apikey")):
        return "secret"
    if any(hint in text for hint in ("sensitive", "financial", "billing", "invoice", "personal", "private data")):
        return "sensitive"
    if any(hint in text for hint in ("ephemeral", "transient", "smalltalk", "public")):
        return "public"
    if any(
        hint in text
        for hint in (
            "private",
            "operational",
            "contextual",
            "project",
            "directive",
            "metadata",
            "technical",
            "procedure",
            "configuration",
            "server",
        )
    ):
        return "private"
    return token or fallback


def normalize_memory_document_type(
    value: Any,
    *,
    fallback: str = "unknown",
    case_id: str = "",
    text: str = "",
) -> str:
    haystack = " ".join(str(part or "").strip().lower() for part in (value, case_id, text))
    token = _safe_token(value, "")
    if token in DOCUMENT_TYPE_VALUES:
        return token
    if any(hint in haystack for hint in ("invoice", "rechnung", "billing", "financial", "bill")):
        return "invoice"
    if any(hint in haystack for hint in ("worksheet", "arbeitsblatt", "exercise sheet", "uebungsblatt", "übungsblatt")):
        return "worksheet"
    if any(hint in haystack for hint in ("chat", "ephemeral", "transient", "smalltalk")):
        return "transient"
    if any(
        hint in haystack
        for hint in (
            "project",
            "projekt",
            "roadmap",
            "planung",
            "spec",
            "podman",
            "docker",
            "server operation",
            "operations",
            "directive",
            "metadata",
        )
    ):
        return "project"
    return token or fallback


def normalize_memory_write_intent_status(value: Any, *, fallback: str = "") -> str:
    text = str(value or "").strip().lower()
    token = _safe_token(text, "")
    if token in MEMORY_WRITE_INTENT_STATUS_VALUES:
        return token
    if any(hint in text for hint in ("pending", "review", "freigabe", "approve")):
        return "review"
    if any(hint in text for hint in ("none", "skip", "no memory", "no_memory", "transient")):
        return "skipped"
    if any(hint in text for hint in ("blocked", "forbidden", "no_go", "no-go")):
        return "blocked"
    if any(hint in text for hint in ("confirm", "summary", "abstract", "topic", "ready", "written")):
        return "ready"
    return token or fallback


def _safe_token(value: Any, fallback: str) -> str:
    token = str(value or fallback).strip().lower().replace("-", "_").replace("/", "_").replace(" ", "_")
    return token if _SAFE_TOKEN_RE.fullmatch(token) else fallback
