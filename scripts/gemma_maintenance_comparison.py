"""CLI for the redacted Gemma-vs-DeepSeek maintenance comparison."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.gemma_maintenance_comparison import (  # noqa: E402
    comparison_report_to_json,
    run_maintenance_comparison,
)
from src.gemma_memory_benchmark import deterministic_fixture_call  # noqa: E402
from src.maintenance_model_policy import DEFAULT_MAINTENANCE_MODEL  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a redacted Gemma 3 vs DeepSeek maintenance comparison."
    )
    parser.add_argument("--gemma-model", default=DEFAULT_MAINTENANCE_MODEL)
    parser.add_argument("--gemma-provider", default="local_ollama")
    parser.add_argument("--gemma-base-url", default="http://localhost:11434/api")
    parser.add_argument("--deepseek-model", default="deepseek-v4-flash")
    parser.add_argument("--deepseek-provider", default="deepseek")
    parser.add_argument("--deepseek-base-url", default="https://api.deepseek.com/v1/chat/completions")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=600)
    parser.add_argument("--live-gemma", action="store_true")
    parser.add_argument("--live-deepseek", action="store_true")
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
    provider_label: str,
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
            surface="maintenance_comparison",
            prompt_type=f"gemma_deepseek_maintenance_comparison:{provider_label}",
        )

    return call


async def _main_async(args: argparse.Namespace) -> int:
    gemma_caller = (
        await _live_call(
            base_url=args.gemma_base_url,
            model=args.gemma_model,
            timeout=args.timeout,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            provider_label="gemma",
        )
        if args.live_gemma
        else deterministic_fixture_call
    )
    deepseek_caller = (
        await _live_call(
            base_url=args.deepseek_base_url,
            model=args.deepseek_model,
            timeout=args.timeout,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            provider_label="deepseek",
        )
        if args.live_deepseek
        else deterministic_fixture_call
    )

    report = await run_maintenance_comparison(
        gemma_call_model=gemma_caller,
        deepseek_call_model=deepseek_caller,
        gemma_model=args.gemma_model,
        gemma_provider=args.gemma_provider,
        deepseek_model=args.deepseek_model,
        deepseek_provider=args.deepseek_provider,
        gemma_live=args.live_gemma,
        deepseek_live=args.live_deepseek,
    )
    encoded = comparison_report_to_json(report)
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
