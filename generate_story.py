import json
import random
import sys
from pathlib import Path

from dotenv import load_dotenv

from ai_mystery_story.application.generators.gemini_story_generator import (
    GeminiStoryGenerator,
)
from ai_mystery_story.infrastructure.ai.gemini_provider import GeminiProvider
from ai_mystery_story.infrastructure.repositories.json_story_repository import (
    JsonStoryRepository,
)


PROJECT_ROOT = Path(__file__).resolve().parent

TOPICS_FILE = PROJECT_ROOT / "data" / "mystery_topics.json"
MECHANISMS_FILE = PROJECT_ROOT / "data" / "mystery_mechanisms.json"
PROJECTS_DIR = PROJECT_ROOT / "projects"

TARGET_DURATION_MINUTES = 60


def load_topics() -> dict:
    if not TOPICS_FILE.exists():
        raise RuntimeError(
            f"Topics file not found: {TOPICS_FILE}"
        )

    return json.loads(
        TOPICS_FILE.read_text(
            encoding="utf-8"
        )
    )


def load_mechanisms() -> dict[str, dict]:
    """Load mystery_mechanisms.json and return a dict keyed by mechanism id."""
    if not MECHANISMS_FILE.exists():
        return {}

    data = json.loads(
        MECHANISMS_FILE.read_text(encoding="utf-8")
    )

    return {
        m["id"]: m
        for m in data.get("mechanisms", [])
    }


def resolve_mechanisms(
    mechanisms_ref: dict | None,
    mechanisms_lib: dict[str, dict],
) -> dict[str, dict | list[dict] | None]:
    """
    Resolve mechanism IDs from the topic's ``mechanisms`` field into
    full mechanism objects from the library.

    Returns a dict with keys ``primary`` and ``secondary``.
    """
    if not mechanisms_ref:
        return {"primary": None, "secondary": []}

    primary_id = mechanisms_ref.get("primary")
    secondary_ids = mechanisms_ref.get("secondary", [])

    primary = mechanisms_lib.get(primary_id) if primary_id else None
    secondary = [
        mechanisms_lib[sid]
        for sid in secondary_ids
        if sid in mechanisms_lib
    ]

    return {"primary": primary, "secondary": secondary}


def format_fair_play_focus(fair_play_focus: str | dict | None) -> str | None:
    """
    Normalise fair_play_focus to a plain string suitable for the prompt.

    Old format: plain string
    New format: {"required_clues": [...], "reader_should_be_able_to_infer": [...]}
    """
    if not fair_play_focus:
        return None

    if isinstance(fair_play_focus, str):
        return fair_play_focus

    lines: list[str] = []

    required_clues = fair_play_focus.get("required_clues", [])
    if required_clues:
        lines.append("Required clues:")
        for clue in required_clues:
            lines.append(f"  - {clue}")

    inferences = fair_play_focus.get("reader_should_be_able_to_infer", [])
    if inferences:
        lines.append("Reader should be able to infer:")
        for inf in inferences:
            lines.append(f"  - {inf}")

    return "\n".join(lines) if lines else None


def save_topics(data: dict) -> None:
    TOPICS_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def find_topic(
    topics: list[dict],
    selector: str,
) -> dict:

    if selector == "random":

        available = [
            topic
            for topic in topics
            if topic.get("status", "pending") == "pending"
        ]

        if not available:
            raise RuntimeError(
                "No pending mystery topics available."
            )

        return random.choice(available)

    if selector == "next":

        for topic in topics:
            if topic.get("status", "pending") == "pending":
                return topic

        raise RuntimeError(
            "No pending mystery topics available."
        )

    for topic in topics:

        if topic.get("id") == selector:
            return topic

    raise RuntimeError(
        f"Topic not found: {selector}"
    )


