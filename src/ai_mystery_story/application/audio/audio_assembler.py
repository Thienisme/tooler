from abc import ABC, abstractmethod
from pathlib import Path

from ai_mystery_story.domain.audio.audio_segment import (
    AudioSegment,
)


class AudioAssembler(ABC):

    @abstractmethod
    def assemble(
        self,
        segments: list[AudioSegment],
        output_path: Path,
    ) -> float:
        """
        Assemble audio segments into one audio file.

        Args:
            segments: Audio segments to assemble.
            output_path: Final output audio path.

        Returns:
            Total duration in seconds.
        """
        raise NotImplementedError
