"""Generic runner for plugin-owned background consolidation jobs."""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.plugin_system import ConsolidationJobSpec, get_consolidation_jobs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConsolidationRunResult:
    job_id: str
    plugin_id: Optional[str]
    ok: bool
    result: Any = None
    error: Optional[str] = None


async def run_consolidation_jobs(
    *,
    owner: Optional[str] = None,
    capability: Optional[str] = None,
    trigger: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> List[ConsolidationRunResult]:
    """Run matching consolidation jobs without letting one failure stop others."""
    jobs = get_consolidation_jobs(capability=capability)
    results: List[ConsolidationRunResult] = []
    for job in jobs:
        try:
            result = await _run_one(job, owner=owner, trigger=trigger, context=context or {})
            results.append(ConsolidationRunResult(
                job_id=job.id,
                plugin_id=job.plugin_id,
                ok=True,
                result=result,
            ))
        except Exception as exc:
            logger.warning("Consolidation job %s failed: %s", job.id, exc)
            results.append(ConsolidationRunResult(
                job_id=job.id,
                plugin_id=job.plugin_id,
                ok=False,
                error=str(exc),
            ))
    return results


async def run_periodic_consolidation_pass(*, owners: Optional[List[str]] = None) -> List[ConsolidationRunResult]:
    """Run one periodic consolidation pass for each owner, or default scope."""
    targets = owners or [None]
    all_results: List[ConsolidationRunResult] = []
    for owner in targets:
        all_results.extend(await run_consolidation_jobs(
            owner=owner,
            capability="periodic",
            trigger="periodic",
            context={},
        ))
    return all_results


async def _run_one(
    job: ConsolidationJobSpec,
    *,
    owner: Optional[str],
    trigger: Optional[str],
    context: Dict[str, Any],
) -> Any:
    kwargs = _accepted_kwargs(job.run, {
        "owner": owner,
        "trigger": trigger,
        "context": context,
    })
    result = job.run(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _accepted_kwargs(fn, candidates: Dict[str, Any]) -> Dict[str, Any]:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return candidates
    params = signature.parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return candidates
    return {name: value for name, value in candidates.items() if name in params}
