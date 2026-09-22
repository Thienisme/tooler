from pathlib import Path

from ai_mystery_story.domain.audio.audio_segment import (
    AudioSegment,
)
from ai_mystery_story.domain.audio.audio_track import (
    AudioTrack,
)


segments = [
    AudioSegment(
        order=1,
        title="Beat 1",
        source_text="Text 1",
        file_path=Path("segment_01.mp3"),
        duration_seconds=10.0,
    ),
    AudioSegment(
        order=2,
        title="Beat 2",
        source_text="Text 2",
        file_path=Path("segment_02.mp3"),
        duration_seconds=20.0,
    ),
    AudioSegment(
        order=3,
        title="Beat 3",
        source_text="Text 3",
        file_path=Path("segment_03.mp3"),
        duration_seconds=30.0,
    ),
]


track = AudioTrack(
    segments=segments,
    full_audio_path=Path(
        "audio/narration_full.mp3"
    ),
    duration_seconds=60.0,
)


assert len(track.segments) == 3

assert track.full_audio_path == Path(
    "audio/narration_full.mp3"
)

assert track.duration_seconds == 60.0

assert track.total_duration() == 60.0


print("AudioTrack created successfully")
print(f"Segments: {len(track.segments)}")
print(f"Duration: {track.duration_seconds}")
print(f"Calculated duration: {track.total_duration()}")
print(f"Full audio: {track.full_audio_path}")
print("TEST PASSED")
