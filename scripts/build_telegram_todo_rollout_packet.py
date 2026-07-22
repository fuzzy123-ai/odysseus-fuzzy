"""Render the plan-only TTD-10 rollout packet to standard output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.telegram_todo_rollout_packet import (
    TelegramTodoRolloutPacketError,
    build_telegram_todo_rollout_packet,
)


def _parse_evidence(values: Sequence[str]) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for value in values:
        key, separator, ref = str(value).partition("=")
        if not separator or not key.strip() or not ref.strip():
            raise TelegramTodoRolloutPacketError(
                "--evidence must use KEY=content-free-reference"
            )
        normalized_key = key.strip().lower().replace("-", "_")
        if normalized_key in evidence:
            raise TelegramTodoRolloutPacketError(
                f"duplicate evidence key: {normalized_key}"
            )
        evidence[normalized_key] = ref
    return evidence


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a non-executable TTD-10 live-gate review packet."
    )
    parser.add_argument("--build-commit", required=True)
    parser.add_argument("--rollback-commit", required=True)
    parser.add_argument("--environment-ref", required=True)
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        packet = build_telegram_todo_rollout_packet(
            build_commit=args.build_commit,
            rollback_commit=args.rollback_commit,
            environment_ref=args.environment_ref,
            evidence_refs=_parse_evidence(args.evidence),
        )
    except TelegramTodoRolloutPacketError as exc:
        print(f"TTD-10 packet error: {exc}")
        return 2
    print(
        json.dumps(
            packet,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
