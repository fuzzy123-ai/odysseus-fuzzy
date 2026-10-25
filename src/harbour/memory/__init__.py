"""Memory bounded context: Unified Source Index (USI) & Context Manifests.

This module consolidates the knowledge runtime, cooperative index job pump,
source adapter registry, and context manifest producers.
"""

from __future__ import annotations

from src.unified_source_index_runtime import (
    KnowledgeRuntime,
)
from src.unified_source_index_job_runner import (
    CooperativeIndexJobPump,
)
from src.unified_source_index_runtime_status import (
    project_unified_source_index_runtime_status,
)
from src.unified_source_index_source_registry import (
    SourceAdapterRegistry,
)
from src.unified_source_index_sources.planning import (
    PlanningSourceAdapter,
)
from src.unified_source_index_sources.planning_markdown import (
    PlanningMarkdownSourceAdapter,
)
from src.unified_source_index_sources.planning_composite import (
    PlanningCompositeSourceAdapter,
)
from src.context_manifest_producer import (
    CombinedContextManifestProducer,
)

__all__ = [
    "KnowledgeRuntime",
    "CooperativeIndexJobPump",
    "project_unified_source_index_runtime_status",
    "SourceAdapterRegistry",
    "PlanningSourceAdapter",
    "PlanningMarkdownSourceAdapter",
    "PlanningCompositeSourceAdapter",
    "CombinedContextManifestProducer",
]
