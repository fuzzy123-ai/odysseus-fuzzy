"""Harmony marker routing and SSE delta helpers for LLM streams."""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Tuple


_HARMONY_MARKER_RE = re.compile(
    r"<\|channel\|>(analysis|commentary|final)"
    r"|<\|start\|>(?:assistant|system|user|tool)?"
    r"|<\|message\|>"
    r"|<\|end\|>"
    r"|<\|return\|>"
    r"|<\|call\|>"
)
_HARMONY_MARKERS = (
    "<|channel|>analysis",
    "<|channel|>commentary",
    "<|channel|>final",
    "<|start|>assistant",
    "<|start|>system",
    "<|start|>user",
    "<|start|>tool",
    "<|start|>",
    "<|message|>",
    "<|end|>",
    "<|return|>",
    "<|call|>",
)
_HARMONY_MAX_MARKER_LEN = max(len(marker) for marker in _HARMONY_MARKERS)


def _harmony_suffix_hold_len(text: str) -> int:
    """Return how many trailing chars could be the start of a harmony marker."""
    limit = min(len(text), _HARMONY_MAX_MARKER_LEN - 1)
    for n in range(limit, 0, -1):
        suffix = text[-n:]
        if any(marker.startswith(suffix) for marker in _HARMONY_MARKERS):
            return n
    return 0


class _HarmonyStreamRouter:
    """Route OpenAI harmony analysis/final channels without leaking markers."""

    def __init__(self) -> None:
        self._buf = ""
        self._seen_harmony = False
        self._channel: Optional[str] = None
        self._in_message = False

    def feed(self, text: str) -> List[Tuple[str, bool]]:
        if not text:
            return []
        self._buf += text
        return self._drain(final=False)

    def flush(self) -> List[Tuple[str, bool]]:
        return self._drain(final=True)

    def _append_text(self, out: List[Tuple[str, bool]], text: str) -> None:
        if not text:
            return
        if not self._seen_harmony:
            out.append((text, False))
            return
        if self._in_message:
            # analysis + commentary (tool-call preambles / function-arg bodies)
            # are internal, not user-facing — route them to thinking so they
            # don't leak into the visible answer; only `final` is visible.
            out.append((text, self._channel in ("analysis", "commentary")))

    def _handle_marker(self, match: re.Match[str]) -> None:
        marker = match.group(0)
        self._seen_harmony = True
        if marker.startswith("<|channel|>"):
            self._channel = match.group(1)
            self._in_message = False
        elif marker == "<|message|>":
            self._in_message = True
        else:
            self._in_message = False
            if marker in {"<|end|>", "<|return|>", "<|call|>"}:
                self._channel = None

    def _drain(self, *, final: bool) -> List[Tuple[str, bool]]:
        out: List[Tuple[str, bool]] = []
        while True:
            match = _HARMONY_MARKER_RE.search(self._buf)
            if not match:
                break
            self._append_text(out, self._buf[:match.start()])
            self._handle_marker(match)
            self._buf = self._buf[match.end():]

        hold = 0 if final else _harmony_suffix_hold_len(self._buf)
        emit = self._buf if hold == 0 else self._buf[:-hold]
        self._buf = "" if hold == 0 else self._buf[-hold:]
        self._append_text(out, emit)
        return out


class AiLensModelStreamCapture:
    """Metadata-only model stream capture for a single turn.

    The capture never retains delta text. It only forwards bounded counts and
    opaque references through an injected ``AiLensEventEmitter``.
    """

    def __init__(
        self,
        emitter: Any,
        *,
        model_ref: Any,
        route_kind: str = "selected",
        locality: str = "unknown",
    ) -> None:
        from src.ai_lens_events import AiLensRedactionLevel, AiLensSourceKind, AiLensSourceRef
        from src.ai_lens_service import opaque_ai_lens_ref

        self._emitter = emitter
        self._delta_count = 0
        self._delta_bytes = 0
        self._thinking_delta_count = 0
        self._started = False
        self._finished = False
        self._model_ref = AiLensSourceRef.create(
            source_id=opaque_ai_lens_ref("model", model_ref or "unknown-model"),
            kind=AiLensSourceKind.MODEL,
            redaction_level=AiLensRedactionLevel.HASHED,
        )
        safe_route = route_kind if route_kind in {"selected", "primary", "fallback", "local"} else "selected"
        safe_locality = locality if locality in {"local", "api", "unknown"} else "unknown"
        self._emit(
            "model_route_selected",
            payload={"route_kind": safe_route, "locality": safe_locality},
            summary="Model route selected.",
        )

    def _emit(self, event_type: str, *, payload: dict[str, Any], summary: str, status: str | None = None, latency_ms: int = 0) -> bool:
        try:
            return bool(self._emitter.emit(
                event_type=event_type,
                source_refs=(self._model_ref,),
                payload=payload,
                summary=summary,
                status=status,
                latency_ms=latency_ms,
            ))
        except Exception:
            try:
                self._emitter.record_rejection("model_capture_failed")
            except Exception:
                pass
            return False

    def start(self) -> None:
        if self._started or self._finished:
            return
        self._started = True
        self._emit("model_stream_started", payload={"streaming": True}, summary="Model stream started.")

    def observe_delta(self, text: Any, *, thinking: bool = False) -> None:
        if self._finished:
            return
        self.start()
        try:
            byte_count = len(str(text or "").encode("utf-8", errors="replace"))
        except Exception:
            byte_count = 0
        self._delta_count = min(self._delta_count + 1, 1_000_000)
        self._delta_bytes = min(self._delta_bytes + byte_count, 1_000_000_000)
        if thinking:
            self._thinking_delta_count = min(self._thinking_delta_count + 1, 1_000_000)

    def safety_gate(self, reason_code: str, *, blocked: bool = True) -> None:
        safe = str(reason_code or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", safe):
            safe = "policy_gate"
        self._emit(
            "safety_gate_triggered",
            payload={"reason_code": safe, "blocked": bool(blocked)},
            summary="Model safety gate triggered.",
            status="blocked" if blocked else "warning",
        )

    def finish(self, *, status: str = "succeeded", latency_ms: int = 0, unsupported_segment_count: int = 0) -> None:
        if self._finished:
            return
        self.start()
        self._finished = True
        safe_status = status if status in {"succeeded", "partial", "failed", "blocked"} else "completed"
        if self._delta_count:
            self._emit(
                "model_stream_delta",
                payload={
                    "delta_count": self._delta_count,
                    "delta_bytes": self._delta_bytes,
                    "thinking_delta_count": self._thinking_delta_count,
                    "content_included": False,
                },
                summary="Model stream metadata aggregated.",
                status="completed",
            )
        self._emit(
            "answer_completed",
            payload={
                "delta_count": self._delta_count,
                "delta_bytes": self._delta_bytes,
                "unsupported_segment_count": max(0, min(int(unsupported_segment_count or 0), 1_000_000)),
                "answer_content_included": False,
            },
            summary="Answer stream completed.",
            status=safe_status,
            latency_ms=max(0, min(int(latency_ms or 0), 86_400_000)),
        )


def _stream_delta_event(text: str, *, thinking: bool = False, ai_lens_capture: Any = None) -> str:
    if ai_lens_capture is not None:
        try:
            ai_lens_capture.observe_delta(text, thinking=thinking)
        except Exception:
            try:
                ai_lens_capture.record_rejection("model_delta_capture_failed")
            except Exception:
                pass
    payload = {"delta": text}
    if thinking:
        payload["thinking"] = True
    return f"data: {json.dumps(payload)}\n\n"
