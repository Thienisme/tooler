from pathlib import Path

from ai_mystery_story.domain.audio.audio_segment import (
    AudioSegment,
)
from ai_mystery_story.domain.audio.audio_track import (
    AudioTrack,
)
from ai_mystery_story.domain.story.horror_story import (
    HorrorStory,
)
from ai_mystery_story.domain.story.story_blueprint import (
    StoryBlueprint,
)


blueprint = StoryBlueprint(
    title="Test Horror Story",
    premise="Test premise",
    setting="Test setting",
    characters=[
        "Minh",
    ],
    beats=[],
    target_duration_minutes=10,
)


story = HorrorStory.create(
    blueprint
)

assert story.narration is None
assert story.audio is None


audio = AudioTrack(
    segments=[
        AudioSegment(
            order=1,
            title="Beat 1",
            source_text="Test text",
            file_path=Path(
                "audio/segment_01.mp3"
            ),
            duration_seconds=10.0,
        )
    ],
    full_audio_path=Path(
        "audio/narration_full.mp3"
    ),
    duration_seconds=10.0,
)


story.audio = audio


assert story.audio is not None
assert len(story.audio.segments) == 1
assert story.audio.segments[0].order == 1
assert story.audio.duration_seconds == 10.0
assert story.audio.full_audio_path == Path(
    "audio/narration_full.mp3"
)


print("HorrorStory audio created successfully")
print(
    f"Segments: {len(story.audio.segments)}"
)
print(
    f"Duration: "
    f"{story.audio.duration_seconds}"
)
print(
    f"Full audio: "
    f"{story.audio.full_audio_path}"
)
print("TEST PASSED")
