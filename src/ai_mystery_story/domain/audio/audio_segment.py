from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioSegment:
    order: int
    title: str
    source_text: str
    file_path: Path
    duration_seconds: float