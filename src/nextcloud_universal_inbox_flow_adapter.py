"""Nextcloud dry-run adapters for the Universal Inbox flow state."""

from __future__ import annotations

from typing import Any, Mapping

from src.universal_inbox_flow_state import UniversalInboxFlowState, build_universal_inbox_flow_state
from src.universal_inbox_review_reasons import normalize_universal_inbox_review_reasons


def build_nextcloud_universal_inbox_flow_state(
    *,
    source_ref: str | None = None,
    import_report: Any | None = None,
    transfer_readiness: Any | None = None,
    live_readiness: Any | None = None,
    transfer_result: Any | None = None,
    pipeline_run: Mapping[str, Any] | None = None,
    memory_intent: Mapping[str, Any] | None = None,
    graph_event: Mapping[str, Any] | None = None,
    allow_live_write: bool = False,
) -> UniversalInboxFlowState:
    """Build a redacted flow state from existing Nextcloud dry-run payloads.

    The adapter does not scan Nextcloud, call WebDAV, start workers, copy files,
    or write memories. It only normalizes already-computed report/readiness
    dictionaries into the canonical Universal Inbox flow state.
    """

    report = _payload(import_report)
    transfer = _payload(transfer_readiness)
    live = _payload(live_readiness)
    result = _payload(transfer_result)
    source = source_ref or _source_ref(report, transfer, live)
    return build_universal_inbox_flow_state(
        source_ref=source,
        item_status=_item_status(report, transfer, live),
        pipeline_run=pipeline_run,
        nextcloud_report=report,
        copy_result=_copy_result(report, transfer, live, result),
        memory_intent=memory_intent,
        graph_event=graph_event,
        live_write_allowed=bool(allow_live_write and _live_ready(transfer, live)),
    )


def _payload(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        return payload if isinstance(payload, Mapping) else {}
    return {}


def _source_ref(
    report: Mapping[str, Any],
    transfer: Mapping[str, Any],
    live: Mapping[str, Any],
) -> str:
    source_id = str(report.get("source_id") or transfer.get("provider_id") or live.get("provider_id") or "nextcloud")
    return f"nextcloud:{source_id}"


def _item_status(
    report: Mapping[str, Any],
    transfer: Mapping[str, Any],
    live: Mapping[str, Any],
) -> dict[str, Any]:
    review_candidates = _int(report.get("review_candidates"))
    blocked = _status(transfer) == "blocked" or _status(live) == "blocked"
    if blocked:
        status = "blocked"
    elif review_candidates > 0:
        status = "needs_review"
    elif report:
        status = "uploaded"
    else:
        status = "pending"
    return {
        "source_kind": "nextcloud",
        "status": status,
        "family": "batch",
        "category": "nextcloud_import_dry_run" if report else "nextcloud_readiness",
        "extractable_now": _int(report.get("document_candidates")) > 0,
        "review_required": review_candidates > 0 or blocked,
        "reason_codes": _review_or_blocker_reasons(report, transfer, live, review_required=review_candidates > 0 or blocked),
    }


def _copy_result(
    report: Mapping[str, Any],
    transfer: Mapping[str, Any],
    live: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    if result:
        return {
            **dict(result),
            "dry_run": bool(result.get("dry_run", True)),
            "writes_performed": bool(result.get("writes_performed")),
            "copy_only": True,
        }
    transfer_status = _status(transfer)
    live_status = _status(live)
    if transfer_status == "blocked" or live_status == "blocked":
        status = "blocked"
    elif transfer_status == "deferred" or live_status == "deferred":
        status = "deferred"
    elif transfer_status in {"needs_operator_input", "ready_for_live_go"} or report:
        status = "dry_run_ready"
    else:
        status = "pending"
    return {
        "status": status,
        "dry_run": True,
        "copy_only": True,
        "writes_performed": False,
        "reasons": _reason_codes(report, transfer, live),
        "document_candidates": report.get("document_candidates"),
        "metadata_only_candidates": report.get("metadata_only_candidates"),
        "review_candidates": report.get("review_candidates"),
        "transfer_status": transfer_status,
        "live_readiness_status": live_status,
        "blocked_live_actions": tuple(
            dict.fromkeys(
                tuple(str(value) for value in transfer.get("blocked_live_actions") or ())
                + tuple(str(value) for value in live.get("blocked_live_actions") or ())
            )
        ),
    }


def _reason_codes(*payloads: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for payload in payloads:
        for key in ("reasons", "errors", "warnings", "blocked_reasons"):
            raw = payload.get(key)
            if isinstance(raw, str):
                values.append(raw)
            elif isinstance(raw, (tuple, list)):
                values.extend(str(item) for item in raw)
        if _int(payload.get("review_candidates")) > 0:
            values.append("nextcloud_review_candidates")
    return normalize_universal_inbox_review_reasons(values)


def _review_or_blocker_reasons(
    report: Mapping[str, Any],
    transfer: Mapping[str, Any],
    live: Mapping[str, Any],
    *,
    review_required: bool,
) -> tuple[str, ...]:
    if not review_required:
        return ()
    values: list[str] = []
    if _int(report.get("review_candidates")) > 0:
        values.append("nextcloud_review_candidates")
    for payload in (transfer, live):
        for key in ("errors", "warnings", "blocked_reasons"):
            raw = payload.get(key)
            if isinstance(raw, str):
                values.append(raw)
            elif isinstance(raw, (tuple, list)):
                values.extend(str(item) for item in raw)
    return normalize_universal_inbox_review_reasons(values)


def _live_ready(transfer: Mapping[str, Any], live: Mapping[str, Any]) -> bool:
    transfer_ready = not transfer or _status(transfer) == "ready_for_live_go"
    live_ready = not live or _status(live) == "ready_for_operator_review"
    return transfer_ready and live_ready


def _status(payload: Mapping[str, Any]) -> str:
    return str(payload.get("status") or "").strip().lower()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

