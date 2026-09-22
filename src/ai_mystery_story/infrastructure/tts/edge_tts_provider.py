import asyncio
import os
import re
import tempfile
import time
from pathlib import Path

import edge_tts
from mutagen.mp3 import MP3

from ai_mystery_story.infrastructure.tts.tts_provider import (
    TTSProvider,
)


class EdgeTTSProvider(TTSProvider):

    def __init__(
        self,
        voice: str | None = None,
        rate: str | None = None,
        pitch: str | None = None,
        max_chars: int | None = None,
        max_retries: int = 3,
        timeout_seconds: int | None = None,
        retry_delay_seconds: int | None = None,
        chunk_pause_ms: int | None = None,
        request_delay_seconds: int | None = None,
    ):
        self.voice = voice or os.getenv(
            "EDGE_TTS_VOICE",
            "vi-VN-NamMinhNeural",
        )

        self.rate = rate or os.getenv(
            "EDGE_TTS_RATE",
            "-7%",
        )

        self.pitch = pitch or os.getenv(
            "EDGE_TTS_PITCH",
            "-2Hz",
        )

        self.max_chars = max_chars or int(
            os.getenv(
                "EDGE_TTS_MAX_CHARS",
                "1300",
            )
        )

        self.max_retries = max_retries

        self.timeout_seconds = timeout_seconds or int(
            os.getenv(
                "EDGE_TTS_TIMEOUT_SECONDS",
                "120",
            )
        )

        self.retry_delay_seconds = retry_delay_seconds or int(
            os.getenv(
                "EDGE_TTS_RETRY_DELAY_SECONDS",
                "2",
            )
        )

        self.chunk_pause_ms = (
            chunk_pause_ms
            if chunk_pause_ms is not None
            else int(
                os.getenv(
                    "EDGE_TTS_CHUNK_PAUSE_MS",
                    "150",
                )
            )
        )

        self.request_delay_seconds = (
            request_delay_seconds
            if request_delay_seconds is not None
            else int(
                os.getenv(
                    "EDGE_TTS_REQUEST_DELAY_SECONDS",
                    "10",
                )
            )
        )
        self._last_response_completed_at: float | None = None

        if self.max_chars <= 0:
            raise ValueError(
                "EDGE_TTS_MAX_CHARS must be greater than 0"
            )

        if self.max_retries <= 0:
            raise ValueError(
                "max_retries must be greater than 0"
            )

        if self.timeout_seconds <= 0:
            raise ValueError(
                "EDGE_TTS_TIMEOUT_SECONDS must be greater than 0"
            )

        if self.retry_delay_seconds < 0:
            raise ValueError(
                "EDGE_TTS_RETRY_DELAY_SECONDS must be "
                "greater than or equal to 0"
            )

        if self.chunk_pause_ms < 0:
            raise ValueError(
                "EDGE_TTS_CHUNK_PAUSE_MS must be "
                "greater than or equal to 0"
            )

        if self.request_delay_seconds < 0:
            raise ValueError(
                "EDGE_TTS_REQUEST_DELAY_SECONDS must be "
                "greater than or equal to 0"
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

        chunks = self._split_text(
            text=text,
            max_chars=self.max_chars,
        )

        print(
            f"Edge TTS: {len(chunks)} chunk(s), "
            f"max {self.max_chars} chars"
        )

        with tempfile.TemporaryDirectory(
            prefix="edge_tts_"
        ) as temp_dir:

            temp_path = Path(temp_dir)

            chunk_paths: list[Path] = []

            for index, chunk in enumerate(
                chunks,
                start=1,
            ):
                chunk_path = (
                    temp_path
                    / f"chunk_{index:03d}.mp3"
                )

                print(
                    f"  Chunk {index}/{len(chunks)} "
                    f"({len(chunk)} chars)"
                )

                self._generate_with_retry(
                    text=chunk,
                    output_path=chunk_path,
                )

                chunk_paths.append(chunk_path)

            self._merge_chunks(
                chunk_paths=chunk_paths,
                output_path=output_path,
            )

        return self._get_duration(
            output_path
        )

    def _split_text(
        self,
        text: str,
        max_chars: int,
    ) -> list[str]:

        if len(text) <= max_chars:
            return [text]

        paragraphs = re.split(
            r"\n\s*\n+",
            text,
        )

        chunks: list[str] = []
        current = ""

        for paragraph in paragraphs:
            paragraph = paragraph.strip()

            if not paragraph:
                continue

            if len(paragraph) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""

                chunks.extend(
                    self._split_long_text(
                        paragraph,
                        max_chars,
                    )
                )

                continue

            candidate = (
                f"{current}\n\n{paragraph}".strip()
                if current
                else paragraph
            )

            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)

                current = paragraph

        if current:
            chunks.append(current)

        return chunks

    def _split_long_text(
        self,
        text: str,
        max_chars: int,
    ) -> list[str]:

        sentences = re.split(
            r"(?<=[.!?。！？])\s+",
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

    def _generate_with_retry(
        self,
        text: str,
        output_path: Path,
    ) -> None:

        last_error: Exception | None = None

        for attempt in range(
            1,
            self.max_retries + 1,
        ):
            try:
                self._wait_before_next_request()

                print(
                    f"    Generating TTS "
                    f"(attempt {attempt}/"
                    f"{self.max_retries}, "
                    f"timeout {self.timeout_seconds}s)..."
                )

                asyncio.run(
                    asyncio.wait_for(
                        self._generate(
                            text=text,
                            output_path=output_path,
                        ),
                        timeout=self.timeout_seconds,
                    )
                )

                if (
                    not output_path.exists()
                    or output_path.stat().st_size == 0
                ):
                    raise RuntimeError(
                        "TTS generated an empty file."
                    )

                # Record time when response was successfully completed
                self._last_response_completed_at = time.monotonic()

                return

            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"Edge TTS request timed out "
                    f"after {self.timeout_seconds} seconds"
                )

                print(
                    f"    Edge TTS attempt "
                    f"{attempt} timed out after "
                    f"{self.timeout_seconds}s"
                )

                if output_path.exists():
                    output_path.unlink()

            except Exception as exc:
                last_error = exc

                print(
                    f"    Edge TTS attempt "
                    f"{attempt} failed: "
                    f"{type(exc).__name__}: {exc}"
                )

                if output_path.exists():
                    output_path.unlink()

            if attempt < self.max_retries:
                delay = (
                    self.retry_delay_seconds
                    * (2 ** (attempt - 1))
                )

                print(
                    f"    Retrying in {delay}s..."
                )

                time.sleep(delay)

        raise RuntimeError(
            "Edge TTS failed after "
            f"{self.max_retries} attempts: "
            f"{last_error}"
        )

    def _wait_before_next_request(self) -> None:
        """Enforce post-response delay between TTS requests (calculated from when previous response finished)."""
        now = time.monotonic()

        if self._last_response_completed_at is not None:
            elapsed = now - self._last_response_completed_at
            delay = self.request_delay_seconds - elapsed
            if delay > 0:
                print(
                    f"    Waiting {delay:.1f} seconds after previous "
                    "response before next Edge TTS request..."
                )
                time.sleep(delay)

    async def _generate(
        self,
        text: str,
        output_path: Path,
    ) -> None:

        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            rate=self.rate,
            pitch=self.pitch,
        )

        await communicate.save(
            str(output_path)
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

        if len(chunk_paths) == 1:
            chunk_paths[0].replace(output_path)
            return

        from pydub import AudioSegment

        combined = AudioSegment.empty()

        pause = AudioSegment.silent(
            duration=self.chunk_pause_ms
        )

        for index, chunk_path in enumerate(
            chunk_paths
        ):
            audio = AudioSegment.from_mp3(
                chunk_path
            )

            if index > 0:
                combined += pause

            combined += audio

        combined.export(
            output_path,
            format="mp3",
            bitrate="48k",
        )

    def _get_duration(
        self,
        path: Path,
    ) -> float:

        audio = MP3(path)

        return float(audio.info.length)
