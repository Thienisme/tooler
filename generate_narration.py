import sys
from pathlib import Path

from dotenv import load_dotenv

from ai_mystery_story.application.narration.narration_generator import (
    NarrationGenerator,
)
from ai_mystery_story.application.narration_service import (
    NarrationService,
)
from ai_mystery_story.infrastructure.ai.ai_provider import AIProvider
from ai_mystery_story.infrastructure.ai.fakes.fake_provider import (
    FakeProvider,
)
from ai_mystery_story.infrastructure.ai.gemini_provider import (
    GeminiProvider,
)
from ai_mystery_story.infrastructure.repositories.json_story_repository import (
    JsonStoryRepository,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def create_provider(mode: str) -> AIProvider:
    if mode == "fake":
        return FakeProvider()

    if mode == "gemini":
        return GeminiProvider()

    raise ValueError(
        f"Unknown provider mode: {mode}"
    )


def main() -> None:

    if len(sys.argv) != 3:
        print("Usage:")
        print(
            "  python generate_narration.py "
            "[fake|gemini] <topic-id>"
        )
        print()
        print("Example:")
        print(
            "  python generate_narration.py "
            "gemini mystery-000"
        )
        sys.exit(1)

    mode = sys.argv[1]
    topic_id = sys.argv[2]

    load_dotenv()

    repository = JsonStoryRepository(
        projects_dir=PROJECT_ROOT / "projects",
    )

    story = repository.get_by_topic_id(topic_id)

    if story is None:
        print(
            f"Story not found for topic: {topic_id}"
        )
        sys.exit(1)

    print()
    print("=" * 60)
    print("GENERATING MYSTERY NARRATION")
    print("=" * 60)
    print()

    print(f"Topic ID: {topic_id}")
    print(f"Story ID: {story.id}")
    print(f"Story: {story.blueprint.title}")
    print(
        f"Target duration: "
        f"{story.blueprint.target_duration_minutes} minutes"
    )
    print(f"Provider: {mode}")
    print()

    provider = create_provider(mode)

    narration_generator = NarrationGenerator(
        provider=provider,
    )

    service = NarrationService(
        narration_generator=narration_generator,
    )

    story = service.generate(story)

    repository.save(
        story,
        topic_id=topic_id,
    )

    print()
    print("=" * 60)
    print("MYSTERY NARRATION GENERATED")
    print("=" * 60)
    print()

    if story.narration is not None:
        print(
            f"Narration segments: "
            f"{len(story.narration.segments)}"
        )

        word_count = sum(
            len(seg.text.split())
            for seg in story.narration.segments
        )

        print(
            f"Narration words: "
            f"{word_count:,}"
        )

    print()

    print(
        f"Saved to: "
        f"projects/{topic_id}/story.json"
    )


if __name__ == "__main__":
    main()
