"""CLI for the Gemma multi-hop chunk benchmark."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.gemma_multihop_chunk_benchmark import (  # noqa: E402
    build_adversarial_chunk_corpus,
    deterministic_multihop_fixture_call,
    report_to_json,
    run_multihop_benchmark,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Odysseus Gemma multi-hop chunk benchmark."
    )
    parser.add_argument("--model", default="gemma3:4b")
    parser.add_argument("--provider", default="local_ollama")
    parser.add_argument("--base-url", default="http://localhost:11434/api")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--retrieval-budget", type=int, default=6)
    parser.add_argument("--adversarial", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="")
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
            prompt_type="gemma_multihop_chunk_benchmark",
        )

    return call


async def _main_async(args: argparse.Namespace) -> int:
    caller = (
        await _live_call(
            base_url=args.base_url,
            model=args.model,
            timeout=args.timeout,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        if args.live
        else deterministic_multihop_fixture_call
    )
    report = await run_multihop_benchmark(
        model=args.model,
        provider=args.provider,
        call_model=caller,
        corpus=build_adversarial_chunk_corpus() if args.adversarial else None,
        retrieval_budget=args.retrieval_budget,
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
