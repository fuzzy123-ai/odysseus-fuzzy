"""CLI for the Gemma memory-efficiency benchmark."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.gemma_memory_benchmark import (  # noqa: E402
    deterministic_fixture_call,
    report_to_json,
    run_benchmark,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Odysseus Gemma memory-efficiency benchmark."
    )
    parser.add_argument("--model", default="gemma4:e4b")
    parser.add_argument("--provider", default="local_ollama")
    parser.add_argument("--base-url", default="http://localhost:11434/api")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=600)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the configured model. Without this, run the deterministic fixture.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path for a redacted JSON report. Raw prompts and outputs are not written.",
    )
    return parser


async def _live_call(
    *,
    base_url: str,
    model: str,
    timeout: int,
    temperature: float,
    max_tokens: int,
):
    from src.llm_core import llm_call_async

    async def call(prompt: str) -> str:
        return await llm_call_async(
            base_url,
            model,
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            owner="homebase",
            surface="memory_benchmark",
            prompt_type="gemma_memory_efficiency_benchmark",
        )

    return call


async def _main_async(args: argparse.Namespace) -> int:
    if args.live:
        caller = await _live_call(
            base_url=args.base_url,
            model=args.model,
            timeout=args.timeout,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    else:
        caller = deterministic_fixture_call

    report = await run_benchmark(
        model=args.model,
        provider=args.provider,
        call_model=caller,
    )
    encoded = report_to_json(report)
    print(encoded)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(encoded + "\n", encoding="utf-8")
    return 0 if report.status == "passed" else 2


def main() -> int:
    return asyncio.run(_main_async(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
