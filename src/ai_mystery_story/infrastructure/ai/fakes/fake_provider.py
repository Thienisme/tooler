import re

from ai_mystery_story.infrastructure.ai.ai_provider import AIProvider


class FakeProvider(AIProvider):

    def __init__(self):
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)

        match = re.search(
            r"Approximate target length:\s*(\d+)\s*words",
            prompt,
        )

        if not match:
            # Full-story narration is generated in one request and no longer
            # has a per-beat target. Return complete sentences so its output
            # can exercise the same TTS splitter as a real provider.
            return " ".join(
                "Minh bước chậm qua căn phòng cũ và lắng nghe âm thanh kỳ lạ."
                for _ in range(30)
            )

        target_word_count = int(match.group(1))

        words = [
            "Minh",
            "bước",
            "chậm",
            "qua",
            "căn",
            "phòng",
            "cũ",
            "và",
            "lắng",
            "nghe",
            "âm",
            "thanh",
            "kỳ",
            "lạ",
            "đang",
            "vang",
            "lên",
            "trong",
            "bóng",
            "tối",
        ]

        return " ".join(
            words[i % len(words)]
            for i in range(target_word_count)
        )
