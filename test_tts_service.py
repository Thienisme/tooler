import shutil
from pathlib import Path

from ai_mystery_story.application.tts_service import (
    TTSService,
)
from ai_mystery_story.infrastructure.repositories.json_story_repository import (
    JsonStoryRepository,
)
from ai_mystery_story.infrastructure.tts.fakes.fake_tts_provider import (
    FakeTTSProvider,
)


STORY_ID = "34affbe3-01a6-4fbb-9fb2-57ca54c46a27"

PROJECTS_DIR = Path("projects")

TEST_AUDIO_DIR = Path(
    "test_audio"
)


def main() -> None:

    if TEST_AUDIO_DIR.exists():
        shutil.rmtree(TEST_AUDIO_DIR)

    repository = JsonStoryRepository(
        projects_dir=PROJECTS_DIR,
    )

    from uuid import UUID

    story = repository.get(
        UUID(STORY_ID)
    )

    assert story is not None

    assert story.narration is not None

    assert len(
        story.narration.segments
    ) == 9

    provider = FakeTTSProvider()

    service = TTSService(
        provider=provider,
    )

    audio_segments = service.generate(
        story=story,
        output_dir=TEST_AUDIO_DIR,
    )

    print("TTS service generated successfully")

    print(
        f"Provider calls: "
        f"{len(provider.calls)}"
    )

    print("Expected calls: 9")

    assert len(provider.calls) == 9

    assert len(audio_segments) == 9

    for index, segment in enumerate(
        audio_segments,
        start=1,
    ):
        assert segment.order == index

        assert segment.source_text

        assert segment.file_path.exists()

        assert (
            segment.duration_seconds
            == 1.0
        )

        print(
            f"{segment.order}. "
            f"{segment.title} -> "
            f"{segment.file_path}"
        )

    print("TEST PASSED")

    shutil.rmtree(TEST_AUDIO_DIR)


if __name__ == "__main__":
    main()