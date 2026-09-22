from dataclasses import dataclass
from uuid import UUID, uuid4

from .story_blueprint import StoryBlueprint
from ai_mystery_story.domain.narration.narration import Narration
from ai_mystery_story.domain.audio.audio_track import AudioTrack


@dataclass
class MysteryStory:
    id: UUID
    blueprint: StoryBlueprint
    narration: Narration | None = None
    audio: AudioTrack | None = None

    @classmethod
    def create(
        cls,
        blueprint: StoryBlueprint,
    ) -> "MysteryStory":
        return cls(
            id=uuid4(),
            blueprint=blueprint,
        )