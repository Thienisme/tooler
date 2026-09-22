from dataclasses import dataclass


@dataclass(frozen=True)
class StoryBeat:
    order: int
    title: str
    summary: str
    purpose: str
    target_duration_minutes: int