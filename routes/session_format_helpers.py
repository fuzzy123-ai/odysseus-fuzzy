# routes/session_format_helpers.py
import re

from core.models import ChatMessage


def sanitize_export_filename(name: str) -> str:
    """Return a conservative filename safe for Content-Disposition."""
    name = name if isinstance(name, str) else ""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:128]


# Blind-compare helper sessions are created with this name prefix. Their real
# model must never surface in the session list / sidebar - otherwise a blind
# comparison can be de-anonymized before the user votes (issue #1285).
COMPARE_SESSION_PREFIX = "[CMP] "


def public_model(name: str, model: str) -> str:
    """Hide the real model for blind-compare helper sessions."""
    if (name or "").startswith(COMPARE_SESSION_PREFIX):
        return ""
    return model


def session_source_channel(name: str) -> str | None:
    normalized = (name or "").strip().lower()
    if normalized == "telegram bot" or normalized.startswith("telegram "):
        return "telegram"
    return None


def readiness_gate_status_message(gate: dict | None) -> str | None:
    if not isinstance(gate, dict) or gate.get("state") != "blocked":
        return None
    gaps = gate.get("gaps") if isinstance(gate.get("gaps"), list) else []
    shown_gaps = [str(gap).replace("_", " ") for gap in gaps[:3]]
    if len(gaps) > 3:
        shown_gaps.append(f"+{len(gaps) - 3} more")
    gap_text = ", ".join(shown_gaps)
    return f"Readiness gate blocked: {gap_text}" if gap_text else "Readiness gate blocked"


def memory_diagnostics_status_message(summary: dict | None) -> str | None:
    if not isinstance(summary, dict) or summary.get("memory_diagnostics_state") != "attention":
        return None
    active_flags = summary.get("memory_diagnostics_active_flags")
    memory_diagnostics = summary.get("memory_diagnostics") if isinstance(summary.get("memory_diagnostics"), dict) else {}
    retrieval_policy = memory_diagnostics.get("retrieval_policy") if isinstance(memory_diagnostics.get("retrieval_policy"), dict) else {}
    raptor_write_gate = memory_diagnostics.get("raptor_write_gate") if isinstance(memory_diagnostics.get("raptor_write_gate"), dict) else {}
    retrieval_details = []
    if retrieval_policy:
        if retrieval_policy.get("filtering_state") is not None:
            retrieval_details.append(f"retrieval {str(retrieval_policy.get('filtering_state')).replace('_', ' ')}")
        if "default_retrieval_is_filtered" in retrieval_policy:
            filtered = "yes" if retrieval_policy.get("default_retrieval_is_filtered") else "no"
            retrieval_details.append(f"default filtered {filtered}")
    if raptor_write_gate and raptor_write_gate.get("state") is not None:
        retrieval_details.append(f"raptor write gate {str(raptor_write_gate.get('state')).replace('_', ' ')}")
    if not isinstance(active_flags, dict) or not active_flags:
        suffix = f" ({', '.join(retrieval_details)})" if retrieval_details else ""
        return f"Memory diagnostics need attention{suffix}"
    entries: list[str] = []
    for family, flags in active_flags.items():
        if not isinstance(flags, list):
            continue
        shown_flags = [str(flag).replace("_", " ") for flag in flags[:3]]
        if len(flags) > 3:
            shown_flags.append(f"+{len(flags) - 3} more")
        flag_text = ", ".join(shown_flags)
        if flag_text:
            family_label = str(family).removesuffix("_flags").replace("_", " ")
            entries.append(f"{family_label}: {flag_text}")
    if retrieval_details:
        entries.append(", ".join(retrieval_details))
    return f"Memory diagnostics need attention: {'; '.join(entries)}" if entries else "Memory diagnostics need attention"


def memory_warnings_status_message(summary: dict | None) -> str | None:
    if not isinstance(summary, dict) or summary.get("memory_warnings_state") != "attention":
        return None
    warnings = [
        text
        for warning in (summary.get("memory_warnings") if isinstance(summary.get("memory_warnings"), list) else [])
        if (text := str(warning).strip())
    ]
    shown = warnings[:2]
    if len(warnings) > 2:
        shown.append(f"+{len(warnings) - 2} more")
    return f"Memory warnings: {'; '.join(shown)}" if shown else "Memory warnings need attention"


def content_to_text(content) -> str:
    """Flatten a message's content to plain text for text-based exports."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("text")
        )
    return ""


def message_role(message) -> str:
    if isinstance(message, ChatMessage):
        return message.role or ""
    if isinstance(message, dict):
        return message.get("role", "") or ""
    return getattr(message, "role", "") or ""


def message_text(message) -> str:
    if isinstance(message, ChatMessage):
        content = message.content
    elif isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    return content_to_text(content)


def message_metadata(message) -> dict:
    if isinstance(message, ChatMessage):
        metadata = message.metadata
    elif isinstance(message, dict):
        metadata = message.get("metadata")
    else:
        metadata = getattr(message, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}
