#!/usr/bin/env bash
set -euo pipefail

cd /opt/odysseus

container="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"
report_ref="autonomous_coding_agent/host-runner-live-smoke.json"

if [ ! -f .env ]; then
  echo "ERROR: /opt/odysseus/.env is missing." >&2
  exit 1
fi

if ! grep -q '^ODYSSEUS_SANDBOX_RUNNER_BACKEND=host_ssh$' .env; then
  echo "ERROR: sandbox host runner is not configured. Run ops/homeserver/setup-sandbox-host-runner.sh and recreate Odysseus first." >&2
  exit 1
fi

if ! podman container exists "$container"; then
  echo "ERROR: Odysseus container is not running: $container" >&2
  exit 1
fi

podman exec "$container" python - <<'PY'
import json
import os
from pathlib import Path
import time

from src.agent_sandbox_contract import SandboxJobRequest
from src.agent_sandbox_worker import SandboxWorker
from src.constants import DATA_DIR
from src.sandbox_job_ledger import SandboxJobLedger

job_id = f"host_runner_smoke_{int(time.time())}"
job = SandboxJobRequest.create(
    job_id=job_id,
    argv=("python", "--version"),
    image="docker.io/library/python:3.14-slim",
    mounts=(),
    limits={"timeout_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "output_bytes": 4096},
    network_mode="none",
    secrets_attached=False,
)

worker = SandboxWorker(ledger=SandboxJobLedger(Path(DATA_DIR) / "sandbox_job_ledger"))
result = worker.submit(job, live_enabled=True, operator_go=True)
status = result.status.to_dict()
report = {
    "schema": "odysseus.sandbox_host_runner_live_smoke.v1",
    "job_id": job_id,
    "status": status.get("status"),
    "exit_code": status.get("exit_code"),
    "executed_live": result.executed_live,
    "backend": os.getenv("ODYSSEUS_SANDBOX_RUNNER_BACKEND") or "",
    "network_mode": job.network_mode,
    "secrets_attached": job.secrets_attached,
    "raw_content_visible": False,
    "tokens_visible": False,
    "host_paths_visible": False,
}

report_dir = Path(DATA_DIR) / "reports" / "autonomous_coding_agent"
report_dir.mkdir(parents=True, exist_ok=True)
report_path = report_dir / "host-runner-live-smoke.json"
report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(report, sort_keys=True))
raise SystemExit(0 if report["status"] == "succeeded" and report["exit_code"] == 0 else 1)
PY

echo "Sandbox host-runner smoke report: data/reports/${report_ref}"
