"""TTS backends: VieNeu for real runs, a fake backend for tests and CI."""

from autovid.infrastructure.tts.backend import (
    FakeTTSBackend,
    TTSBackend,
    atempo_filters,
    voice_catalog,
)
from autovid.infrastructure.tts.vieneu_backend import VieNeuTTSBackend

__all__ = [
    "FakeTTSBackend",
    "TTSBackend",
    "VieNeuTTSBackend",
    "atempo_filters",
    "voice_catalog",
]