def main() -> None:

    if len(sys.argv) != 2:

        print("Usage:")
        print(
            "  python generate_story.py <topic-id>"
        )
        print(
            "  python generate_story.py random"
        )
        print(
            "  python generate_story.py next"
        )

        sys.exit(1)

    selector = sys.argv[1]

    load_dotenv()

    topics_data = load_topics()
    mechanisms_lib = load_mechanisms()

    topics = topics_data.get("topics", [])

    if not topics:
        raise RuntimeError(
            "No topics found in mystery_topics.json"
        )

    topic = find_topic(
        topics=topics,
        selector=selector,
    )

    topic_id = topic["id"]

    title = topic["title"]
    premise = topic["premise"]
    setting = topic["setting"]

    crime_type = topic["crime_type"]
    protagonist = topic["protagonist"]
    ending_type = topic["ending_type"]
    mystery_difficulty = topic.get("mystery_difficulty", "medium")
    creative_constraints = topic.get("creative_constraints", {})
    core_mechanism = topic.get("core_mechanism")
    false_assumption = topic.get("false_assumption")
    misdirection_strategy = topic.get("misdirection_strategy")
    reveal_condition = topic.get("reveal_condition")

    # Resolve mechanism IDs to full objects
    resolved_mechanisms = resolve_mechanisms(
        mechanisms_ref=topic.get("mechanisms"),
        mechanisms_lib=mechanisms_lib,
    )

    # Normalise fair_play_focus: old format is a plain string,
    # new format is an object with required_clues / reader_should_be_able_to_infer
    fair_play_focus = format_fair_play_focus(
        topic.get("fair_play_focus")
    )

    print()
    print("=" * 60)
    print("GENERATING MYSTERY STORY")
    print("=" * 60)
    print()

    print(f"Topic ID: {topic_id}")
    print(f"Topic title: {title}")
    print(f"Status: {topic.get('status', 'pending')}")
    print()

    print("Premise:")
    print(premise)
    print()

    print("Setting:")
    print(setting)
    print()

    print("Crime type:")
    print(crime_type)
    print()

    print("Protagonist:")
    print(protagonist)
    print()

    print("Ending type:")
    print(ending_type)
    print()

    print("Mystery difficulty:")
    print(mystery_difficulty)
    print()

    if resolved_mechanisms["primary"]:
        m = resolved_mechanisms["primary"]
        print(f"Primary mechanism: [{m['id']}] {m['name_vi']}")
        print()

    if resolved_mechanisms["secondary"]:
        labels = ", ".join(
            f"[{m['id']}] {m['name_vi']}"
            for m in resolved_mechanisms["secondary"]
        )
        print(f"Secondary mechanisms: {labels}")
        print()

    if core_mechanism:
        print("Core mechanism:")
        print(core_mechanism)
        print()

    if false_assumption:
        print("False assumption:")
        print(false_assumption)
        print()

    if misdirection_strategy:
        print("Misdirection strategy:")
        print(misdirection_strategy)
        print()

    if reveal_condition:
        print("Reveal condition:")
        print(reveal_condition)
        print()

    if fair_play_focus:
        print("Fair-play focus:")
        print(fair_play_focus)
        print()

    if creative_constraints:
        print("Creative constraints:")
        print(", ".join(
            key
            for key, enabled in creative_constraints.items()
            if enabled
        ))
        print()

    provider = GeminiProvider()

    generator = GeminiStoryGenerator(
        provider=provider,
        max_retries=2,
    )

    repository = JsonStoryRepository(
        projects_dir=PROJECTS_DIR,
    )

    story = generator.generate(
        title=title,
        premise=premise,
        setting=setting,
        crime_type=crime_type,
        protagonist=protagonist,
        ending_type=ending_type,
        mystery_difficulty=mystery_difficulty,
        creative_constraints=creative_constraints,
        core_mechanism=core_mechanism,
        false_assumption=false_assumption,
        misdirection_strategy=misdirection_strategy,
        reveal_condition=reveal_condition,
        resolved_mechanisms=resolved_mechanisms,
        fair_play_focus=fair_play_focus,
        target_duration_minutes=TARGET_DURATION_MINUTES,
    )

    # Save the generated story.
    #
    # Project directory:
    #
    # projects/
    # └── {topic_id}/
    #
    # Example:
    #
    # projects/
    # └── mystery-001/

    repository.save(
        story,
        topic_id=topic_id,
    )

    # Mark the topic as generated only after
    # the story has been successfully saved.

    topic["status"] = "generated"
    topic["story_id"] = str(story.id)

    save_topics(topics_data)

    print()
    print("=" * 60)
    print("MYSTERY STORY GENERATED")
    print("=" * 60)
    print()

    print(f"Topic ID: {topic_id}")
    print(f"Story ID: {story.id}")
    print(f"Title: {story.blueprint.title}")
    print()

    print("Premise:")
    print(story.blueprint.premise)
    print()

    print("Setting:")
    print(story.blueprint.setting)
    print()

    print("Crime type:")
    print(story.blueprint.crime_type)
    print()

    print("Investigation type:")
    print(story.blueprint.investigation_type)
    print()

    print("Protagonist:")
    print(story.blueprint.protagonist)
    print()

    print("Central mystery:")
    print(story.blueprint.central_mystery)
    print()

    print("Twist type:")
    print(story.blueprint.twist_type)
    print()

    print("Ending type:")
    print(story.blueprint.ending_type)
    print()

    print("Characters:")

    for character in story.blueprint.characters:
        print(f"- {character}")

    print()
    print("Story Beats:")

    total_duration = 0

    for beat in story.blueprint.beats:

        total_duration += beat.target_duration_minutes

        print(
            f"{beat.order}. "
            f"{beat.title} "
            f"({beat.target_duration_minutes} min)"
        )

        print(
            f"   {beat.summary}"
        )

        print(
            f"   Purpose: {beat.purpose}"
        )

        print()

    print(
        f"Target duration: "
        f"{story.blueprint.target_duration_minutes} minutes"
    )

    print(
        f"Planned beat duration: "
        f"{total_duration} minutes"
    )

    print()

    print(
        f"Saved to: "
        f"projects/{topic_id}/story.json"
    )

    print(
        f"Topic status updated: "
        f"{TOPICS_FILE}"
    )


if __name__ == "__main__":
    main()
