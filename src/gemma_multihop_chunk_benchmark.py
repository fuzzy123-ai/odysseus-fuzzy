"""Multi-hop chunk retrieval benchmark for local Gemma memory triage.

The benchmark builds a synthetic chunk graph with distractors, superseded
facts, sensitive review material, and linked evidence. Reports store only
chunk ids and hashes, never full prompt or chunk text.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from typing import Any, Awaitable, Callable, Mapping

from src.gemma_memory_benchmark import (
    BenchmarkCase,
    BenchmarkExpectation,
    ModelCaller,
    build_pipeline_summary,
    normalize_model_triage,
    parse_model_json,
)
from src.memory_triage_contract import memory_triage_enum_instruction


MULTIHOP_SCHEMA = "odysseus.gemma_multihop_chunk_benchmark.v1"
REQUIRED_FIELDS = (
    "classification",
    "document_type",
    "should_remember",
    "memory_write_intent_status",
    "local_only_required",
    "api_escalation_allowed",
    "raptor_target",
    "recall_answer",
    "tags",
    "evidence_chunk_ids",
)


@dataclass(frozen=True)
class SyntheticChunk:
    chunk_id: str
    text: str
    tags: tuple[str, ...]
    links: tuple[str, ...] = ()
    status: str = "active"

    @property
    def text_hash(self) -> str:
        return _hash_text(self.text)


@dataclass(frozen=True)
class MultihopCase:
    case_id: str
    title: str
    query: str
    settings: Mapping[str, Any]
    expected: BenchmarkExpectation
    required_chunk_ids: tuple[str, ...]
    forbidden_chunk_ids: tuple[str, ...] = ()
    supporting_chunk_ids: tuple[str, ...] = ()
    target_duration_ms: int = 45_000

    def to_memory_case(self, selected_chunks: tuple[SyntheticChunk, ...]) -> BenchmarkCase:
        chunk_refs = " ".join(chunk.chunk_id for chunk in selected_chunks)
        return BenchmarkCase(
            case_id=self.case_id,
            title=self.title,
            source_channel="raptor_graphrag",
            context=f"{self.query} selected_chunk_refs: {chunk_refs}",
            settings=self.settings,
            expected=self.expected,
            target_duration_ms=self.target_duration_ms,
        )


@dataclass(frozen=True)
class RetrievalResult:
    selected_chunks: tuple[SyntheticChunk, ...]
    seed_chunk_ids: tuple[str, ...]
    expanded_chunk_ids: tuple[str, ...]
    query_terms: tuple[str, ...]
    budget: int

    def to_redacted_dict(self) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        for chunk in self.selected_chunks:
            status_counts[chunk.status] = status_counts.get(chunk.status, 0) + 1
        return {
            "selected_chunk_ids": tuple(chunk.chunk_id for chunk in self.selected_chunks),
            "selected_chunk_hashes": tuple(chunk.text_hash for chunk in self.selected_chunks),
            "seed_chunk_ids": self.seed_chunk_ids,
            "expanded_chunk_ids": self.expanded_chunk_ids,
            "query_terms": self.query_terms,
            "budget": self.budget,
            "selected_count": len(self.selected_chunks),
            "selected_distractor_count": sum(
                1 for chunk in self.selected_chunks if chunk.chunk_id.startswith("distractor_")
            ),
            "selected_status_counts": status_counts,
        }


@dataclass(frozen=True)
class MultihopCaseResult:
    case_id: str
    duration_ms: int
    input_chars: int
    output_chars: int
    retrieval_selected_count: int
    retrieval_required_rank: Mapping[str, int | None]
    retrieval_irrelevant_selected_count: int
    retrieval_budget_waste_rate: float
    retrieval_supporting_chunk_ratio: float
    retrieval_precision: float
    retrieval_pass: bool
    retrieval_precision_pass: bool
    chunk_budget_pass: bool
    forbidden_chunk_pass: bool
    schema_valid: bool
    evidence_pass: bool
    policy_pass: bool
    memory_pass: bool
    speed_pass: bool
    pipeline_valid: bool
    score: float
    failure_reasons: tuple[str, ...]
    retrieval: Mapping[str, Any]
    parsed_summary: Mapping[str, Any]
    pipeline: Mapping[str, Any]
    prompt_hash: str

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "duration_ms": self.duration_ms,
            "input_chars": self.input_chars,
            "output_chars": self.output_chars,
            "retrieval_selected_count": self.retrieval_selected_count,
            "retrieval_required_rank": dict(self.retrieval_required_rank),
            "retrieval_irrelevant_selected_count": self.retrieval_irrelevant_selected_count,
            "retrieval_budget_waste_rate": round(self.retrieval_budget_waste_rate, 4),
            "retrieval_supporting_chunk_ratio": round(self.retrieval_supporting_chunk_ratio, 4),
            "retrieval_precision": round(self.retrieval_precision, 4),
            "retrieval_pass": self.retrieval_pass,
            "retrieval_precision_pass": self.retrieval_precision_pass,
            "chunk_budget_pass": self.chunk_budget_pass,
            "forbidden_chunk_pass": self.forbidden_chunk_pass,
            "schema_valid": self.schema_valid,
            "evidence_pass": self.evidence_pass,
            "policy_pass": self.policy_pass,
            "memory_pass": self.memory_pass,
            "speed_pass": self.speed_pass,
            "pipeline_valid": self.pipeline_valid,
            "score": round(self.score, 2),
            "failure_reasons": self.failure_reasons,
            "retrieval": dict(self.retrieval),
            "parsed_summary": dict(self.parsed_summary),
            "pipeline": dict(self.pipeline),
            "prompt_hash": self.prompt_hash,
        }


@dataclass(frozen=True)
class MultihopBenchmarkReport:
    model: str
    provider: str
    started_at: str
    finished_at: str
    total_duration_ms: int
    score: float
    status: str
    corpus_chunk_count: int
    retrieval_budget: int
    cases: tuple[MultihopCaseResult, ...]
    schema: str = MULTIHOP_SCHEMA

    def to_redacted_dict(self) -> dict[str, Any]:
        total = max(len(self.cases), 1)
        return {
            "schema": self.schema,
            "model": self.model,
            "provider": self.provider,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_ms": self.total_duration_ms,
            "score": round(self.score, 2),
            "status": self.status,
            "corpus_chunk_count": self.corpus_chunk_count,
            "retrieval_budget": self.retrieval_budget,
            "metrics": {
                "case_count": len(self.cases),
                "avg_latency_ms": round(sum(case.duration_ms for case in self.cases) / total, 2),
                "max_latency_ms": max((case.duration_ms for case in self.cases), default=0),
                "avg_selected_chunks": round(
                    sum(case.retrieval_selected_count for case in self.cases) / total,
                    2,
                ),
                "retrieval_pass_rate": round(100 * sum(case.retrieval_pass for case in self.cases) / total, 2),
                "retrieval_precision_pass_rate": round(
                    100 * sum(case.retrieval_precision_pass for case in self.cases) / total,
                    2,
                ),
                "avg_retrieval_precision": round(sum(case.retrieval_precision for case in self.cases) / total, 4),
                "avg_budget_waste_rate": round(
                    sum(case.retrieval_budget_waste_rate for case in self.cases) / total,
                    4,
                ),
                "evidence_pass_rate": round(100 * sum(case.evidence_pass for case in self.cases) / total, 2),
                "policy_pass_rate": round(100 * sum(case.policy_pass for case in self.cases) / total, 2),
            },
            "cases": tuple(case.to_redacted_dict() for case in self.cases),
        }


def build_synthetic_chunk_corpus() -> tuple[SyntheticChunk, ...]:
    core = (
        SyntheticChunk(
            "ops_runtime_001",
            "Odysseus server operations run with rootless Podman pods on the Debian homeserver.",
            ("odysseus", "runtime", "podman", "server"),
            links=("ops_memory_002", "ops_gpu_003"),
        ),
        SyntheticChunk(
            "ops_memory_002",
            "Memory maintenance must use the local Gemma3 4B model and avoid loading Gemma4 during routine document triage.",
            ("odysseus", "memory", "gemma3", "maintenance"),
            links=("ops_runtime_001", "ops_keepalive_004"),
        ),
        SyntheticChunk(
            "ops_gpu_003",
            "The homeserver has Intel UHD graphics only; local LLM inference is CPU bound.",
            ("hardware", "cpu", "gpu", "local_ai"),
            links=("ops_runtime_001",),
        ),
        SyntheticChunk(
            "ops_keepalive_004",
            "Gemma3 must stay warm through Ollama keep_alive forever and a periodic warmup timer.",
            ("gemma3", "ollama", "keepalive"),
            links=("ops_memory_002",),
        ),
        SyntheticChunk(
            "legacy_model_099",
            "Legacy note: use Gemma4 e4b for every maintenance task.",
            ("gemma4", "legacy", "maintenance"),
            status="superseded",
        ),
        SyntheticChunk(
            "privacy_invoice_010",
            "A redacted invoice-like document in DSGVO mode contains billing semantics and must remain local.",
            ("invoice", "dsgvo", "sensitive"),
            links=("privacy_review_011",),
        ),
        SyntheticChunk(
            "privacy_review_011",
            "Sensitive or invoice material requires review before memory write; raw document content is not stored.",
            ("review", "sensitive", "memory"),
            links=("privacy_invoice_010",),
        ),
        SyntheticChunk(
            "project_nextcloud_020",
            "Nextcloud import contains a project roadmap document whose safe abstract should be durable memory.",
            ("nextcloud", "project", "roadmap"),
            links=("project_graph_021",),
        ),
        SyntheticChunk(
            "project_graph_021",
            "The roadmap connects import policy, universal inbox routing, and RaptorGraph provenance nodes.",
            ("raptor", "graph", "routing", "project"),
            links=("project_nextcloud_020",),
        ),
    )
    distractors = tuple(
        SyntheticChunk(
            f"distractor_{index:03d}",
            (
                f"Synthetic unrelated note {index}: calendar cleanup, UI polish, "
                "weather reminders, or generic archive metadata with no durable policy."
            ),
            ("distractor", "archive", f"topic_{index % 7}"),
        )
        for index in range(1, 73)
    )
    return (*core, *distractors)


def build_adversarial_chunk_corpus() -> tuple[SyntheticChunk, ...]:
    """Return the default corpus plus high-overlap non-supporting chunks."""

    adversarial = (
        SyntheticChunk(
            "distractor_nextcloud_overlap_900",
            "Nextcloud import project note about generic durable archive policy; it does not define RaptorGraph routing evidence.",
            ("nextcloud", "import", "project", "archive"),
        ),
        SyntheticChunk(
            "distractor_invoice_overlap_901",
            "Invoice-like document template for public accounting examples without DSGVO review requirements.",
            ("invoice", "document", "template"),
        ),
        SyntheticChunk(
            "distractor_runtime_overlap_902",
            "Server runtime maintenance reminder for a different toolchain that does not mention Odysseus Podman or Gemma3.",
            ("server", "runtime", "maintenance"),
        ),
        SyntheticChunk(
            "legacy_invoice_review_903",
            "Legacy invoice workflow says cloud review is fine for DSGVO material.",
            ("invoice", "dsgvo", "review"),
            links=("privacy_invoice_010",),
            status="superseded",
        ),
        SyntheticChunk(
            "legacy_raptor_project_904",
            "Legacy RaptorGraph project note points to a deprecated routing model.",
            ("raptorgraph", "project", "routing"),
            links=("project_graph_021",),
            status="superseded",
        ),
    )
    return (*build_synthetic_chunk_corpus(), *adversarial)


def default_multihop_cases() -> tuple[MultihopCase, ...]:
    return (
        MultihopCase(
            case_id="multihop_runtime_memory_policy",
            title="Runtime plus memory policy",
            query="What should Odysseus remember about server runtime and local memory maintenance?",
            settings={"dsgvo_mode": False},
            expected=BenchmarkExpectation(
                classification="private",
                document_type="project",
                should_remember=True,
                memory_write_intent_status="ready",
                local_only_required=False,
                api_escalation_allowed=True,
                recall_terms=("podman", "gemma3", "memory"),
            ),
            required_chunk_ids=("ops_runtime_001", "ops_memory_002"),
            forbidden_chunk_ids=("legacy_model_099",),
            supporting_chunk_ids=("ops_gpu_003", "ops_keepalive_004"),
        ),
        MultihopCase(
            case_id="multihop_dsgvo_invoice_review",
            title="DSGVO invoice review boundary",
            query="How should an invoice-like DSGVO document be handled for memory?",
            settings={"dsgvo_mode": True},
            expected=BenchmarkExpectation(
                classification="sensitive",
                document_type="invoice",
                should_remember=True,
                memory_write_intent_status="review",
                local_only_required=True,
                api_escalation_allowed=False,
                recall_terms=("invoice", "review", "local"),
            ),
            required_chunk_ids=("privacy_invoice_010", "privacy_review_011"),
            supporting_chunk_ids=(),
        ),
        MultihopCase(
            case_id="multihop_nextcloud_raptor_project",
            title="Nextcloud project graph memory",
            query="What durable project memory links Nextcloud import and RaptorGraph routing?",
            settings={"dsgvo_mode": False},
            expected=BenchmarkExpectation(
                classification="private",
                document_type="project",
                should_remember=True,
                memory_write_intent_status="ready",
                local_only_required=False,
                api_escalation_allowed=True,
                recall_terms=("nextcloud", "raptor", "routing"),
            ),
            required_chunk_ids=("project_nextcloud_020", "project_graph_021"),
            supporting_chunk_ids=(),
        ),
    )


def retrieve_multihop_chunks(
    query: str,
    corpus: tuple[SyntheticChunk, ...],
    *,
    budget: int = 6,
) -> RetrievalResult:
    query_terms = _terms(query)
    active = [chunk for chunk in corpus if chunk.status == "active"]
    term_weights = _term_weights(active)
    by_id = {chunk.chunk_id: chunk for chunk in active}
    scored = sorted(
        ((chunk, _score_chunk(chunk, query_terms, term_weights)) for chunk in active),
        key=lambda item: (-item[1][0], -item[1][1], -len(item[0].links), item[0].chunk_id),
    )
    seeds = tuple(
        chunk.chunk_id
        for chunk, score in scored
        if _selectable_score(score)
    )[: max(1, min(3, budget))]
    selected_ids: list[str] = []
    expanded_ids: list[str] = []
    for chunk_id in seeds:
        if chunk_id not in selected_ids:
            selected_ids.append(chunk_id)
        expanded_before = len(expanded_ids)
        for linked in by_id.get(chunk_id, SyntheticChunk("", "", ())).links:
            if linked in by_id and linked not in selected_ids:
                selected_ids.append(linked)
                expanded_ids.append(linked)
            if len(selected_ids) >= budget:
                break
        if len(expanded_ids) > expanded_before and len(selected_ids) >= 2:
            break
        if len(selected_ids) >= budget:
            break
    if len(selected_ids) < budget and not expanded_ids:
        for chunk, score in scored:
            if not _selectable_score(score):
                break
            if chunk.chunk_id not in selected_ids:
                selected_ids.append(chunk.chunk_id)
            if len(selected_ids) >= budget:
                break
    return RetrievalResult(
        selected_chunks=tuple(by_id[chunk_id] for chunk_id in selected_ids[:budget]),
        seed_chunk_ids=seeds,
        expanded_chunk_ids=tuple(expanded_ids),
        query_terms=query_terms,
        budget=budget,
    )


async def run_multihop_benchmark(
    *,
    model: str,
    provider: str,
    call_model: ModelCaller,
    cases: tuple[MultihopCase, ...] | None = None,
    corpus: tuple[SyntheticChunk, ...] | None = None,
    retrieval_budget: int = 6,
) -> MultihopBenchmarkReport:
    selected_cases = cases or default_multihop_cases()
    chunk_corpus = corpus or build_synthetic_chunk_corpus()
    started = _now_iso()
    start = time.perf_counter()
    results = []
    for case in selected_cases:
        results.append(
            await run_multihop_case(
                case,
                corpus=chunk_corpus,
                retrieval_budget=retrieval_budget,
                model=model,
                provider=provider,
                call_model=call_model,
            )
        )
    total_duration_ms = int((time.perf_counter() - start) * 1000)
    score = sum(result.score for result in results) / max(len(results), 1)
    status = (
        "passed"
        if score >= 85 and all(result.retrieval_pass and result.retrieval_precision_pass and result.policy_pass for result in results)
        else "failed"
    )
    return MultihopBenchmarkReport(
        model=model,
        provider=provider,
        started_at=started,
        finished_at=_now_iso(),
        total_duration_ms=total_duration_ms,
        score=score,
        status=status,
        corpus_chunk_count=len(chunk_corpus),
        retrieval_budget=retrieval_budget,
        cases=tuple(results),
    )


async def run_multihop_case(
    case: MultihopCase,
    *,
    corpus: tuple[SyntheticChunk, ...],
    retrieval_budget: int,
    model: str,
    provider: str,
    call_model: ModelCaller,
) -> MultihopCaseResult:
    retrieval = retrieve_multihop_chunks(case.query, corpus, budget=retrieval_budget)
    prompt = _build_prompt(case, retrieval)
    start = time.perf_counter()
    raw = await call_model(prompt)
    duration_ms = int((time.perf_counter() - start) * 1000)
    parsed, parse_error = parse_model_json(raw)
    schema_valid = parse_error is None and all(field in parsed for field in REQUIRED_FIELDS)
    memory_case = case.to_memory_case(retrieval.selected_chunks)
    if schema_valid:
        parsed = normalize_model_triage(memory_case, parsed)
    pipeline = build_pipeline_summary(memory_case, parsed, model=model, provider=provider)
    checks = _score_multihop_case(
        case,
        parsed,
        pipeline,
        retrieval,
        duration_ms=duration_ms,
        schema_valid=schema_valid,
        parse_error=parse_error,
    )
    precision = _retrieval_precision(case, retrieval)
    return MultihopCaseResult(
        case_id=case.case_id,
        duration_ms=duration_ms,
        input_chars=len(prompt),
        output_chars=len(str(raw or "")),
        retrieval_selected_count=len(retrieval.selected_chunks),
        retrieval_required_rank=precision["required_rank"],
        retrieval_irrelevant_selected_count=precision["irrelevant_selected_count"],
        retrieval_budget_waste_rate=precision["budget_waste_rate"],
        retrieval_supporting_chunk_ratio=precision["supporting_chunk_ratio"],
        retrieval_precision=precision["precision"],
        retrieval_pass=checks["retrieval_pass"],
        retrieval_precision_pass=checks["retrieval_precision_pass"],
        chunk_budget_pass=checks["chunk_budget_pass"],
        forbidden_chunk_pass=checks["forbidden_chunk_pass"],
        schema_valid=schema_valid,
        evidence_pass=checks["evidence_pass"],
        policy_pass=checks["policy_pass"],
        memory_pass=checks["memory_pass"],
        speed_pass=checks["speed_pass"],
        pipeline_valid=checks["pipeline_valid"],
        score=checks["score"],
        failure_reasons=tuple(checks["failure_reasons"]),
        retrieval=retrieval.to_redacted_dict(),
        parsed_summary=_parsed_summary(parsed),
        pipeline=pipeline,
        prompt_hash=_hash_text(prompt),
    )


async def deterministic_multihop_fixture_call(prompt: str) -> str:
    case_id = _case_id_from_prompt(prompt)
    fixtures = {
        "multihop_runtime_memory_policy": {
            "classification": "private",
            "document_type": "project",
            "should_remember": True,
            "memory_write_intent_status": "ready",
            "local_only_required": False,
            "api_escalation_allowed": True,
            "raptor_target": "runtime_memory_policy",
            "recall_answer": "Odysseus uses rootless Podman on the Debian server and Gemma3 for local memory maintenance.",
            "tags": ["odysseus", "podman", "gemma3", "memory"],
            "evidence_chunk_ids": ["ops_runtime_001", "ops_memory_002"],
        },
        "multihop_dsgvo_invoice_review": {
            "classification": "sensitive",
            "document_type": "invoice",
            "should_remember": True,
            "memory_write_intent_status": "review",
            "local_only_required": True,
            "api_escalation_allowed": False,
            "raptor_target": "review_queue",
            "recall_answer": "Invoice-like DSGVO material remains local and requires review before memory write.",
            "tags": ["invoice", "dsgvo", "review"],
            "evidence_chunk_ids": ["privacy_invoice_010", "privacy_review_011"],
        },
        "multihop_nextcloud_raptor_project": {
            "classification": "private",
            "document_type": "project",
            "should_remember": True,
            "memory_write_intent_status": "ready",
            "local_only_required": False,
            "api_escalation_allowed": True,
            "raptor_target": "nextcloud_raptor_project",
            "recall_answer": "Nextcloud import project memory links the roadmap with RaptorGraph routing provenance.",
            "tags": ["nextcloud", "raptor", "routing"],
            "evidence_chunk_ids": ["project_nextcloud_020", "project_graph_021"],
        },
    }
    await asyncio.sleep(0)
    return json.dumps(fixtures.get(case_id, {}), sort_keys=True)


def report_to_json(report: MultihopBenchmarkReport) -> str:
    return json.dumps(report.to_redacted_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def _build_prompt(case: MultihopCase, retrieval: RetrievalResult) -> str:
    chunk_lines = "\n".join(
        f"- id={chunk.chunk_id}; tags={','.join(chunk.tags)}; text={chunk.text}"
        for chunk in retrieval.selected_chunks
    )
    return (
        "You are Odysseus local multi-hop memory triage. Analyze only the selected "
        "synthetic redacted chunks. Return JSON only with these keys: "
        f"{', '.join(REQUIRED_FIELDS)}. "
        "evidence_chunk_ids must list the chunk ids that support the decision. "
        "Ignore superseded or missing chunks. No markdown, no explanations, no raw source quotes. "
        f"{memory_triage_enum_instruction()}\n\n"
        f"case_id: {case.case_id}\n"
        f"title: {case.title}\n"
        f"dsgvo_mode: {bool(case.settings.get('dsgvo_mode'))}\n"
        f"query: {case.query}\n"
        f"selected_chunks:\n{chunk_lines}\n"
    )


def _score_multihop_case(
    case: MultihopCase,
    parsed: Mapping[str, Any],
    pipeline: Mapping[str, Any],
    retrieval: RetrievalResult,
    *,
    duration_ms: int,
    schema_valid: bool,
    parse_error: str | None,
) -> dict[str, Any]:
    failures: list[str] = []
    selected_ids = tuple(chunk.chunk_id for chunk in retrieval.selected_chunks)
    retrieval_pass = all(chunk_id in selected_ids for chunk_id in case.required_chunk_ids)
    if not retrieval_pass:
        failures.append("required_chunks_not_retrieved")
    chunk_budget_pass = len(selected_ids) <= retrieval.budget
    if not chunk_budget_pass:
        failures.append("chunk_budget_exceeded")
    forbidden_chunk_pass = not any(chunk_id in selected_ids for chunk_id in case.forbidden_chunk_ids)
    if not forbidden_chunk_pass:
        failures.append("forbidden_or_superseded_chunk_selected")
    precision = _retrieval_precision(case, retrieval)
    retrieval_precision_pass = bool(precision["irrelevant_selected_count"] == 0 and precision["budget_waste_rate"] == 0)
    if not retrieval_precision_pass:
        failures.append("retrieval_precision_gate_failed")
    if not schema_valid:
        failures.append(f"schema_invalid:{parse_error or 'missing_fields'}")

    evidence_ids = tuple(str(chunk_id) for chunk_id in parsed.get("evidence_chunk_ids") or ())
    evidence_pass = schema_valid and all(chunk_id in evidence_ids for chunk_id in case.required_chunk_ids)
    if not evidence_pass:
        failures.append("evidence_chunks_missing")

    expected = case.expected
    policy_pass = (
        parsed.get("classification") == expected.classification
        and parsed.get("document_type") == expected.document_type
        and bool(parsed.get("local_only_required")) == expected.local_only_required
        and bool(parsed.get("api_escalation_allowed")) == expected.api_escalation_allowed
    )
    if not policy_pass:
        failures.append("policy_mismatch")

    memory_pass = (
        bool(parsed.get("should_remember")) == expected.should_remember
        and str(pipeline.get("intent_status") or parsed.get("memory_write_intent_status")) == expected.memory_write_intent_status
    )
    if not memory_pass:
        failures.append("memory_intent_mismatch")
    recall = str(parsed.get("recall_answer") or "").lower()
    if expected.should_remember and not any(term in recall for term in expected.recall_terms):
        memory_pass = False
        failures.append("recall_terms_missing")

    pipeline_valid = bool(pipeline) and pipeline.get("raw_content_stored") is False
    if not pipeline_valid:
        failures.append("pipeline_invalid_or_raw_content")
    speed_pass = duration_ms <= case.target_duration_ms
    if not speed_pass:
        failures.append("target_duration_exceeded")

    score = 0.0
    score += 25.0 if retrieval_pass and chunk_budget_pass and forbidden_chunk_pass and retrieval_precision_pass else 0.0
    score += 15.0 if schema_valid and evidence_pass else 0.0
    score += 25.0 if policy_pass else 0.0
    score += 25.0 if memory_pass and pipeline_valid else 0.0
    score += 10.0 if speed_pass else max(0.0, 10.0 * (case.target_duration_ms / max(duration_ms, 1)))
    return {
        "retrieval_pass": retrieval_pass,
        "retrieval_precision_pass": retrieval_precision_pass,
        "chunk_budget_pass": chunk_budget_pass,
        "forbidden_chunk_pass": forbidden_chunk_pass,
        "evidence_pass": evidence_pass,
        "policy_pass": policy_pass,
        "memory_pass": memory_pass,
        "pipeline_valid": pipeline_valid,
        "speed_pass": speed_pass,
        "score": min(score, 100.0),
        "failure_reasons": failures,
    }


def _retrieval_precision(case: MultihopCase, retrieval: RetrievalResult) -> dict[str, Any]:
    selected_ids = tuple(chunk.chunk_id for chunk in retrieval.selected_chunks)
    relevant_ids = set(case.required_chunk_ids) | set(case.supporting_chunk_ids)
    irrelevant_count = sum(1 for chunk_id in selected_ids if chunk_id not in relevant_ids)
    selected_count = max(len(selected_ids), 1)
    supporting_count = sum(1 for chunk_id in selected_ids if chunk_id in relevant_ids)
    return {
        "required_rank": {
            chunk_id: (selected_ids.index(chunk_id) + 1 if chunk_id in selected_ids else None)
            for chunk_id in case.required_chunk_ids
        },
        "irrelevant_selected_count": irrelevant_count,
        "budget_waste_rate": irrelevant_count / selected_count,
        "supporting_chunk_ratio": supporting_count / selected_count,
        "precision": supporting_count / selected_count,
    }


def _score_chunk(
    chunk: SyntheticChunk,
    query_terms: tuple[str, ...],
    term_weights: Mapping[str, int],
) -> tuple[int, int]:
    haystack = set(_terms(" ".join((chunk.chunk_id, chunk.text, " ".join(chunk.tags)))))
    matched = [term for term in query_terms if term in haystack]
    weighted = sum(term_weights.get(term, 1) for term in matched)
    return (max(0, weighted - _negative_evidence_penalty(chunk.text)), len(matched))


def _selectable_score(score: tuple[int, int]) -> bool:
    weighted, matched = score
    return weighted > 0 and (matched >= 2 or weighted >= 4)


def _term_weights(chunks: list[SyntheticChunk]) -> dict[str, int]:
    document_frequency: dict[str, int] = {}
    for chunk in chunks:
        for term in set(_terms(" ".join((chunk.chunk_id, chunk.text, " ".join(chunk.tags))))):
            document_frequency[term] = document_frequency.get(term, 0) + 1
    total = max(len(chunks), 1)
    weights = {}
    for term, frequency in document_frequency.items():
        if frequency <= 1:
            weights[term] = 5
        elif frequency <= max(2, total // 20):
            weights[term] = 4
        elif frequency <= max(3, total // 10):
            weights[term] = 2
        else:
            weights[term] = 1
    return weights


def _negative_evidence_penalty(text: str) -> int:
    lowered = str(text or "").lower()
    cues = (
        "does not",
        "without",
        "different toolchain",
        "generic archive",
        "generic durable",
        "template",
        "legacy",
        "deprecated",
    )
    return 12 if any(cue in lowered for cue in cues) else 0


def _terms(text: str) -> tuple[str, ...]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "what",
        "how",
        "should",
        "about",
        "eine",
        "oder",
        "und",
        "document",
        "durable",
        "handled",
        "local",
        "memory",
        "remember",
    }
    terms = []
    for match in re.finditer(r"[a-zA-Z0-9_]{3,}", str(text).lower()):
        token = match.group(0)
        if token not in stop and token not in terms:
            terms.append(token)
    return tuple(terms)


def _parsed_summary(parsed: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "classification": parsed.get("classification"),
        "document_type": parsed.get("document_type"),
        "should_remember": bool(parsed.get("should_remember")),
        "memory_write_intent_status": parsed.get("memory_write_intent_status"),
        "local_only_required": bool(parsed.get("local_only_required")),
        "api_escalation_allowed": bool(parsed.get("api_escalation_allowed")),
        "evidence_chunk_ids": tuple(str(chunk_id) for chunk_id in parsed.get("evidence_chunk_ids") or ()),
        "tag_count": len(parsed.get("tags") or ()) if isinstance(parsed.get("tags"), list) else 0,
        "recall_answer_hash": _hash_text(parsed.get("recall_answer")),
    }


def _case_id_from_prompt(prompt: str) -> str:
    match = re.search(r"^case_id:\s*([a-z0-9_]+)\s*$", prompt, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _hash_text(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
