"""
Audio measurement and processing via ffmpeg.

Everything here is deliberately ffmpeg-only: no numpy, no pydub, no
librosa.  That keeps the pipeline's dependency list unchanged and means
the same code path runs on every machine with the bundled binaries.

The functions the pacing engine depends on are the silence measurements —
a pause is only skipped when the voice model *already* left enough silence
at the end of a sentence, so those numbers have to be real measurements of
the rendered audio, not estimates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from autovid.infrastructure.ffmpeg import probe_duration, run

# Silence is anything quieter than this.  -35 dBFS catches the breath
# pauses a voice model leaves without tripping on room tone.
DEFAULT_SILENCE_THRESHOLD_DB = -35.0

# Ignore blips shorter than this; they are articulation, not pauses.
DEFAULT_SILENCE_MIN_S = 0.08

LEADING_TOLERANCE_S = 0.03

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_CHANNELS = 2

# Broadcast ceilings.
DEFAULT_TRUE_PEAK_DB = -1.5

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


class AudioError(RuntimeError):
    """Raised when ffmpeg cannot produce or measure audio."""


@dataclass(frozen=True)
class SilenceInfo:
    """Natural silences found inside one audio file."""

    duration_s: float
    leading_ms: int
    trailing_ms: int
    internal: tuple[tuple[float, float], ...]

    @property
    def internal_count(self) -> int:
        return len(self.internal)

    def to_dict(self) -> dict:
        return {
            "duration_s": round(self.duration_s, 4),
            "leading_ms": self.leading_ms,
            "trailing_ms": self.trailing_ms,
            "internal_count": self.internal_count,
            "internal": [
                [round(start, 4), round(end, 4)] for start, end in self.internal
            ],
        }


@dataclass(frozen=True)
class LoudnessInfo:
    """EBU R128 loudness of one audio file."""

    integrated_lufs: float
    true_peak_db: float
    lra: float
    loudness_range_ok: bool = True

    @property
    def clipping(self) -> bool:
        """True when the true peak is at or over 0 dBFS."""
        return self.true_peak_db >= -0.1

    def to_dict(self) -> dict:
        return {
            "integrated_lufs": round(self.integrated_lufs, 2),
            "true_peak_db": round(self.true_peak_db, 2),
            "lra": round(self.lra, 2),
        }


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


def measure_silence(
    path: Path,
    *,
    threshold_db: float = DEFAULT_SILENCE_THRESHOLD_DB,
    min_duration_s: float = DEFAULT_SILENCE_MIN_S,
) -> SilenceInfo:
    """
    Find the leading, trailing and internal silences of an audio file.

    FFmpeg reports an unclosed `silence_start` when a file ends mid-silence,
    so that open interval is closed against the known duration.
    """
    duration = probe_duration(path)

    result = run(
        [
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={threshold_db}dB:d={min_duration_s}",
            "-f",
            "null",
            "-",
        ],
        timeout=300,
    )

    starts = [float(value) for value in _SILENCE_START.findall(result.stderr)]
    ends = [float(value) for value in _SILENCE_END.findall(result.stderr)]

    if len(starts) > len(ends):
        # The file ended while still silent, so ffmpeg never closed the
        # last interval; close it against the measured duration.
        ends = [*ends, duration]

    intervals = [
        (max(0.0, start), min(end, duration))
        for start, end in zip(starts, ends)
        if end > start
    ]

    leading = 0
    trailing = 0
    internal: list[tuple[float, float]] = []

    for start, end in intervals:
        length_ms = int(round((end - start) * 1000))
        at_start = start <= LEADING_TOLERANCE_S
        at_end = end >= duration - LEADING_TOLERANCE_S

        if at_start and at_end:
            # The whole file is silent.
            leading, trailing = length_ms, 0
        elif at_start:
            leading = length_ms
        elif at_end:
            trailing = length_ms
        else:
            internal.append((start, end))

    return SilenceInfo(
        duration_s=duration,
        leading_ms=leading,
        trailing_ms=trailing,
        internal=tuple(internal),
    )


def measure_loudness(
    path: Path,
    *,
    target_lufs: float = -14.0,
    true_peak_db: float = DEFAULT_TRUE_PEAK_DB,
    lra: float = 11.0,
) -> LoudnessInfo:
    """
    Measure integrated loudness (LUFS), true peak and LRA.

    This is also pass one of the two-pass normaliser, so the parsed values
    are returned rather than thrown away.
    """
    result = run(
        [
            "-i",
            str(path),
            "-af",
            (
                f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA={lra}:"
                "print_format=json"
            ),
            "-f",
            "null",
            "-",
        ],
        timeout=600,
    )

    match = _LOUDNORM_JSON.search(result.stderr)
    if match is None:
        raise AudioError(
            f"Could not read loudness for {path}. ffmpeg said:\n"
            f"{result.stderr.strip()[-500:]}"
        )

    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise AudioError(
            f"Malformed loudnorm output for {path}: {error}"
        ) from error

    def number(key: str) -> float:
        raw = payload.get(key, "-inf")
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float("-inf")

    return LoudnessInfo(
        integrated_lufs=number("input_i"),
        true_peak_db=number("input_tp"),
        lra=number("input_lra"),
    )


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------


def canonical_format_args(
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
) -> list[str]:
    """
    The single audio format used everywhere in the workspace.

    Concatenation only works when every input already matches, so this is
    applied when audio is first written and again on every conversion.
    """
    return ["-ar", str(sample_rate), "-ac", str(channels)]


def normalize_loudness(
    source: Path,
    destination: Path,
    *,
    target_lufs: float = -14.0,
    true_peak_db: float = DEFAULT_TRUE_PEAK_DB,
    lra: float = 11.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    linear: bool = True,
) -> LoudnessInfo:
    """
    Two-pass EBU R128 normalisation.

    Pass one measures, pass two applies those measurements with
    `linear=true`, which scales the whole file by one gain instead of
    riding the level dynamically.  For narration that matters: single-pass
    loudnorm audibly pumps between loud and quiet sentences, and it makes
    every scene a different loudness, which is exactly what we are trying
    to avoid.

    Returns the loudness measured before normalisation.
    """
    measured = measure_loudness(
        source, target_lufs=target_lufs, true_peak_db=true_peak_db, lra=lra
    )

    if measured.integrated_lufs == float("-inf"):
        raise AudioError(f"{source} measures as pure silence; nothing to normalise")

    pass_two = (
        f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA={lra}:"
        f"measured_I={measured.integrated_lufs}:"
        f"measured_TP={measured.true_peak_db}:"
        f"measured_LRA={max(measured.lra, 0.1)}:"
        f"measured_thresh={measured.integrated_lufs - 10}:"
        f"offset=0:linear={'true' if linear else 'false'}:print_format=summary"
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-af",
            pass_two,
            *canonical_format_args(sample_rate=sample_rate, channels=channels),
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        timeout=900,
    )
    return measured


def limit_peak(
    source: Path,
    destination: Path,
    *,
    ceiling_db: float = -1.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
) -> None:
    """Hard-limit true peaks, used only to rescue a clipped TTS take."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-af",
            f"alimiter=limit={db_to_linear(ceiling_db):.6f}:level=disabled",
            *canonical_format_args(sample_rate=sample_rate, channels=channels),
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        timeout=300,
    )


