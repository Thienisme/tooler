from abc import ABC, abstractmethod
from uuid import UUID

from .mystery_story import MysteryStory


class StoryRepository(ABC):

    @abstractmethod
    def save(
        self,
        story: MysteryStory,
        topic_id: str | None = None,
    ) -> None:
        pass

    @abstractmethod
    def get(
        self,
        story_id: UUID,
    ) -> MysteryStory | None:
        pass
