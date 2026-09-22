from dataclasses import dataclass, field

from .story_beat import StoryBeat


@dataclass(frozen=True)
class StoryCaseFile:
    """Immutable facts that every beat must obey."""

    culprit: str
    victim: str
    motive: str
    method: str
    real_timeline: list[str]
    false_timeline: list[str]
    initial_belief: str
    alibi: str
    clues: list[str]
    red_herrings: list[str]
    investigation_solution: str
    twist: str
    final_resolution: str
    immutable_facts: list[str]
    reveal_order: list["StoryReveal"]


@dataclass(frozen=True)
class StoryReveal:
    """A controlled piece of information disclosed to the audience."""

    order: int
    beat: int
    event: str
    reveals: str
    audience_belief_after: str


@dataclass
class StoryBlueprint:
    title: str
    premise: str
    setting: str

    crime_type: str
    investigation_type: str
    protagonist: str
    central_mystery: str
    twist_type: str
    ending_type: str

    characters: list[str] = field(default_factory=list)
    beats: list[StoryBeat] = field(default_factory=list)

    target_duration_minutes: int = 60
    case_file: StoryCaseFile | None = None
