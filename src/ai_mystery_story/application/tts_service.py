import re
from pathlib import Path

from ai_mystery_story.domain.audio.audio_segment import (
    AudioSegment,
)
from ai_mystery_story.domain.story.mystery_story import MysteryStory
from ai_mystery_story.infrastructure.tts.tts_provider import (
    TTSProvider,
)


class TTSService:

    MAX_TTS_CHARS = 1_000

    def __init__(
        self,
        provider: TTSProvider,
    ):
        self.provider = provider

    def generate(
        self,
        story: MysteryStory,
        output_dir: Path,
    ) -> list[AudioSegment]:

        if story.narration is None:
            raise RuntimeError(
                "Cannot generate audio: narration is empty."
            )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        segments: list[AudioSegment] = []

        narration_segments = self._split_for_tts(
            story.narration.full_text()
        )

        total = len(narration_segments)

        for order, text in narration_segments:

            output_path = (
                output_dir
                / f"segment_{order:02d}.mp3"
            )

            # -------------------------------------------------
            # Resume support
            # -------------------------------------------------

            if (
                output_path.exists()
                and output_path.stat().st_size > 0
            ):
                print()
                print(
                    f"Segment "
                    f"{order:02d}/{total:02d} "
                    f"already exists. Skipping TTS."
                )

                from mutagen.mp3 import MP3

                duration_seconds = float(
                    MP3(output_path).info.length
                )

            else:
                print()
                print(
                    f"Generating segment "
                    f"{order:02d}/{total:02d}..."
                )

                duration_seconds = self.provider.generate(
                    text=text,
                    output_path=output_path,
                )

            segments.append(
                AudioSegment(
                    order=order,
                    title=f"Phần {order}",
                    source_text=text,
                    file_path=output_path,
                    duration_seconds=duration_seconds,
                )
            )

        return segments

    def _split_for_tts(
        self,
        full_text: str,
    ) -> list[tuple[int, str]]:
        """Split only at completed sentence boundaries for the TTS provider."""
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?…])(?:\s+|$)", full_text)
            if sentence.strip()
        ]
        if not sentences:
            raise RuntimeError("Narration contains no complete sentence")

        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            if len(sentence) > self.MAX_TTS_CHARS:
                raise RuntimeError(
                    "A narration sentence exceeds the TTS limit of "
                    f"{self.MAX_TTS_CHARS} characters"
                )
            separator = " " if current else ""
            if len(current) + len(separator) + len(sentence) > self.MAX_TTS_CHARS:
                chunks.append(current)
                current = sentence
            else:
                current += separator + sentence
        if current:
            chunks.append(current)

        return list(enumerate(chunks, start=1))
