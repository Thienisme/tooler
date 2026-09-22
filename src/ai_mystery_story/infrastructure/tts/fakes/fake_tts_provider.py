import subprocess
from pathlib import Path

from ai_mystery_story.infrastructure.tts.tts_provider import (
    TTSProvider,
)


class FakeTTSProvider(TTSProvider):

    def __init__(self):
        self.calls: list[str] = []

    def generate(
        self,
        text: str,
        output_path: Path,
    ) -> float:

        self.calls.append(text)

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        duration = 1.0

        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            str(duration),
            "-c:a",
            "libmp3lame",
            "-q:a",
            "9",
            str(output_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "FakeTTSProvider failed to generate audio:\n"
                f"{result.stderr}"
            )

        return duration