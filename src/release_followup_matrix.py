"""Build an owner/parallelism matrix from release follow-up slices."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.release_slice_router import ReleaseFollowupSlice


OWNER_ORDER = ("Alice", "Bob", "Charlie")


@dataclass(frozen=True)
class ReleaseOwnerQueue:
    owner: str
    slice_ids: tuple[str, ...]
    parallel_safe_slice_ids: tuple[str, ...]
    sequential_slice_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "slice_ids": self.slice_ids,
            "parallel_safe_slice_ids": self.parallel_safe_slice_ids,
            "sequential_slice_ids": self.sequential_slice_ids,
        }


@dataclass(frozen=True)
class ReleaseFollowupMatrix:
    queues: tuple[ReleaseOwnerQueue, ...]
    parallel_batch_ids: tuple[str, ...]
    sequential_gate_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "queues": tuple(queue.to_dict() for queue in self.queues),
            "parallel_batch_ids": self.parallel_batch_ids,
            "sequential_gate_ids": self.sequential_gate_ids,
        }


def build_release_followup_matrix(slices: Iterable[ReleaseFollowupSlice]) -> ReleaseFollowupMatrix:
    normalized = tuple(slices)
    queues: list[ReleaseOwnerQueue] = []
    owners = _ordered_owners(normalized)
    for owner in owners:
        owner_slices = tuple(item for item in normalized if item.owner == owner)
        queues.append(
            ReleaseOwnerQueue(
                owner=owner,
                slice_ids=tuple(item.slice_id for item in owner_slices),
                parallel_safe_slice_ids=tuple(item.slice_id for item in owner_slices if item.parallel_safe),
                sequential_slice_ids=tuple(item.slice_id for item in owner_slices if not item.parallel_safe),
            )
        )

    return ReleaseFollowupMatrix(
        queues=tuple(queues),
        parallel_batch_ids=tuple(item.slice_id for item in normalized if item.parallel_safe),
        sequential_gate_ids=tuple(item.slice_id for item in normalized if not item.parallel_safe),
    )


def _ordered_owners(slices: tuple[ReleaseFollowupSlice, ...]) -> tuple[str, ...]:
    present = {item.owner for item in slices}
    ordered = [owner for owner in OWNER_ORDER if owner in present]
    ordered.extend(sorted(present - set(OWNER_ORDER)))
    return tuple(ordered)
