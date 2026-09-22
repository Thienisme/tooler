from dataclasses import dataclass, field
from pathlib import Path

from .audio_segment import AudioSegment


@dataclass
class AudioTrack:
    segments: list[AudioSegment] = field(
        default_factory=list
    )
    full_audio_path: Path | None = None
    duration_seconds: float = 0.0

    def total_duration(self) -> float:
        return sum(
            segment.duration_seconds
            for segment in self.segments
        )
