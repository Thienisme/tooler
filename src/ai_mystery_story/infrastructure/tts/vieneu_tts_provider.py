"""
VieNeu-TTS Provider - Local Vietnamese TTS (ONNX, torch-free)

Uses the open-source VieNeu-TTS v3 Turbo model (48 kHz) running locally
on CPU via ONNX Runtime. Vietnamese-first phonemization (sea-g2p) keeps
diacritics accurate — the main weakness of Fish Audio s2.1.

Trade-offs vs Edge TTS:
- Slower: renders near real-time (RTF ~0.7-1.5 on a desktop i5).
- No word/sentence timestamps: EdgeTTSTimestamped remains required for
  subtitle sync (page 8).
- First model load takes ~15-60s and downloads ~282MB from Hugging Face;
  cache the provider instance (e.g. @st.cache_resource in Streamlit).

Requires: pip install vieneu  (plus ffmpeg for MP3 export via pydub)
"""

import os
import tempfile
from pathlib import Path

from ai_mystery_story.infrastructure.tts.tts_provider import (
    TTSProvider,
)

# Curated VieNeu preset voices for horror/mystery narration.
# Full catalog: 23 voices across North/Central/South regions.
VIENEU_VOICES: dict[str, str] = {
    "Mỹ Duyên (Nữ · Nam · Đọc truyện)": "Mỹ Duyên",
    "Thái Sơn (Nam · Nam · Kể chuyện)": "Thái Sơn",
    "Quỳnh Anh (Nữ · Bắc · Đọc truyện)": "Quỳnh Anh",
    "Anh Khôi (Nam · Bắc · Kể chuyện)": "Anh Khôi",
    "Adam (Nam · Nam · Tự nhiên)": "Adam",
    "Kim Thanh (Nữ · Nam · Đọc truyện)": "Kim Thanh",
    "Đức Trí (Nam · Nam · Đọc truyện)": "Đức Trí",
    "Thùy Dung (Nữ · Nam · Tin tức)": "Thùy Dung",
}

DEFAULT_VOICE_NAME = "Thái Sơn (Nam · Nam · Kể chuyện)"


