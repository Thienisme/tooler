from abc import ABC, abstractmethod

from ai_mystery_story.domain.story.mystery_story import MysteryStory


class StoryGenerator(ABC):

    @abstractmethod
    def generate(
        self,
        premise: str,
        setting: str,
        target_duration_minutes: int = 60,
    ) -> MysteryStory:
        pass