"""
Stage 7 — captions: optional subtitles for the finished video.

Timings come from stage 2's measurements, not from a guess and not from a
second speech recogniser re-listening to the audio.  That matters more than it
sounds: the pipeline already knows exactly when every sentence starts, because
it synthesized the sentences individually and laid out the pauses between
them.  Re-transcribing the finished mix would cost minutes of inference and
produce *worse* timings than the ones already on disk.

`--asr` is still available for the case where the audio was not produced by
this pipeline, or where word-level timing is wanted: it runs faster-whisper on
the mixed track and uses those segments instead.  It is optional, so it is
imported lazily and a missing install is a clear error rather than a crash.

Outputs
-------
    output/captions.srt                  the subtitle file
    output/final_video_subtitled.mp4     the video with those subtitles
    output/captions_report.json          cue counts, coverage, checks

The subtitles are muxed as a soft track by default (stream copy, so the
picture is untouched).  `--burn` renders them into the picture instead, which
costs one re-encode but guarantees they are visible everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from autovid.application.issues import Issue, IssueCollector
from autovid.application.quality import write_quality_report
from autovid.domain.script import Script
from autovid.domain.sentences import split_sentences
from autovid.infrastructure.ffmpeg import (
    ffmpeg_available,
    probe_duration,
    probe_streams,
    run,
)
from autovid.paths import Paths, read_json, write_json

# Two lines of around 42 characters is the broadcast subtitling convention:
# any longer and the eye cannot finish the line before it changes.
DEFAULT_MAX_CHARS_PER_LINE = 42
MAX_LINES = 2

# A cue on screen for less than this cannot be read at all, so it is merged
# into the neighbouring cue instead of being shown.
MIN_CUE_SECONDS = 0.8

# A cue shorter than this is not worth its own entry either; it is what
# remains when a sentence is split into very uneven pieces.
MIN_SPLIT_SECONDS = 0.6

# Burned-in style: white text with a black outline reads on any artwork.
BURN_STYLE = (
    "FontName=DejaVu Sans,FontSize=24,BorderStyle=1,Outline=2,"
    "Shadow=0,MarginV=36,Alignment=2,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000"
)



@dataclass
class Cue:
    """One subtitle: a time window and the text to show in it."""

    index: int
    start_s: float
    end_s: float
    text: str
    scene_id: int | None = None
    source: str = "timeline"

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    def to_dict(self) -> dict:
        payload = {
            "index": self.index,
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
            "duration_s": round(self.duration_s, 3),
            "lines": self.lines,
            "source": self.source,
        }
        if self.scene_id is not None:
            payload["scene_id"] = self.scene_id
        return payload


@dataclass
class CaptionsStageResult:
    status: str
    subtitles: Path | None
    video: Path | None
    cues: list[Cue]
    report: dict
    issues: list[Issue] = field(default_factory=list)


class CaptionsStage:
    """
    Writes subtitles for a rendered episode and attaches them to the video.

    Reads `output/final_video.mp4` plus the stage 2/4 timings, and writes
    `output/captions.srt` and `output/final_video_subtitled.mp4`.
    """

    def __init__(
        self,
        script: Script,
        paths: Paths,
        *,
        asr: bool = False,
        burn: bool = False,
        max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE,
        asr_model: str = "small",
        progress=None,
    ) -> None:
        self.script = script
        self.paths = paths
        self.asr = asr
        self.burn = burn
        self.max_chars_per_line = max(12, max_chars_per_line)
        self.asr_model = asr_model
        self.progress = progress or (lambda message: None)
        self.issues = IssueCollector()

        # Probed from the ffmpeg build once, on first use, because both
        # answers are properties of the binary rather than of this run.
        self._filters: set[str] | None = None
        self._mov_text: bool | None = None

    # -- entry point ------------------------------------------------------

    def run(self) -> CaptionsStageResult:
        if not ffmpeg_available():
            self.issues.error(
                "ffmpeg_missing",
                "ffmpeg not found; captions need a static binary in bin/ffmpeg "
                "or an ffmpeg on PATH",
            )
            return self._abort()

        video = self.paths.final_video_path
        if not video.exists() or video.stat().st_size == 0:
            self.issues.error(
                "render_missing",
                "output/final_video.mp4 not found; run the render stage first "
                "so there is a video to subtitle",
            )
            return self._abort()

        try:
            video_s = probe_duration(video)
        except (RuntimeError, FileNotFoundError) as error:
            self.issues.error(
                "render_unreadable", f"could not read the video: {error}"
            )
            return self._abort()

        source = "asr" if self.asr else "timeline"
        try:
            cues = (
                self._cues_from_asr(video_s)
                if self.asr
                else self._cues_from_timeline(video_s)
            )
        except CaptionsError as error:
            self.issues.error("captions_source_failed", str(error))
            return self._abort()

        cues = [cue for cue in cues if cue.duration_s > 0 and cue.text.strip()]

        if not cues:
            self.issues.error(
                "no_cues",
                "no subtitle cues could be derived from the timings; "
                "subtitle the episode by hand or check the script's text",
            )
            return self._abort()

        self._check_cues(cues, video_s)

        self.paths.create()
        subtitles = self.paths.subtitles_path
        subtitles.write_text(render_srt(cues), encoding="utf-8")

        if self.burn and not self._has_filter("subtitles"):
            self.issues.error(
                "subtitles_filter_missing",
                "this ffmpeg build has no 'subtitles' filter (libass); burn-in "
                "is impossible. Re-run without --burn to mux soft subtitles",
            )
            return self._abort()

        output = self.paths.subtitled_video_path
        try:
            if self.burn:
                self._burn(video, subtitles, output, video_s)
            else:
                self._mux(video, subtitles, output)
        except RuntimeError as error:
            output.unlink(missing_ok=True)
            self.issues.error(
                "captions_video_failed",
                f"could not attach the subtitles: {error}",
            )
            return self._abort()

        self._verify(output, video_s)

        report = self._build_report(
            cues=cues,
            source=source,
            video_s=video_s,
            subtitles=subtitles,
            output=output,
        )
        write_json(self.paths.output_dir / "captions_report.json", report)

        # The merged report was written by the render stage; now that captions
        # exist, it would otherwise describe an episode without them.
        if isinstance(read_json(self.paths.quality_report_path), dict):
            write_quality_report(self.script, self.paths)

        return CaptionsStageResult(
            status=self.issues.status,
            subtitles=subtitles,
            video=output,
            cues=cues,
            report=report,
            issues=self.issues.issues,
        )

    def _abort(self) -> CaptionsStageResult:
        return CaptionsStageResult(
            status="fail",
            subtitles=None,
            video=None,
            cues=[],
            report={"stage": "captions", "status": "fail", "cues": []},
            issues=self.issues.issues,
        )

    # -- cue sources ------------------------------------------------------

    def _cues_from_timeline(self, video_s: float) -> list[Cue]:
        """
        Build cues from the timings stage 2 measured.

        The order of authority is: the sentence durations stage 2 measured,
        the inter-sentence pauses the pacing engine placed, and the scene
        start times from the timeline.  All three are on disk, so nothing has
        to be estimated — except when stage 2's report is missing, in which
        case the text is spread proportionally across each scene's narration
        and the report says so.
        """
        timeline = read_json(self.paths.output_dir / "timeline.json")
        if not isinstance(timeline, dict) or not timeline.get("scenes"):
            raise CaptionsError(
                "output/timeline.json not found; run the tts stage first, or "
                "use --asr to transcribe the finished audio"
            )

        tts = read_json(self.paths.output_dir / "tts_report.json")
        tts_scenes: dict[int, dict] = {}
        if isinstance(tts, dict):
            for entry in tts.get("scenes") or []:
                if isinstance(entry, dict) and isinstance(entry.get("id"), int):
                    tts_scenes[entry["id"]] = entry

        pacing = read_json(self.paths.output_dir / "pacing_report.json")
        paused: dict[int, list[int]] = {}
        if isinstance(pacing, dict):
            for entry in pacing.get("scenes") or []:
                if not isinstance(entry, dict):
                    continue
                sentences = entry.get("sentences") or []
                paused[entry.get("id")] = [
                    int(sentence.get("pause_ms") or 0)
                    for sentence in sentences
                    if isinstance(sentence, dict)
                ]

        if not tts_scenes:
            self.issues.warn(
                "captions_estimated_timing",
                "output/tts_report.json not found, so sentence lengths are "
                "estimated from the scene's narration instead of measured; "
                "cues may lag or lead the voice",
            )

        cues: list[Cue] = []
        for scene in self.script.scenes:
            entry = _scene_entry(timeline, scene.id)
            if entry is None:
                self.issues.warn(
                    "scene_not_in_timeline",
                    "this scene has no timeline entry, so its text cannot be "
                    "timed and gets no subtitles",
                    scene_id=scene.id,
                )
                continue

            start_s = float(entry.get("start_s") or 0.0)
            narration_s = float(entry.get("narration_s") or 0.0)
            if narration_s <= 0:
                continue

            report_scene = tts_scenes.get(scene.id)
            measured = (
                report_scene.get("units") if report_scene else None
            )
            pauses = paused.get(scene.id) or []

            cues.extend(
                self._scene_cues(
                    scene_id=scene.id,
                    text=scene.text,
                    start_s=start_s,
                    narration_s=narration_s,
                    units=measured if isinstance(measured, list) else None,
                    pauses=pauses,
                    video_s=video_s,
                    source="timeline" if measured else "estimated",
                )
            )

        return _numbered(cues)

    def _scene_cues(
        self,
        *,
        scene_id: int,
        text: str,
        start_s: float,
        narration_s: float,
        units: list | None,
        pauses: list[int],
        video_s: float,
        source: str,
    ) -> list[Cue]:
        """
        One scene's cues.

        When measured units exist, each unit's own duration is used and the
        pauses the pacing engine inserted are honoured, so the cue changes
        when the voice does.  Otherwise the sentences share out the narration
        in proportion to their length.
        """
        measured: list[tuple[str, float]] = []
        if units:
            for unit in units:
                if not isinstance(unit, dict):
                    continue
                unit_text = str(unit.get("text") or "").strip()
                unit_s = float(unit.get("duration_s") or 0.0)
                if unit_text and unit_s > 0:
                    measured.append((unit_text, unit_s))

        if not measured:
            sentences = split_sentences(text) or [text.strip()]
            if not sentences:
                return []
            total_chars = sum(len(sentence) for sentence in sentences) or 1
            measured = [
                (
                    sentence,
                    narration_s * (len(sentence) / total_chars),
                )
                for sentence in sentences
            ]
            pauses = []

        cues: list[Cue] = []
        cursor = start_s
        for position, (unit_text, unit_s) in enumerate(measured):
            end_s = min(cursor + unit_s, video_s)
            for piece, piece_s in self._split_unit(
                unit_text, unit_s, cursor, end_s
            ):
                cues.append(
                    Cue(
                        index=len(cues) + 1,
                        start_s=piece_s[0],
                        end_s=piece_s[1],
                        text=piece,
                        scene_id=scene_id,
                        source=source,
                    )
                )
            cursor += unit_s
            if position < len(measured) - 1 and position < len(pauses):
                cursor += max(0, pauses[position]) / 1000.0

        return cues

    def _split_unit(
        self, text: str, duration_s: float, start_s: float, end_s: float
    ) -> list[tuple[str, tuple[float, float]]]:
        """
        Break one sentence into cues that fit the reading window.

        Wrapping happens first and pagination second, which is what keeps
        both limits true at once: every line fits the width, and every cue
        holds no more lines than a viewer can read before it changes.  A
        sentence that needs three lines therefore becomes two cues, not one
        cue with a line nobody finishes.

        Durations are shared in proportion to the characters on each page,
        which tracks speech closely enough to stay in sync.
        """
        pages = wrap_pages(text, self.max_chars_per_line, MAX_LINES)
        if len(pages) == 1:
            return [(pages[0], (start_s, end_s))]

        total_chars = sum(len(page) for page in pages) or 1
        span = max(end_s - start_s, 0.0)

        cues: list[tuple[str, tuple[float, float]]] = []
        cursor = start_s
        for position, page in enumerate(pages):
            page_s = span * (len(page) / total_chars)
            page_end = end_s if position == len(pages) - 1 else cursor + page_s
            if page_end - cursor < MIN_SPLIT_SECONDS and cues:
                # Too short to register: fold it into the previous cue rather
                # than flashing it. The previous cue keeps its text and grows.
                previous_text, previous_span = cues[-1]
                cues[-1] = (previous_text, (previous_span[0], page_end))
                cursor = page_end
                continue
            cues.append((page, (cursor, page_end)))
            cursor = page_end

        return cues

    def _cues_from_asr(self, video_s: float) -> list[Cue]:
        """Transcribe the mixed audio with faster-whisper and use its cues."""
        audio = self.paths.mix_path
        if not audio.exists() or audio.stat().st_size == 0:
            audio = self.paths.voiceover_path
        if not audio.exists() or audio.stat().st_size == 0:
            raise CaptionsError(
                "no audio to transcribe: run the tts stage (or the mix stage) "
                "first"
            )

        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as error:
            raise CaptionsError(
                "faster-whisper is not installed; install it with "
                "'pip install faster-whisper', or drop --asr to time the "
                "captions from the script and the measured narration"
            ) from error

        self.progress(f"transcribing {audio.name} with {self.asr_model}")
        try:
            model = WhisperModel(self.asr_model, device="auto")
            segments, _info = model.transcribe(
                str(audio),
                language=self.script.video_metadata.language or None,
                vad_filter=True,
            )
            segments = list(segments)
        except Exception as error:  # noqa: BLE001 - reported as an issue
            raise CaptionsError(f"transcription failed: {error}") from error

        cues: list[Cue] = []
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            start_s = max(0.0, float(segment.start))
            end_s = min(float(segment.end), video_s)
            if end_s <= start_s:
                continue
            cues.append(
                Cue(
                    index=len(cues) + 1,
                    start_s=start_s,
                    end_s=end_s,
                    text=wrap_pages(
                        text, self.max_chars_per_line, MAX_LINES
                    )[0],
                    source="asr",
                )
            )

        if not cues:
            raise CaptionsError(
                "the transcription produced no segments; the audio may be "
                "silent or in a language the model cannot hear"
            )
        return cues

    # -- checks -----------------------------------------------------------

    def _check_cues(self, cues: list[Cue], video_s: float) -> None:
        for cue in cues:
            if cue.end_s <= cue.start_s:
                self.issues.warn(
                    "cue_empty",
                    f"cue {cue.index} has no duration and will not be shown",
                    scene_id=cue.scene_id,
                )
            elif cue.duration_s < MIN_CUE_SECONDS:
                self.issues.warn(
                    "cue_too_short",
                    f"cue {cue.index} is on screen for "
                    f"{cue.duration_s:.2f}s ({cue.text.splitlines()[0][:40]!r}"
                    "); it cannot be read before it changes",
                    scene_id=cue.scene_id,
                )

            longest = max((len(line) for line in cue.lines), default=0)
            if longest > self.max_chars_per_line:
                self.issues.warn(
                    "cue_line_too_long",
                    f"cue {cue.index} has a {longest}-character line, over "
                    f"the {self.max_chars_per_line} maximum",
                    scene_id=cue.scene_id,
                )

        last = cues[-1]
        if last.end_s > video_s + 0.05:
            self.issues.warn(
                "cue_past_video",
                f"the last cue ends at {last.end_s:.2f}s, past the video's "
                f"{video_s:.2f}s",
            )

    def _has_filter(self, name: str) -> bool:
        """Whether the ffmpeg build has a filter (checked once, cached)."""
        if self._filters is None:
            try:
                result = run(["-hide_banner", "-filters"])
            except RuntimeError:
                self._filters = set()
            else:
                names = set()
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        names.add(parts[1])
                self._filters = names
        return name in self._filters

    # -- attaching --------------------------------------------------------

    def _mux(self, video: Path, subtitles: Path, output: Path) -> None:
        """
        Attach the subtitles as a soft track, copying the picture.

        This is the default because it is lossless and instant: the viewer's
        player draws the subtitles, and they can be turned off.
        """
        if not self._supports_mov_text():
            self.issues.warn(
                "mov_text_missing",
                "this ffmpeg build cannot write mov_text subtitles; burning "
                "them into the picture instead",
            )
            self._burn(video, subtitles, output, probe_duration(video))
            return

        run(
            [
                "-y",
                "-i",
                str(video),
                "-i",
                str(subtitles),
                "-map",
                "0",
                "-map",
                "1:0",
                "-c",
                "copy",
                "-c:s",
                "mov_text",
                "-metadata:s:s:0",
                "language=vie",
                "-movflags",
                "+faststart",
                str(output),
            ],
            timeout=None,
        )

    def _supports_mov_text(self) -> bool:
        if self._mov_text is None:
            try:
                result = run(["-hide_banner", "-encoders"])
            except RuntimeError:
                self._mov_text = False
            else:
                self._mov_text = any(
                    line.split()[1:2] == ["mov_text"]
                    for line in result.stdout.splitlines()
                    if len(line.split()) >= 2
                )
        return self._mov_text

    def _burn(
        self, video: Path, subtitles: Path, output: Path, video_s: float
    ) -> None:
        """
        Render the subtitles into the picture.

        Costs a re-encode, which is the price of guaranteeing they are
        visible on a player that ignores soft tracks — social platforms,
        for instance.
        """
        run(
            [
                "-y",
                "-i",
                str(video),
                "-vf",
                f"subtitles=filename={_escape_filter_path(subtitles)}:"
                f"force_style='{BURN_STYLE}'",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(output),
            ],
            timeout=None,
        )

    def _verify(self, output: Path, video_s: float) -> None:
        if not output.exists() or output.stat().st_size == 0:
            self.issues.error(
                "captions_output_missing", "the subtitle pass wrote no file"
            )
            return

        try:
            streams = probe_streams(output)
            duration_s = probe_duration(output)
        except (RuntimeError, FileNotFoundError) as error:
            self.issues.error(
                "captions_output_unreadable",
                f"could not inspect the subtitled video: {error}",
            )
            return

        types = [stream.get("codec_type") for stream in streams.get("streams", [])]

        if not self.burn and "subtitle" not in types:
            self.issues.error(
                "captions_stream_missing",
                "the output has no subtitle stream; the soft subs were not "
                "muxed",
            )
        if "audio" not in types:
            self.issues.error(
                "captions_lost_audio",
                "the subtitled video has no audio stream",
            )
        if "video" not in types:
            self.issues.error(
                "captions_lost_video",
                "the subtitled video has no video stream",
            )

        drift = duration_s - video_s
        if abs(drift) > 0.2:
            self.issues.warn(
                "captions_duration_changed",
                f"the subtitled video is {duration_s:.3f}s against the "
                f"original {video_s:.3f}s ({drift:+.3f}s)",
            )

        self.progress(
            f"attached   : {output.name} ({duration_s:.2f}s, "
            f"{'burned in' if self.burn else 'soft subs'})"
        )

    # -- report -----------------------------------------------------------

    def _build_report(
        self,
        *,
        cues: list[Cue],
        source: str,
        video_s: float,
        subtitles: Path,
        output: Path,
    ) -> dict:
        covered_s = sum(cue.duration_s for cue in cues)
        durations = [cue.duration_s for cue in cues]
        lines = [line for cue in cues for line in cue.lines]

        return {
            "stage": "captions",
            "status": self.issues.status,
            "settings": {
                "mode": "burn" if self.burn else "soft",
                "source": source,
                "asr_model": self.asr_model if self.asr else None,
                "max_chars_per_line": self.max_chars_per_line,
                "max_lines": MAX_LINES,
            },
            "totals": {
                "cues": len(cues),
                "covered_s": round(covered_s, 3),
                "video_s": round(video_s, 3),
                "coverage_pct": (
                    round(100 * covered_s / video_s, 1) if video_s else 0.0
                ),
                "cue_shortest_s": round(min(durations), 3) if durations else 0.0,
                "cue_longest_s": round(max(durations), 3) if durations else 0.0,
                "longest_line_chars": max((len(line) for line in lines), default=0),
                "scenes_with_text": len({cue.scene_id for cue in cues}),
                "warnings": len(self.issues.warnings),
                "errors": len(self.issues.errors),
            },
            "subtitles": str(subtitles),
            "output": str(output),
            "video": {
                "source": str(self.paths.final_video_path),
                "duration_s": round(video_s, 3),
                "subtitles": "burned" if self.burn else "soft",
            },
            "cues": [cue.to_dict() for cue in cues],
            "warnings": [issue.to_dict() for issue in self.issues.warnings],
            "errors": [issue.to_dict() for issue in self.issues.errors],
        }


class CaptionsError(RuntimeError):
    """Raised when no honest cue list can be produced."""


# --------------------------------------------------------------------------
# Text layout and SRT formatting
# --------------------------------------------------------------------------


def wrap_lines(text: str, max_chars: int) -> list[str]:
    """
    Wrap text into lines of at most `max_chars`, breaking at word boundaries.

    No line is ever merged to fit a line budget: a line that is too long to
    read is worse than an extra cue, so the budget is applied by
    `wrap_pages` instead.  A single word longer than `max_chars` is allowed
    to overflow, since breaking it would misread it.
    """
    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def paginate(lines: list[str], max_lines: int) -> list[list[str]]:
    """Group wrapped lines into pages of at most `max_lines` lines."""
    if max_lines <= 0:
        return [lines] if lines else []
    return [
        lines[start : start + max_lines]
        for start in range(0, len(lines), max_lines)
    ]


def wrap_pages(text: str, max_chars: int, max_lines: int) -> list[str]:
    """
    Text as it will appear on screen: one string per cue, lines inside it.

    Every line fits `max_chars` and every page holds at most `max_lines`
    lines, which is the pair of constraints a subtitle has to satisfy at
    once for the text to be readable before it changes.
    """
    pages = paginate(wrap_lines(text, max_chars), max_lines)
    if not pages:
        return [text.strip()]
    return ["\n".join(page) for page in pages]


def _numbered(cues: list[Cue]) -> list[Cue]:
    """Drop empty cues and renumber so indexes match the SRT."""
    kept: list[Cue] = []
    for cue in cues:
        if not cue.text.strip() or cue.end_s <= cue.start_s:
            continue
        kept.append(
            Cue(
                index=len(kept) + 1,
                start_s=cue.start_s,
                end_s=cue.end_s,
                text=cue.text,
                scene_id=cue.scene_id,
                source=cue.source,
            )
        )
    return kept


def _scene_entry(timeline: dict, scene_id: int) -> dict | None:
    for entry in timeline.get("scenes") or []:
        if isinstance(entry, dict) and entry.get("id") == scene_id:
            return entry
    return None


def format_timestamp(seconds: float) -> str:
    """SRT timestamp: HH:MM:SS,mmm."""
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(cues: list[Cue]) -> str:
    """The whole subtitle file, as the SRT spec wants it."""
    blocks: list[str] = []
    for cue in cues:
        blocks.append(
            f"{cue.index}\n"
            f"{format_timestamp(cue.start_s)} --> {format_timestamp(cue.end_s)}\n"
            f"{cue.text}\n"
        )
    return "\n".join(blocks)


def _escape_filter_path(path: Path) -> str:
    """
    Escape a path for use inside an ffmpeg filter argument.

    Filter arguments are parsed before they reach the OS, so a colon (a
    Windows drive letter, or a filename) and a quote both have to be escaped.
    """
    text = str(path.resolve())
    text = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"'{text}'"
