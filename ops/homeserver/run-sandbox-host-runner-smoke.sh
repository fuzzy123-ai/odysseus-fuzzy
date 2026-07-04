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

podman exec -i "$container" python - <<'PY'
import json
import os
from pathlib import Path
import time

from src.agent_sandbox_contract import SandboxJobRequest, SandboxMount
from src.agent_sandbox_worker import SandboxWorker
from src.constants import DATA_DIR
from src.sandbox_job_ledger import SandboxJobLedger

data_dir = Path(DATA_DIR).resolve()
report_dir = data_dir / "reports" / "autonomous_coding_agent"
rw_smoke_dir = report_dir / "sandbox_rw_smoke"
report_dir.mkdir(parents=True, exist_ok=True)
rw_smoke_dir.mkdir(parents=True, exist_ok=True)

stamp = int(time.time())
terminal_job_id = f"host_runner_terminal_smoke_{stamp}"
terminal_job = SandboxJobRequest.create(
    job_id=terminal_job_id,
    argv=("python", "--version"),
    image="docker.io/library/python:3.14-slim",
    mounts=(),
    limits={"timeout_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "output_bytes": 4096},
    network_mode="none",
    secrets_attached=False,
)

rw_job_id = f"host_runner_rw_smoke_{stamp}"
rw_result_rel = "data/reports/autonomous_coding_agent/sandbox_rw_smoke/result.txt"
rw_result_path = data_dir.parent / rw_result_rel
if rw_result_path.exists():
    rw_result_path.unlink()
rw_job = SandboxJobRequest.create(
    job_id=rw_job_id,
    argv=(
        "python",
        "-c",
        "__import__('pathlib').Path('/workspace/smoke/result.txt').write_text('ok', encoding='utf-8')",
    ),
    image="docker.io/library/python:3.14-slim",
    mounts=(SandboxMount.create(source="data/reports/autonomous_coding_agent/sandbox_rw_smoke", target="/workspace/smoke", mode="rw"),),
    limits={"timeout_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "output_bytes": 4096},
    network_mode="none",
    secrets_attached=False,
)

worker = SandboxWorker(ledger=SandboxJobLedger(Path(DATA_DIR) / "sandbox_job_ledger"))
terminal_result = worker.submit(terminal_job, live_enabled=True, operator_go=True)
rw_result = worker.submit(rw_job, live_enabled=True, operator_go=True, allow_rw_mounts=True)
terminal_status = terminal_result.status.to_dict()
rw_status = rw_result.status.to_dict()
rw_result_ok = rw_result_path.exists() and rw_result_path.read_text(encoding="utf-8") == "ok"
report = {
    "schema": "odysseus.sandbox_host_runner_live_smoke.v1",
    "terminal_job_id": terminal_job_id,
    "terminal_status": terminal_status.get("status"),
    "terminal_exit_code": terminal_status.get("exit_code"),
    "terminal_executed_live": terminal_result.executed_live,
    "rw_job_id": rw_job_id,
    "rw_status": rw_status.get("status"),
    "rw_exit_code": rw_status.get("exit_code"),
    "rw_executed_live": rw_result.executed_live,
    "rw_result_present": rw_result_ok,
    "status": "succeeded"
    if (
        terminal_status.get("status") == "succeeded"
        and terminal_status.get("exit_code") == 0
        and rw_status.get("status") == "succeeded"
        and rw_status.get("exit_code") == 0
        and rw_result_ok
    )
    else "failed",
    "backend": os.getenv("ODYSSEUS_SANDBOX_RUNNER_BACKEND") or "",
    "network_mode": "none",
    "secrets_attached": False,
    "write_mount_scope": "data/reports/autonomous_coding_agent/sandbox_rw_smoke",
    "raw_content_visible": False,
    "tokens_visible": False,
    "host_paths_visible": False,
}

report_path = report_dir / "host-runner-live-smoke.json"
report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(report, sort_keys=True))
raise SystemExit(0 if report["status"] == "succeeded" else 1)
PY

echo "Sandbox host-runner smoke report: data/reports/${report_ref}"
