from ai_mystery_story.application.narration.narration_generator import (
    NarrationGenerator,
)
from ai_mystery_story.application.narration.narration_plan_generator import (
    NarrationPlanGenerator,
)
from ai_mystery_story.application.narration_service import (
    NarrationService,
)
from ai_mystery_story.domain.story.horror_story import HorrorStory
from ai_mystery_story.domain.story.story_beat import StoryBeat
from ai_mystery_story.domain.story.story_blueprint import StoryBlueprint
from ai_mystery_story.infrastructure.ai.ai_provider import AIProvider


class FakeAIProvider(AIProvider):

    def __init__(self):
        self.calls = 0
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)

        words = [
            "Minh",
            "tỉnh",
            "giấc",
            "giữa",
            "đêm.",
            "Căn",
            "phòng",
            "cũ",
            "chìm",
            "trong",
            "im",
            "lặng.",
            "Ngoài",
            "cửa",
            "sổ,",
            "mưa",
            "rơi",
            "đều",
            "đặn",
            "trên",
            "mái",
            "tôn.",
        ]

        return " ".join(words * 6) + " " + "Minh"


blueprint = StoryBlueprint(
    title="Test Horror Story",
    premise="Một căn phòng kỳ lạ.",
    setting="Một khu tập thể cũ ở Hà Nội.",
    characters=[
        "Minh: nhân vật chính.",
    ],
    beats=[
        StoryBeat(
            order=1,
            title="Đêm đầu tiên",
            summary="Minh nghe thấy một âm thanh kỳ lạ.",
            purpose="Giới thiệu hiện tượng siêu nhiên.",
            target_duration_minutes=1,
        ),
        StoryBeat(
            order=2,
            title="Âm thanh trở lại",
            summary="Âm thanh xuất hiện lần nữa.",
            purpose="Tăng mức độ căng thẳng.",
            target_duration_minutes=1,
        ),
    ],
    target_duration_minutes=2,
)


story = HorrorStory.create(blueprint)

provider = FakeAIProvider()

plan_generator = NarrationPlanGenerator()

narration_generator = NarrationGenerator(
    provider=provider,
)

service = NarrationService(
    plan_generator=plan_generator,
    narration_generator=narration_generator,
)


result = service.generate(story)


assert result is story

assert result.narration

assert result.narration is not None
assert len(result.narration.segments) == 2
assert result.narration.full_text()

assert provider.calls == 2

assert len(provider.prompts) == 2

assert result.narration.segments[0].text
assert result.narration.segments[1].text

print("Narration service generated successfully")
print(f"Provider calls: {provider.calls}")
print(f"Expected calls: 2")
print(f"Narration length: {len(result.narration.full_text())} characters")
print("TEST PASSED")
