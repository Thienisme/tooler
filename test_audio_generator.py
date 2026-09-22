import shutil
from pathlib import Path

from ai_mystery_story.application.audio.audio_generator import (
    AudioGenerator,
)
from ai_mystery_story.application.audio_assembly_service import (
    AudioAssemblyService,
)
from ai_mystery_story.application.tts_service import (
    TTSService,
)
from ai_mystery_story.domain.narration.narration import (
    Narration,
)
from ai_mystery_story.domain.narration.narration_segment import (
    NarrationSegment,
)
from ai_mystery_story.domain.story.horror_story import (
    HorrorStory,
)
from ai_mystery_story.domain.story.story_blueprint import (
    StoryBlueprint,
)
from ai_mystery_story.infrastructure.audio.fakes.fake_audio_assembler import (
    FakeAudioAssembler,
)
from ai_mystery_story.infrastructure.tts.fakes.fake_tts_provider import (
    FakeTTSProvider,
)


OUTPUT_DIR = Path("test_generated_audio")


def create_story() -> HorrorStory:

    blueprint = StoryBlueprint(
        title="Test Story",
        premise="Test",
        setting="Test",
        characters=[],
        beats=[],
        target_duration_minutes=2,
    )

    story = HorrorStory.create(
        blueprint
    )

    story.narration = Narration(
        segments=[
            NarrationSegment(
                order=1,
                title="Beat 1",
                text="Đoạn một",
            ),
            NarrationSegment(
                order=2,
                title="Beat 2",
                text="Đoạn hai",
            ),
        ]
    )

    return story


def main():

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)

    story = create_story()

    tts = TTSService(
        FakeTTSProvider()
    )

    assembler = AudioAssemblyService(
        FakeAudioAssembler()
    )

    generator = AudioGenerator(
        tts_service=tts,
        audio_assembly_service=assembler,
    )

    audio = generator.generate(
        story=story,
        output_dir=OUTPUT_DIR,
    )

    assert story.audio is audio

    assert len(audio.segments) == 2

    assert (
        OUTPUT_DIR
        / "segments"
        / "segment_01.mp3"
    ).exists()

    assert (
        OUTPUT_DIR
        / "segments"
        / "segment_02.mp3"
    ).exists()

    assert (
        OUTPUT_DIR
        / "narration_full.mp3"
    ).exists()

    print("Audio generator completed")
    print(
        f"Segments: {len(audio.segments)}"
    )
    print(
        f"Duration: {audio.duration_seconds}"
    )
    print(
        f"Full audio: {audio.full_audio_path}"
    )
    print("TEST PASSED")

    shutil.rmtree(OUTPUT_DIR)


if __name__ == "__main__":
    main()
