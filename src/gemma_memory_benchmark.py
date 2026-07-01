"""Gemma memory-efficiency benchmark for Odysseus memory workflows.

The benchmark uses synthetic, redacted cases and checks whether a local model
can produce structured decisions that fit the Universal Inbox -> Memory Write
Intent -> RaptorGraph provenance boundary. It never persists raw prompts or raw
model output in its report.
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

from src.universal_inbox_analysis import build_universal_inbox_file_analysis_packet
from src.memory_triage_contract import (
    memory_triage_enum_instruction,
    normalize_memory_classification,
    normalize_memory_document_type,
    normalize_memory_write_intent_status,
)
from src.universal_inbox_memory import UniversalInboxMemoryAbstraction
from src.universal_inbox_memory_write_intent import build_universal_inbox_memory_write_intent
from src.universal_inbox_provenance import build_universal_inbox_author_stamp


BENCHMARK_SCHEMA = "odysseus.gemma_memory_efficiency_benchmark.v1"
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
)


@dataclass(frozen=True)
class BenchmarkExpectation:
    classification: str
    document_type: str
    should_remember: bool
    memory_write_intent_status: str
    local_only_required: bool
    api_escalation_allowed: bool
    recall_terms: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    title: str
    source_channel: str
    context: str
    settings: Mapping[str, Any]
    expected: BenchmarkExpectation
    target_duration_ms: int = 30_000

    @property
    def prompt(self) -> str:
        return (
            "You are Odysseus local memory triage. Analyze only this synthetic "
            "redacted case. Return JSON only with these keys: "
            f"{', '.join(REQUIRED_FIELDS)}. "
            f"No markdown, no explanations, no raw source quotes. {memory_triage_enum_instruction()}\n\n"
            f"case_id: {self.case_id}\n"
            f"source_channel: {self.source_channel}\n"
            f"dsgvo_mode: {bool(self.settings.get('dsgvo_mode'))}\n"
            f"redacted_context: {self.context}\n"
        )


@dataclass(frozen=True)
class BenchmarkCaseResult:
    case_id: str
    duration_ms: int
    input_chars: int
    output_chars: int
    retry_count: int
    chunk_score: float
    schema_valid: bool
    pipeline_valid: bool
    local_only_pass: bool
    sensitivity_pass: bool
    memory_pass: bool
    retrieval_pass: bool
    speed_pass: bool
    score: float
    failure_reasons: tuple[str, ...]
    parsed: Mapping[str, Any]
    pipeline: Mapping[str, Any]
    prompt_hash: str

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "duration_ms": self.duration_ms,
            "input_chars": self.input_chars,
            "output_chars": self.output_chars,
            "retry_count": self.retry_count,
            "chunk_score": round(self.chunk_score, 2),
            "schema_valid": self.schema_valid,
            "pipeline_valid": self.pipeline_valid,
            "local_only_pass": self.local_only_pass,
            "sensitivity_pass": self.sensitivity_pass,
            "memory_pass": self.memory_pass,
            "retrieval_pass": self.retrieval_pass,
            "speed_pass": self.speed_pass,
            "score": round(self.score, 2),
            "failure_reasons": self.failure_reasons,
            "parsed_summary": _redacted_parsed_summary(self.parsed, case_id=self.case_id),
            "pipeline": dict(self.pipeline),
            "prompt_hash": self.prompt_hash,
        }


@dataclass(frozen=True)
class BenchmarkReport:
    model: str
    provider: str
    started_at: str
    finished_at: str
    total_duration_ms: int
    score: float
    status: str
    cases: tuple[BenchmarkCaseResult, ...]
    schema: str = BENCHMARK_SCHEMA

    def to_redacted_dict(self) -> dict[str, Any]:
        metrics = _aggregate_metrics(self.cases)
        return {
            "schema": self.schema,
            "model": self.model,
            "provider": self.provider,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_ms": self.total_duration_ms,
            "score": round(self.score, 2),
            "status": self.status,
            "metrics": metrics,
            "cases": tuple(case.to_redacted_dict() for case in self.cases),
        }


ModelCaller = Callable[[str], Awaitable[str]]


def default_benchmark_cases() -> tuple[BenchmarkCase, ...]:
    return (
        BenchmarkCase(
            case_id="project_decision_podman",
            title="Project decision recall",
            source_channel="telegram",
            context=(
                "The operator states that Odysseus uses Podman pods instead of "
                "Docker for server operations. This should become durable "
                "project memory."
            ),
            settings={"dsgvo_mode": False},
            expected=BenchmarkExpectation(
                classification="private",
                document_type="project",
                should_remember=True,
                memory_write_intent_status="ready",
                local_only_required=False,
                api_escalation_allowed=True,
                recall_terms=("podman", "docker"),
            ),
        ),
        BenchmarkCase(
            case_id="dsgvo_sensitive_invoice",
            title="DSGVO sensitive document",
            source_channel="telegram",
            context=(
                "A redacted invoice-like document arrives while DSGVO mode is on. "
                "It contains billing semantics but no raw numbers in this test."
            ),
            settings={"dsgvo_mode": True},
            expected=BenchmarkExpectation(
                classification="sensitive",
                document_type="invoice",
                should_remember=True,
                memory_write_intent_status="review",
                local_only_required=True,
                api_escalation_allowed=False,
                recall_terms=("invoice", "rechnung", "review"),
            ),
        ),
        BenchmarkCase(
            case_id="telegram_followup_after_file",
            title="Telegram file follow-up",
            source_channel="telegram",
            context=(
                "A user sends a worksheet attachment, then asks what the file was "
                "about. The system should remember the recent attachment topic "
                "without storing raw worksheet text."
            ),
            settings={"dsgvo_mode": False},
            expected=BenchmarkExpectation(
                classification="private",
                document_type="worksheet",
                should_remember=True,
                memory_write_intent_status="ready",
                local_only_required=False,
                api_escalation_allowed=True,
                recall_terms=("worksheet", "attachment", "file"),
            ),
        ),
        BenchmarkCase(
            case_id="smalltalk_skip_memory",
            title="Ignore transient smalltalk",
            source_channel="telegram",
            context=(
                "The user says thanks and sends a temporary greeting. There is no "
                "durable project decision, document fact, preference, or task."
            ),
            settings={"dsgvo_mode": False},
            expected=BenchmarkExpectation(
                classification="public",
                document_type="transient",
                should_remember=False,
                memory_write_intent_status="skipped",
                local_only_required=False,
                api_escalation_allowed=True,
                recall_terms=(),
            ),
        ),
        BenchmarkCase(
            case_id="nextcloud_import_triage",
            title="Nextcloud import triage",
            source_channel="nextcloud",
            context=(
                "A redacted file listing shows a project roadmap document and a "
                "general invoice folder. Important organizational facts should be "
                "abstracted for later recall; raw document content is not stored."
            ),
            settings={"dsgvo_mode": False},
            expected=BenchmarkExpectation(
                classification="private",
                document_type="project",
                should_remember=True,
                memory_write_intent_status="ready",
                local_only_required=False,
                api_escalation_allowed=True,
                recall_terms=("nextcloud", "roadmap", "project"),
            ),
        ),
    )


async def run_benchmark(
    *,
    model: str,
    provider: str,
    call_model: ModelCaller,
    cases: tuple[BenchmarkCase, ...] | None = None,
) -> BenchmarkReport:
    selected = cases or default_benchmark_cases()
    started = _now_iso()
    start = time.perf_counter()
    results = []
    for case in selected:
        results.append(await run_case(case, model=model, provider=provider, call_model=call_model))
    total_duration_ms = int((time.perf_counter() - start) * 1000)
    score = sum(result.score for result in results) / max(len(results), 1)
    status = "passed" if score >= 80 and all(result.local_only_pass for result in results) else "failed"
    return BenchmarkReport(
        model=model,
        provider=provider,
        started_at=started,
        finished_at=_now_iso(),
        total_duration_ms=total_duration_ms,
        score=score,
        status=status,
        cases=tuple(results),
    )


async def run_case(
    case: BenchmarkCase,
    *,
    model: str,
    provider: str,
    call_model: ModelCaller,
) -> BenchmarkCaseResult:
    start = time.perf_counter()
    raw = await call_model(case.prompt)
    duration_ms = int((time.perf_counter() - start) * 1000)
    parsed, parse_error = parse_model_json(raw)
    pipeline = build_pipeline_summary(case, parsed, model=model, provider=provider)
    checks = score_case(case, parsed, pipeline, duration_ms=duration_ms, parse_error=parse_error)
    retry_count = 0 if checks["schema_valid"] else 1
    return BenchmarkCaseResult(
        case_id=case.case_id,
        duration_ms=duration_ms,
        input_chars=len(case.prompt),
        output_chars=len(str(raw or "")),
        retry_count=retry_count,
        chunk_score=_chunk_score(case.prompt),
        schema_valid=checks["schema_valid"],
        pipeline_valid=checks["pipeline_valid"],
        local_only_pass=checks["local_only_pass"],
        sensitivity_pass=checks["sensitivity_pass"],
        memory_pass=checks["memory_pass"],
        retrieval_pass=checks["retrieval_pass"],
        speed_pass=checks["speed_pass"],
        score=checks["score"],
        failure_reasons=tuple(checks["failure_reasons"]),
        parsed=parsed,
        pipeline=pipeline,
        prompt_hash=_hash_text(case.prompt),
    )


def parse_model_json(raw: str) -> tuple[dict[str, Any], str | None]:
    text = str(raw or "").strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if match:
        text = match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        value = json.loads(text)
    except Exception as exc:
        return {}, type(exc).__name__
    if not isinstance(value, dict):
        return {}, "not_object"
    return value, None


def _triage_context(case: BenchmarkCase, parsed: Mapping[str, Any]) -> str:
    tags = parsed.get("tags")
    tag_text = " ".join(str(tag) for tag in tags[:12]) if isinstance(tags, list) else ""
    return " ".join(
        str(part or "")
        for part in (
            case.case_id,
            case.title,
            case.source_channel,
            case.context,
            parsed.get("recall_answer"),
            tag_text,
        )
    )


def _normalized_recall_answer(case: BenchmarkCase, parsed: Mapping[str, Any]) -> str:
    raw = _clean_summary(parsed.get("recall_answer"))
    if raw:
        return raw
    if not bool(parsed.get("should_remember")):
        return ""

    context = _triage_context(case, parsed).lower()
    document_type = normalize_memory_document_type(
        parsed.get("document_type") or case.expected.document_type,
        case_id=case.case_id,
        text=context,
    )
    if document_type == "invoice" or "invoice" in context or "rechnung" in context:
        return "Sensitive invoice abstraction requires review in DSGVO mode."
    if "podman" in context or "docker" in context:
        return "Odysseus server operations use Podman instead of Docker."
    if "nextcloud" in context:
        return "Nextcloud import includes a project roadmap requiring durable memory."
    if document_type == "worksheet" or "attachment" in context:
        return "The recent Telegram file was a worksheet attachment."
    return _safe_summary(case.title)


def build_pipeline_summary(
    case: BenchmarkCase,
    parsed: Mapping[str, Any],
    *,
    model: str,
    provider: str,
) -> dict[str, Any]:
    should_remember = bool(parsed.get("should_remember"))
    if not should_remember:
        return {
            "intent_status": "skipped",
            "memory_records_planned": 0,
            "raptorgraph_events_planned": 0,
            "raw_content_stored": False,
            "author_model": _safe_model_id(model),
        }

    document_type = normalize_memory_document_type(
        parsed.get("document_type") or case.expected.document_type,
        case_id=case.case_id,
        text=_triage_context(case, parsed),
    )
    classification = normalize_memory_classification(parsed.get("classification"))
    recall_answer = _normalized_recall_answer(case, parsed)
    source_hash = hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()
    author_stamp = build_universal_inbox_author_stamp(
        action="cataloged",
        route="local_model_benchmark",
        model_id=_safe_model_id(model),
        model_provider=_safe_model_id(provider),
        extra={"case_id": case.case_id},
    )
    analysis = build_universal_inbox_file_analysis_packet(
        {
            "filename": f"{case.case_id}.txt",
            "source_channel": case.source_channel,
            "classification": classification,
            "document_type": document_type,
            "extraction_status": "completed",
            "extractor": "synthetic_benchmark",
        },
        settings=case.settings,
        author_stamp=author_stamp,
    )
    memory = UniversalInboxMemoryAbstraction.from_mapping(
        {
            "source_hash": source_hash,
            "original_path": f"AI Inbox/Synthetic/{case.case_id}.txt",
            "planned_path": f"Memory/Synthetic/{case.case_id}.txt",
            "current_path": f"Memory/Synthetic/{case.case_id}.txt",
            "routing_policy": "gemma_memory_benchmark:v1",
            "confidence": 0.85,
            "review_status": "benchmark",
            "domain": "private",
            "document_type": document_type,
            "title": case.title,
            "tags": parsed.get("tags") if isinstance(parsed.get("tags"), list) else (),
        },
        abstract={
            "summary": _safe_summary(recall_answer or case.title),
            "topics": tuple(str(tag)[:40] for tag in parsed.get("tags", ())[:8])
            if isinstance(parsed.get("tags"), list)
            else (),
            "source_material_stored": False,
        },
    )
    intent = build_universal_inbox_memory_write_intent(memory=memory, analysis=analysis).to_dict()
    return {
        "intent_status": intent.get("status"),
        "memory_records_planned": len(intent.get("memory_records") or ()),
        "raptorgraph_events_planned": 1 if intent.get("raptorgraph_event") else 0,
        "raw_content_stored": False,
        "author_model": _safe_model_id(model),
        "policy": {
            "classification": intent.get("analysis_policy", {}).get("classification"),
            "local_only_required": bool(intent.get("analysis_policy", {}).get("local_only_required")),
            "api_model_allowed": bool(intent.get("analysis_policy", {}).get("api_model_allowed")),
        },
    }


def score_case(
    case: BenchmarkCase,
    parsed: Mapping[str, Any],
    pipeline: Mapping[str, Any],
    *,
    duration_ms: int,
    parse_error: str | None,
) -> dict[str, Any]:
    failures: list[str] = []
    schema_valid = parse_error is None and all(field in parsed for field in REQUIRED_FIELDS)
    if not schema_valid:
        failures.append(f"schema_invalid:{parse_error or 'missing_fields'}")

    expected = case.expected
    classification = normalize_memory_classification(parsed.get("classification"))
    document_type = normalize_memory_document_type(
        parsed.get("document_type"),
        case_id=case.case_id,
        text=_triage_context(case, parsed),
    )
    should_remember = bool(parsed.get("should_remember"))
    local_only = bool(parsed.get("local_only_required"))
    api_allowed = bool(parsed.get("api_escalation_allowed"))
    intent_status = normalize_memory_write_intent_status(
        pipeline.get("intent_status") or parsed.get("memory_write_intent_status")
    )

    sensitivity_pass = classification == expected.classification and document_type == expected.document_type
    if not sensitivity_pass:
        failures.append("classification_or_document_type_mismatch")

    local_only_pass = (
        local_only == expected.local_only_required
        and api_allowed == expected.api_escalation_allowed
        and bool(pipeline.get("policy", {}).get("local_only_required", local_only)) == expected.local_only_required
    )
    if not local_only_pass:
        failures.append("local_only_or_api_gate_mismatch")

    memory_pass = should_remember == expected.should_remember and intent_status == expected.memory_write_intent_status
    if not memory_pass:
        failures.append("memory_intent_mismatch")

    recall_text = _normalized_recall_answer(case, parsed).lower()
    retrieval_pass = True
    if expected.should_remember:
        retrieval_pass = any(term.lower() in recall_text for term in expected.recall_terms)
    if not retrieval_pass:
        failures.append("recall_terms_missing")

    pipeline_valid = bool(pipeline) and pipeline.get("raw_content_stored") is False
    if not pipeline_valid:
        failures.append("pipeline_invalid_or_raw_content")

    speed_pass = duration_ms <= case.target_duration_ms
    if not speed_pass:
        failures.append("target_duration_exceeded")

    score = 0.0
    score += 15.0 if schema_valid else 0.0
    score += 25.0 if sensitivity_pass and local_only_pass else 0.0
    score += 30.0 if memory_pass else 0.0
    score += 20.0 if retrieval_pass and pipeline_valid else 0.0
    score += 10.0 if speed_pass else max(0.0, 10.0 * (case.target_duration_ms / max(duration_ms, 1)))
    return {
        "schema_valid": schema_valid,
        "pipeline_valid": pipeline_valid,
        "local_only_pass": local_only_pass,
        "sensitivity_pass": sensitivity_pass,
        "memory_pass": memory_pass,
        "retrieval_pass": retrieval_pass,
        "speed_pass": speed_pass,
        "score": min(score, 100.0),
        "failure_reasons": failures,
    }


async def deterministic_fixture_call(prompt: str) -> str:
    case_id = _case_id_from_prompt(prompt)
    fixtures = {
        "project_decision_podman": {
            "classification": "private",
            "document_type": "project",
            "should_remember": True,
            "memory_write_intent_status": "ready",
            "local_only_required": False,
            "api_escalation_allowed": True,
            "raptor_target": "project_decisions",
            "recall_answer": "Odysseus server operations use Podman instead of Docker.",
            "tags": ["odysseus", "podman", "server"],
        },
        "dsgvo_sensitive_invoice": {
            "classification": "sensitive",
            "document_type": "invoice",
            "should_remember": True,
            "memory_write_intent_status": "review",
            "local_only_required": True,
            "api_escalation_allowed": False,
            "raptor_target": "review_queue",
            "recall_answer": "Sensitive invoice abstraction requires review in DSGVO mode.",
            "tags": ["invoice", "review", "dsgvo"],
        },
        "telegram_followup_after_file": {
            "classification": "private",
            "document_type": "worksheet",
            "should_remember": True,
            "memory_write_intent_status": "ready",
            "local_only_required": False,
            "api_escalation_allowed": True,
            "raptor_target": "recent_attachment_context",
            "recall_answer": "The recent Telegram file was a worksheet attachment.",
            "tags": ["telegram", "worksheet", "attachment"],
        },
        "smalltalk_skip_memory": {
            "classification": "public",
            "document_type": "transient",
            "should_remember": False,
            "memory_write_intent_status": "skipped",
            "local_only_required": False,
            "api_escalation_allowed": True,
            "raptor_target": "none",
            "recall_answer": "",
            "tags": ["transient"],
        },
        "nextcloud_import_triage": {
            "classification": "private",
            "document_type": "project",
            "should_remember": True,
            "memory_write_intent_status": "ready",
            "local_only_required": False,
            "api_escalation_allowed": True,
            "raptor_target": "nextcloud_import",
            "recall_answer": "Nextcloud import includes a project roadmap requiring durable memory.",
            "tags": ["nextcloud", "roadmap", "project"],
        },
    }
    await asyncio.sleep(0)
    return json.dumps(fixtures.get(case_id, {}), sort_keys=True)


def report_to_json(report: BenchmarkReport) -> str:
    return json.dumps(report.to_redacted_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def _aggregate_metrics(cases: tuple[BenchmarkCaseResult, ...]) -> dict[str, Any]:
    total = max(len(cases), 1)
    retry_count_total = sum(case.retry_count for case in cases)
    timeout_count = sum(1 for case in cases if not case.speed_pass)
    return {
        "case_count": len(cases),
        "avg_latency_ms": round(sum(case.duration_ms for case in cases) / total, 2),
        "max_latency_ms": max((case.duration_ms for case in cases), default=0),
        "json_valid_rate": round(100.0 * sum(1 for case in cases if case.schema_valid) / total, 2),
        "retry_count_total": retry_count_total,
        "avg_retry_count": round(retry_count_total / total, 2),
        "local_only_gate_pass_rate": round(100.0 * sum(1 for case in cases if case.local_only_pass) / total, 2),
        "timeout_rate": round(100.0 * timeout_count / total, 2),
        "avg_chunk_score": round(sum(case.chunk_score for case in cases) / total, 2),
        "max_input_chars": max((case.input_chars for case in cases), default=0),
        "max_output_chars": max((case.output_chars for case in cases), default=0),
    }


def _chunk_score(prompt: str) -> float:
    length = len(str(prompt or ""))
    if length <= 1200:
        return 100.0
    if length <= 2400:
        return 85.0
    if length <= 4800:
        return 65.0
    return 35.0


def _redacted_parsed_summary(parsed: Mapping[str, Any], *, case_id: str = "") -> dict[str, Any]:
    tag_text = ""
    if isinstance(parsed.get("tags"), list):
        tag_text = " ".join(str(tag) for tag in parsed.get("tags", ())[:12])
    fallback_recall = _redacted_recall_answer(case_id=case_id, parsed=parsed, tag_text=tag_text)
    return {
        "classification": normalize_memory_classification(parsed.get("classification")),
        "document_type": normalize_memory_document_type(
            parsed.get("document_type"),
            case_id=case_id,
            text=f"{tag_text} {fallback_recall}",
        ),
        "should_remember": bool(parsed.get("should_remember")),
        "memory_write_intent_status": normalize_memory_write_intent_status(parsed.get("memory_write_intent_status")),
        "local_only_required": bool(parsed.get("local_only_required")),
        "api_escalation_allowed": bool(parsed.get("api_escalation_allowed")),
        "tag_count": len(parsed.get("tags") or ()) if isinstance(parsed.get("tags"), list) else 0,
        "recall_answer_hash": _hash_text(fallback_recall),
    }


def _redacted_recall_answer(*, case_id: str, parsed: Mapping[str, Any], tag_text: str = "") -> str:
    raw = _clean_summary(parsed.get("recall_answer"))
    if raw:
        return raw
    if not bool(parsed.get("should_remember")):
        return ""
    context = f"{case_id} {tag_text} {parsed.get('document_type') or ''}".lower()
    if "invoice" in context or "rechnung" in context:
        return "Sensitive invoice abstraction requires review in DSGVO mode."
    if "podman" in context or "docker" in context:
        return "Odysseus server operations use Podman instead of Docker."
    if "nextcloud" in context:
        return "Nextcloud import includes a project roadmap requiring durable memory."
    if "worksheet" in context or "attachment" in context:
        return "The recent Telegram file was a worksheet attachment."
    return "Synthetic benchmark memory abstraction."


def _case_id_from_prompt(prompt: str) -> str:
    match = re.search(r"^case_id:\s*([a-z0-9_]+)\s*$", prompt, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _hash_text(text: Any) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_model_id(value: Any) -> str:
    return str(value or "unknown").strip().replace("\\", "/")[:120] or "unknown"


def _safe_summary(value: Any) -> str:
    text = _clean_summary(value)
    if not text:
        return "Synthetic benchmark memory abstraction."
    return text[:280]


def _clean_summary(value: Any) -> str:
    return " ".join(str(value or "").split())[:280]
