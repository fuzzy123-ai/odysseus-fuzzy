"""Strict local Faster-Whisper adapter for immutable transcription evidence.

This boundary deliberately accepts neither model aliases nor remote model
references. A deployment provisions the frozen model into a resolved local
directory before the application starts; this module never downloads a model.
Audio reaches the model only through the store's verified binary handle.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path
from typing import Any, BinaryIO, Callable, Protocol

from src.transcription_contracts import AudioArtifact, ContractError, RawTranscriptSegment


FASTER_WHISPER_VERSION = "1.2.1"
FASTER_WHISPER_MODEL_ID = "Systran/faster-whisper-small"
FASTER_WHISPER_MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
_REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
_RECEIPT_FILENAME = "odysseus-faster-whisper-receipt.json"
_MODEL_BIN_SHA256 = "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"
_LANGUAGE = "de"
_BEAM_SIZE = 5
_MAX_SEGMENTS = 256


class LocalTranscriptionError(RuntimeError):
    """Content-free error raised by the local ASR boundary."""


class VerifiedAudioStore(Protocol):
    def open_verified_audio(self, owner_id: str, artifact_id: str) -> BinaryIO: ...


ModelFactory = Callable[..., Any]


def _is_reparse_point(mode: int, attributes: int) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(mode) or bool(attributes & reparse)


def _strict_json_object(value: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError
            result[key] = item
        return result

    if not 1 <= len(value) <= 4096:
        raise ValueError
    decoded = json.loads(value.decode("utf-8"), object_pairs_hook=reject_duplicates)
    if not isinstance(decoded, dict):
        raise ValueError
    return decoded


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _model_tree(directory: Path, *, require_receipt: bool = True) -> tuple[str, int, dict[str, str]]:
    """Return the frozen four-file runtime tree receipt digest.

    The deployment directory is intentionally materialised rather than a
    Hugging Face cache tree. Extra files, nested directories, symlinks and
    junctions are all rejected so the receipt names one small, exact surface.
    """
    entries: list[tuple[str, Path]] = []
    names = {item.name for item in directory.iterdir()}
    expected_names = set(_REQUIRED_MODEL_FILES) | ({_RECEIPT_FILENAME} if require_receipt else set())
    if names != expected_names:
        raise ValueError
    for filename in _REQUIRED_MODEL_FILES:
        item = directory / filename
        status = os.lstat(item)
        if _is_reparse_point(status.st_mode, getattr(status, "st_file_attributes", 0)) or not stat.S_ISREG(status.st_mode):
            raise ValueError
        entries.append((filename, item))
    digest = hashlib.sha256()
    hashes: dict[str, str] = {}
    for relative, item in sorted(entries):
        item_digest = _sha256_file(item)
        hashes[relative] = item_digest
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(item_digest.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), len(entries), hashes


def _safe_absolute_directory(value: str | Path) -> Path:
    """Return one existing, canonical, non-reparse-point local model directory."""
    if not isinstance(value, (str, Path)):
        raise LocalTranscriptionError("invalid local model directory")
    try:
        candidate = Path(value)
        if not candidate.is_absolute():
            raise ValueError
        resolved = candidate.resolve(strict=True)
        # Reject path spelling with aliases, '..', symlinks, junctions, or a
        # branch/cache indirection. The runtime receives the canonical leaf.
        if candidate != resolved or not resolved.is_dir():
            raise ValueError
        current = Path(candidate.anchor)
        for part in candidate.parts[1:]:
            current = current / part
            status = os.lstat(current)
            if _is_reparse_point(status.st_mode, getattr(status, "st_file_attributes", 0)):
                raise ValueError
        for filename in _REQUIRED_MODEL_FILES:
            item = resolved / filename
            status = os.lstat(item)
            if _is_reparse_point(status.st_mode, getattr(status, "st_file_attributes", 0)) or not stat.S_ISREG(status.st_mode) or status.st_size <= 0:
                raise ValueError
        receipt_path = resolved / _RECEIPT_FILENAME
        receipt_status = os.lstat(receipt_path)
        if _is_reparse_point(receipt_status.st_mode, getattr(receipt_status, "st_file_attributes", 0)) or not stat.S_ISREG(receipt_status.st_mode) or not 1 <= receipt_status.st_size <= 4096:
            raise ValueError
        with receipt_path.open("rb") as receipt_handle:
            receipt_bytes = receipt_handle.read(4097)
        if len(receipt_bytes) != receipt_status.st_size or len(receipt_bytes) > 4096:
            raise ValueError
        receipt = _strict_json_object(receipt_bytes)
        required = {"schema_version", "model_id", "revision", "model_bin_sha256", "tree_sha256", "file_count"}
        if set(receipt) != required or not isinstance(receipt["schema_version"], int) or isinstance(receipt["schema_version"], bool) or receipt["schema_version"] != 1 or receipt["model_id"] != FASTER_WHISPER_MODEL_ID or receipt["revision"] != FASTER_WHISPER_MODEL_REVISION or receipt["model_bin_sha256"] != _MODEL_BIN_SHA256:
            raise ValueError
        if not isinstance(receipt["tree_sha256"], str) or len(receipt["tree_sha256"]) != 64 or any(char not in "0123456789abcdef" for char in receipt["tree_sha256"]):
            raise ValueError
        if not isinstance(receipt["file_count"], int) or isinstance(receipt["file_count"], bool) or receipt["file_count"] != len(_REQUIRED_MODEL_FILES):
            raise ValueError
        tree_sha256, file_count, hashes = _model_tree(resolved)
        if file_count != receipt["file_count"] or tree_sha256 != receipt["tree_sha256"]:
            raise ValueError
        if hashes.get("model.bin") != _MODEL_BIN_SHA256:
            raise ValueError
    except (OSError, RuntimeError, TypeError, ValueError):
        raise LocalTranscriptionError("invalid local model directory") from None
    return resolved


def _finite_metric(value: Any, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LocalTranscriptionError("invalid local transcription evidence")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise LocalTranscriptionError("invalid local transcription evidence")
    return number


def _milliseconds(value: Any) -> int:
    seconds = _finite_metric(value, minimum=0.0, maximum=86_400.0)
    milliseconds = round(seconds * 1000)
    if not 0 <= milliseconds <= 86_400_000:
        raise LocalTranscriptionError("invalid local transcription evidence")
    return milliseconds


def _field(value: object, name: str) -> Any:
    try:
        return getattr(value, name)
    except Exception:
        raise LocalTranscriptionError("invalid local transcription evidence") from None


class LocalFasterWhisperAdapter:
    """Run the frozen German CPU/INT8 profile without provider fallbacks."""

    def __init__(self, model_directory: str | Path, *, model_factory: ModelFactory | None = None) -> None:
        self._model_directory = _safe_absolute_directory(model_directory)
        if model_factory is not None and not callable(model_factory):
            raise LocalTranscriptionError("invalid local transcription model")
        self._model_factory = model_factory
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        # The constructor already fully hashed and canonicalised this directory.
        # Later mutation by the same service identity is outside this boundary;
        # re-hashing model.bin would turn ordinary ASR into a repeated 486 MB
        # integrity scan.
        model_directory = self._model_directory
        factory = self._model_factory
        if factory is None:
            try:
                from faster_whisper import WhisperModel
            except Exception:
                raise LocalTranscriptionError("local transcription unavailable") from None
            factory = WhisperModel
        try:
            model = factory(
                str(model_directory),
                device="cpu",
                compute_type="int8",
                num_workers=1,
                local_files_only=True,
            )
        except Exception:
            raise LocalTranscriptionError("local transcription unavailable") from None
        if model is None:
            raise LocalTranscriptionError("local transcription unavailable")
        self._model = model
        return model

    @staticmethod
    def _segment_id(artifact: AudioArtifact, ordinal: int, start_ms: int, end_ms: int, text: str) -> str:
        # The id is deterministic yet opaque; it never contains transcript text
        # or an owner/artifact identifier in plaintext.
        material = "\x1f".join((artifact.source_sha256, str(ordinal), str(start_ms), str(end_ms), text))
        return "asr_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]

    def _segments_from_result(self, artifact: AudioArtifact, raw_segments: Any, info: Any) -> tuple[RawTranscriptSegment, ...]:
        materialized: list[Any] = []
        try:
            language = _field(info, "language")
            if language != _LANGUAGE:
                raise LocalTranscriptionError("invalid local transcription evidence")
            language_probability = _finite_metric(_field(info, "language_probability"), minimum=0.0, maximum=1.0)
            try:
                iterator = iter(raw_segments)
            except TypeError:
                raise LocalTranscriptionError("invalid local transcription evidence") from None
            for item in iterator:
                materialized.append(item)
                if len(materialized) > _MAX_SEGMENTS:
                    raise LocalTranscriptionError("invalid local transcription evidence")
        except LocalTranscriptionError:
            raise
        except Exception:
            raise LocalTranscriptionError("local transcription failed") from None
        finally:
            try:
                closer = getattr(raw_segments, "close", None)
            except Exception:
                raise LocalTranscriptionError("local transcription failed") from None
            if callable(closer):
                try:
                    closer()
                except Exception:
                    raise LocalTranscriptionError("local transcription failed") from None

        if not materialized:
            raise LocalTranscriptionError("invalid local transcription evidence")
        result: list[RawTranscriptSegment] = []
        previous_end = -1
        for ordinal, item in enumerate(materialized):
            start_ms = _milliseconds(_field(item, "start"))
            end_ms = _milliseconds(_field(item, "end"))
            text = _field(item, "text")
            if not isinstance(text, str) or not text.strip():
                raise LocalTranscriptionError("invalid local transcription evidence")
            if start_ms >= end_ms or start_ms < previous_end:
                raise LocalTranscriptionError("invalid local transcription evidence")
            try:
                segment = RawTranscriptSegment(
                    segment_id=self._segment_id(artifact, ordinal, start_ms, end_ms, text),
                    artifact_id=artifact.artifact_id,
                    owner_id=artifact.owner_id,
                    source_sha256=artifact.source_sha256,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                    ordinal=ordinal,
                    avg_logprob=_finite_metric(_field(item, "avg_logprob"), minimum=-100.0, maximum=10.0),
                    no_speech_prob=_finite_metric(_field(item, "no_speech_prob"), minimum=0.0, maximum=1.0),
                    compression_ratio=_finite_metric(_field(item, "compression_ratio"), minimum=0.01, maximum=100.0),
                    language=language,
                    language_probability=language_probability,
                )
            except ContractError:
                raise LocalTranscriptionError("invalid local transcription evidence") from None
            result.append(segment)
            previous_end = end_ms
        return tuple(result)

    def transcribe(self, store: VerifiedAudioStore, artifact: AudioArtifact) -> tuple[RawTranscriptSegment, ...]:
        """Create immutable raw segments from one store-verified audio handle."""
        if not isinstance(artifact, AudioArtifact) or not hasattr(store, "open_verified_audio"):
            raise LocalTranscriptionError("invalid local transcription input")
        try:
            with store.open_verified_audio(artifact.owner_id, artifact.artifact_id) as audio:
                # Authorization and verified-handle acquisition are deliberately
                # ahead of expensive model construction.
                model = self._load_model()
                output = model.transcribe(
                    audio,
                    task="transcribe",
                    language=None,
                    beam_size=_BEAM_SIZE,
                    temperature=0.0,
                    vad_filter=True,
                    condition_on_previous_text=False,
                    word_timestamps=False,
                    initial_prompt=None,
                    hotwords=None,
                )
                if not isinstance(output, tuple) or len(output) != 2:
                    raise LocalTranscriptionError("invalid local transcription evidence")
                return self._segments_from_result(artifact, output[0], output[1])
        except LocalTranscriptionError:
            raise
        except Exception:
            raise LocalTranscriptionError("local transcription failed") from None
