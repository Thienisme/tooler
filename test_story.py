from pathlib import Path

from ai_mystery_story.domain.story.horror_story import HorrorStory
from ai_mystery_story.domain.story.story_beat import StoryBeat
from ai_mystery_story.domain.story.story_blueprint import StoryBlueprint
from ai_mystery_story.infrastructure.repositories.json_story_repository import (
    JsonStoryRepository,
)


repository = JsonStoryRepository(Path("projects"))

blueprint = StoryBlueprint(
    title="Căn phòng 404",
    premise=(
        "Một người đàn ông thuê một căn phòng trong khu chung cư cũ "
        "và bắt đầu nghe thấy tiếng động từ căn phòng 404, "
        "dù tòa nhà không hề có phòng này."
    ),
    setting="Một khu chung cư cũ ở Hà Nội.",
    characters=[
        "Minh - nhân viên văn phòng 28 tuổi.",
        "Bà chủ nhà - một phụ nữ khoảng 60 tuổi.",
    ],
    beats=[
        StoryBeat(
            order=1,
            title="Người thuê mới",
            summary="Minh chuyển vào căn phòng 304.",
            purpose="Giới thiệu nhân vật và bối cảnh.",
            target_duration_minutes=5,
        ),
        StoryBeat(
            order=2,
            title="Tiếng kéo ghế",
            summary="Minh nghe tiếng kéo ghế vào lúc 3 giờ sáng.",
            purpose="Đưa yếu tố kinh dị đầu tiên vào câu chuyện.",
            target_duration_minutes=7,
        ),
    ],
    target_duration_minutes=60,
)

story = HorrorStory.create(blueprint)

story.narration = (
    "Đêm đầu tiên ở căn phòng 304, Minh nghe thấy một tiếng kéo ghế "
    "vang lên từ phía trên."
)

repository.save(story)

print(f"Story saved: {story.id}")

loaded_story = repository.get(story.id)

if loaded_story is None:
    raise RuntimeError("Story not found")

print(f"Story loaded: {loaded_story.id}")
print(f"Title: {loaded_story.blueprint.title}")
print(f"Characters: {loaded_story.blueprint.characters}")
print(f"Beat count: {len(loaded_story.blueprint.beats)}")
print(f"First beat: {loaded_story.blueprint.beats[0].title}")
print(f"Narration: {loaded_story.narration}")
