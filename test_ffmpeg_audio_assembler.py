import json
import shutil
import subprocess
from pathlib import Path

from ai_mystery_story.domain.audio.audio_segment import (
    AudioSegment,
)
from ai_mystery_story.infrastructure.audio.ffmpeg_audio_assembler import (
    FFmpegAudioAssembler,
)


TEST_AUDIO_DIR = Path("test_ffmpeg_audio")


def create_audio(
    output_path: Path,
    duration: float,
    frequency: int,
    volume: float = 1.0,
) -> None:

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration={duration}",
        "-af",
        f"volume={volume}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(output_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr)


def measure_lufs(path: Path) -> float:
    """
    Measure integrated loudness (LUFS) of an audio file
    using ffmpeg loudnorm analysis pass.
    """

    command = [
        "ffmpeg",
        "-i",
        str(path),
        "-af",
        "loudnorm=print_format=json",
        "-f",
        "null",
        "-",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    stderr = result.stderr

    start = stderr.rindex("{")
    end = stderr.rindex("}") + 1

    stats = json.loads(stderr[start:end])

    return float(stats["input_i"])


def main() -> None:

    if TEST_AUDIO_DIR.exists():
        shutil.rmtree(TEST_AUDIO_DIR)

    TEST_AUDIO_DIR.mkdir()

    segment_01 = (
        TEST_AUDIO_DIR / "segment_01.mp3"
    )

    segment_02 = (
        TEST_AUDIO_DIR / "segment_02.mp3"
    )

    segment_03 = (
        TEST_AUDIO_DIR / "segment_03.mp3"
    )

    # Different volumes, like intro/outro vs narration.
    create_audio(
        segment_01,
        duration=1.0,
        frequency=440,
        volume=1.0,   # loud (0 dB)
    )

    create_audio(
        segment_02,
        duration=2.0,
        frequency=550,
        volume=0.1,   # quiet (-20 dB)
    )

    create_audio(
        segment_03,
        duration=3.0,
        frequency=660,
        volume=0.5,   # medium (-6 dB)
    )

    lufs_01 = measure_lufs(segment_01)
    lufs_02 = measure_lufs(segment_02)
    lufs_03 = measure_lufs(segment_03)

    print("Input loudness (before):")
    print(f"  segment_01: {lufs_01:.1f} LUFS")
    print(f"  segment_02: {lufs_02:.1f} LUFS")
    print(f"  segment_03: {lufs_03:.1f} LUFS")

    segments = [
        AudioSegment(
            order=3,
            title="Beat 3",
            source_text="Text 3",
            file_path=segment_03,
            duration_seconds=3.0,
        ),
        AudioSegment(
            order=1,
            title="Beat 1",
            source_text="Text 1",
            file_path=segment_01,
            duration_seconds=1.0,
        ),
        AudioSegment(
            order=2,
            title="Beat 2",
            source_text="Text 2",
            file_path=segment_02,
            duration_seconds=2.0,
        ),
    ]

    output_path = (
        TEST_AUDIO_DIR / "narration_full.mp3"
    )

    assembler = FFmpegAudioAssembler()

    duration = assembler.assemble(
        segments=segments,
        output_path=output_path,
    )

    print("FFmpeg audio assembly completed")
    print(f"Duration: {duration:.2f} seconds")
    print(f"Output: {output_path}")

    assert output_path.exists()
    assert output_path.stat().st_size > 0

    # Small tolerance because MP3 encoding can alter
    # the exact duration slightly.
    assert 5.5 <= duration <= 6.5

    # Loudness sync: every segment was normalized to the
    # same target (-16 LUFS), so the final assembled file
    # must also sit at that target (small tolerance).
    lufs_output = measure_lufs(output_path)

    print(f"Output loudness (after): {lufs_output:.1f} LUFS")

    assert -17.5 <= lufs_output <= -14.5, (
        f"Output loudness {lufs_output:.1f} LUFS is not "
        f"near the -16 LUFS target"
    )

    print("TEST PASSED")

    shutil.rmtree(TEST_AUDIO_DIR)


if __name__ == "__main__":
    main()
