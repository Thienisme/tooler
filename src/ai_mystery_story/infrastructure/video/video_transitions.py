"""
Video Transitions - Add professional transitions between video segments.

Supports:
1. Cross-dissolve (xfade) between segments
2. Fade-in at video start
3. Fade-out at video end
4. Various transition effects

Strategy: batch processing to avoid OOM when there are many segments.
"""

import subprocess
from pathlib import Path


# Resolve ffmpeg binary - prefer project's bundled binary
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_FFMPEG_BIN = _PROJECT_ROOT / "bin" / "ffmpeg"
_FFMPEG = str(_FFMPEG_BIN) if _FFMPEG_BIN.exists() else "ffmpeg"
_FFPROBE_BIN = _PROJECT_ROOT / "bin" / "ffprobe"
_FFPROBE = str(_FFPROBE_BIN) if _FFPROBE_BIN.exists() else "ffprobe"


# Available transition effects
TRANSITIONS = {
    "fade": "fade",
    "dissolve": "dissolve",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "wipeup": "wipeup",
    "wipedown": "wipedown",
    "slideleft": "slideleft",
    "slideright": "slideright",
    "smoothleft": "smoothleft",
    "smoothright": "smoothright",
    "circlecrop": "circlecrop",
    "rectcrop": "rectcrop",
    "circleclose": "circleclose",
    "circleopen": "circleopen",
    "pixelize": "pixelize",
    "radial": "radial",
    "horzclose": "horzclose",
    "horzopen": "horzopen",
    "vertclose": "vertclose",
    "vertopen": "vertopen",
    "diagbl": "diagbl",
    "diagbr": "diagbr",
    "diagtl": "diagtl",
    "diagtr": "diagtr",
    "hlslice": "hlslice",
    "hrslice": "hrslice",
    "vuslice": "vuslice",
    "vdslice": "vdslice",
    "fadeblack": "fadeblack",
    "fadewhite": "fadewhite",
    "fadegrays": "fadegrays",
    "squeezeh": "squeezeh",
    "squeezev": "squeezev",
}

# Maximum segments per FFmpeg call to avoid OOM
_BATCH_SIZE = 8


