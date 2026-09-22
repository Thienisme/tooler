from ai_mystery_story.application.generators.gemini_story_generator import (
    GeminiStoryGenerator,
)
from ai_mystery_story.infrastructure.ai.ai_provider import AIProvider


class FakeAIProvider(AIProvider):

    def generate(self, prompt: str) -> str:
        print("AI provider called")
        print(f"Prompt length: {len(prompt)} characters")

        return """
{
  "title": "Căn phòng 404",
  "premise": "Một người đàn ông thuê căn phòng trong khu chung cư cũ và phát hiện một căn phòng không tồn tại.",
  "setting": "Một khu chung cư cũ ở Hà Nội.",
  "characters": [
    "Minh - nhân viên văn phòng 28 tuổi",
    "Bà chủ nhà - phụ nữ khoảng 60 tuổi"
  ],
  "beats": [
    {
      "order": 1,
      "title": "Người thuê mới",
      "summary": "Minh chuyển vào căn phòng 304.",
      "purpose": "Giới thiệu nhân vật và bối cảnh.",
      "target_duration_minutes": 5
    },
    {
      "order": 2,
      "title": "Tiếng kéo ghế",
      "summary": "Minh nghe tiếng kéo ghế lúc 3 giờ sáng.",
      "purpose": "Đưa yếu tố kinh dị đầu tiên.",
      "target_duration_minutes": 10
    },
    {
      "order": 3,
      "title": "Căn phòng không tồn tại",
      "summary": "Minh phát hiện tầng 4 không có phòng 404.",
      "purpose": "Mở rộng bí ẩn chính.",
      "target_duration_minutes": 15
    },
    {
      "order": 4,
      "title": "Bí mật của tòa nhà",
      "summary": "Minh tìm thấy những manh mối về lịch sử tòa nhà.",
      "purpose": "Đưa câu chuyện đến gần sự thật.",
      "target_duration_minutes": 15
    },
    {
      "order": 5,
      "title": "Đêm cuối",
      "summary": "Minh đối mặt với nguồn gốc của căn phòng.",
      "purpose": "Cao trào và giải quyết bí ẩn.",
      "target_duration_minutes": 15
    }
  ],
  "target_duration_minutes": 60
}
"""


generator = GeminiStoryGenerator(
    provider=FakeAIProvider(),
)

story = generator.generate(
    premise=(
        "Một người đàn ông thuê một căn phòng trong khu chung cư cũ "
        "và phát hiện tiếng động phát ra từ một căn phòng không tồn tại."
    ),
    setting="Một khu chung cư cũ ở Hà Nội.",
    target_duration_minutes=60,
)

print()
print("Story generated successfully")
print(f"ID: {story.id}")
print(f"Title: {story.blueprint.title}")
print(f"Characters: {len(story.blueprint.characters)}")
print(f"Beats: {len(story.blueprint.beats)}")
print(
    "Duration:",
    story.blueprint.target_duration_minutes,
)