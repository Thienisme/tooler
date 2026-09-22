"""
🎬 Video Generator - Generate mystery story video with:
- Background music (ambient mystery atmosphere)
- Subtitles (synced with narration)
- Transitions (cross-dissolve between scenes)
- Ken Burns effect (camera movement)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

# ============================================================
# Resolve ffmpeg binary - prefer bundled binary from bin/
# ============================================================
_FFMPEG_BIN = PROJECT_ROOT / "bin" / "ffmpeg"
_FFMPEG = str(_FFMPEG_BIN) if _FFMPEG_BIN.exists() else "ffmpeg"
_FFPROBE_BIN = PROJECT_ROOT / "bin" / "ffprobe"
_FFPROBE = str(_FFPROBE_BIN) if _FFPROBE_BIN.exists() else "ffprobe"

# ============================================================
# Video settings
# ============================================================

IMAGE_DURATION = 29
THUMBNAIL_DURATION = 9

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080

FPS = 30

MODES = {
    "fast": {
        "output_name": "final_fast.mp4",
        "preset": "ultrafast",
        "crf": "28",
    },
    "quality": {
        "output_name": "final_quality.mp4",
        "preset": "medium",
        "crf": "20",
    },
}

# ============================================================
# Default settings for new features
# ============================================================

DEFAULT_MUSIC_VOLUME = 0.12
DEFAULT_TRANSITION = "dissolve"
DEFAULT_TRANSITION_DURATION = 1.0
DEFAULT_FADE_IN = 1.5
DEFAULT_FADE_OUT = 2.0
DEFAULT_SUBTITLE_FONT_SIZE = 22


def get_audio_duration(audio_file: Path) -> float:
    result = subprocess.run(
        [
            _FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_file),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    return float(result.stdout.strip())


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))

    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    return f"{minutes:02d}:{seconds:02d}"


def render_video(
    *,
    thumbnail: Path,
    images: list[Path],
    audio_file: Path,
    output_file: Path,
    duration: float,
    mode: str,
    # New features
    background_music_path: Path | None = None,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
    subtitle_text: str | None = None,
    subtitle_timestamps: list[dict] | None = None,
    enable_transitions: bool = True,
    transition_type: str = DEFAULT_TRANSITION,
    transition_duration: float = DEFAULT_TRANSITION_DURATION,
    # Intro/outro offsets so subtitles align with narration inside the audio
    intro_offset: float = 0.0,
    outro_offset: float = 0.0,
) -> None:

    video_dir = output_file.parent
    video_dir.mkdir(parents=True, exist_ok=True)

    temp_dir = video_dir / "_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 60)
    print("GENERATING MYSTERY VIDEO")
    print("=" * 60)
    print()
    print(f"Mode: {mode}")
    print(f"Duration: {format_time(duration)}")
    print(f"Resolution: {VIDEO_WIDTH}x{VIDEO_HEIGHT}")
    print(f"FPS: {FPS}")
    print()
    print("Features:")
    print(f"  🎵 Background Music: {'✅' if background_music_path else '❌'}")
    print(f"  📝 Subtitles: {'✅' if subtitle_text else '❌'}{'(timestamped)' if subtitle_timestamps else ''}")
    print(f"  ✨ Transitions: {'✅' if enable_transitions else '❌'}")
    if enable_transitions:
        print(f"     Type: {transition_type} ({transition_duration}s)")
    print()

    # ============================================================
    # Step 1: Create video segments with Ken Burns effect
    # (skipped if segments already exist from a previous run)
    # ============================================================

    print("Step 1/5: Creating video segments...")

    # Check if segments already exist (resume support)
    existing_segments = sorted(temp_dir.glob("segment_*.mp4"))
    if existing_segments:
        print(f"  ⏩ Resuming: found {len(existing_segments)} existing segments, skipping Step 1")
        segment_files = existing_segments
    else:
        segment_files = _create_video_segments(
            thumbnail=thumbnail,
            images=images,
            duration=duration,
            temp_dir=temp_dir,
            mode=mode,
            enable_transitions=enable_transitions,
            transition_duration=transition_duration if enable_transitions else 0.0,
        )

    # ============================================================
    # Step 2: Apply transitions (or simple concat)
    # (skipped if _silent.mp4 already exists from a previous run)
    # ============================================================

    print()
    print("Step 2/5: Applying transitions...")

    silent_video = temp_dir / "_silent.mp4"

    if silent_video.exists() and silent_video.stat().st_size > 0:
        print(f"  ⏩ Resuming: _silent.mp4 already exists, skipping Step 2")
    elif enable_transitions and len(segment_files) > 1:
        from src.ai_mystery_story.infrastructure.video.video_transitions import (
            apply_xfade_transitions,
        )

        apply_xfade_transitions(
            segment_files=segment_files,
            output_path=silent_video,
            transition=transition_type,
            transition_duration=transition_duration,
            fade_in_duration=DEFAULT_FADE_IN,
            fade_out_duration=DEFAULT_FADE_OUT,
            fps=FPS,
        )
    else:
        # Simple concat without transitions
        _simple_concat(segment_files, silent_video)

    # ============================================================
    # Step 2b: Pad _silent.mp4 to match audio duration
    # Transitions shorten the video (each xfade overlaps 2 clips),
    # so we hold the last frame to fill the gap.
    # ============================================================

    actual_video_dur = _get_stream_duration(silent_video)
    gap = duration - actual_video_dur
    if gap > 0.5:
        print()
        print(f"  ⚠️  Video ({actual_video_dur:.2f}s) shorter than audio "
              f"({duration:.2f}s) by {gap:.2f}s — padding with last frame...")
        padded_video = temp_dir / "_silent_padded.mp4"
        _pad_video_to_duration(
            input_path=silent_video,
            output_path=padded_video,
            target_duration=duration,
            fps=FPS,
        )
        silent_video = padded_video
        print(f"  ✅ Padded to {duration:.2f}s")

    # ============================================================
    # Step 3: Mix audio (narration + background music)
    # ============================================================

    print()
    print("Step 3/5: Processing audio...")

    mixed_audio = temp_dir / "_mixed_audio.mp3"

    if background_music_path and background_music_path.exists():
        from src.ai_mystery_story.infrastructure.audio.background_music_provider import (
            BackgroundMusicProvider,
            AudioMixer,
        )

        # Generate/process background music to match duration
        music_provider = BackgroundMusicProvider(
            music_path=background_music_path,
            volume=music_volume,
        )

        processed_music = temp_dir / "_bgm.mp3"
        music_provider.get_music(
            duration_seconds=duration,
            output_path=processed_music,
        )

        # Mix narration + music
        mixer = AudioMixer(
            narration_volume=1.0,
            music_volume=1.0,  # Volume already applied in provider
        )

        mixer.mix(
            narration_path=audio_file,
            music_path=processed_music,
            output_path=mixed_audio,
        )

        final_audio = mixed_audio
    else:
        # Use narration only
        final_audio = audio_file

    # ============================================================
    # Step 4: Combine video + audio
    # ============================================================

    print()
    print("Step 4/5: Combining video and audio...")

    video_with_audio = temp_dir / "_video_with_audio.mp4"

    command = [
        _FFMPEG,
        "-y",
        "-i", str(silent_video),
        "-i", str(final_audio),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", str(duration),
        "-movflags", "+faststart",
        str(video_with_audio),
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        print()
        print("FFmpeg error:")
        print(result.stderr)
        raise RuntimeError(
            f"FFmpeg failed while adding audio "
            f"(exit code {result.returncode})"
        )

    # ============================================================
    # Step 5: Burn subtitles (if provided)
    # ============================================================

    print()
    print("Step 5/5: Adding subtitles...")

    if subtitle_text and subtitle_text.strip():
        from src.ai_mystery_story.infrastructure.subtitle.subtitle_generator import (
            SubtitleGenerator,
            burn_subtitles_into_video,
        )

        # Generate SRT file
        srt_path = temp_dir / "_subtitles.srt"

        subtitle_gen = SubtitleGenerator(
            max_chars_per_line=42,
            max_lines=2,
        )

        narration_duration = duration - intro_offset - outro_offset
        if intro_offset > 0 or outro_offset > 0:
            print(
                f"  Subtitle window: [{intro_offset:.2f}s → "
                f"{duration - outro_offset:.2f}s] "
                f"(narration: {narration_duration:.2f}s)"
            )

        # Use real timestamps if available, else fallback to proportional
        if subtitle_timestamps:
            print("  Using real word timestamps from TTS (shifted by intro)")
            # Shift every word timestamp forward by intro_offset
            shifted = [
                {
                    **word,
                    "start": word.get("start", 0) + intro_offset,
                    "end": word.get("end", 0) + intro_offset,
                }
                for word in subtitle_timestamps
                # Also drop words that fall inside outro
                if word.get("end", 0) + intro_offset < duration - outro_offset
            ]
            subtitle_gen.generate_from_word_timestamps(
                words=shifted,
                output_path=srt_path,
            )
        else:
            print("  Using proportional timing (no timestamps available)")
            subtitle_segments = _split_subtitle_text(
                subtitle_text,
                narration_duration=max(narration_duration, 1.0),
                start_offset=intro_offset,
            )
            subtitle_gen.generate_from_segments(
                segments=subtitle_segments,
                output_path=srt_path,
            )

        # Burn subtitles into video
        burn_subtitles_into_video(
            video_path=video_with_audio,
            srt_path=srt_path,
            output_path=output_file,
            font_size=DEFAULT_SUBTITLE_FONT_SIZE,
        )
    else:
        # No subtitles - just copy
        import shutil
        shutil.copy2(video_with_audio, output_file)

    # ============================================================
    # Cleanup
    # ============================================================

    print()
    print("Cleaning up temporary files...")

    import shutil
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("VIDEO GENERATED SUCCESSFULLY!")
    print("=" * 60)
    print()
    print(f"Output: {output_file}")
    print(f"Size: {output_file.stat().st_size // (1024 * 1024)} MB")
    print()


def _create_video_segments(
    *,
    thumbnail: Path,
    images: list[Path],
    duration: float,
    temp_dir: Path,
    mode: str,
    enable_transitions: bool = True,
    transition_duration: float = 1.0,
) -> list[Path]:
    """Create individual video segments from images."""
    segment_files = []

    thumbnail_duration = min(THUMBNAIL_DURATION, duration)
    remaining_duration = max(0.0, duration - thumbnail_duration)

    # Effective duration of each image segment after transition overlap loss
    effective_img_duration = IMAGE_DURATION

    image_segment_count = int(
        (remaining_duration + effective_img_duration - 0.001)
        // effective_img_duration
    )
    total_segments = 1 + image_segment_count

    print(f"  Creating {total_segments} segments...")

    extra_trans_pad = transition_duration if enable_transitions else 0.0

    # Build sequence
    sequence: list[tuple[Path, float, bool]] = [
        (thumbnail, thumbnail_duration + extra_trans_pad, True)
    ]
    for index in range(image_segment_count):
        segment_start = thumbnail_duration + index * effective_img_duration
        segment_dur = min(effective_img_duration, duration - segment_start) + extra_trans_pad
        sequence.append((images[index % len(images)], segment_dur, False))

    preset = MODES[mode]["preset"]
    crf = MODES[mode]["crf"]

    for index, (image, segment_duration, is_thumbnail) in enumerate(sequence):
        segment_file = temp_dir / f"segment_{index:04d}.mp4"
        segment_files.append(segment_file)

        print(f"  [{index + 1}/{total_segments}] {image.name} ({segment_duration:.2f}s)")

        # Image normalization
        scale_filter = (
            f"scale={VIDEO_WIDTH * 1.3}:{VIDEO_HEIGHT * 1.3}:"
            "force_original_aspect_ratio=increase"
        )

        duration_expr = f"{segment_duration:.6f}"

        if not is_thumbnail and index % 4 == 0:
            crop_filter = (
                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                f"(in_w-out_w)/2:"
                f"(in_h-out_h)*t/{duration_expr}"
            )
        elif not is_thumbnail and index % 4 == 1:
            crop_filter = (
                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                f"(in_w-out_w)/2:"
                f"(in_h-out_h)*(1-t/{duration_expr})"
            )
        elif not is_thumbnail and index % 4 == 2:
            crop_filter = (
                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                f"(in_w-out_w)*t/{duration_expr}:"
                f"(in_h-out_h)/2"
            )
        elif not is_thumbnail:
            crop_filter = (
                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                f"(in_w-out_w)*(1-t/{duration_expr}):"
                f"(in_h-out_h)/2"
            )

        # Firelight flicker effect
        firelight_filter = (
            "eq="
            "brightness="
            "'"
            "0.015"
            "+0.020*sin(2*PI*t/3.7)"
            "+0.012*sin(2*PI*t/1.9)"
            "+0.008*sin(2*PI*t/5.3)"
            "'"
        )

        if is_thumbnail:
            filter_complex = (
                f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                "(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,format=yuv420p"
            )
        else:
            filter_complex = (
                f"{scale_filter},{crop_filter},{firelight_filter},"
                "setsar=1,format=yuv420p"
            )

        command = [
            _FFMPEG,
            "-y",
            "-loop", "1",
            "-i", str(image),
            "-t", str(segment_duration),
            "-vf", filter_complex,
            "-r", str(FPS),
            "-an",
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            str(segment_file),
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            print()
            print("FFmpeg error:")
            print(result.stderr)
            raise RuntimeError(
                f"FFmpeg failed while creating segment {index + 1} "
                f"(exit code {result.returncode})"
            )

    return segment_files


def _simple_concat(
    segment_files: list[Path],
    output_path: Path,
) -> None:
    """Simple concatenation without transitions."""
    print("  Concatenating segments...")

    concat_file = output_path.parent / "_concat.txt"

    with concat_file.open("w", encoding="utf-8") as f:
        for segment in segment_files:
            f.write(f"file '{segment.resolve()}'\n")

    result = subprocess.run(
        [
            _FFMPEG,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    concat_file.unlink(missing_ok=True)

    if result.returncode != 0:
        print()
        print("FFmpeg concat error:")
        print(result.stderr)
        raise RuntimeError(
            f"FFmpeg failed while joining segments "
            f"(exit code {result.returncode})"
        )


def _get_stream_duration(video_path: Path) -> float:
    """Get actual video stream duration via ffprobe."""
    result = subprocess.run(
        [
            _FFPROBE,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _pad_video_to_duration(
    input_path: Path,
    output_path: Path,
    target_duration: float,
    fps: int = 30,
) -> None:
    """
    Extend a video to target_duration by holding (cloning) the last frame.

    Uses FFmpeg's tpad filter which is fast and does not re-encode
    the existing frames — only the added tail frames are encoded.
    """
    actual_dur = _get_stream_duration(input_path)
    gap = max(0.0, target_duration - actual_dur)

    if gap <= 0.1:
        import shutil
        shutil.copy2(input_path, output_path)
        return

    result = subprocess.run(
        [
            _FFMPEG,
            "-y",
            "-i", str(input_path),
            "-vf", f"tpad=stop_mode=clone:stop_duration={gap:.4f}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            "-t", str(target_duration),
            "-an",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed while padding video: {result.stderr[-400:]}"
        )


def _split_subtitle_text(
    text: str,
    narration_duration: float,
    start_offset: float = 0.0,
) -> list[dict]:
    """Split subtitle text into timed segments.

    Args:
        text: Narration text to split.
        narration_duration: Duration of the narration portion only
            (total audio minus intro and outro).
        start_offset: Time in seconds where narration starts inside
            the full audio (i.e. intro duration).
    """
    import re

    # Split by paragraphs or double newlines
    paragraphs = [
        p.strip()
        for p in text.split("\n\n")
        if p.strip()
    ]

    if len(paragraphs) <= 1:
        # Split by sentences
        paragraphs = re.split(r'(?<=[.!?。！？])\s+', text.strip())
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # Distribute timing evenly across narration window
    total_chars = sum(len(p) for p in paragraphs)
    if total_chars == 0:
        return []

    segments = []
    current_time = start_offset  # Start after intro

    for para in paragraphs:
        seg_duration = narration_duration * len(para) / total_chars
        segments.append({
            "text": para,
            "start_time": current_time,
            "end_time": current_time + seg_duration,
        })
        current_time += seg_duration

    return segments


def main() -> None:

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python generate_video.py <topic-id> [mode] [options]")
        print()
        print("Modes: fast (default), quality")
        print()
        print("Options:")
        print("  --music <path>        Background music file")
        print("  --music-volume <0-1>  Music volume (default: 0.12)")
        print("  --no-transitions      Disable transitions")
        print("  --transition <type>   Transition type (default: dissolve)")
        print("  --no-subtitles        Disable subtitles")
        sys.exit(1)

    load_dotenv()

    topic_id = sys.argv[1]

    # Parse arguments
    mode = "fast"
    music_path = None
    music_volume = DEFAULT_MUSIC_VOLUME
    enable_transitions = True
    transition_type = DEFAULT_TRANSITION
    enable_subtitles = True

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "fast" or args[i] == "quality":
            mode = args[i]
        elif args[i] == "--music" and i + 1 < len(args):
            music_path = Path(args[i + 1])
            i += 1
        elif args[i] == "--music-volume" and i + 1 < len(args):
            music_volume = float(args[i + 1])
            i += 1
        elif args[i] == "--no-transitions":
            enable_transitions = False
        elif args[i] == "--transition" and i + 1 < len(args):
            transition_type = args[i + 1]
            i += 1
        elif args[i] == "--no-subtitles":
            enable_subtitles = False
        i += 1

    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode}. Use: fast or quality")

    project_dir = PROJECT_ROOT / "projects" / topic_id

    audio_file = project_dir / "audio" / "narration_full.mp3"
    images_dir = project_dir / "images"
    thumbnail_dir = project_dir / "thumbnail"
    video_dir = project_dir / "video"

    output_file = video_dir / MODES[mode]["output_name"]

    if not audio_file.exists():
        raise FileNotFoundError(f"Audio not found: {audio_file}")

    if not images_dir.exists():
        raise FileNotFoundError(f"Images directory not found: {images_dir}")

    if not thumbnail_dir.exists():
        raise FileNotFoundError(f"Thumbnail directory not found: {thumbnail_dir}")

    images = sorted([
        path
        for path in images_dir.iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ])

    if not images:
        raise RuntimeError(f"No images found in: {images_dir}")

    thumbnails = sorted([
        path
        for path in thumbnail_dir.iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ])

    if not thumbnails:
        raise RuntimeError(f"No thumbnail images found in: {thumbnail_dir}")

    thumbnail = thumbnails[0]

    video_dir.mkdir(parents=True, exist_ok=True)

    duration = get_audio_duration(audio_file)

    # ----------------------------------------------------------------
    # Detect intro / outro durations so subtitles can be offset.
    # generate_audio.py prepends intro and appends outro to the full
    # narration_full.mp3, so we need to skip those windows.
    # ----------------------------------------------------------------
    intro_offset = 0.0
    outro_offset = 0.0

    intro_path = PROJECT_ROOT / "projects" / "_intro" / "intro.mp3"
    outro_path = PROJECT_ROOT / "projects" / "_outro" / "outro.mp3"

    if intro_path.exists():
        try:
            intro_offset = get_audio_duration(intro_path)
            print(f"  🎬 Intro detected: {intro_offset:.2f}s")
        except Exception:
            intro_offset = 0.0

    if outro_path.exists():
        try:
            outro_offset = get_audio_duration(outro_path)
            print(f"  🎬 Outro detected: {outro_offset:.2f}s")
        except Exception:
            outro_offset = 0.0

    # Load subtitle text and timestamps if available
    subtitle_text = None
    subtitle_timestamps = None
    if enable_subtitles:
        # Priority 1: Load real timestamps from timestamps.json
        # These timestamps are relative to the narration audio only
        # (before intro/outro were added), so we shift by intro_offset.
        timestamps_file = project_dir / "audio" / "timestamps.json"
        if timestamps_file.exists():
            import json
            ts_data = json.loads(
                timestamps_file.read_text(encoding="utf-8")
            )
            subtitle_timestamps = ts_data.get("words", [])
            if subtitle_timestamps:
                print(f"  📝 Loaded {len(subtitle_timestamps)} word timestamps")

        # Priority 2: Load narration text
        narration_file = project_dir / "narration_full.txt"
        if narration_file.exists():
            subtitle_text = narration_file.read_text(encoding="utf-8")
        else:
            # Try to get from story.json
            story_file = project_dir / "story.json"
            if story_file.exists():
                import json
                story_data = json.loads(
                    story_file.read_text(encoding="utf-8")
                )
                narration = story_data.get("narration", {})
                segments = narration.get("segments", [])
                subtitle_text = "\n\n".join(
                    s.get("text", "") for s in segments
                )

    print()
    print(f"Topic ID: {topic_id}")
    print(f"Mode: {mode}")
    print(f"Audio duration: {format_time(duration)}")
    print(f"  Intro offset: {intro_offset:.2f}s")
    print(f"  Outro offset: {outro_offset:.2f}s")
    print(f"  Narration window: {format_time(intro_offset)} → {format_time(duration - outro_offset)}")
    print(f"Images: {len(images)}")
    print(f"Thumbnail: {thumbnail.name}")
    if music_path:
        print(f"Background music: {music_path}")
    print()

    render_video(
        thumbnail=thumbnail,
        images=images,
        audio_file=audio_file,
        output_file=output_file,
        duration=duration,
        mode=mode,
        background_music_path=music_path,
        music_volume=music_volume,
        subtitle_text=subtitle_text,
        subtitle_timestamps=subtitle_timestamps,
        enable_transitions=enable_transitions,
        transition_type=transition_type,
        intro_offset=intro_offset,
        outro_offset=outro_offset,
    )


if __name__ == "__main__":
    main()
