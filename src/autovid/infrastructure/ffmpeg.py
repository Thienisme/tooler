"""
ffmpeg / ffprobe access.

The repository ships static binaries in `bin/` so the pipeline does not
depend on whatever ffmpeg happens to be on PATH.  Bundled binaries win;
PATH is only a fallback for machines without them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from autovid.paths import PROJECT_ROOT

BIN_DIR = PROJECT_ROOT / "bin"


def _resolve(name: str) -> str | None:
    bundled = BIN_DIR / name
    if bundled.exists() and bundled.is_file():
        return str(bundled)
    return shutil.which(name)


FFMPEG = _resolve("ffmpeg")
FFPROBE = _resolve("ffprobe")


def ffmpeg_available() -> bool:
    return FFMPEG is not None


def ffprobe_available() -> bool:
    return FFPROBE is not None


def ffmpeg_version() -> str | None:
    """Return the first line of `ffmpeg -version`, or None if unavailable."""
    if FFMPEG is None:
        return None
    try:
        result = subprocess.run(
            [FFMPEG, "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.splitlines()[0] if result.stdout else None


def run(args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
    """
    Run ffmpeg with the given arguments.

    Callers pass everything after the binary name.  Raises
    `RuntimeError` with the last lines of stderr when ffmpeg fails, which
    is almost always more useful than a bare CalledProcessError.
    """
    if FFMPEG is None:
        raise RuntimeError(
            "ffmpeg not found. Add a static binary to bin/ffmpeg or install ffmpeg."
        )

    result = subprocess.run(
        # -nostdin matters: without it ffmpeg can consume the terminal's
        # stdin and stall a long render with no visible reason.
        [FFMPEG, "-nostdin", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-12:])
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}) for: {' '.join(args)}\n{tail}"
        )
    return result


def probe_duration(path: Path) -> float:
    """
    Exact media duration in seconds via ffprobe.

    This is the number the whole sync strategy depends on, so a failure
    here is fatal rather than silently defaulting to zero.
    """
    if FFPROBE is None:
        raise RuntimeError(
            "ffprobe not found. Add a static binary to bin/ffprobe or install ffmpeg."
        )
    if not path.exists():
        raise FileNotFoundError(f"Cannot probe duration, file not found: {path}")

    result = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {path}: {result.stderr.strip()[:300]}"
        )

    try:
        return float(result.stdout.strip())
    except ValueError as error:
        raise RuntimeError(
            f"ffprobe returned a non-numeric duration for {path}: "
            f"{result.stdout.strip()!r}"
        ) from error


def probe_streams(path: Path) -> dict:
    """Return ffprobe's JSON stream/format info for a media file."""
    if FFPROBE is None:
        raise RuntimeError("ffprobe not found.")
    if not path.exists():
        raise FileNotFoundError(f"Cannot probe, file not found: {path}")

    result = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate,channels,"
            "sample_rate,bit_rate",
            "-show_entries",
            "format=duration,size,bit_rate,format_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {path}: {result.stderr.strip()[:300]}"
        )
    return json.loads(result.stdout)
