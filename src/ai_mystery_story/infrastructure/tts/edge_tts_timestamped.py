"""
Edge TTS Provider with Timestamps.

Generates TTS audio AND extracts timing data for subtitle sync.
Vietnamese voices produce SentenceBoundary events (not WordBoundary).
"""

import asyncio
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import edge_tts
from mutagen.mp3 import MP3


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_FFMPEG_BIN = _PROJECT_ROOT / "bin" / "ffmpeg"
_FFMPEG = str(_FFMPEG_BIN) if _FFMPEG_BIN.exists() else "ffmpeg"


class EdgeTTSTimestamped:
    """
    Edge TTS provider that outputs audio with sentence-level timestamps.

    Vietnamese voices emit SentenceBoundary events with offset/duration
    for each sentence. We use these for accurate subtitle timing.
    """

    def __init__(
        self,
        voice: str | None = None,
        rate: str | None = None,
        pitch: str | None = None,
        max_chars: int = 1300,
        request_delay_seconds: int = 5,
    ):
        self.voice = voice or os.getenv("EDGE_TTS_VOICE", "vi-VN-NamMinhNeural")
        self.rate = rate or os.getenv("EDGE_TTS_RATE", "-7%")
        self.pitch = pitch or os.getenv("EDGE_TTS_PITCH", "-2Hz")
        self.max_chars = max_chars
        self.request_delay_seconds = request_delay_seconds
        self._last_response_completed_at: float | None = None

    def generate_with_timestamps(
        self,
        text: str,
        audio_output: Path,
        timestamps_output: Path | None = None,
    ) -> dict:
        """
        Generate TTS audio with sentence-level timestamps.

        Returns dict with: audio_path, duration, words, segments
        """
        text = text.strip()
        if not text:
            raise ValueError("Cannot generate TTS from empty text.")

        audio_output.parent.mkdir(parents=True, exist_ok=True)

        # Split text into chunks
        chunks = self._split_text(text, self.max_chars)
        print(f"  Edge TTS (timestamped): {len(chunks)} chunk(s)")

        all_words = []
        all_sentences = []
        chunk_durations = []

        with tempfile.TemporaryDirectory(prefix="edge_tts_ts_") as temp_dir:
            temp_path = Path(temp_dir)

            for idx, chunk in enumerate(chunks, 1):
                chunk_audio = temp_path / f"chunk_{idx:03d}.mp3"

                print(f"  Chunk {idx}/{len(chunks)} ({len(chunk)} chars)")

                # Generate audio + extract timestamps
                words, sentences = self._generate_chunk(chunk, chunk_audio)

                # Get chunk duration
                duration = self._get_duration(chunk_audio)
                chunk_durations.append(duration)

                # Adjust timestamps for chunk offset
                offset = sum(chunk_durations[:-1])
                for w in words:
                    w["start"] += offset
                    w["end"] += offset
                for s in sentences:
                    s["start"] += offset
                    s["end"] += offset

                all_words.extend(words)
                all_sentences.extend(sentences)

            # Merge audio chunks
            self._merge_audio_chunks(
                [temp_path / f"chunk_{i:03d}.mp3" for i in range(1, len(chunks) + 1)],
                audio_output,
            )

        total_duration = self._get_duration(audio_output)

        # If no WordBoundary, create pseudo-words from sentences
        if not all_words and all_sentences:
            all_words = self._sentences_to_words(all_sentences)

        result = {
            "audio_path": str(audio_output),
            "duration": total_duration,
            "words": all_words,
            "segments": all_sentences,
        }

        # Save timestamps
        if timestamps_output:
            timestamps_output.parent.mkdir(parents=True, exist_ok=True)
            timestamps_output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  ✅ Timestamps saved: {timestamps_output.name}")

        return result

    def _generate_chunk(self, text: str, output_path: Path) -> tuple[list, list]:
        """Generate audio for a chunk and extract timestamps."""
        self._rate_limit()
        words, sentences = asyncio.run(self._generate_async(text, output_path))
        self._last_response_completed_at = time.monotonic()
        return words, sentences

    async def _generate_async(self, text: str, output_path: Path) -> tuple[list, list]:
        """Async generation using edge_tts stream()."""
        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            rate=self.rate,
            pitch=self.pitch,
        )

        words = []
        sentences = []
        audio_data = b""

        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
            elif chunk["type"] == "WordBoundary":
                words.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / 10_000_000,
                    "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                })
            elif chunk["type"] == "SentenceBoundary":
                sentences.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / 10_000_000,
                    "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                })

        # Write audio
        output_path.write_bytes(audio_data)

        return words, sentences

    def _sentences_to_words(self, sentences: list[dict]) -> list[dict]:
        """
        Convert sentence-level timestamps to pseudo-word timestamps.
        Split each sentence into words with interpolated timing.
        """
        words = []
        for sent in sentences:
            text = sent["text"]
            start = sent["start"]
            end = sent["end"]

            # Split sentence into words
            parts = text.split()
            if not parts:
                continue

            duration = end - start
            total_chars = sum(len(p) for p in parts)

            current_time = start
            for part in parts:
                word_duration = duration * len(part) / total_chars
                words.append({
                    "text": part,
                    "start": current_time,
                    "end": current_time + word_duration,
                })
                current_time += word_duration

        return words

    def _split_text(self, text: str, max_chars: int) -> list[str]:
        """Split text into chunks respecting sentence boundaries."""
        if len(text) <= max_chars:
            return [text]

        sentences = re.split(r'(?<=[.!?。！？])\s+', text)
        chunks = []
        current = ""

        for sentence in sentences:
            if not sentence.strip():
                continue
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sentence

        if current:
            chunks.append(current)

        return chunks

    def _merge_audio_chunks(self, chunk_paths: list[Path], output: Path):
        """Merge audio chunks using ffmpeg concat."""
        concat_file = output.parent / f".concat_{output.stem}.txt"
        with concat_file.open("w") as f:
            for p in chunk_paths:
                f.write(f"file '{p.resolve()}'\n")

        subprocess.run([
            _FFMPEG,
            "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(output),
        ], capture_output=True, check=True)

        concat_file.unlink(missing_ok=True)

    def _get_duration(self, path: Path) -> float:
        return float(MP3(str(path)).info.length)

    def _rate_limit(self):
        if self._last_response_completed_at is not None:
            elapsed = time.monotonic() - self._last_response_completed_at
            wait = self.request_delay_seconds - elapsed
            if wait > 0:
                print(f"    Waiting {wait:.1f}s after previous response...")
                time.sleep(wait)
