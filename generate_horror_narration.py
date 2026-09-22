"""generate_horror_narration.py

Convert a horror story text file (projects/<seed-id>/story.txt) into the
MysteryStory / Narration format that generate_audio.py expects, then save
the result back to projects/<seed-id>/story.json.

This script is entirely independent of the mystery pipeline — it does NOT
call any AI model. It simply wraps the existing plain text into the domain
objects that the audio generator understands.

Usage:
    python generate_horror_narration.py <seed-id>

Example:
    python generate_horror_narration.py horror-001

Prerequisites:
    • projects/<seed-id>/story.txt  ← produced by generate_horror_story.py

After running this script:
    python generate_audio.py edge horror-001
"""

import json
import sys
from pathlib import Path
from uuid import uuid4

# ---------------------------------------------------------------------------
# Domain / infrastructure imports (same src package used by the rest of
# the project — no changes to existing code)
# ---------------------------------------------------------------------------
from ai_mystery_story.domain.narration.narration import Narration
from ai_mystery_story.domain.narration.narration_segment import NarrationSegment
from ai_mystery_story.domain.story.mystery_story import MysteryStory
from ai_mystery_story.domain.story.story_blueprint import StoryBlueprint
from ai_mystery_story.infrastructure.repositories.json_story_repository import (
    JsonStoryRepository,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = PROJECT_ROOT / "projects"
SEEDS_FILE = PROJECT_ROOT / "data" / "horror_story_seeds.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_horror_metadata(seed_id: str) -> dict:
    """
    Read the horror-specific story.json written by generate_horror_story.py.
    Falls back to empty values if the file uses the old horror format.
    """
    json_path = PROJECTS_DIR / seed_id / "story.json"
    if not json_path.exists():
        return {}
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return data


def load_story_text(seed_id: str) -> str:
    txt_path = PROJECTS_DIR / seed_id / "story.txt"
    if not txt_path.exists():
        raise RuntimeError(
            f"story.txt not found: {txt_path}\n"
            "Run generate_horror_story.py first."
        )
    text = txt_path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"story.txt is empty: {txt_path}")
    return text


def build_stub_blueprint(meta: dict) -> StoryBlueprint:
    """
    Build a minimal StoryBlueprint that satisfies the serializer without
    any of the mystery-specific fields (crime_type, protagonist, etc.).
    These are filled with placeholder values — they are never used for
    horror stories because the audio generator only reads narration.segments.
    """
    return StoryBlueprint(
        title=meta.get("title", "Horror Story"),
        premise=meta.get("seed", ""),
        setting=meta.get("setting", ""),
        # Mandatory mystery fields — unused by generate_audio.py but
        # required by StoryBlueprint's dataclass definition.
        crime_type="supernatural",
        investigation_type="horror_narration",
        protagonist="narrator",
        central_mystery=meta.get("seed", ""),
        twist_type=", ".join(meta.get("ending_type", [])),
        ending_type=", ".join(meta.get("ending_type", [])),
        characters=[],
        beats=[],
        target_duration_minutes=60,
        case_file=None,
    )


def count_words(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage:")
        print("  python generate_horror_narration.py <seed-id>")
        print()
        print("Example:")
        print("  python generate_horror_narration.py horror-001")
        sys.exit(1)

    seed_id = sys.argv[1]

    # ------------------------------------------------------------------
    # Load inputs
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("WRAPPING HORROR STORY AS NARRATION")
    print("=" * 60)
    print()
    print(f"Seed ID : {seed_id}")

    meta = load_horror_metadata(seed_id)
    story_text = load_story_text(seed_id)

    word_count = count_words(story_text)
    print(f"Title   : {meta.get('title', '(unknown)')}")
    print(f"Words   : {word_count:,}")
    print()

    # ------------------------------------------------------------------
    # Build MysteryStory with a single narration segment
    # ------------------------------------------------------------------
    blueprint = build_stub_blueprint(meta)

    # Preserve the story_id from the horror metadata if available so
    # generate_audio.py can match files correctly; otherwise mint a new one.
    story_uuid = None
    existing_id = meta.get("story_id")
    if existing_id:
        from uuid import UUID
        try:
            story_uuid = UUID(existing_id)
        except ValueError:
            pass

    story = MysteryStory(
        id=story_uuid or uuid4(),
        blueprint=blueprint,
        narration=Narration(
            segments=[
                NarrationSegment(
                    order=1,
                    title="Full narration",
                    text=story_text,
                )
            ]
        ),
    )

    # ------------------------------------------------------------------
    # Save using the same JsonStoryRepository used by the audio generator
    # ------------------------------------------------------------------
    repository = JsonStoryRepository(projects_dir=PROJECTS_DIR)
    repository.save(story, topic_id=seed_id)

    print("Narration wrapped and saved.")
    print()
    print(f"story.json updated : projects/{seed_id}/story.json")
    print()
    print("Next step:")
    print(f"  python generate_audio.py edge {seed_id}")
    print()

    # ------------------------------------------------------------------
    # Quick sanity check — reload and verify
    # ------------------------------------------------------------------
    reloaded = repository.get_by_topic_id(seed_id)
    assert reloaded is not None, "Reload failed"
    assert reloaded.narration is not None, "Narration missing after reload"
    assert len(reloaded.narration.segments) == 1, "Expected 1 segment"
    reloaded_words = count_words(reloaded.narration.segments[0].text)
    print(f"Sanity check passed — reloaded {reloaded_words:,} words from story.json.")
    print()


if __name__ == "__main__":
    main()
