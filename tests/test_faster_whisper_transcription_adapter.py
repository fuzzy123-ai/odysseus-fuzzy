"""Fixture-only tests for the frozen local Faster-Whisper boundary."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.transcription.faster_whisper_adapter as adapter_module
from services.transcription.faster_whisper_adapter import (
    FASTER_WHISPER_MODEL_ID,
    FASTER_WHISPER_MODEL_REVISION,
    LocalFasterWhisperAdapter,
    LocalTranscriptionError,
)
from src.transcription_contracts import AudioArtifact


OWNER = "owner_0123456789abcdef"
SOURCE = b"fixture-audio"


@pytest.fixture(autouse=True)
def _fixture_model_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "_MODEL_BIN_SHA256", sha256(b"fixture").hexdigest())


def _artifact() -> AudioArtifact:
    return AudioArtifact(
        artifact_id="artifact_a",
        owner_id=OWNER,
        source_sha256=sha256(SOURCE).hexdigest(),
        byte_count=len(SOURCE),
        media_type="audio/wav",
        storage_locator="blobs/ar/artifact_a.audio",
        created_at="2026-07-26T20:00:00Z",
    )


def _model_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "frozen-small"
    directory.mkdir()
    for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        (directory / filename).write_bytes(b"fixture")
    tree_sha256, file_count, _ = adapter_module._model_tree(directory, require_receipt=False)
    (directory / "odysseus-faster-whisper-receipt.json").write_text(
        '{"schema_version":1,"model_id":"Systran/faster-whisper-small","revision":"536b0662742c02347bc0e980a01041f333bce120","model_bin_sha256":"'
        + adapter_module._MODEL_BIN_SHA256
        + '","tree_sha256":"'
        + tree_sha256
        + '","file_count":'
        + str(file_count)
        + '}',
        encoding="utf-8",
    )
    return directory.resolve()


class _Store:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    @contextmanager
    def open_verified_audio(self, owner_id: str, artifact_id: str):
        self.calls.append((owner_id, artifact_id))
        handle = BytesIO(SOURCE)
        try:
            yield handle
        finally:
            handle.close()
            self.closed = True


class _ClosableSegments:
    def __init__(self, rows: list[object], *, failing: bool = False) -> None:
        self.rows = rows
        self.failing = failing
        self.closed = False

    def __iter__(self):
        for row in self.rows:
            yield row
        if self.failing:
            raise RuntimeError("private transcript should never escape")

    def close(self) -> None:
        self.closed = True


class _Model:
    def __init__(self, segments: _ClosableSegments, info: object) -> None:
        self.segments = segments
        self.info = info
        self.calls: list[tuple[object, dict[str, object]]] = []

    def transcribe(self, audio: object, **kwargs: object):
        self.audio_bytes = audio.read()  # type: ignore[union-attr]
        audio.seek(0)  # type: ignore[union-attr]
        self.calls.append((audio, kwargs))
        return self.segments, self.info


def _row(start: float = 0.0, end: float = 1.25, text: str = "Guten Morgen") -> object:
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        avg_logprob=-0.25,
        no_speech_prob=0.01,
        compression_ratio=1.2,
    )


def test_fixture_model_receives_only_local_cpu_int8_profile_and_maps_evidence(tmp_path: Path) -> None:
    segments = _ClosableSegments([_row(), _row(1.25, 2.0, "Wir beginnen jetzt")])
    model = _Model(segments, SimpleNamespace(language="de", language_probability=0.99))
    construction: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def factory(*args: object, **kwargs: object) -> _Model:
        construction.append((args, kwargs))
        return model

    artifact = _artifact()
    store = _Store()
    adapter = LocalFasterWhisperAdapter(_model_directory(tmp_path), model_factory=factory)
    result = adapter.transcribe(store, artifact)

    assert (FASTER_WHISPER_MODEL_ID, FASTER_WHISPER_MODEL_REVISION) == (
        "Systran/faster-whisper-small",
        "536b0662742c02347bc0e980a01041f333bce120",
    )
    assert construction[0][1] == {
        "device": "cpu",
        "compute_type": "int8",
        "num_workers": 1,
        "local_files_only": True,
    }
    assert model.audio_bytes == SOURCE
    assert model.calls[0][1] == {
        "task": "transcribe",
        "language": None,
        "beam_size": 5,
        "temperature": 0.0,
        "vad_filter": True,
        "condition_on_previous_text": False,
        "word_timestamps": False,
        "initial_prompt": None,
        "hotwords": None,
    }
    assert store.calls == [(OWNER, artifact.artifact_id)] and store.closed and segments.closed
    assert [(item.ordinal, item.start_ms, item.end_ms, item.text) for item in result] == [
        (0, 0, 1250, "Guten Morgen"),
        (1, 1250, 2000, "Wir beginnen jetzt"),
    ]
    assert all(item.artifact_id == artifact.artifact_id and item.owner_id == OWNER for item in result)
    assert all(item.source_sha256 == artifact.source_sha256 and item.language == "de" for item in result)
    assert result[0].avg_logprob == -0.25 and result[0].no_speech_prob == 0.01
    assert result[0].compression_ratio == 1.2 and result[0].language_probability == 0.99
    assert result[0].segment_id == adapter._segment_id(artifact, 0, 0, 1250, "Guten Morgen")
    adapter.transcribe(store, artifact)
    assert len(construction) == 1


def test_model_directory_is_validated_once_per_adapter_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = _model_directory(tmp_path)
    original = adapter_module._safe_absolute_directory
    calls: list[object] = []

    def counted(value: object) -> Path:
        calls.append(value)
        return original(value)  # type: ignore[arg-type]

    model = _Model(_ClosableSegments([_row()]), SimpleNamespace(language="de", language_probability=0.5))
    monkeypatch.setattr(adapter_module, "_safe_absolute_directory", counted)
    adapter = LocalFasterWhisperAdapter(directory, model_factory=lambda *args, **kwargs: model)
    adapter.transcribe(_Store(), _artifact())
    adapter.transcribe(_Store(), _artifact())
    assert calls == [directory]


def test_rejecting_store_does_not_construct_a_model(tmp_path: Path) -> None:
    calls: list[bool] = []

    def factory(*args: object, **kwargs: object) -> object:
        calls.append(True)
        return object()

    class RejectingStore:
        def open_verified_audio(self, owner_id: str, artifact_id: str):
            raise RuntimeError("owner or audio detail must not escape")

    adapter = LocalFasterWhisperAdapter(_model_directory(tmp_path), model_factory=factory)
    with pytest.raises(LocalTranscriptionError, match="^local transcription failed$"):
        adapter.transcribe(RejectingStore(), _artifact())
    assert not calls


def test_none_factory_result_is_not_cached(tmp_path: Path) -> None:
    adapter = LocalFasterWhisperAdapter(_model_directory(tmp_path), model_factory=lambda *args, **kwargs: None)
    with pytest.raises(LocalTranscriptionError, match="^local transcription unavailable$"):
        adapter.transcribe(_Store(), _artifact())


@pytest.mark.parametrize("bad", ["relative-model", "https://models.example/small"])
def test_model_directory_must_be_absolute_local_and_complete(tmp_path: Path, bad: str) -> None:
    with pytest.raises(LocalTranscriptionError, match="^invalid local model directory$"):
        LocalFasterWhisperAdapter(bad)
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    with pytest.raises(LocalTranscriptionError, match="^invalid local model directory$"):
        LocalFasterWhisperAdapter(incomplete)


def test_model_directory_rejects_symlink_or_alias_path(tmp_path: Path) -> None:
    target = _model_directory(tmp_path)
    link = tmp_path / "model-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this fixture filesystem")
    with pytest.raises(LocalTranscriptionError, match="^invalid local model directory$"):
        LocalFasterWhisperAdapter(link.absolute())


@pytest.mark.parametrize(
    "receipt",
    [
        "not-json",
        '{"schema_version":1,"schema_version":1}',
        '{"schema_version":1,"model_id":"other","revision":"536b0662742c02347bc0e980a01041f333bce120","model_bin_sha256":"' + "0" * 64 + '","tree_sha256":"' + "a" * 64 + '","file_count":4}',
    ],
)
def test_model_receipt_must_be_strict_and_frozen(tmp_path: Path, receipt: str) -> None:
    directory = _model_directory(tmp_path)
    (directory / "odysseus-faster-whisper-receipt.json").write_text(receipt, encoding="utf-8")
    with pytest.raises(LocalTranscriptionError, match="^invalid local model directory$"):
        LocalFasterWhisperAdapter(directory)


def test_model_receipt_detects_a_required_file_mutation(tmp_path: Path) -> None:
    directory = _model_directory(tmp_path)
    (directory / "tokenizer.json").write_bytes(b"tampered fixture")
    with pytest.raises(LocalTranscriptionError, match="^invalid local model directory$"):
        LocalFasterWhisperAdapter(directory)


def test_model_receipt_rejects_boolean_schema_version(tmp_path: Path) -> None:
    directory = _model_directory(tmp_path)
    receipt = (directory / "odysseus-faster-whisper-receipt.json").read_text(encoding="utf-8")
    (directory / "odysseus-faster-whisper-receipt.json").write_text(receipt.replace('"schema_version":1', '"schema_version":true'), encoding="utf-8")
    with pytest.raises(LocalTranscriptionError, match="^invalid local model directory$"):
        LocalFasterWhisperAdapter(directory)


def test_model_receipt_rejects_oversized_input_before_parsing(tmp_path: Path) -> None:
    directory = _model_directory(tmp_path)
    (directory / "odysseus-faster-whisper-receipt.json").write_text("x" * 4097, encoding="utf-8")
    with pytest.raises(LocalTranscriptionError, match="^invalid local model directory$"):
        LocalFasterWhisperAdapter(directory)


@pytest.mark.parametrize(
    "rows,info",
    [
        ([_row(0.0, 1.0), _row(0.5, 2.0)], SimpleNamespace(language="de", language_probability=0.5)),
        ([_row(text="   ")], SimpleNamespace(language="de", language_probability=0.5)),
        ([_row()], SimpleNamespace(language="en", language_probability=0.5)),
        ([_row()], SimpleNamespace(language="de", language_probability=float("nan"))),
        ([SimpleNamespace(start=0.0, end=1.0, text="Hallo", avg_logprob=-0.2, no_speech_prob=0.1, compression_ratio=float("inf"))], SimpleNamespace(language="de", language_probability=0.5)),
    ],
)
def test_invalid_or_overlapping_evidence_is_rejected_without_content(tmp_path: Path, rows: list[object], info: object) -> None:
    private = "PRIVATE AUDIO IN FEHLERMELDUNG"
    model = _Model(_ClosableSegments(rows), info)
    adapter = LocalFasterWhisperAdapter(_model_directory(tmp_path), model_factory=lambda *args, **kwargs: model)
    with pytest.raises(LocalTranscriptionError) as raised:
        adapter.transcribe(_Store(), _artifact())
    assert private not in str(raised.value)
    assert model.segments.closed


def test_generator_failure_is_closed_and_content_free(tmp_path: Path) -> None:
    segments = _ClosableSegments([_row()], failing=True)
    model = _Model(segments, SimpleNamespace(language="de", language_probability=0.5))
    adapter = LocalFasterWhisperAdapter(_model_directory(tmp_path), model_factory=lambda *args, **kwargs: model)
    with pytest.raises(LocalTranscriptionError, match="^local transcription failed$") as raised:
        adapter.transcribe(_Store(), _artifact())
    assert "private transcript" not in str(raised.value)
    assert segments.closed


def test_more_than_256_segments_is_rejected_and_closed(tmp_path: Path) -> None:
    segments = _ClosableSegments([_row(float(index), float(index + 1), "Text") for index in range(257)])
    model = _Model(segments, SimpleNamespace(language="de", language_probability=0.5))
    adapter = LocalFasterWhisperAdapter(_model_directory(tmp_path), model_factory=lambda *args, **kwargs: model)
    with pytest.raises(LocalTranscriptionError, match="^invalid local transcription evidence$"):
        adapter.transcribe(_Store(), _artifact())
    assert segments.closed


def test_hostile_generator_close_lookup_is_content_free(tmp_path: Path) -> None:
    class HostileSegments:
        def __iter__(self):
            return iter([_row()])

        @property
        def close(self):
            raise RuntimeError("private close detail")

    model = _Model(HostileSegments(), SimpleNamespace(language="de", language_probability=0.5))
    adapter = LocalFasterWhisperAdapter(_model_directory(tmp_path), model_factory=lambda *args, **kwargs: model)
    with pytest.raises(LocalTranscriptionError, match="^local transcription failed$") as raised:
        adapter.transcribe(_Store(), _artifact())
    assert "private close detail" not in str(raised.value)


def test_package_locks_and_single_docker_stt_install_path() -> None:
    root = Path(__file__).resolve().parents[1]
    optional = (root / "requirements-optional.txt").read_text(encoding="utf-8")
    stt_lock = (root / "requirements-stt.txt").read_text(encoding="utf-8")
    stt_lines = [line.strip() for line in stt_lock.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")

    assert "\nfaster-whisper\n" not in optional
    assert all("==" in line and not line.startswith(("-", ".")) for line in stt_lines)
    assert {"faster-whisper==1.2.1", "ctranslate2==4.8.1", "av==18.0.0"} <= set(stt_lines)
    assert dockerfile.count("pip install --no-cache-dir --only-binary=:all: -r requirements-stt.txt") == 1
    assert 'if [ "$INSTALL_STT" = "true" ]' in dockerfile
    assert "importlib.metadata" in dockerfile and "faster-whisper':'1.2.1" in dockerfile
    assert "python -m pip check" in dockerfile and "import faster_whisper, ctranslate2, av, onnxruntime" in dockerfile
    assert "importlib.util.find_spec('faster_whisper') is None" in dockerfile
    assert "pip install --no-cache-dir faster-whisper" not in dockerfile
    assert "FROM python:3.11-slim" in dockerfile
    assert "CPython 3.11 Linux images" in stt_lock
    assert "sys.version_info[:2] == (3, 11)" in dockerfile
