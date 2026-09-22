"""
VieNeu-TTS backend (local, offline, ONNX on CPU).

Renders one text unit at a time straight to the workspace's canonical
48kHz WAV.  Going through WAV rather than the story pipeline's 128kbps MP3
matters here: the voiceover is a master that gets mixed with BGM and SFX,
and encoding it lossily twice would audibly degrade the result.

The model is heavy (~282MB, 15-60s to load), so one backend instance is
created per run and reused for every sentence.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from autovid.infrastructure.audio.tools import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    canonical_format_args,
)
from autovid.infrastructure.ffmpeg import run
from autovid.infrastructure.tts.backend import atempo_filters, voice_catalog


class VieNeuTTSBackend:
    """Vietnamese TTS via the `vieneu` SDK."""

    name = "vieneu"

    def __init__(
        self,
        voice: str,
        *,
        speed: float = 1.0,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        engine=None,
    ) -> None:
        if not voice.strip():
            raise ValueError("A voice name is required")

        # Accept either the friendly label ("Thái Sơn (Nam · Nam · Kể
        # chuyện)") or the raw voice name the SDK expects.
        catalog = voice_catalog()
        self.voice = catalog.get(voice, voice)
        self.speed = speed
        self.sample_rate = sample_rate
        self.channels = channels
        self._closed = False

        if engine is not None:
            # Reusing a loaded engine avoids paying the model load again.
            self._engine = engine
        else:
            from vieneu import Vieneu

            print(f"VieNeu-TTS: loading model (voice={self.voice})...")
            self._engine = Vieneu()

    def synthesize(self, text: str, destination: Path) -> None:
        if self._closed:
            raise RuntimeError("VieNeuTTSBackend has been closed")

        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Cannot synthesize empty text")

        # NOTE: the SDK's `infer` may or may not apply `speed` itself; the
        # story pipeline passes it here *and* time-stretches with atempo,
        # and its output is correct in production, so this mirrors that
        # exact call pattern rather than guessing.
        audio = self._engine.infer(
            text=cleaned,
            voice=self.voice,
            speed=self.speed,
        )

        destination.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="autovid_tts_") as temp_dir:
            wav_path = Path(temp_dir) / "unit.wav"
            self._engine.save(audio, str(wav_path))

            filters = atempo_filters(self.speed)
            run(
                [
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(wav_path),
                    *(["-af", ",".join(filters)] if filters else []),
                    *canonical_format_args(
                        sample_rate=self.sample_rate, channels=self.channels
                    ),
                    "-c:a",
                    "pcm_s16le",
                    str(destination),
                ],
                timeout=600,
            )

        if not destination.exists() or destination.stat().st_size == 0:
            raise RuntimeError(
                f"VieNeu-TTS produced an empty file for '{cleaned[:40]}'"
            )

    def close(self) -> None:
        self._closed = True
        self._engine = None