def apply_xfade_transitions(
    segment_files: list[Path],
    output_path: Path,
    transition: str = "dissolve",
    transition_duration: float = 1.0,
    fade_in_duration: float = 1.5,
    fade_out_duration: float = 2.0,
    fps: int = 30,
) -> Path:
    """
    Apply cross-dissolve transitions between video segments.

    Uses ffmpeg's xfade filter in batches to avoid OOM on large projects.

    Args:
        segment_files: List of video segment files
        output_path: Output video path
        transition: Transition effect name
        transition_duration: Duration of each transition in seconds
        fade_in_duration: Duration of fade-in at start
        fade_out_duration: Duration of fade-out at end
        fps: Output frame rate

    Returns:
        Path to output video
    """
    if not segment_files:
        raise ValueError("No segment files provided")

    if len(segment_files) == 1:
        # Single segment - just add fade in/out
        return _apply_fade_only(
            segment_files[0],
            output_path,
            fade_in_duration,
            fade_out_duration,
            fps,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(segment_files)
    print(f"  Applying transitions: {transition} "
          f"({transition_duration}s) on {total} segments "
          f"[batch size: {_BATCH_SIZE}]")

    if total <= _BATCH_SIZE:
        # Small enough to do in a single pass
        return _xfade_batch(
            segment_files=segment_files,
            output_path=output_path,
            transition=transition,
            transition_duration=transition_duration,
            fade_in_duration=fade_in_duration,
            fade_out_duration=fade_out_duration,
            fps=fps,
            is_first_batch=True,
            is_last_batch=True,
        )

    # ---------------------------------------------------------------
    # Large project: process in batches, then join the batch outputs
    # ---------------------------------------------------------------
    temp_dir = output_path.parent
    batch_outputs: list[Path] = []

    # Split into batches
    batches = [
        segment_files[i: i + _BATCH_SIZE]
        for i in range(0, total, _BATCH_SIZE)
    ]
    num_batches = len(batches)
    print(f"  Processing {num_batches} batches...")

    for batch_idx, batch in enumerate(batches):
        batch_output = temp_dir / f"_batch_{batch_idx:04d}.mp4"
        print(f"  Batch {batch_idx + 1}/{num_batches} "
              f"({len(batch)} segments)...")

        is_first = batch_idx == 0
        is_last = batch_idx == num_batches - 1

        _xfade_batch(
            segment_files=batch,
            output_path=batch_output,
            transition=transition,
            transition_duration=transition_duration,
            fade_in_duration=fade_in_duration if is_first else 0.0,
            fade_out_duration=fade_out_duration if is_last else 0.0,
            fps=fps,
            is_first_batch=is_first,
            is_last_batch=is_last,
        )
        batch_outputs.append(batch_output)

    # ---------------------------------------------------------------
    # Join all batch outputs with simple concat (no re-encoding)
    # Each batch already has transitions applied inside.
    # ---------------------------------------------------------------
    print(f"  Joining {num_batches} batches...")
    _simple_concat_copy(batch_outputs, output_path)

    # Clean up intermediate batch files
    for f in batch_outputs:
        f.unlink(missing_ok=True)

    print(f"  ✅ Transitions applied: {output_path.name}")
    return output_path


def _xfade_batch(
    segment_files: list[Path],
    output_path: Path,
    transition: str,
    transition_duration: float,
    fade_in_duration: float,
    fade_out_duration: float,
    fps: int,
    is_first_batch: bool = True,
    is_last_batch: bool = True,
) -> Path:
    """
    Apply xfade transitions to a single batch of segments (≤ BATCH_SIZE).
    Falls back to simple concat if xfade fails.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(segment_files) == 1:
        # Only one segment in this batch
        if fade_in_duration > 0 or fade_out_duration > 0:
            return _apply_fade_only(
                segment_files[0], output_path,
                fade_in_duration, fade_out_duration, fps,
            )
        import shutil
        shutil.copy2(segment_files[0], output_path)
        return output_path

    durations = [_get_video_duration(f) for f in segment_files]
    n = len(segment_files)

    # Build input arguments
    inputs: list[str] = []
    for seg_file in segment_files:
        inputs.extend(["-i", str(seg_file)])

    # Build filter complex
    filter_parts: list[str] = []
    xfade_outputs: list[str] = []
    cumulative_duration = 0.0

    for i in range(n):
        if i == 0:
            if fade_in_duration > 0:
                filter_parts.append(
                    f"[0:v]fade=t=in:st=0:d={fade_in_duration}[v0]"
                )
                xfade_outputs.append("[v0]")
            else:
                xfade_outputs.append("[0:v]")
        else:
            cumulative_duration += durations[i - 1]
            offset = cumulative_duration - i * transition_duration
            if offset < 0:
                offset = 0

            prev_label = (
                xfade_outputs[0].replace("[", "").replace("]", "")
                if i == 1
                else f"xf{i - 1}"
            )
            curr_input = f"{i}:v"
            output_label = f"xf{i}"

            filter_parts.append(
                f"[{prev_label}][{curr_input}]xfade=transition={transition}"
                f":duration={transition_duration}"
                f":offset={offset:.4f}[{output_label}]"
            )
            xfade_outputs.append(f"[{output_label}]")

    final_video = f"xf{n - 1}"

    if fade_out_duration > 0:
        total_duration = sum(durations) - (n - 1) * transition_duration
        fade_out_start = max(0, total_duration - fade_out_duration)
        filter_parts.append(
            f"[{final_video}]fade=t=out"
            f":st={fade_out_start:.4f}"
            f":d={fade_out_duration}[vout]"
        )
        final_label = "vout"
    else:
        final_label = final_video

    filter_complex = ";".join(filter_parts)

    command = [
        _FFMPEG, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{final_label}]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "copy",
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"  ⚠️  xfade failed for batch, falling back to concat\n"
              f"      stderr: {result.stderr[-300:]}")
        return _fallback_concat(segment_files, output_path)

    return output_path


def _simple_concat_copy(
    files: list[Path],
    output_path: Path,
) -> Path:
    """
    Join video files using stream copy (no re-encode).
    All files must have identical codec/resolution.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_file = output_path.parent / "_batch_concat.txt"

    try:
        with concat_file.open("w", encoding="utf-8") as f:
            for seg in files:
                f.write(f"file '{seg.resolve()}'\n")

        command = [
            _FFMPEG, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Batch concat failed: {result.stderr[-300:]}"
            )

        return output_path

    finally:
        concat_file.unlink(missing_ok=True)


def _apply_fade_only(
    video_path: Path,
    output_path: Path,
    fade_in_duration: float,
    fade_out_duration: float,
    fps: int,
) -> Path:
    """Apply fade-in and fade-out to a single video."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    duration = _get_video_duration(video_path)

    filter_parts = []
    if fade_in_duration > 0:
        filter_parts.append(
            f"fade=t=in:st=0:d={fade_in_duration}"
        )
    if fade_out_duration > 0:
        fade_out_start = max(0, duration - fade_out_duration)
        filter_parts.append(
            f"fade=t=out:st={fade_out_start:.4f}:d={fade_out_duration}"
        )

    if not filter_parts:
        import shutil
        shutil.copy2(video_path, output_path)
        return output_path

    vf = ",".join(filter_parts)

    command = [
        _FFMPEG, "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "copy",
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        import shutil
        shutil.copy2(video_path, output_path)
        return output_path

    return output_path


def _fallback_concat(
    segment_files: list[Path],
    output_path: Path,
) -> Path:
    """Fallback: simple concatenation without transitions."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    concat_file = output_path.parent / "_concat_simple.txt"

    try:
        with concat_file.open("w", encoding="utf-8") as f:
            for seg in segment_files:
                f.write(f"file '{seg.resolve()}'\n")

        command = [
            _FFMPEG, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]

        subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
        )

        return output_path

    finally:
        if concat_file.exists():
            concat_file.unlink()


def _get_video_duration(video_path: Path) -> float:
    """Get video duration using ffprobe."""
    command = [
        _FFPROBE,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return 10.0  # Default fallback

    try:
        return float(result.stdout.strip())
    except ValueError:
        return 10.0
