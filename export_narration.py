import sys
from pathlib import Path

from ai_mystery_story.infrastructure.repositories.json_story_repository import (
    JsonStoryRepository,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> None:
    if len(sys.argv) != 2:
        print(
            "Usage: "
            "python export_narration.py <topic-id>"
        )
        sys.exit(1)

    topic_id = sys.argv[1]

    repository = JsonStoryRepository(
        projects_dir=PROJECT_ROOT / "projects",
    )

    story = repository.get_by_topic_id(topic_id)

    if story is None:
        print(f"Story not found for topic: {topic_id}")
        sys.exit(1)

    if story.narration is None:
        print(
            f"Narration not found for topic: {topic_id}"
        )
        sys.exit(1)

    narration_text = story.narration.full_text().strip()

    narration_text = narration_text.replace(
            "\n\n",
            "\n",
        )

    if not narration_text:
        print(
            f"Narration is empty for topic: {topic_id}"
        )
        sys.exit(1)

    output_path = (
        PROJECT_ROOT
        / "projects"
        / topic_id
        / "narration_full.txt"
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        narration_text + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 60)
    print("NARRATION EXPORTED")
    print("=" * 60)
    print()
    print(f"Story: {story.blueprint.title}")
    print(
        f"Segments: "
        f"{len(story.narration.segments)}"
    )
    print(
        f"Characters: "
        f"{len(narration_text)}"
    )
    print()
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()


