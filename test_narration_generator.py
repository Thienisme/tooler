from ai_mystery_story.application.narration.narration_generator import (
    NarrationGenerator,
)
from ai_mystery_story.application.narration.narration_plan_generator import (
    NarrationPlanGenerator,
)
from ai_mystery_story.domain.story.story_blueprint import StoryBlueprint
from ai_mystery_story.domain.story.story_beat import StoryBeat


class FakeNarrationProvider:

    def __init__(self):
        self.calls = 0
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)

        return " ".join(
            ["Minh"] * 150
        )


blueprint = StoryBlueprint(
    title="Tiếng Kéo Ghế Phòng 404",
    premise=(
        "Một người đàn ông chuyển đến một khu chung cư cũ "
        "và nghe thấy tiếng kéo ghế từ căn phòng không tồn tại."
    ),
    setting="Một khu tập thể cũ ở Hà Nội.",
    characters=[
        "Minh: một người đàn ông 30 tuổi.",
        "Vy: em họ của Minh.",
        "Ông Đức: người bảo vệ khu tập thể.",
    ],
    beats=[
        StoryBeat(
            order=1,
            title="Căn phòng số 304",
            summary="Minh chuyển đến căn phòng 304.",
            purpose="Giới thiệu nhân vật và bối cảnh.",
            target_duration_minutes=1,
        ),
        StoryBeat(
            order=2,
            title="Tiếng động lúc 3 giờ 13 phút",
            summary="Minh nghe tiếng kéo ghế.",
            purpose="Giới thiệu hiện tượng siêu nhiên.",
            target_duration_minutes=1,
        ),
    ],
    target_duration_minutes=2,
)


plan_generator = NarrationPlanGenerator()
plan = plan_generator.generate(blueprint)

provider = FakeNarrationProvider()
generator = NarrationGenerator(provider)

narration = generator.generate(
    blueprint=blueprint,
    plan=plan,
)

print("Narration generated successfully")
print(f"Provider calls: {len(provider.prompts)}")
print("Expected calls: 2")

assert len(narration.segments) == 2

assert narration.segments[0].order == 1
assert narration.segments[0].title == "Căn phòng số 304"
assert narration.segments[0].text

assert narration.segments[1].order == 2
assert narration.segments[1].title == "Tiếng động lúc 3 giờ 13 phút"
assert narration.segments[1].text

print(
    "Word count:",
    sum(
        segment.word_count
        for segment in narration.segments
    ),
)

print("TEST PASSED")