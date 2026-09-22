from dataclasses import dataclass


@dataclass
class NarrationSegment:
    order: int
    title: str
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())