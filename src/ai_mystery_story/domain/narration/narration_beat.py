from dataclasses import dataclass, field


@dataclass
class NarrationBeat:
    order: int
    title: str

    target_duration_minutes: int
    target_word_count: int

    objective: str

    required_events: list[str] = field(
        default_factory=list
    )

    required_clues: list[str] = field(
        default_factory=list
    )

    emotional_state: str = ""