from dataclasses import dataclass, field

from .narration_beat import NarrationBeat


@dataclass
class NarrationPlan:
    target_duration_minutes: int
    target_word_count: int

    beats: list[NarrationBeat] = field(
        default_factory=list
    )