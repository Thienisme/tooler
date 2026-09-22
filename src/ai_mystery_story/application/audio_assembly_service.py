from pathlib import Path

from ai_mystery_story.application.audio.audio_assembler import (
    AudioAssembler,
)
from ai_mystery_story.domain.audio.audio_segment import (
    AudioSegment,
)


class AudioAssemblyService:

    def __init__(
        self,
        assembler: AudioAssembler,
    ):
        self.assembler = assembler

    def assemble(
        self,
        segments: list[AudioSegment],
        output_path: Path,
    ) -> float:

        if not segments:
            raise ValueError(
                "Cannot assemble audio: no segments provided"
            )

        ordered_segments = sorted(
            segments,
            key=lambda segment: segment.order,
        )

        return self.assembler.assemble(
            segments=ordered_segments,
            output_path=output_path,
        )
