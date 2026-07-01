"""Print a read-only RAG chunk-generation reindex dry-run plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.chroma_client import get_chroma_client
from src.rag_reindex_dry_run import build_rag_reindex_dry_run_plan
from src.rag_text_chunking import STRUCTURED_SPLITTER_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only RAG reindex dry-run planner.")
    parser.add_argument("--generation", default=STRUCTURED_SPLITTER_VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = get_chroma_client()
        payload = build_rag_reindex_dry_run_plan(chroma_client=client, generation=args.generation)
    except Exception as exc:
        payload = {
            "schema": "odysseus.rag_reindex_generation_readonly_plan.v1",
            "dry_run": True,
            "read_only": True,
            "generation": args.generation,
            "status": "blocked_chromadb_unreachable",
            "targets": [],
            "writes_performed": 0,
            "rollback_supported": False,
            "next_action": "start_or_point_to_chromadb_before_reindex",
            "error_class": type(exc).__name__,
            "private_content_visible": False,
            "secret_values_visible": False,
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"ready", "degraded_no_rag_lane_collections"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
