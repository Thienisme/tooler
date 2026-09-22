from pathlib import Path
import shutil

from ai_mystery_story.application.audio_assembly_service import (
    AudioAssemblyService,
)
from ai_mystery_story.domain.audio.audio_segment import (
    AudioSegment,
)
from ai_mystery_story.infrastructure.audio.fakes.fake_audio_assembler import (
    FakeAudioAssembler,
)


TEST_AUDIO_DIR = Path("test_audio_assembly")


def main() -> None:

    if TEST_AUDIO_DIR.exists():
        shutil.rmtree(TEST_AUDIO_DIR)

    assembler = FakeAudioAssembler()

    service = AudioAssemblyService(
        assembler=assembler,
    )

    segments = [
        AudioSegment(
            order=3,
            title="Beat 3",
            source_text="Text 3",
            file_path=Path("segment_03.mp3"),
            duration_seconds=3.0,
        ),
        AudioSegment(
            order=1,
            title="Beat 1",
            source_text="Text 1",
            file_path=Path("segment_01.mp3"),
            duration_seconds=1.0,
        ),
        AudioSegment(
            order=2,
            title="Beat 2",
            source_text="Text 2",
            file_path=Path("segment_02.mp3"),
            duration_seconds=2.0,
        ),
    ]

    output_path = (
        TEST_AUDIO_DIR / "narration_full.mp3"
    )

    duration = service.assemble(
        segments=segments,
        output_path=output_path,
    )

    print("Audio assembly service completed")

    assert len(assembler.calls) == 1

    assembled_segments = assembler.calls[0]

    # Verify ordering.
    assert assembled_segments[0].order == 1
    assert assembled_segments[1].order == 2
    assert assembled_segments[2].order == 3

    # Verify total duration.
    assert duration == 6.0

    # Verify output file.
    assert output_path.exists()

    assert output_path.read_bytes() == (
        b"FAKE_FULL_AUDIO"
    )

    print("Segments:", len(assembled_segments))
    print("Duration:", duration)
    print("Output:", output_path)
    print("TEST PASSED")

    shutil.rmtree(TEST_AUDIO_DIR)


if __name__ == "__main__":
    main()
