"""
TTS backend contract.

The rest of the pipeline only ever sees this protocol, which means the
whole voiceover stage can be exercised without the VieNeu model installed
and without downloading 282MB of weights.  `FakeTTSBackend` produces audio
whose *length* is realistic — that is what the sync strategy depends on —
so tests measure real durations from real files rather than stubbing
numbers out.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

from autovid.infrastructure.audio.tools import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    canonical_format_args,
)
from autovid.infrastructure.ffmpeg import run

# Vietnamese narration lands around 13-15 characters per second.
DEFAULT_CHARS_PER_SECOND = 14.0


@runtime_checkable
class TTSBackend(Protocol):
    """Anything that can turn text into a WAV file."""

    name: str

    def synthesize(self, text: str, destination: Path) -> None:
        """
        Write the audio for `text` to `destination`.

        Implementations must write the workspace's canonical format
        (48kHz PCM) so concatenation never has to resample.  They may raise
        on failure; retrying is the caller's job.
        """
        ...

    def close(self) -> None:
        """Release any loaded model.  Safe to call more than once."""
        ...


def atempo_filters(speed: float) -> list[str]:
    """
    Build pitch-preserving ffmpeg `atempo` filters for a speed factor.

    `atempo` only accepts 0.5-2.0 per instance, so extreme speeds are
    chained.  Kept here (rather than in a backend) because both the real
    and fake backends need identical pacing.
    """
    if abs(speed - 1.0) < 1e-9:
        return []

    filters: list[str] = []
    remaining = speed
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    filters.append(f"atempo={remaining:.4f}")
    return filters


def voice_catalog() -> dict[str, str]:
    """
    VieNeu's voice labels, imported from the existing story pipeline so the
    two projects cannot drift apart.  Returns {} if unavailable.
    """
    try:
        from ai_mystery_story.infrastructure.tts.vieneu_tts_provider import (
            VIENEU_VOICES,
        )
    except Exception:
        return {}
    return dict(VIENEU_VOICES)


class FakeTTSBackend:
    """
    Deterministic stand-in for a real voice model.

    The generated audio is a quiet tone whose duration tracks the text
    length, followed by a short silence so the natural-pause logic has
    something real to measure.  Different texts get different tones, which
    makes a rendered video audibly traceable back to its scenes.
    """

    name = "fake"

    def __init__(
        self,
        *,
        chars_per_second: float = DEFAULT_CHARS_PER_SECOND,
        trailing_silence_ms: int = 250,
        speed: float = 1.0,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        fail_attempts: int = 0,
        fail_texts: set[str] | None = None,
    ) -> None:
        self.chars_per_second = chars_per_second
        self.trailing_silence_ms = trailing_silence_ms
        self.speed = speed
        self.sample_rate = sample_rate
        self.channels = channels
        self.fail_attempts = fail_attempts
        self.fail_texts = fail_texts or set()
        self._attempts: dict[str, int] = {}
        self.closed = False

    def speech_seconds(self, text: str) -> float:
        rate = max(self.chars_per_second * self.speed, 1.0)
        return max(0.25, len(text.strip()) / rate)

    def synthesize(self, text: str, destination: Path) -> None:
        if self.closed:
            raise RuntimeError("FakeTTSBackend has been closed")

        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Cannot synthesize empty text")

        attempts = self._attempts.get(cleaned, 0) + 1
        self._attempts[cleaned] = attempts

        # Deliberate failures, for exercising the retry path.
        if attempts <= self.fail_attempts and cleaned in self.fail_texts:
            raise RuntimeError(
                f"FakeTTSBackend: injected failure {attempts}/"
                f"{self.fail_attempts} for '{cleaned[:40]}'"
            )

        speech = self.speech_seconds(cleaned)
        # Stable per-text tone, so nothing is random between runs.
        digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()
        frequency = 180 + int(digest[:4], 16) % 420

        destination.parent.mkdir(parents=True, exist_ok=True)
        filters = [
            "volume=0.25",
            f"apad=pad_dur={self.trailing_silence_ms / 1000.0:.3f}",
            *atempo_filters(self.speed),
        ]

        run(
            [
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-t",
                f"{speech:.3f}",
                "-i",
                f"sine=frequency={frequency}:sample_rate={self.sample_rate}",
                "-af",
                ",".join(filters),
                *canonical_format_args(
                    sample_rate=self.sample_rate, channels=self.channels
                ),
                "-c:a",
                "pcm_s16le",
                str(destination),
            ],
            timeout=120,
        )

    def close(self) -> None:
        self.closed = True
