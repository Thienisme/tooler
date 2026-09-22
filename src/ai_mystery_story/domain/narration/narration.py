from dataclasses import dataclass, field

from .narration_segment import NarrationSegment


@dataclass
class Narration:
    segments: list[NarrationSegment] = field(
        default_factory=list
    )

    def full_text(self) -> str:
        return "\n\n".join(
            segment.text
            for segment in self.segments
        )