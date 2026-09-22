import json
from pathlib import Path

from ai_mystery_story.application.narration.narration_plan_generator import (
    NarrationPlanGenerator,
)
from ai_mystery_story.infrastructure.serializers.story_serializer import (
    StorySerializer,
)


story_path = Path(
    "projects/34affbe3-01a6-4fbb-9fb2-57ca54c46a27/story.json"
)

data = json.loads(
    story_path.read_text(encoding="utf-8")
)

story = StorySerializer.from_dict(data)

generator = NarrationPlanGenerator()

plan = generator.generate(
    story.blueprint
)

print("Narration plan generated")
print(
    f"Duration: {plan.target_duration_minutes} minutes"
)
print(
    f"Word count: {plan.target_word_count}"
)
print(
    f"Beats: {len(plan.beats)}"
)

for beat in plan.beats:
    print(
        f"{beat.order}. "
        f"{beat.title} - "
        f"{beat.target_word_count} words"
    )
