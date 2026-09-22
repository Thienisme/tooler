from pathlib import Path

from ai_mystery_story.application.audio.audio_assembler import (
    AudioAssembler,
)
from ai_mystery_story.domain.audio.audio_segment import (
    AudioSegment,
)


class FakeAudioAssembler(AudioAssembler):

    def __init__(self):
        self.calls: list[list[AudioSegment]] = []

    def assemble(
        self,
        segments: list[AudioSegment],
        output_path: Path,
    ) -> float:

        self.calls.append(segments)

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_path.write_bytes(
            b"FAKE_FULL_AUDIO"
        )

        return sum(
            segment.duration_seconds
            for segment in segments
        )
