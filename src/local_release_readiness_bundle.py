"""Local read-only release readiness bundle.

This helper evaluates local plugin metadata from disk and feeds it into the
release readiness pipeline. It does not download, install, import plugins, run
tests, call providers, or dispatch work.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.plugin_local_audit import LocalPluginAuditSummary, audit_plugins_directory
from src.plugin_local_audit_markdown import render_local_plugin_audit_markdown
from src.plugin_release_markdown import render_plugin_release_gate_markdown
from src.release_artifact_markdown import render_release_artifact_manifest_markdown
from src.plugin_release_gate import PluginReleaseGate, evaluate_plugin_release_gate
from src.release_artifact_manifest import ReleaseArtifactManifest, build_release_artifact_manifest
from src.release_handoff_markdown import render_release_handoff_markdown
from src.release_readiness_pipeline import ReleaseReadinessPipelineSnapshot, build_current_release_readiness_pipeline


@dataclass(frozen=True)
class LocalReleaseReadinessBundle:
    plugin_gate: PluginReleaseGate
    local_plugin_audit: LocalPluginAuditSummary
    artifact_manifest: ReleaseArtifactManifest
    pipeline: ReleaseReadinessPipelineSnapshot
    plugin_markdown: str
    local_plugin_audit_markdown: str
    artifact_markdown: str
    handoff_markdown: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_gate": self.plugin_gate.to_dict(),
            "local_plugin_audit": {
                "ok": self.local_plugin_audit.ok,
                "plugin_count": self.local_plugin_audit.plugin_count,
                "loaded_count": self.local_plugin_audit.loaded_count,
                "failing_ids": self.local_plugin_audit.failing_ids,
            },
            "artifact_manifest": self.artifact_manifest.to_dict(),
            "pipeline": self.pipeline.to_dict(),
            "plugin_markdown": self.plugin_markdown,
            "local_plugin_audit_markdown": self.local_plugin_audit_markdown,
            "artifact_markdown": self.artifact_markdown,
            "handoff_markdown": self.handoff_markdown,
        }


def build_local_release_readiness_bundle(
    *,
    registry_path: str | Path = "plugins/registry.json",
    plugin_directory: str | Path = "plugins",
    artifact_root: str | Path = ".",
) -> LocalReleaseReadinessBundle:
    plugin_gate = _evaluate_local_plugin_gate(Path(registry_path), Path(plugin_directory))
    local_plugin_audit = audit_plugins_directory(plugin_directory)
    artifact_manifest = build_release_artifact_manifest(root=artifact_root)
    pipeline = build_current_release_readiness_pipeline(plugin_gate=plugin_gate)
    return LocalReleaseReadinessBundle(
        plugin_gate=plugin_gate,
        local_plugin_audit=local_plugin_audit,
        artifact_manifest=artifact_manifest,
        pipeline=pipeline,
        plugin_markdown=render_plugin_release_gate_markdown(plugin_gate),
        local_plugin_audit_markdown=render_local_plugin_audit_markdown(local_plugin_audit),
        artifact_markdown=render_release_artifact_manifest_markdown(artifact_manifest),
        handoff_markdown=render_release_handoff_markdown(pipeline),
    )


def _evaluate_local_plugin_gate(registry_path: Path, plugin_directory: Path) -> PluginReleaseGate:
    try:
        registry_document = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return PluginReleaseGate(
            ok=False,
            registry_ok=False,
            local_plugins_ok=True,
            registry_plugin_count=0,
            local_plugin_count=0,
            errors=("registry:file:missing",),
        )
    except json.JSONDecodeError:
        return PluginReleaseGate(
            ok=False,
            registry_ok=False,
            local_plugins_ok=True,
            registry_plugin_count=0,
            local_plugin_count=0,
            errors=("registry:file:invalid_json",),
        )
    return evaluate_plugin_release_gate(registry_document, str(plugin_directory))
