"""Run the metadata-only Nextcloud import preparation pipeline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.nextcloud_import_config import load_nextcloud_import_config
from src.nextcloud_import_report import build_nextcloud_import_dry_run_report
from src.nextcloud_resumable_scanner import run_nextcloud_scanner_dry_run
from src.nextcloud_software_archives import plan_nextcloud_software_archive_metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a safe metadata-only dry-run for the Nextcloud import pipeline."
    )
    parser.add_argument(
        "--config",
        default=str(REPO_ROOT / "config" / "nextcloud_import_config.json"),
        help="Path to the versioned dry-run import config.",
    )
    parser.add_argument(
        "--root",
        default="",
        help="Runtime-only local Nextcloud source root. If omitted, source_root_env from config is used.",
    )
    parser.add_argument("--ledger-path", required=True, help="BigData JSONL ledger path outside the source root.")
    parser.add_argument("--source-id", default="", help="Override source_id from config.")
    parser.add_argument("--batch-limit", type=int, default=None, help="Optional scan batch limit.")
    parser.add_argument(
        "--scan-profile",
        default="full",
        choices=("full", "documents_only", "software_detection", "media_catalog"),
        help="Metadata label stored on inventory records.",
    )
    parser.add_argument("--skip-scan", action="store_true", help="Do not scan; report from existing ledger only.")
    parser.add_argument(
        "--skip-software-plan",
        action="store_true",
        help="Do not append dry-run software archive analysis records.",
    )
    parser.add_argument("--max-samples", type=int, default=10, help="Maximum sample paths in reports.")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Output format.")
    return parser


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    config = load_nextcloud_import_config(args.config)
    source_id = str(args.source_id or config["source_id"]).strip()
    ledger_path = Path(args.ledger_path)
    root = _runtime_root(args.root, config)

    scan_payload: dict[str, Any] | None = None
    if not args.skip_scan:
        if root is None:
            raise SystemExit("source root is required unless --skip-scan is set")
        scan_result = run_nextcloud_scanner_dry_run(
            root=root,
            ledger_path=ledger_path,
            source_id=source_id,
            batch_limit=args.batch_limit,
            config=config,
            scan_profile=args.scan_profile,
        )
        scan_payload = scan_result.to_dict()

    software_payload: dict[str, Any] | None = None
    software_config = dict(config.get("software_archives") or {})
    if not args.skip_software_plan and bool(software_config.get("enabled", True)):
        software_result = plan_nextcloud_software_archive_metadata(
            ledger_path=str(ledger_path),
            source_id=source_id,
            target_root=str(software_config.get("target_root") or "Software Archives"),
        )
        software_payload = software_result.to_dict()

    report = build_nextcloud_import_dry_run_report(
        ledger_path=ledger_path,
        source_id=source_id,
        max_samples=args.max_samples,
        software_archive_target_root=str(software_config.get("target_root") or "Software Archives"),
    )
    return {
        "schema": "odysseus.nextcloud_import_pipeline_dry_run.v1",
        "dry_run": True,
        "source_id": source_id,
        "scan": scan_payload,
        "software_archives": software_payload,
        "report": report.to_dict(),
        "private_content_visible": False,
        "secret_values_visible": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    payload = run_pipeline(args)
    if args.format == "markdown":
        print(_to_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _runtime_root(value: str, config: dict[str, Any]) -> Path | None:
    explicit = str(value or "").strip()
    if explicit:
        return Path(explicit)
    env_name = str(config.get("source_root_env") or "").strip()
    env_value = os.environ.get(env_name, "").strip() if env_name else ""
    return Path(env_value) if env_value else None


def _to_markdown(payload: dict[str, Any]) -> str:
    report = payload["report"]
    scan = payload.get("scan") or {}
    software = payload.get("software_archives") or {}
    lines = [
        "# Nextcloud Import Pipeline Dry-run",
        "",
        f"- Source: `{payload['source_id']}`",
        f"- Scanned: `{scan.get('scanned', 'skipped')}`",
        f"- Inventory records: `{report['inventory_total']}`",
        f"- Document candidates: `{report['document_candidates']}`",
        f"- Metadata-only candidates: `{report['metadata_only_candidates']}`",
        f"- Review candidates: `{report['review_candidates']}`",
        f"- Long paths: `{report['long_path_count']}`",
        f"- Software plans appended: `{software.get('planned', 'skipped')}`",
        "",
        "Private contents and secret values are intentionally not included.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
