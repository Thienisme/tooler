from ai_mystery_story.domain.story.story_blueprint import StoryBlueprint
from ai_mystery_story.domain.narration.narration_beat import NarrationBeat
from ai_mystery_story.domain.narration.narration_plan import NarrationPlan


WORDS_PER_MINUTE = 130


class NarrationPlanGenerator:

    def generate(
        self,
        blueprint: StoryBlueprint,
    ) -> NarrationPlan:

        beats: list[NarrationBeat] = []

        for beat in blueprint.beats:
            target_word_count = (
                beat.target_duration_minutes
                * WORDS_PER_MINUTE
            )

            beats.append(
                NarrationBeat(
                    order=beat.order,
                    title=beat.title,
                    target_duration_minutes=(
                        beat.target_duration_minutes
                    ),
                    target_word_count=target_word_count,
                    objective=beat.purpose,
                    required_events=[
                        beat.summary,
                    ],
                    required_clues=[],
                    emotional_state="",
                )
            )

        return NarrationPlan(
            target_duration_minutes=(
                blueprint.target_duration_minutes
            ),
            target_word_count=(
                blueprint.target_duration_minutes
                * WORDS_PER_MINUTE
            ),
            beats=beats,
        )