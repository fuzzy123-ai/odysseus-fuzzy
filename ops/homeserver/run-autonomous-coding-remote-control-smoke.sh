#!/usr/bin/env bash
set -euo pipefail

cd /opt/odysseus

container="${ODYSSEUS_CONTAINER_NAME:-odysseus_odysseus_1}"
report_ref="autonomous_coding_production/workstation-telegram-control-live-smoke.json"

if ! podman container exists "$container"; then
  echo "ERROR: Odysseus container is not running: $container" >&2
  exit 1
fi

podman exec -i "$container" python - <<'PY'
import json
from pathlib import Path
import time

from plugins.telegram.parsing import _telegram_control_command
from plugins.telegram.plugin import _handle_agent_task_control_command
from src.agent_task_ledger import read_task_control_events, record_task_event
from src.coding_agent_runner_state import (
    CodingRunnerState,
    CodingRunnerStateStore,
    transition_from_task_control_event,
)
from src.constants import DATA_DIR


data_dir = Path(DATA_DIR).resolve()
report_dir = data_dir / "reports" / "autonomous_coding_production"
report_dir.mkdir(parents=True, exist_ok=True)

stamp = int(time.time())
task_id = f"acpr9_remote_control_{stamp}"
target_ref = "repo:odysseus"

workstation_record = record_task_event(
    task_id=task_id,
    task_type="coding_agent_task",
    status="running",
    surface="workstation",
    correlation_id=task_id,
    target_ref=target_ref,
    progress_percent=20,
    gates_waiting=("sandbox_execution_policy",),
    summary="Workstation coding task smoke.",
)

store = CodingRunnerStateStore()
initial_state = store.write(
    CodingRunnerState.create(
        task_id=task_id,
        repo_id="odysseus",
        phase="scoped",
        progress_percent=20,
        gates_waiting=("sandbox_execution_policy",),
        next_human_decision="Remote control smoke is allowed to pause and resume this task.",
    )
)

status_command = _telegram_control_command({"kind": "text", "text": "/task status"})
status_result = _handle_agent_task_control_command(status_command)

pause_command = _telegram_control_command({"kind": "text", "text": "/task pause"})
pause_result = _handle_agent_task_control_command(pause_command)
pause_events = read_task_control_events(task_id=task_id, limit=5)["records"]
pause_event = next(event for event in pause_events if event.get("status") == "pause_requested")
paused_state = transition_from_task_control_event(store=store, event=pause_event)

resume_command = _telegram_control_command({"kind": "text", "text": "/task weiter"})
resume_result = _handle_agent_task_control_command(resume_command)
resume_events = read_task_control_events(task_id=task_id, limit=5)["records"]
resume_event = next(event for event in resume_events if event.get("status") == "resume_requested")
resumed_state = transition_from_task_control_event(store=store, event=resume_event)

status_matches_task = (status_result.get("agent_task") or {}).get("task_id") == task_id
pause_matches_task = (pause_result.get("agent_task") or {}).get("task_id") == task_id
resume_matches_task = (resume_result.get("agent_task") or {}).get("task_id") == task_id
ok = (
    workstation_record.get("surface") == "workstation"
    and status_command == "agent_task_status"
    and pause_command == "agent_task_pause"
    and resume_command == "agent_task_resume"
    and status_matches_task
    and pause_matches_task
    and resume_matches_task
    and pause_event.get("status") == "pause_requested"
    and resume_event.get("status") == "resume_requested"
    and paused_state.phase == "blocked"
    and "telegram_pause_requested" in paused_state.gates_waiting
    and resumed_state.phase == "scoped"
    and not resumed_state.gates_waiting
)

report = {
    "schema": "odysseus.autonomous_coding_remote_control_live_smoke.v1",
    "status": "succeeded" if ok else "failed",
    "task_id": task_id,
    "target_ref": target_ref,
    "workstation_task_recorded": workstation_record.get("status") == "running",
    "telegram_status_command": status_command,
    "telegram_status_matched_task": status_matches_task,
    "telegram_pause_command": pause_command,
    "telegram_pause_matched_task": pause_matches_task,
    "telegram_resume_command": resume_command,
    "telegram_resume_matched_task": resume_matches_task,
    "initial_runner_phase": initial_state.phase,
    "pause_ledger_status": pause_event.get("status"),
    "pause_runner_phase": paused_state.phase,
    "pause_runner_gate": "telegram_pause_requested" in paused_state.gates_waiting,
    "resume_ledger_status": resume_event.get("status"),
    "resume_runner_phase": resumed_state.phase,
    "resume_runner_gate_cleared": not resumed_state.gates_waiting,
    "writes_performed": True,
    "telegram_network_delivery": False,
    "deploy_performed": False,
    "raw_content_visible": False,
    "tokens_visible": False,
    "chat_ids_visible": False,
    "host_paths_visible": False,
}

report_path = report_dir / "workstation-telegram-control-live-smoke.json"
report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(report, sort_keys=True))
raise SystemExit(0 if ok else 1)
PY

echo "Autonomous coding remote-control smoke report: data/reports/${report_ref}"
