import json
import shutil
from pathlib import Path

from ai_mystery_story.application.narration.narration_generator import (
    NarrationGenerator,
)
from ai_mystery_story.application.narration.narration_plan_generator import (
    NarrationPlanGenerator,
)
from ai_mystery_story.application.narration_service import (
    NarrationService,
)
from ai_mystery_story.domain.story.horror_story import HorrorStory
from ai_mystery_story.domain.story.story_beat import StoryBeat
from ai_mystery_story.domain.story.story_blueprint import StoryBlueprint
from ai_mystery_story.infrastructure.ai.fakes.fake_provider import (
    FakeProvider,
)
from ai_mystery_story.infrastructure.repositories.json_story_repository import (
    JsonStoryRepository,
)


TEST_PROJECTS_DIR = Path("test_projects")


def create_test_story() -> HorrorStory:
    blueprint = StoryBlueprint(
        title="Test Horror Story",
        premise="A test horror story.",
        setting="An old apartment building.",
        characters=[
            "Minh: nhân vật chính.",
        ],
        beats=[
            StoryBeat(
                order=1,
                title="Beat 1",
                summary="Minh hears a strange sound.",
                purpose="Introduce the horror.",
                target_duration_minutes=1,
            ),
            StoryBeat(
                order=2,
                title="Beat 2",
                summary="The sound becomes closer.",
                purpose="Increase the tension.",
                target_duration_minutes=1,
            ),
        ],
        target_duration_minutes=2,
    )

    return HorrorStory.create(blueprint)


def main() -> None:
    if TEST_PROJECTS_DIR.exists():
        shutil.rmtree(TEST_PROJECTS_DIR)

    repository = JsonStoryRepository(
        projects_dir=TEST_PROJECTS_DIR,
    )

    story = create_test_story()

    # Save the initial story first.
    repository.save(story)

    # Load it back exactly like the real application will.
    loaded_story = repository.get(story.id)

    assert loaded_story is not None
    assert loaded_story.id == story.id
    assert loaded_story.narration is None

    provider = FakeProvider()

    plan_generator = NarrationPlanGenerator()

    narration_generator = NarrationGenerator(
        provider=provider,
    )

    service = NarrationService(
        plan_generator=plan_generator,
        narration_generator=narration_generator,
    )

    result = service.generate(loaded_story)

    # Service should return the same story object.
    assert result is loaded_story

    # Narration should have been generated.
    assert result.narration is not None

    assert len(result.narration.segments) == 2

    assert result.narration.segments[0].order == 1
    assert result.narration.segments[0].title == "Beat 1"
    assert result.narration.segments[0].text

    assert result.narration.segments[1].order == 2
    assert result.narration.segments[1].title == "Beat 2"
    assert result.narration.segments[1].text

    # There should be one narration segment per story beat.
    assert len(result.narration.segments) == 2

    # Verify segment order.
    assert result.narration.segments[0].order == 1
    assert result.narration.segments[1].order == 2

    # Verify segment titles.
    assert result.narration.segments[0].title == "Beat 1"
    assert result.narration.segments[1].title == "Beat 2"

    # Verify segment text exists.
    assert result.narration.segments[0].text
    assert result.narration.segments[1].text

    # One AI call per beat.
    assert len(provider.prompts) == 2

    # Verify the prompts contain the expected beat information.
    assert "Beat 1" in provider.prompts[0]
    assert "Beat 2" in provider.prompts[1]

    # Save the generated narration.
    repository.save(result)

    # Load the persisted story again.
    persisted_story = repository.get(story.id)

    assert persisted_story is not None
    assert persisted_story.narration is not None

    # Verify persisted narration structure.
    assert len(persisted_story.narration.segments) == 2

    assert (
        persisted_story.narration.segments[0].text
        == result.narration.segments[0].text
    )

    assert (
        persisted_story.narration.segments[1].text
        == result.narration.segments[1].text
    )

    # Verify the actual JSON file exists.
    story_file = (
        TEST_PROJECTS_DIR
        / str(story.id)
        / "story.json"
    )

    assert story_file.exists()

    # Verify JSON can be parsed.
    data = json.loads(
        story_file.read_text(encoding="utf-8")
    )

    assert data["id"] == str(story.id)

    # Verify narration is stored as an object.
    assert isinstance(data["narration"], dict)

    assert "segments" in data["narration"]

    assert len(data["narration"]["segments"]) == 2

    # Verify persisted JSON segment data.
    assert data["narration"]["segments"][0]["order"] == 1
    assert data["narration"]["segments"][0]["title"] == "Beat 1"

    assert data["narration"]["segments"][1]["order"] == 2
    assert data["narration"]["segments"][1]["title"] == "Beat 2"

    assert data["narration"]["segments"][0]["text"]
    assert data["narration"]["segments"][1]["text"]

    # Full narration should still be available as plain text.
    full_text = result.narration.full_text()

    assert full_text
    assert (
        result.narration.segments[0].text
        in full_text
    )
    assert (
        result.narration.segments[1].text
        in full_text
    )

    print("Generate narration pipeline test passed")
    print(f"Provider calls: {len(provider.prompts)}")
    print("Expected calls: 2")
    print(
        "Narration segments: "
        f"{len(result.narration.segments)}"
    )
    print(
        "Narration length: "
        f"{len(full_text)} characters"
    )
    print(f"Saved to: {story_file}")

    shutil.rmtree(TEST_PROJECTS_DIR)


if __name__ == "__main__":
    main()