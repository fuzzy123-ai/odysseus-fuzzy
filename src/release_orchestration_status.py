"""Compact release orchestration status for dashboards/runbooks.

This layer summarizes the read-only release pipeline into the few fields a
coordinator UI needs: overall status, active owners, parallel candidates, and
sequential gates. It never dispatches work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.release_readiness_pipeline import ReleaseReadinessPipelineSnapshot


@dataclass(frozen=True)
class ReleaseOrchestrationStatus:
    status: str
    external_release_go: bool
    active_owners: tuple[str, ...]
    parallel_candidate_ids: tuple[str, ...]
    sequential_gate_ids: tuple[str, ...]
    next_action_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "external_release_go": self.external_release_go,
            "active_owners": self.active_owners,
            "parallel_candidate_ids": self.parallel_candidate_ids,
            "sequential_gate_ids": self.sequential_gate_ids,
            "next_action_ids": self.next_action_ids,
        }


def build_release_orchestration_status(
    pipeline: ReleaseReadinessPipelineSnapshot,
) -> ReleaseOrchestrationStatus:
    return ReleaseOrchestrationStatus(
        status=pipeline.report.status,
        external_release_go=pipeline.external_release_go,
        active_owners=tuple(queue.owner for queue in pipeline.followup_matrix.queues),
        parallel_candidate_ids=pipeline.followup_matrix.parallel_batch_ids,
        sequential_gate_ids=pipeline.followup_matrix.sequential_gate_ids,
        next_action_ids=tuple(item.slice_id for item in pipeline.followup_slices),
    )
