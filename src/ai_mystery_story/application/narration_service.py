from ai_mystery_story.application.narration.narration_generator import (
    NarrationGenerator,
)
from ai_mystery_story.domain.story.mystery_story import MysteryStory


class NarrationService:

    def __init__(
        self,
        narration_generator: NarrationGenerator,
    ):
        self.narration_generator = narration_generator

    def generate(
        self,
        story: MysteryStory,
    ) -> MysteryStory:

        narration = self.narration_generator.generate(
            blueprint=story.blueprint,
        )

        story.narration = narration

        return story
