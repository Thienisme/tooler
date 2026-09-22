"""
Stage 6 — render: the deliverable.

Everything up to here produced parts: a silent, frame-exact video (stage 4)
and one finished audio track (stage 5).  This stage muxes them and checks the
result, which makes it the only place where picture and sound meet — and
therefore the only place where a sync mistake has to be caught once instead
of 48 times.

The video stream is **copied, not re-encoded**.  Stage 4 already encoded every
frame at the delivery quality, and re-encoding here would be a second lossy
generation of the same frames for no gain.  Only the audio is encoded (PCM to
AAC), which is also why this stage costs seconds rather than minutes.

The output length is pinned to the video, not to the audio: the video *is*
the timeline.  Audio shorter than the picture is padded with silence so the
ending is never cut off, and audio longer than the picture is trimmed with a
warning, because a longer audio track means a timeline the two stages
disagree about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from autovid.application.issues import Issue, IssueCollector
from autovid.application.quality import write_quality_report
from autovid.domain.script import Script
from autovid.infrastructure.ffmpeg import (
    ffmpeg_available,
    probe_duration,
    probe_streams,
    run,
)
from autovid.paths import Paths, read_json, write_json

DEFAULT_AUDIO_BITRATE = "192k"

# A one-frame disagreement between the plan and the encoded file is
# rounding; anything larger means frame counts were lost in the join.
FRAME_TOLERANCE = 1

# Extra slack on top of the frame tolerance when comparing container
# durations, which are reported to the millisecond.
DURATION_SLACK_S = 0.05

# AAC is encoded in 1024-sample frames, so the last frame can pad the
# stream slightly past the video it was cut against.
AUDIO_TAIL_TOLERANCE_S = 0.1


@dataclass
class RenderStageResult:
    status: str
    output: Path | None
    report: dict
    quality: dict
    issues: list[Issue] = field(default_factory=list)


class RenderStage:
    """
    Muxes the assembled video and the mixed audio into `final_video.mp4`.

    Reads `output/preview_video.mp4` and `audio/mix.wav`, and writes the
    deliverable plus the merged quality report.
    """

    def __init__(
        self,
        script: Script,
        paths: Paths,
        *,
        audio_bitrate: str = DEFAULT_AUDIO_BITRATE,
        use_voiceover: bool = False,
        dry_run: bool = False,
        progress=None,
    ) -> None:
        self.script = script
        self.paths = paths
        self.audio_bitrate = audio_bitrate
        self.use_voiceover = use_voiceover
        self.dry_run = dry_run
        self.progress = progress or (lambda message: None)
        self.issues = IssueCollector()
        # Filled by `_verify` so the report can describe the file that was
        # actually written rather than the one that was planned.
        self._last_streams: dict | None = None

        metadata = script.video_metadata
        self.fps = metadata.fps
        self.frame_size = (metadata.width, metadata.height)
        self.tolerance_s = FRAME_TOLERANCE / self.fps

    # -- entry point ------------------------------------------------------

    def run(self) -> RenderStageResult:
        if not ffmpeg_available():
            self.issues.error(
                "ffmpeg_missing",
                "ffmpeg not found; rendering needs a static binary in "
                "bin/ffmpeg or an ffmpeg on PATH",
            )
            return self._abort()

        video = self.paths.preview_path
        if not video.exists() or video.stat().st_size == 0:
            self.issues.error(
                "preview_missing",
                "output/preview_video.mp4 not found; run the assembly stage "
                "first so there is a video to add sound to",
            )
            return self._abort()

        audio = self._pick_audio()
        if audio is None:
            return self._abort()

        try:
            video_s = probe_duration(video)
            audio_s = probe_duration(audio)
        except (RuntimeError, FileNotFoundError) as error:
            self.issues.error(
                "input_unreadable",
                f"could not measure the stage inputs: {error}",
            )
            return self._abort()

        self._cross_check_timeline(video_s)

        if audio_s + self.tolerance_s < video_s:
            self.issues.warn(
                "audio_shorter_than_video",
                f"the audio is {audio_s:.3f}s but the video is "
                f"{video_s:.3f}s; the last {video_s - audio_s:.3f}s will be "
                "silent",
            )
        elif audio_s - self.tolerance_s > video_s:
            self.issues.warn(
                "audio_longer_than_video",
                f"the audio is {audio_s:.3f}s but the video is "
                f"{video_s:.3f}s; the audio is trimmed, which means the "
                "timeline and the video disagree",
            )

        output = self.paths.final_video_path

        if self.dry_run:
            self.progress(
                f"would mux {video.name} ({video_s:.3f}s) with "
                f"{audio.name} ({audio_s:.3f}s) at {self.audio_bitrate}"
            )
            report = self._build_report(
                video=video,
                audio=audio,
                output=output,
                video_s=video_s,
                audio_s=audio_s,
                encoded_s=None,
                streams=None,
                size_mb=None,
            )
            return RenderStageResult(
                status=self.issues.status,
                output=None,
                report=report,
                quality={},
                issues=self.issues.issues,
            )

        self.paths.create()
        try:
            self._encode(video, audio, output, video_s)
        except RuntimeError as error:
            output.unlink(missing_ok=True)
            self.issues.error(
                "render_failed", f"the mux failed: {error}"
            )
            return self._abort()

        encoded_s = self._verify(output, video_s)
        report = self._build_report(
            video=video,
            audio=audio,
            output=output,
            video_s=video_s,
            audio_s=audio_s,
            encoded_s=encoded_s,
            # The verified streams, not the intended ones, so the report
            # describes the file that actually came out.
            streams=self._last_streams,
            size_mb=(
                round(output.stat().st_size / (1024 * 1024), 1)
                if output.exists()
                else None
            ),
        )
        write_json(self.paths.output_dir / "render_report.json", report)

        quality = write_quality_report(self.script, self.paths)
        self.progress(
            f"quality    : {quality['status']} "
            f"({quality['totals']['errors']} error(s), "
            f"{quality['totals']['warnings']} warning(s))"
        )

        return RenderStageResult(
            status=self.issues.status,
            output=output,
            report=report,
            quality=quality,
            issues=self.issues.issues,
        )

    def _abort(self) -> RenderStageResult:
        """Fail with a report on disk, so the merged report can see why."""
        report = {
            "stage": "render",
            "status": "fail",
            "settings": {
                "audio_bitrate": self.audio_bitrate,
                "voice_only": self.use_voiceover,
                "dry_run": self.dry_run,
            },
            "inputs": {},
            "totals": {
                "duration_s": None,
                "has_audio": False,
                "scenes": self.script.scene_count,
                "warnings": len(self.issues.warnings),
                "errors": len(self.issues.errors),
            },
            "deliverable": None,
            "output": None,
            "warnings": [issue.to_dict() for issue in self.issues.warnings],
            "errors": [issue.to_dict() for issue in self.issues.errors],
        }
        write_json(self.paths.output_dir / "render_report.json", report)
        return RenderStageResult(
            status="fail",
            output=None,
            report=report,
            quality={},
            issues=self.issues.issues,
        )

    # -- inputs -----------------------------------------------------------

    def _pick_audio(self) -> Path | None:
        """
        The mixed track, or the raw narration as a deliberate fallback.

        Falling back is allowed because a voice-only cut is a legitimate thing
        to review — but it is never silent about what is missing, since
        shipping it by accident would ship a video with no music.
        """
        if self.use_voiceover:
            audio = self.paths.voiceover_path
            if not audio.exists() or audio.stat().st_size == 0:
                self.issues.error(
                    "voiceover_missing",
                    f"{audio.name} not found; run the tts stage first",
                )
                return None
            self.issues.warn(
                "voice_only_render",
                "--voice-only is on: rendering the narration without music "
                "or SFX",
            )
            return audio

        mix = self.paths.mix_path
        if mix.exists() and mix.stat().st_size > 0:
            return mix

        voice = self.paths.voiceover_path
        if voice.exists() and voice.stat().st_size > 0:
            self.issues.warn(
                "audio_not_mixed",
                "audio/mix.wav not found; rendering the narration alone "
                "(run the mix stage for music and SFX)",
            )
            return voice

        self.issues.error(
            "audio_missing",
            "no audio to mux: run the mix stage (audio/mix.wav) or the tts "
            "stage (audio/voiceover_full.wav)",
        )
        return None

    def _cross_check_timeline(self, video_s: float) -> None:
        """The video must be the timeline stage 2 measured, to the frame."""
        timeline = read_json(self.paths.output_dir / "timeline.json")
        if not isinstance(timeline, dict):
            self.issues.warn(
                "timeline_missing",
                "output/timeline.json not found; the finished video's length "
                "cannot be checked against the script's timeline",
            )
            return

        planned = float(timeline.get("total_duration_s") or 0.0)
        if not planned:
            return

        delta = video_s - planned
        if abs(delta) > self.tolerance_s:
            self.issues.warn(
                "video_length_mismatch",
                f"the assembled video is {video_s:.3f}s but the timeline is "
                f"{planned:.3f}s ({delta:+.3f}s)",
            )

    # -- encode -----------------------------------------------------------

    def _encode(
        self, video: Path, audio: Path, output: Path, video_s: float
    ) -> None:
        """
        Copy the picture, encode the sound, and pin the length to the video.

        `-t` is applied to the output rather than to either input: with the
        audio padded, whichever stream is longer no longer decides the
        result, and the finished file is exactly as long as the picture.
        """
        run(
            [
                "-y",
                "-i",
                str(video),
                "-i",
                str(audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                self.audio_bitrate,
                "-ar",
                "48000",
                "-ac",
                "2",
                # Pad rather than guess: an audio track that ends early would
                # otherwise leave the muxer to decide what the tail sounds
                # like.
                "-af",
                "apad",
                "-t",
                f"{video_s:.4f}",
                "-movflags",
                "+faststart",
                str(output),
            ],
            timeout=None,
        )
        self.progress(
            f"muxed      : {video.name} + {audio.name} -> {output.name}"
        )

    # -- verification -----------------------------------------------------

    def _verify(self, output: Path, video_s: float) -> float | None:
        """
        Check the file that was actually written.

        Every check is something a viewer would notice: the wrong size, a
        dropped frame rate, a missing audio stream, a length that no longer
        matches the timeline.
        """
        if not output.exists() or output.stat().st_size == 0:
            self.issues.error(
                "render_output_missing", "the render wrote no file"
            )
            return None

        try:
            streams = probe_streams(output)
        except (RuntimeError, FileNotFoundError) as error:
            self.issues.error(
                "render_unreadable", f"could not inspect the output: {error}"
            )
            return None

        self._last_streams = streams
        video_streams = [
            stream
            for stream in streams.get("streams", [])
            if stream.get("codec_type") == "video"
        ]
        audio_streams = [
            stream
            for stream in streams.get("streams", [])
            if stream.get("codec_type") == "audio"
        ]

        if len(video_streams) != 1:
            self.issues.error(
                "render_video_streams",
                f"the output has {len(video_streams)} video stream(s); "
                "exactly one is expected",
            )
        else:
            self._check_video_stream(video_streams[0])

        if not audio_streams:
            self.issues.error(
                "render_has_no_audio",
                "the output has no audio stream; the video would play silent",
            )
        else:
            self._check_audio_stream(audio_streams[0])

        try:
            encoded_s = probe_duration(output)
        except (RuntimeError, FileNotFoundError) as error:
            self.issues.error(
                "render_duration_unreadable",
                f"could not read the output's duration: {error}",
            )
            return None

        drift = encoded_s - video_s
        if abs(drift) > self.tolerance_s + DURATION_SLACK_S + AUDIO_TAIL_TOLERANCE_S:
            self.issues.error(
                "render_duration_mismatch",
                f"the output is {encoded_s:.3f}s but the video is "
                f"{video_s:.3f}s ({drift:+.3f}s)",
            )
        else:
            self.progress(
                f"verified   : {encoded_s:.3f}s (delta {drift * 1000:+.0f}ms)"
            )

        return encoded_s

    def _check_video_stream(self, stream: dict) -> None:
        codec = stream.get("codec_name")
        if codec != "h264":
            self.issues.warn(
                "render_video_codec",
                f"the video stream is {codec} rather than h264, which may not "
                "play on every device",
            )

        size = (stream.get("width"), stream.get("height"))
        if size != self.frame_size:
            self.issues.error(
                "render_wrong_size",
                f"the output is {size[0]}x{size[1]} but the script asks for "
                f"{self.frame_size[0]}x{self.frame_size[1]}",
            )

        rate = _parse_rate(stream.get("r_frame_rate"))
        if rate is not None and abs(rate - self.fps) > 0.01:
            self.issues.error(
                "render_wrong_fps",
                f"the output runs at {rate:g}fps but the script asks for "
                f"{self.fps}fps; the voiceover would drift against the "
                "picture",
            )

    def _check_audio_stream(self, stream: dict) -> None:
        codec = stream.get("codec_name")
        if codec != "aac":
            self.issues.warn(
                "render_audio_codec",
                f"the audio stream is {codec} rather than AAC; players are "
                "less predictable about other codecs in MP4",
            )

        channels = stream.get("channels")
        if channels not in (None, 2):
            self.issues.warn(
                "render_audio_channels",
                f"the audio is {channels}-channel rather than stereo",
            )

        sample_rate = stream.get("sample_rate")
        if sample_rate not in (None, "48000", 48000):
            self.issues.warn(
                "render_audio_sample_rate",
                f"the audio is {sample_rate}Hz rather than 48000Hz",
            )

    # -- report -----------------------------------------------------------

    def _build_report(
        self,
        *,
        video: Path,
        audio: Path,
        output: Path,
        video_s: float,
        audio_s: float,
        encoded_s: float | None,
        streams: dict | None,
        size_mb: float | None,
    ) -> dict:
        metadata = self.script.video_metadata
        has_audio = bool(
            streams
            and any(
                stream.get("codec_type") == "audio"
                for stream in streams.get("streams", [])
            )
        )

        deliverable = None
        if encoded_s is not None:
            deliverable = {
                "file": str(output),
                "duration_s": round(encoded_s, 3),
                "resolution": metadata.resolution,
                "fps": self.fps,
                "video_codec": "h264",
                "video_reencoded": False,
                "audio_codec": "aac",
                "audio_bitrate": self.audio_bitrate,
                "has_audio": has_audio,
                "size_mb": size_mb,
            }

        return {
            "stage": "render",
            "status": self.issues.status,
            "settings": {
                "audio_bitrate": self.audio_bitrate,
                "voice_only": self.use_voiceover,
                "dry_run": self.dry_run,
                "frame_tolerance": FRAME_TOLERANCE,
            },
            "inputs": {
                "video": str(video),
                "video_duration_s": round(video_s, 4),
                "audio": str(audio),
                "audio_duration_s": round(audio_s, 4),
                "audio_delta_s": round(audio_s - video_s, 4),
            },
            "totals": {
                "duration_s": (
                    round(encoded_s, 4) if encoded_s is not None else None
                ),
                "duration_delta_s": (
                    round(encoded_s - video_s, 4)
                    if encoded_s is not None
                    else None
                ),
                "size_mb": size_mb,
                "scenes": self.script.scene_count,
                "has_audio": has_audio,
                "warnings": len(self.issues.warnings),
                "errors": len(self.issues.errors),
            },
            "deliverable": deliverable,
            "output": str(output) if encoded_s is not None else None,
            "warnings": [issue.to_dict() for issue in self.issues.warnings],
            "errors": [issue.to_dict() for issue in self.issues.errors],
        }


def _parse_rate(value) -> float | None:
    """Parse ffprobe's "30000/1001" style frame rate into a number."""
    if isinstance(value, (int, float)):
        return float(value) if value else None
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, _, denominator = value.partition("/")
    try:
        top = float(numerator)
        bottom = float(denominator)
    except ValueError:
        return None
    if bottom == 0:
        return None
    return top / bottom
