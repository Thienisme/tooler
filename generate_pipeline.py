from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
TOPICS_FILE = PROJECT_ROOT / "data" / "horror_topics.json"

DELAY_SECONDS = int(os.getenv("PIPELINE_DELAY_SECONDS", "10"))


def load_topic(topic_id: str) -> dict:
    if not TOPICS_FILE.exists():
        raise FileNotFoundError(
            f"Topics file not found: {TOPICS_FILE}"
        )

    data = json.loads(
        TOPICS_FILE.read_text(encoding="utf-8")
    )

    for topic in data.get("topics", []):
        if topic.get("id") == topic_id:
            return topic

    raise RuntimeError(
        f"Topic not found: {topic_id}"
    )


def run_command(command: list[str]) -> None:
    print()
    print("$", " ".join(command))
    print()

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code "
            f"{result.returncode}: {' '.join(command)}"
        )


def sleep_between_steps() -> None:
    if DELAY_SECONDS <= 0:
        return

    print()
    print(
        f"Waiting {DELAY_SECONDS} seconds "
        f"before the next step..."
    )

    time.sleep(DELAY_SECONDS)


def main() -> None:
    if len(sys.argv) != 2:
        print(
            "Usage: python generate_pipeline.py <topic_id>"
        )
        print()
        print(
            "Example: "
            "python generate_pipeline.py horror-001"
        )
        sys.exit(1)

    topic_id = sys.argv[1]

    print()
    print("=" * 60)
    print("HORROR STORY PIPELINE")
    print("=" * 60)
    print()
    print(f"Topic ID: {topic_id}")
    print(f"Delay: {DELAY_SECONDS}s")

    topic = load_topic(topic_id)
    story_id = topic.get("story_id")

    # ---------------------------------------------------------
    # STEP 1: Generate story
    # ---------------------------------------------------------

    print()
    print("=" * 60)
    print("STEP 1/3 - GENERATE STORY")
    print("=" * 60)

    if story_id:
        print()
        print("Story already exists.")
        print(f"Story ID: {story_id}")
        print("Skipping generate_story.py")

    else:
        run_command(
            [
                sys.executable,
                "generate_story.py",
                topic_id,
            ]
        )

        topic = load_topic(topic_id)
        story_id = topic.get("story_id")

        if not story_id:
            raise RuntimeError(
                f"generate_story.py completed but no "
                f"story_id was found for topic: {topic_id}"
            )

        print()
        print(f"Story ID: {story_id}")

        sleep_between_steps()

    # ---------------------------------------------------------
    # STEP 2: Generate narration
    # ---------------------------------------------------------

    print()
    print("=" * 60)
    print("STEP 2/3 - GENERATE NARRATION")
    print("=" * 60)
    print()
    print(f"Story ID: {story_id}")

    run_command(
        [
            sys.executable,
            "generate_narration.py",
            "gemini",
            story_id,
        ]
    )

    sleep_between_steps()

    # ---------------------------------------------------------
    # STEP 3: Export narration
    # ---------------------------------------------------------

    print()
    print("=" * 60)
    print("STEP 3/3 - EXPORT NARRATION")
    print("=" * 60)
    print()
    print(f"Story ID: {story_id}")

    run_command(
        [
            sys.executable,
            "export_narration.py",
            story_id,
        ]
    )

    narration_file = (
        PROJECT_ROOT
        / "projects"
        / story_id
        / "narration_full.txt"
    )

    print()
    print("=" * 60)
    print("PIPELINE COMPLETED")
    print("=" * 60)
    print()
    print(f"Topic ID: {topic_id}")
    print(f"Story ID: {story_id}")
    print()
    print(f"Narration: {narration_file}")
    print()


if __name__ == "__main__":
    main()