def db_to_linear(db: float) -> float:
    return 10 ** (db / 20.0)


def silence_file(
    cache_dir: Path,
    milliseconds: int,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
) -> Path:
    """
    Return a silent WAV of exactly `milliseconds`, generating it once.

    The ffmpeg concat demuxer can only take real files, so pauses have to
    exist on disk.  Caching by duration keeps the count bounded: a 13
    minute video uses a few dozen distinct values, not a few thousand.
    """
    milliseconds = max(0, int(milliseconds))
    path = (
        cache_dir
        / f"silence_{sample_rate}_{channels}_{milliseconds:06d}ms.wav"
    )
    if path.exists() and path.stat().st_size > 0:
        return path

    cache_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={sample_rate}:cl={'mono' if channels == 1 else 'stereo'}",
            "-t",
            f"{milliseconds / 1000.0:.3f}",
            *canonical_format_args(sample_rate=sample_rate, channels=channels),
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        timeout=120,
    )

    actual_ms = probe_duration(path) * 1000
    if abs(actual_ms - milliseconds) > 2:
        raise AudioError(
            f"Generated silence is {actual_ms:.1f}ms but {milliseconds}ms was "
            "requested; pause placement would drift"
        )
    return path


def concatenate(
    sources: list[Path],
    destination: Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    work_dir: Path | None = None,
) -> float:
    """
    Concatenate WAV files losslessly and return the resulting duration.

    Uses the concat demuxer rather than a `concat=` filter graph: a filter
    graph with hundreds of inputs hits ffmpeg's command-line and filter
    limits, while the demuxer streams one file at a time.
    """
    if not sources:
        raise AudioError("Nothing to concatenate.")

    for source in sources:
        if not source.exists():
            raise AudioError(f"Missing audio file: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)

    if len(sources) == 1:
        run(
            [
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(sources[0]),
                *canonical_format_args(sample_rate=sample_rate, channels=channels),
                "-c:a",
                "pcm_s16le",
                str(destination),
            ],
            timeout=300,
        )
        return probe_duration(destination)

    list_dir = work_dir or destination.parent
    list_dir.mkdir(parents=True, exist_ok=True)
    list_path = list_dir / f"{destination.stem}_concat.txt"
    list_path.write_text(
        "".join(f"file '{_escape(source)}'\n" for source in sources),
        encoding="utf-8",
    )

    run(
        [
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            *canonical_format_args(sample_rate=sample_rate, channels=channels),
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        timeout=1800,
    )
    return probe_duration(destination)


def _escape(path: Path) -> str:
    """Escape a path for the concat demuxer's single-quoted syntax."""
    return str(path.resolve()).replace("'", "'\\''")


def convert_to_wav(
    source: Path,
    destination: Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
) -> None:
    """Force any audio file into the workspace's canonical WAV format."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            *canonical_format_args(sample_rate=sample_rate, channels=channels),
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        timeout=300,
    )