class VieNeuTTSProvider(TTSProvider):
    """
    VieNeu-TTS v3 Turbo provider (local, CPU, 48 kHz ONNX).

    Generates numpy float32 audio via the vieneu SDK, converts WAV to MP3
    with pydub, and merges chunks with configurable silence pauses.
    """

    def __init__(
        self,
        voice: str | None = None,
        speed: float | None = None,
        chunk_pause_ms: int | None = None,
        engine=None,
    ):
        from vieneu import Vieneu

        self.voice = (
            voice
            or os.getenv("VIENEU_VOICE", DEFAULT_VOICE_NAME)
        )

        # Map the friendly label (e.g. "Thái Sơn (Nam · Nam · Kể chuyện)")
        # to the raw voice name ("Thái Sơn") expected by the vieneu SDK.
        self.voice = VIENEU_VOICES.get(self.voice, self.voice)

        self.speed = (
            speed
            if speed is not None
            else float(os.getenv("VIENEU_SPEED", "1.0"))
        )

        self.chunk_pause_ms = (
            chunk_pause_ms
            if chunk_pause_ms is not None
            else int(
                os.getenv("VIENEU_CHUNK_PAUSE_MS", "150")
            )
        )

        if self.chunk_pause_ms < 0:
            raise ValueError(
                "chunk_pause_ms must be greater than or equal to 0"
            )

        if not 0.5 <= self.speed <= 2.0:
            raise ValueError("speed must be between 0.5 and 2.0")

        # NOTE: v3 Turbo's infer() has no `speed` parameter (README's speed
        # knob only exists on v3 Nano). Pacing is therefore applied via
        # ffmpeg atempo post-processing, which time-stretches WITHOUT
        # changing pitch. speed < 1.0 = slower, > 1.0 = faster.

        # Reuse a shared engine instance when provided (e.g. cached in
        # Streamlit), so switching voices never reloads the model.
        if engine is not None:
            self._tts = engine
        else:
            print("VieNeu-TTS: loading v3 Turbo model (ONNX/CPU)...")
            self._tts = Vieneu()
        print(
            f"VieNeu-TTS: ready (voice={self.voice}, "
            f"speed={self.speed})"
        )

    def generate(
        self,
        text: str,
        output_path: Path,
    ) -> float:
        text = text.strip()

        if not text:
            raise ValueError(
                "Cannot generate TTS from empty text."
            )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        print(
            f"VieNeu-TTS: synthesizing "
            f"({len(text)} chars)..."
        )

        audio = self._tts.infer(
            text=text,
            voice=self.voice,
            speed=self.speed,
        )

        with tempfile.TemporaryDirectory(
            prefix="vieneu_tts_"
        ) as temp_dir:
            wav_path = (
                Path(temp_dir) / "chunk.wav"
            )

            self._tts.save(
                audio,
                str(wav_path),
            )

            if self.speed != 1.0:
                wav_path = self._apply_tempo(
                    wav_path=wav_path,
                )

            self._wav_to_mp3(
                wav_path=wav_path,
                output_path=output_path,
            )

        return self._get_duration(output_path)

    def _apply_tempo(
        self,
        wav_path: Path,
    ) -> Path:
        """Time-stretch audio via ffmpeg atempo (pitch-preserving)."""
        import subprocess

        out_path = wav_path.with_name(
            f"{wav_path.stem}_tempo.wav"
        )

        # atempo accepts [0.5, 2.0] per filter; our range fits,
        # but chain defensively for robustness.
        tempo = self.speed
        filters: list[str] = []

        while tempo < 0.5:
            filters.append("atempo=0.5")
            tempo /= 0.5

        while tempo > 2.0:
            filters.append("atempo=2.0")
            tempo /= 2.0

        filters.append(f"atempo={tempo:.4f}")

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(wav_path),
                "-filter:a",
                ",".join(filters),
                str(out_path),
            ],
            check=True,
        )

        wav_path.unlink(missing_ok=True)

        return out_path

    def generate_long(
        self,
        text: str,
        output_path: Path,
        max_chars: int = 1_000,
        cache_dir: Path | None = None,
    ) -> float:
        """
        Generate audio for long text by chunking at sentence
        boundaries, then merging with silence pauses.

        If cache_dir is provided, each chunk is saved to that directory
        as chunk_001.mp3, chunk_002.mp3, ... and already-completed chunks
        are skipped on resume (resume support).  The cache directory is
        NOT deleted after a successful run so the caller can clean it up
        if desired.

        If cache_dir is None, a temporary directory is used (no resume).
        """
        text = text.strip()

        if not text:
            raise ValueError(
                "Cannot generate TTS from empty text."
            )

        chunks = self._split_text(
            text=text,
            max_chars=max_chars,
        )

        total = len(chunks)

        print(
            f"VieNeu-TTS: {total} chunk(s), "
            f"max {max_chars} chars"
        )

        if total == 1:
            return self.generate(
                text=chunks[0],
                output_path=output_path,
            )

        # --------------------------------------------------------
        # Determine where to store chunk files.
        # Persistent cache_dir → resume support.
        # No cache_dir → fall back to a temporary directory.
        # --------------------------------------------------------
        use_persistent = cache_dir is not None
        if use_persistent:
            cache_dir.mkdir(parents=True, exist_ok=True)
            work_dir = cache_dir
        else:
            _tmpdir = tempfile.TemporaryDirectory(prefix="vieneu_long_")
            work_dir = Path(_tmpdir.name)

        try:
            chunk_paths: list[Path] = []

            for index, chunk in enumerate(chunks, start=1):
                chunk_path = work_dir / f"chunk_{index:03d}.mp3"

                # Resume: skip chunks that already completed successfully
                if (
                    use_persistent
                    and chunk_path.exists()
                    and chunk_path.stat().st_size > 0
                ):
                    print(
                        f"  Chunk {index}/{total} "
                        f"({len(chunk)} chars) — skipped (already done)"
                    )
                else:
                    print(
                        f"  Chunk {index}/{total} "
                        f"({len(chunk)} chars)"
                    )
                    self.generate(
                        text=chunk,
                        output_path=chunk_path,
                    )

                chunk_paths.append(chunk_path)

            self._merge_chunks(
                chunk_paths=chunk_paths,
                output_path=output_path,
            )

        finally:
            if not use_persistent:
                _tmpdir.cleanup()

        return self._get_duration(output_path)

    def _split_text(
        self,
        text: str,
        max_chars: int,
    ) -> list[str]:
        import re

        if len(text) <= max_chars:
            return [text]

        sentences = re.split(
            r"(?<=[.!?…])\s+",
            text,
        )

        chunks: list[str] = []
        current = ""

        for sentence in sentences:
            sentence = sentence.strip()

            if not sentence:
                continue

            if len(sentence) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""

                chunks.extend(
                    self._split_long_sentence(
                        sentence,
                        max_chars,
                    )
                )

                continue

            candidate = (
                f"{current} {sentence}".strip()
                if current
                else sentence
            )

            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)

                current = sentence

        if current:
            chunks.append(current)

        return chunks

    def _split_long_sentence(
        self,
        sentence: str,
        max_chars: int,
    ) -> list[str]:
        chunks: list[str] = []

        remaining = sentence.strip()

        while len(remaining) > max_chars:
            split_at = remaining.rfind(
                " ",
                0,
                max_chars,
            )

            if split_at <= 0:
                split_at = max_chars

            chunks.append(
                remaining[:split_at].strip()
            )

            remaining = (
                remaining[split_at:]
                .strip()
            )

        if remaining:
            chunks.append(remaining)

        return chunks

    def _wav_to_mp3(
        self,
        wav_path: Path,
        output_path: Path,
    ) -> None:
        from pydub import AudioSegment

        audio = AudioSegment.from_wav(
            str(wav_path)
        )

        audio.export(
            output_path,
            format="mp3",
            bitrate="128k",
        )

    def _merge_chunks(
        self,
        chunk_paths: list[Path],
        output_path: Path,
    ) -> None:
        if not chunk_paths:
            raise RuntimeError(
                "No TTS chunks were generated."
            )

        from pydub import AudioSegment

        combined = AudioSegment.empty()

        pause = AudioSegment.silent(
            duration=self.chunk_pause_ms
        )

        for index, chunk_path in enumerate(
            chunk_paths
        ):
            audio = AudioSegment.from_mp3(
                str(chunk_path)
            )

            if index > 0:
                combined += pause

            combined += audio

        combined.export(
            output_path,
            format="mp3",
            bitrate="128k",
        )

    def _get_duration(
        self,
        path: Path,
    ) -> float:
        if path.stat().st_size == 0:
            raise RuntimeError(
                "TTS generated an empty file."
            )

        from mutagen.mp3 import MP3

        return float(MP3(str(path)).info.length)

    @staticmethod
    def get_available_voices() -> dict[str, str]:
        """Get curated VieNeu voices (label -> voice name)."""
        return dict(VIENEU_VOICES)
