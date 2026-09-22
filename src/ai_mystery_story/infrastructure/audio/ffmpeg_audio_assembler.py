import json
import subprocess
from pathlib import Path

from ai_mystery_story.application.audio.audio_assembler import (
    AudioAssembler,
)
from ai_mystery_story.domain.audio.audio_segment import (
    AudioSegment,
)

# Broadcast loudness target for spoken-word content
# (podcasts / audiobooks / YouTube narration).
_LOUDNORM_I = -16.0   # Integrated loudness (LUFS)
_LOUDNORM_TP = -1.5   # Max true peak (dBTP)
_LOUDNORM_LRA = 11.0  # Loudness range

# ffmpeg filter applied to every segment: resample to a uniform
# rate so concat never fails on mixed-rate inputs, then normalize
# loudness (loudnorm runs its own 192k internal upsampling, so a
# final aresample back to 48 kHz is required).
_SEGMENT_FILTER = (
    f"loudnorm=I={_LOUDNORM_I}:TP={_LOUDNORM_TP}:LRA={_LOUDNORM_LRA},"
    "aresample=48000"
)


class FFmpegAudioAssembler(AudioAssembler):

    def assemble(
        self,
        segments: list[AudioSegment],
        output_path: Path,
    ) -> float:

        if not segments:
            raise ValueError(
                "Cannot assemble audio: no segments provided"
            )

        ordered_segments = sorted(
            segments,
            key=lambda segment: segment.order,
        )

        for segment in ordered_segments:
            if not segment.file_path.exists():
                raise FileNotFoundError(
                    f"Audio segment not found: "
                    f"{segment.file_path}"
                )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        normalized_paths: list[Path] = []

        try:
            for segment in ordered_segments:
                normalized_paths.append(
                    self._normalize_segment(
                        segment.file_path
                    )
                )

            concat_file = (
                output_path.parent
                / f".{output_path.stem}_concat.txt"
            )

            try:
                lines = []

                for path in normalized_paths:
                    path = path.resolve()

                    escaped_path = (
                        str(path)
                        .replace("'", "'\\''")
                    )

                    lines.append(
                        f"file '{escaped_path}'"
                    )

                concat_file.write_text(
                    "\n".join(lines),
                    encoding="utf-8",
                )

                command = [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    str(output_path),
                ]

                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                )

                if result.returncode != 0:
                    raise RuntimeError(
                        "FFmpeg failed to assemble audio:\n"
                        f"{result.stderr}"
                    )

                return self._get_duration(
                    output_path
                )

            finally:
                if concat_file.exists():
                    concat_file.unlink()

        finally:
            for path in normalized_paths:
                path.unlink(missing_ok=True)

    def _normalize_segment(
        self,
        source_path: Path,
    ) -> Path:
        """
        Normalize one segment to the shared loudness target.

        Uses ffmpeg loudnorm in dynamic (single-pass) mode,
        which is transparent for spoken word and keeps the
        segment's natural dynamics.

        Returns the path of the normalized temp file.
        """
        normalized_path = (
            source_path.parent
            / f".{source_path.stem}_norm_{source_path.suffix.lstrip('.') or 'mp3'}.mp3"
        )

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-af",
            _SEGMENT_FILTER,
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(normalized_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg failed to normalize audio segment:\n"
                f"{result.stderr}"
            )

        return normalized_path

    def _get_duration(
        self,
        audio_path: Path,
    ) -> float:

        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "ffprobe failed:\n"
                f"{result.stderr}"
            )

        return float(result.stdout.strip())
