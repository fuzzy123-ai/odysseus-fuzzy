"""Local-only transcription service boundaries.

The package deliberately exposes no HTTP/provider integration.  Callers must
obtain source bytes from :class:`src.transcription_store.TranscriptionStore`.
"""

from .faster_whisper_adapter import (
    FASTER_WHISPER_MODEL_ID,
    FASTER_WHISPER_MODEL_REVISION,
    LocalFasterWhisperAdapter,
    LocalTranscriptionError,
)

__all__ = (
    "FASTER_WHISPER_MODEL_ID",
    "FASTER_WHISPER_MODEL_REVISION",
    "LocalFasterWhisperAdapter",
    "LocalTranscriptionError",
)
