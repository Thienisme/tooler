from ai_mystery_story.application.parsers.story_blueprint_parser import (
    StoryBlueprintParser,
)
from ai_mystery_story.application.prompts.story_blueprint_prompt import (
    StoryBlueprintPrompt,
)
from ai_mystery_story.application.story_generator import StoryGenerator
from ai_mystery_story.application.validators.story_blueprint_validator import (
    StoryBlueprintValidator,
)
from ai_mystery_story.domain.story.mystery_story import MysteryStory
from typing import Any
from ai_mystery_story.infrastructure.ai.ai_provider import AIProvider


class GeminiStoryGenerator(StoryGenerator):

    def __init__(
        self,
        provider: AIProvider,
        max_retries: int = 2,
    ):
        self.provider = provider
        self.max_retries = max_retries
        self.parser = StoryBlueprintParser()
        self.validator = StoryBlueprintValidator()

    def generate(
        self,
        title: str,
        premise: str,
        setting: str,
        crime_type: str,
        protagonist: str,
        ending_type: str,
        mystery_difficulty: str = "medium",
        creative_constraints: dict[str, Any] | None = None,
        core_mechanism: str | None = None,
        fair_play_focus: str | None = None,
        false_assumption: str | None = None,
        misdirection_strategy: str | None = None,
        reveal_condition: str | None = None,
        resolved_mechanisms: dict | None = None,
        target_duration_minutes: int = 60,
    ) -> MysteryStory:

        prompt = StoryBlueprintPrompt.build(
            title=title,
            premise=premise,
            setting=setting,
            crime_type=crime_type,
            protagonist=protagonist,
            ending_type=ending_type,
            mystery_difficulty=mystery_difficulty,
            creative_constraints=creative_constraints or {},
            core_mechanism=core_mechanism,
            fair_play_focus=fair_play_focus,
            false_assumption=false_assumption,
            misdirection_strategy=misdirection_strategy,
            reveal_condition=reveal_condition,
            resolved_mechanisms=resolved_mechanisms,
            target_duration_minutes=target_duration_minutes,
        )

        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):

            try:
                response = self.provider.generate(prompt)

                blueprint = self.parser.parse(response)

                self.validator.validate(blueprint)

                audited_response = self.provider.generate(
                    self._build_consistency_audit_prompt(response)
                )
                blueprint = self.parser.parse(audited_response)
                self.validator.validate(blueprint)

                return MysteryStory.create(blueprint)

            except (ValueError, RuntimeError) as exc:
                last_error = exc

                if attempt >= self.max_retries:
                    break

                prompt = self._build_retry_prompt(
                    original_prompt=prompt,
                    error=exc,
                )

        raise RuntimeError(
            "Failed to generate a valid mystery story blueprint"
        ) from last_error

    def _build_consistency_audit_prompt(
        self,
        blueprint_json: str,
    ) -> str:
        return f"""
You are a meticulous detective-fiction continuity editor.

Audit the following Vietnamese mystery-story blueprint. Repair every logical
conflict before returning it. Verify that the real timeline, false timeline, reveal order, method, evidence,
alibi, clues, red herrings, twist, and final resolution can all be true at the
same time. Check technical details only when the story actually uses them.

The case_file is authoritative. If it conflicts with a beat, correct the beat.
If the case_file itself is impossible or incomplete, correct both it and the
affected beats. Any claimed physical or technical method must have an explicit,
realistic explanation in the case_file and must agree with every beat.

Do not add magical technology, coincidences, or a new culprit. Preserve the
title, genre, central mystery, requested ending, and total beat duration.

Return ONLY one complete valid JSON object in exactly the same schema as the
input; do not return an audit report or Markdown.

BLUEPRINT TO AUDIT
{blueprint_json}
""".strip()

    def _build_retry_prompt(
        self,
        original_prompt: str,
        error: Exception,
    ) -> str:

        return f"""
{original_prompt}

IMPORTANT CORRECTION

The previous response was invalid.

Validation error:
{error}

Generate the complete JSON again.

Do not return only the corrected field.

Return the entire valid JSON object.

Make sure that:

- all required mystery fields are present
- all story beats are present
- all beat target durations are positive integers
- the story has a coherent beginning, investigation,
  escalation, climax, and resolution
- the central mystery remains logically solvable
- important clues are introduced before their final explanation
- real_timeline, false_timeline, and reveal_order are complete and consistent
- red herrings are meaningful and logically resolved
- the twist is properly foreshadowed
- clues and revelations remain consistent
- the ending matches the requested ending type
- story quality and pacing are prioritized over exact duration
- the target duration is treated as an approximate guideline

Do not pad the story simply to reach the target duration.

Return ONLY the complete JSON object.
""".strip()
