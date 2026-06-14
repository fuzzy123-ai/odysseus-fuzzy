"""Reproducible Phase 0 performance probes for Odysseus.

The script intentionally avoids live model calls. It captures local hot-path
costs that matter before the optimization phases: context token estimation,
context trimming, and session context materialization.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.models import ChatMessage, Session
from src.context_compactor import trim_for_context
from src.model_context import estimate_tokens


DEFAULT_MESSAGE_COUNTS = (25, 50, 100, 200)
DEFAULT_CONTEXT_BUDGET = 32_768
DEFAULT_RESERVE_TOKENS = 1_024
DEFAULT_ITERATIONS = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _payload(index: int, width: int = 320) -> str:
    base = (
        f"Turn {index:04d}: Odysseus performance baseline text with "
        "session context, tool-output summaries, scheduler notes, and "
        "database-hot-path markers. "
    )
    return (base * ((width // len(base)) + 1))[:width]


def synthetic_message_dicts(message_count: int) -> list[dict[str, Any]]:
    """Build deterministic API-shaped messages for context benchmarks."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are Odysseus running a performance baseline."}
    ]
    roles = ("user", "assistant")
    for index in range(max(0, message_count - 1)):
        messages.append({"role": roles[index % 2], "content": _payload(index)})
    return messages


def synthetic_session(message_count: int) -> Session:
    history = [
        ChatMessage(
            role=("user" if index % 2 == 0 else "assistant"),
            content=_payload(index),
            metadata={"timestamp": f"2026-06-14T00:{index % 60:02d}:00Z"},
        )
        for index in range(message_count)
    ]
    return Session(
        id="perf-baseline",
        name="Performance Baseline",
        endpoint_url="http://example.invalid",
        model="baseline-model",
        history=history,
        owner="perf",
        message_count=len(history),
    )


def measure_ms(fn: Callable[[], Any], *, iterations: int = DEFAULT_ITERATIONS) -> dict[str, Any]:
    """Run a callable repeatedly and return stable timing stats plus last value."""
    elapsed: list[float] = []
    result: Any = None
    for _ in range(max(1, iterations)):
        started = time.perf_counter()
        result = fn()
        elapsed.append((time.perf_counter() - started) * 1000)
    return {
        "min_ms": round(min(elapsed), 3),
        "avg_ms": round(statistics.fmean(elapsed), 3),
        "max_ms": round(max(elapsed), 3),
        "iterations": max(1, iterations),
        "result": result,
    }


def profile_long_chat(
    message_counts: Iterable[int] = DEFAULT_MESSAGE_COUNTS,
    *,
    context_budget: int = DEFAULT_CONTEXT_BUDGET,
    reserve_tokens: int = DEFAULT_RESERVE_TOKENS,
    iterations: int = DEFAULT_ITERATIONS,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for message_count in message_counts:
        messages = synthetic_message_dicts(int(message_count))

        estimate_probe = measure_ms(lambda: estimate_tokens(messages), iterations=iterations)
        before_tokens = int(estimate_probe["result"])

        def _trim() -> list[dict[str, Any]]:
            return trim_for_context(messages, context_budget, reserve_tokens=reserve_tokens)

        trim_probe = measure_ms(_trim, iterations=iterations)
        trimmed = trim_probe["result"]
        after_tokens = estimate_tokens(trimmed)

        rows.append({
            "message_count": len(messages),
            "tokens_before": before_tokens,
            "tokens_after_trim": after_tokens,
            "messages_after_trim": len(trimmed),
            "estimate_tokens": _without_result(estimate_probe),
            "trim_for_context": _without_result(trim_probe),
        })
    return rows


def profile_session_materialization(
    message_counts: Iterable[int] = DEFAULT_MESSAGE_COUNTS,
    *,
    iterations: int = DEFAULT_ITERATIONS,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for message_count in message_counts:
        session = synthetic_session(int(message_count))
        probe = measure_ms(session.get_context_messages, iterations=iterations)
        context_messages = probe["result"]
        rows.append({
            "message_count": int(message_count),
            "context_message_count": len(context_messages),
            "get_context_messages": _without_result(probe),
        })
    return rows


def build_report(
    *,
    message_counts: Iterable[int] = DEFAULT_MESSAGE_COUNTS,
    context_budget: int = DEFAULT_CONTEXT_BUDGET,
    reserve_tokens: int = DEFAULT_RESERVE_TOKENS,
    iterations: int = DEFAULT_ITERATIONS,
    phase: int = 0,
) -> dict[str, Any]:
    counts = tuple(int(value) for value in message_counts)
    return {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "phase": int(phase),
        "config": {
            "message_counts": list(counts),
            "context_budget": int(context_budget),
            "reserve_tokens": int(reserve_tokens),
            "iterations": max(1, int(iterations)),
        },
        "long_chat": profile_long_chat(
            counts,
            context_budget=context_budget,
            reserve_tokens=reserve_tokens,
            iterations=iterations,
        ),
        "session_materialization": profile_session_materialization(
            counts,
            iterations=iterations,
        ),
    }


def _without_result(probe: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in probe.items() if key != "result"}


def parse_counts(raw: str) -> tuple[int, ...]:
    values = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    if not values:
        raise argparse.ArgumentTypeError("at least one message count is required")
    if any(value <= 0 for value in values):
        raise argparse.ArgumentTypeError("message counts must be positive")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Odysseus Phase 0 performance baseline probes.")
    parser.add_argument("--counts", type=parse_counts, default=DEFAULT_MESSAGE_COUNTS)
    parser.add_argument("--context-budget", type=int, default=DEFAULT_CONTEXT_BUDGET)
    parser.add_argument("--reserve-tokens", type=int, default=DEFAULT_RESERVE_TOKENS)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--phase", type=int, default=0)
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    args = parser.parse_args(argv)

    report = build_report(
        message_counts=args.counts,
        context_budget=args.context_budget,
        reserve_tokens=args.reserve_tokens,
        iterations=args.iterations,
        phase=args.phase,
    )
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
