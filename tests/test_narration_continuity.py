import unittest

from ai_mystery_story.application.narration.narration_generator import (
    NarrationGenerator,
)
from ai_mystery_story.domain.story.story_beat import StoryBeat
from ai_mystery_story.domain.story.story_blueprint import (
    StoryBlueprint,
    StoryCaseFile,
    StoryReveal,
)


class RecordingProvider:

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return " ".join(
            "Một câu chuyện được viết đầy đủ và kết thúc bằng dấu chấm."
            for _ in range(300)
        )


class NarrationContinuityTest(unittest.TestCase):

    def test_full_story_is_generated_once(self):
        blueprint = StoryBlueprint(
            title="Cuộc gọi lúc 23:47",
            premise="Một cuộc gọi báo án được thực hiện sau khi nạn nhân chết.",
            setting="Chung cư cũ ở Hà Nội.",
            crime_type="murder",
            investigation_type="impossible_timeline",
            protagonist="Đại úy Minh",
            central_mystery="Ai thực hiện cuộc gọi?",
            twist_type="false_timeline",
            ending_type="fully_resolved",
            characters=["Nguyễn Văn Trọng: nạn nhân."],
            beats=[
                StoryBeat(
                    order=1,
                    title="Hiện trường",
                    summary="Điện thoại của Trọng ở cạnh thi thể.",
                    purpose="Thiết lập bí ẩn.",
                    target_duration_minutes=1,
                ),
                StoryBeat(
                    order=2,
                    title="Đường dây cố định",
                    summary=(
                        "Cuộc gọi được thực hiện từ số máy bàn của Trọng "
                        "qua đường dây nối lén."
                    ),
                    purpose="Giải thích cuộc gọi.",
                    target_duration_minutes=1,
                ),
            ],
            target_duration_minutes=2,
            case_file=StoryCaseFile(
                culprit="Người ở phòng 401",
                victim="Tùng",
                motive="Bị Tùng tống tiền.",
                method="Sát hại Tùng rồi dựng cuộc gọi giả.",
                real_timeline=[
                    "22:25 — hung thủ vào phòng 402.",
                    "23:47 — hệ thống thực hiện cuộc gọi giả.",
                ],
                false_timeline=[
                    "23:47 — Tùng còn sống và gọi 113.",
                ],
                initial_belief="Tùng còn sống lúc 23:47.",
                alibi="Hung thủ nói đang ở cửa hàng.",
                clues=["Log máy tính cho thấy cuộc gọi tự động."],
                red_herrings=["Hàng xóm từng tranh cãi với Tùng."],
                investigation_solution="Nam đối chiếu log và camera.",
                twist="Cuộc gọi là bản ghi âm.",
                final_resolution="Hung thủ nhận tội.",
                immutable_facts=[
                    "Tùng dùng điện thoại có số được 113 ghi nhận.",
                    "Cuộc gọi diễn ra lúc 23:47.",
                    "Hung thủ ở phòng 401.",
                ],
                reveal_order=[
                    StoryReveal(
                        order=1,
                        beat=1,
                        event="discover_body",
                        reveals="Tùng được tìm thấy đã chết.",
                        audience_belief_after=(
                            "Cuộc gọi 23:47 có thể là lời kêu cứu cuối cùng."
                        ),
                    ),
                ],
            ),
        )
        provider = RecordingProvider()
        generator = NarrationGenerator(provider=provider)

        narration = generator.generate(
            blueprint=blueprint,
        )

        self.assertEqual(1, len(provider.prompts))
        self.assertIn("PRIVATE, IMMUTABLE CASE FILE", provider.prompts[0])
        self.assertIn("số máy bàn của Trọng", provider.prompts[0])
        self.assertIn("Initial false timeline / audience belief", provider.prompts[0])
        self.assertIn("Planned information-reveal order", provider.prompts[0])
        self.assertEqual(1, len(narration.segments))
        self.assertEqual("Full narration", narration.segments[0].title)


if __name__ == "__main__":
    unittest.main()