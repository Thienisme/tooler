"""
Stage 5 — mix: narration, background music and SFX onto one audio track.

Stage 2 produced a single narration master and stage 4 produced a single
silent video.  This stage produces the single audio file that stage 6 muxes
into it, so there is exactly one place where the three sound layers meet and
exactly one file to listen to before the render.

The three layers are built as separate stems first
(`audio/stem_music.wav`, `audio/stem_sfx.wav`) and only then summed with the
narration.  That split is what makes a wrong mix diagnosable: a stem can be
played on its own and compared against the script's intents, which a
combined `filter_complex` of 50 inputs cannot.

Design notes
------------
* The music bed is looped to the video's length and faded, because a stock
  track is almost never the same length as the episode.  A seam that a loop
  introduces is reported, since only the writer knows whether the track
  loops cleanly.
* SFX are placed at their absolute timeline position
  (`scene.start_s + time_offset_ms`) rather than per scene, so an offset is
  never accidentally interpreted against the wrong origin.
* The sum is not normalised blindly.  Blind normalisation would undo the
  deliberate balance between voice and music; instead the mix is measured,
  corrected only when it has drifted from the target, and peak-limited only
  when it actually exceeds the ceiling.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from autovid.application.issues import Issue, IssueCollector
from autovid.domain.characters import character_sfx_cues, resolve_character_cues
from autovid.domain.script import Script
from autovid.infrastructure.audio.tools import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_TRUE_PEAK_DB,
    AudioError,
    LoudnessInfo,
    convert_to_wav,
    limit_peak,
    measure_loudness,
    silence_file,
)
from autovid.infrastructure.ffmpeg import (
    ffmpeg_available,
    probe_duration,
    run,
)
from autovid.paths import Paths, read_json, resolve_asset, write_json

# How far the mix may sit from the loudness target before it is corrected.
# The voice master is already on target, so anything beyond this came from
# the music or the SFX, not from the narration.
MIX_LEVEL_TOLERANCE_LUFS = 0.5

# Ceiling the finished mix must respect.  Matched to the limiter default so
# "corrected" and "sent" always describe the same number.
MIX_PEAK_CEILING_DB = DEFAULT_TRUE_PEAK_DB

# A mix whose length differs from the video by more than this will not line
# up at the mux.  One frame at 30fps is 33ms; this is a little looser.
MIX_DURATION_TOLERANCE_S = 0.05

STEM_VERSION = 1


@dataclass
class SfxPlacement:
    """One SFX, resolved to an absolute position in the mix."""

    scene_id: int
    index: int
    file: str
    start_s: float
    volume: float
    duration_s: float | None
    placed: bool
    note: str = ""
    # "script" for an SFX the script listed, otherwise the character cue
    # that asked for it -- an entrance sound is part of the character, not
    # of the scene, and the report should say which is which.
    source: str = "script"

    @property
    def end_s(self) -> float:
        return self.start_s + (self.duration_s or 0.0)

    def to_dict(self) -> dict:
        payload = {
            "scene_id": self.scene_id,
            "index": self.index,
            "source": self.source,
            "file": self.file,
            "start_s": round(self.start_s, 4),
            "volume": self.volume,
            "duration_s": (
                round(self.duration_s, 4) if self.duration_s is not None else None
            ),
            "placed": self.placed,
        }
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass
class MixStageResult:
    status: str
    mix: Path | None
    loudness: LoudnessInfo | None
    placements: list[SfxPlacement]
    report: dict
    issues: list[Issue] = field(default_factory=list)


class MixStage:
    """
    Builds the finished audio track for one episode.

    Reads `output/timeline.json` (for scene start times and the video's
    length) and writes `audio/mix.wav`, which is what stage 6 muxes.
    """

    def __init__(
        self,
        script: Script,
        paths: Paths,
        *,
        skip_music: bool = False,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        progress=None,
    ) -> None:
        self.script = script
        self.paths = paths
        self.skip_music = skip_music
        self.sample_rate = sample_rate
        self.channels = channels
        self.progress = progress or (lambda message: None)
        self.issues = IssueCollector()

        self.stems_dir = paths.audio_dir
        self.tolerance_s = MIX_DURATION_TOLERANCE_S

    # -- entry point ------------------------------------------------------

    def run(self) -> MixStageResult:
        if not ffmpeg_available():
            self.issues.error(
                "ffmpeg_missing",
                "ffmpeg not found; mixing needs a static binary in bin/ffmpeg "
                "or an ffmpeg on PATH",
            )
            return self._abort({})

        timeline = read_json(self.paths.output_dir / "timeline.json")
        if not isinstance(timeline, dict):
            self.issues.error(
                "timeline_missing",
                "output/timeline.json not found; run the tts stage first so "
                "SFX land on the narration's real timeline",
            )
            return self._abort({})

        voice = self.paths.voiceover_path
        if not voice.exists() or voice.stat().st_size == 0:
            self.issues.error(
                "voiceover_missing",
                f"{voice.name} not found; run the tts stage first",
            )
            return self._abort({})

        self.paths.create()

        target_s, source = self._target_duration(timeline)
        if target_s <= 0:
            self.issues.error(
                "empty_timeline",
                "the timeline reports a duration of zero; there is nothing "
                "to mix against",
            )
            return self._abort({})

        placements = self._place_sfx(timeline, target_s)
        music = self._build_music_stem(target_s)
        sfx_stem = self._build_sfx_stem(target_s, placements)

        sources = [voice]
        if music is not None:
            sources.append(music)
        if sfx_stem is not None:
            sources.append(sfx_stem)

        mixed = self._sum(target_s, sources)
        shaped, loudness, corrections = self._shape_level(mixed, target_s)

        mix = self.paths.mix_path
        if shaped != mix:
            shaped.replace(mix)

        measured_s = probe_duration(mix)
        delta = measured_s - target_s
        if abs(delta) > self.tolerance_s:
            self.issues.warn(
                "mix_duration_mismatch",
                f"the mix is {measured_s:.3f}s but the video is "
                f"{target_s:.3f}s ({delta:+.3f}s); the mux would drift",
            )

        report = self._build_report(
            timeline=timeline,
            target_s=target_s,
            target_source=source,
            placements=placements,
            music=music,
            sfx_stem=sfx_stem,
            mixed_s=measured_s,
            loudness=loudness,
            corrections=corrections,
            stem_count=len(sources),
        )
        write_json(self.paths.output_dir / "mix_report.json", report)

        return MixStageResult(
            status=self.issues.status,
            mix=mix,
            loudness=loudness,
            placements=placements,
            report=report,
            issues=self.issues.issues,
        )

    def _abort(self, report: dict) -> MixStageResult:
        return MixStageResult(
            status="fail",
            mix=None,
            loudness=None,
            placements=[],
            report=report or {"stage": "mix", "status": "fail", "stems": []},
            issues=self.issues.issues,
        )

    # -- target -----------------------------------------------------------

    def _target_duration(self, timeline: dict) -> tuple[float, str]:
        """
        How long the mix must be.

        The rendered video is the authority: audio is muxed into it, so
        anything else would either cut the video short or leave its last
        moment silent.  The timeline is the fallback for a run where stage 4
        has not produced a preview yet.
        """
        preview = self.paths.preview_path
        if preview.exists() and preview.stat().st_size > 0:
            try:
                video_s = probe_duration(preview)
            except (RuntimeError, FileNotFoundError) as error:
                self.issues.warn(
                    "preview_unreadable",
                    f"could not probe the preview video ({error}); mixing "
                    "against the timeline instead",
                )
            else:
                timeline_s = float(timeline.get("total_duration_s") or 0.0)
                if timeline_s and abs(video_s - timeline_s) > 0.5:
                    self.issues.warn(
                        "preview_length_mismatch",
                        f"the preview video is {video_s:.3f}s but the "
                        f"timeline says {timeline_s:.3f}s; mixing to the "
                        "video",
                    )
                return video_s, "preview_video"

        timeline_s = float(timeline.get("total_duration_s") or 0.0)
        if timeline_s and not self.paths.preview_path.exists():
            self.issues.warn(
                "preview_missing",
                "output/preview_video.mp4 not found; mixing against the "
                "timeline, so the mix may not line up with a video rendered "
                "from different narration",
            )
        return timeline_s, "timeline"

    # -- music ------------------------------------------------------------

    def _build_music_stem(self, target_s: float) -> Path | None:
        """
        Loop, gain and fade the background track to the video's length.

        Returning None is the "no music" case, not a failure: a script may
        legitimately declare no track, and `--skip-music` exists for
        checking the voice and SFX on their own.
        """
        config = self.script.audio_config

        if self.skip_music:
            return None

        if not config.background_music:
            return None

        asset = resolve_asset(config.background_music, self.paths.workspace)
        if asset is None:
            self.issues.error(
                "background_music_missing",
                f"background music not found: {config.background_music}",
            )
            return None

        try:
            native_s = probe_duration(asset)
        except (RuntimeError, FileNotFoundError) as error:
            self.issues.error(
                "background_music_unreadable",
                f"could not read {config.background_music}: {error}",
            )
            return None

        stem = self.stems_dir / "stem_music.wav"
        fade_in = max(0.0, config.fade_in_seconds)
        fade_out = max(0.0, config.fade_out_seconds)
        fade_out_start = max(0.0, target_s - fade_out)

        filters = [f"volume={config.background_volume:.4f}"]
        if fade_in > 0:
            filters.append(f"afade=t=in:st=0:d={min(fade_in, target_s):.4f}")
        if fade_out > 0:
            filters.append(
                f"afade=t=out:st={fade_out_start:.4f}:"
                f"d={min(fade_out, target_s):.4f}"
            )

        try:
            # `-stream_loop -1` repeats the track for as long as `-t` asks
            # for, which is the only way to fill a video longer than the
            # music without generating an intermediate file first.
            run(
                [
                    "-y",
                    "-loglevel",
                    "error",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(asset),
                    "-t",
                    f"{target_s:.4f}",
                    "-af",
                    ",".join(filters),
                    "-ar",
                    str(self.sample_rate),
                    "-ac",
                    str(self.channels),
                    "-c:a",
                    "pcm_s16le",
                    str(stem),
                ],
                timeout=900,
            )
        except RuntimeError as error:
            stem.unlink(missing_ok=True)
            self.issues.error(
                "background_music_failed",
                f"could not build the music bed from "
                f"{config.background_music}: {error}",
            )
            return None

        if native_s + 0.05 < target_s:
            self.issues.warn(
                "background_music_looped",
                f"the music track is {native_s:.1f}s for a "
                f"{target_s:.1f}s video, so it is looped; a seam is audible "
                "unless the track was written to loop",
            )

        self.progress(
            f"music bed  : {native_s:.1f}s track -> {target_s:.1f}s "
            f"at {config.background_volume:.2f}"
        )
        return stem

    # -- SFX --------------------------------------------------------------

    def _sfx_requests(self, timeline: dict) -> list[dict]:
        """
        Every sound effect the episode asks for, scripted or character.

        Character sounds are resolved with the same helper stage 4 uses, so
        a landing is heard at the instant the landing was rendered -- the
        two stages cannot drift apart, because they are not each doing the
        arithmetic.
        """
        requests: list[dict] = []

        for scene in self.script.scenes:
            for index, sfx in enumerate(scene.sfx):
                requests.append(
                    {
                        "scene_id": scene.id,
                        "index": index,
                        "source": "script",
                        "file": sfx.file,
                        "volume": sfx.volume,
                        "offset_s": sfx.time_offset_ms / 1000.0,
                    }
                )

        if not any(scene.characters for scene in self.script.scenes):
            return requests

        plan = resolve_character_cues(
            self.script,
            timeline=timeline,
            tts_report=read_json(self.paths.output_dir / "tts_report.json"),
            pacing_report=read_json(self.paths.output_dir / "pacing_report.json"),
        )
        for placement in character_sfx_cues(plan):
            requests.append(
                {
                    "scene_id": placement["scene_id"],
                    "index": placement["offset_index"],
                    "source": placement["source"],
                    "file": placement["file"],
                    "volume": placement["volume"],
                    "offset_s": placement["offset_s"],
                }
            )

        return requests

    def _place_sfx(self, timeline: dict, target_s: float) -> list[SfxPlacement]:
        """
        Resolve every SFX to an absolute time.

        A scene that the timeline does not contain has no start time, so its
        SFX cannot be placed anywhere honest; that is reported rather than
        guessed at zero.
        """
        starts = {
            int(entry["id"]): float(entry.get("start_s") or 0.0)
            for entry in timeline.get("scenes") or []
            if isinstance(entry, dict) and isinstance(entry.get("id"), int)
        }

        placements: list[SfxPlacement] = []

        for request in self._sfx_requests(timeline):
            scene_id = request["scene_id"]
            index = request["index"]
            source = request["source"]
            reference = request["file"]

            if scene_id not in starts:
                placements.append(
                    SfxPlacement(
                        scene_id=scene_id,
                        index=index,
                        file=reference,
                        start_s=0.0,
                        volume=request["volume"],
                        duration_s=None,
                        placed=False,
                        note="scene missing from timeline",
                        source=source,
                    )
                )
                self.issues.warn(
                    "sfx_scene_not_in_timeline",
                    f"this scene has no timeline entry, so its SFX cannot "
                    f"be placed ({reference}, from {source})",
                    scene_id=scene_id,
                    unit_index=index,
                )
                continue

            asset = resolve_asset(reference, self.paths.workspace)
            start_s = starts[scene_id] + request["offset_s"]

            if asset is None:
                placements.append(
                    SfxPlacement(
                        scene_id=scene_id,
                        index=index,
                        file=reference,
                        start_s=start_s,
                        volume=request["volume"],
                        duration_s=None,
                        placed=False,
                        note="file not found",
                        source=source,
                    )
                )
                self.issues.error(
                    "sfx_missing",
                    f"sfx not found: {reference} (from {source})",
                    scene_id=scene_id,
                    unit_index=index,
                )
                continue

            try:
                duration_s = probe_duration(asset)
            except (RuntimeError, FileNotFoundError) as error:
                placements.append(
                    SfxPlacement(
                        scene_id=scene_id,
                        index=index,
                        file=reference,
                        start_s=start_s,
                        volume=request["volume"],
                        duration_s=None,
                        placed=False,
                        note=str(error),
                        source=source,
                    )
                )
                self.issues.error(
                    "sfx_unreadable",
                    f"could not read {reference}: {error}",
                    scene_id=scene_id,
                    unit_index=index,
                )
                continue

            if start_s >= target_s:
                placements.append(
                    SfxPlacement(
                        scene_id=scene_id,
                        index=index,
                        file=reference,
                        start_s=start_s,
                        volume=request["volume"],
                        duration_s=duration_s,
                        placed=False,
                        note="starts after the video ends",
                        source=source,
                    )
                )
                self.issues.warn(
                    "sfx_outside_timeline",
                    f"sfx {reference} starts at {start_s:.2f}s but the "
                    f"video ends at {target_s:.2f}s; dropped",
                    scene_id=scene_id,
                    unit_index=index,
                )
                continue

            if start_s + duration_s > target_s + 0.05:
                self.issues.warn(
                    "sfx_overruns_video",
                    f"sfx {reference} would end at "
                    f"{start_s + duration_s:.2f}s, past the video's "
                    f"{target_s:.2f}s; it is cut short",
                    scene_id=scene_id,
                    unit_index=index,
                )

            placements.append(
                SfxPlacement(
                    scene_id=scene_id,
                    index=index,
                    file=reference,
                    start_s=start_s,
                    volume=request["volume"],
                    duration_s=duration_s,
                    placed=True,
                    source=source,
                )
            )

        return placements

    def _canonical_sfx(self, reference: str, asset: Path) -> Path | None:
        """
        A canonical-format copy of one SFX, cached against the source file.

        The mix graph mixes many inputs at once; converting them up front
        means every branch of the graph is 48kHz stereo and none of the
        mixing arithmetic is done on mismatched rates.
        """
        stat = asset.stat()
        key = hashlib.sha1(
            f"{asset}:{stat.st_size}:{int(stat.st_mtime)}".encode("utf-8")
        ).hexdigest()[:12]
        target = self.paths.mix_cache_dir / f"sfx_{key}.wav"

        if target.exists() and target.stat().st_size > 0:
            return target

        try:
            convert_to_wav(
                asset,
                target,
                sample_rate=self.sample_rate,
                channels=self.channels,
            )
        except (AudioError, RuntimeError) as error:
            self.issues.error(
                "sfx_convert_failed",
                f"could not convert {reference}: {error}",
            )
            return None
        return target

    def _build_sfx_stem(
        self, target_s: float, placements: list[SfxPlacement]
    ) -> Path | None:
        """
        A full-length silent bed with every SFX placed on top of it.

        Placing them on a silence bed rather than mixing them into the music
        keeps the two layers independent: the bed can be listened to alone,
        and an SFX that is too loud is not confused with the music.
        """
        ready: list[tuple[SfxPlacement, Path]] = []
        for placement in placements:
            if not placement.placed:
                continue
            asset = resolve_asset(placement.file, self.paths.workspace)
            if asset is None:
                continue
            canonical = self._canonical_sfx(placement.file, asset)
            if canonical is None:
                continue
            ready.append((placement, canonical))

        if not ready:
            return None

        bed = silence_file(
            self.paths.mix_cache_dir,
            int(round(target_s * 1000)),
            sample_rate=self.sample_rate,
            channels=self.channels,
        )

        statements: list[str] = ["[0:a]anull[base]"]
        labels: list[str] = ["[base]"]
        inputs: list[str] = ["-i", str(bed)]

        for position, (placement, canonical) in enumerate(ready, start=1):
            offset_ms = max(0, int(round(placement.start_s * 1000)))
            statements.append(
                f"[{position}:a]aresample={self.sample_rate},"
                f"adelay={offset_ms}:all=1,"
                f"volume={placement.volume:.4f}[s{position}]"
            )
            labels.append(f"[s{position}]")
            inputs.extend(["-i", str(canonical)])

        statements.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:"
            "dropout_transition=0:normalize=0[out]"
        )

        stem = self.stems_dir / "stem_sfx.wav"
        try:
            run(
                [
                    "-y",
                    *inputs,
                    "-filter_complex",
                    ";\n".join(statements),
                    "-map",
                    "[out]",
                    "-ar",
                    str(self.sample_rate),
                    "-ac",
                    str(self.channels),
                    "-c:a",
                    "pcm_s16le",
                    str(stem),
                ],
                timeout=900,
            )
        except RuntimeError as error:
            stem.unlink(missing_ok=True)
            self.issues.error(
                "sfx_stem_failed",
                f"could not place the SFX ({len(ready)} of them): {error}",
            )
            return None

        self.progress(
            f"sfx stem   : {len(ready)} placed across {target_s:.1f}s"
        )
        return stem

    # -- summing ----------------------------------------------------------

    def _sum(self, target_s: float, sources: list[Path]) -> Path:
        """
        Sum the stems into one raw mix.

        `normalize=0` is the whole point: ffmpeg's default for `amix` divides
        every input by the count, which would drop the narration by 6-10 dB
        the moment a second layer exists.  Levels are the script's job, and
        they were already applied to each stem.
        """
        raw = self.paths.audio_dir / "mix_raw.wav"

        # The narration is padded rather than the output trusted to be long
        # enough: whichever layer is longest, `-t` makes the mix exactly the
        # video's length, so nothing downstream has to reconcile the two.
        statements = [
            f"[0:a]aresample={self.sample_rate},apad[voice]",
        ]
        labels = ["[voice]"]
        for position in range(1, len(sources)):
            statements.append(f"[{position}:a]aresample={self.sample_rate}[s{position}]")
            labels.append(f"[s{position}]")

        if len(labels) == 1:
            statements = [f"[0:a]aresample={self.sample_rate},apad[out]"]
        else:
            statements.append(
                f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:"
                "dropout_transition=0:normalize=0[out]"
            )

        inputs: list[str] = []
        for source in sources:
            inputs.extend(["-i", str(source)])

        try:
            run(
                [
                    "-y",
                    *inputs,
                    "-filter_complex",
                    ";\n".join(statements),
                    "-map",
                    "[out]",
                    "-t",
                    f"{target_s:.4f}",
                    "-ar",
                    str(self.sample_rate),
                    "-ac",
                    str(self.channels),
                    "-c:a",
                    "pcm_s16le",
                    str(raw),
                ],
                timeout=1800,
            )
        except RuntimeError as error:
            raw.unlink(missing_ok=True)
            raise AudioError(f"could not mix the stems: {error}") from error

        self.progress(f"summed     : {len(sources)} stem(s) -> {target_s:.1f}s")
        return raw

    def _shape_level(
        self, mixed: Path, target_s: float
    ) -> tuple[Path, LoudnessInfo, list[dict]]:
        """
        Measure the mix and correct only what is actually off.

        Two corrections exist and each is a separate, reported decision:

        * **level** — adding music and SFX to a normalised narration raises
          the integrated loudness.  A single linear gain brings the whole mix
          back to the target without re-balancing the layers against each
          other.
        * **peak** — a transient that survives the gain gets limited, because
          a true peak over the broadcast ceiling clips on playout.

        A full loudnorm pass would do both, but single-pass loudnorm rides the
        gain dynamically, which for a finished mix means audible pumping
        between quiet and loud passages.

        Returns the file that survived the corrections, along with its final
        measurements and a record of what was changed.
        """
        target_lufs = self.script.audio_config.master_volume
        corrections: list[dict] = []

        try:
            measured = measure_loudness(mixed, target_lufs=target_lufs)
        except AudioError as error:
            raise AudioError(f"could not measure the mix: {error}") from error

        if measured.integrated_lufs == float("-inf"):
            raise AudioError("the mix measures as pure silence; nothing to send")

        gain_db = target_lufs - measured.integrated_lufs
        working = mixed

        if abs(gain_db) > MIX_LEVEL_TOLERANCE_LUFS:
            working = self.paths.audio_dir / "mix_gain.wav"
            try:
                run(
                    [
                        "-y",
                        "-i",
                        str(mixed),
                        "-af",
                        f"volume={gain_db:.3f}dB",
                        "-ar",
                        str(self.sample_rate),
                        "-ac",
                        str(self.channels),
                        "-c:a",
                        "pcm_s16le",
                        str(working),
                    ],
                    timeout=900,
                )
            except RuntimeError as error:
                raise AudioError(
                    f"could not correct the mix level: {error}"
                ) from error

            corrections.append(
                {
                    "kind": "level",
                    "gain_db": round(gain_db, 3),
                    "before_lufs": round(measured.integrated_lufs, 2),
                    "target_lufs": round(target_lufs, 2),
                }
            )
            self.issues.warn(
                "mix_level_corrected",
                f"the mix measured {measured.integrated_lufs:.1f} LUFS against "
                f"a target of {target_lufs:.1f}; applied {gain_db:+.2f} dB",
            )
            mixed.unlink(missing_ok=True)
            measured = measure_loudness(working, target_lufs=target_lufs)

        if measured.true_peak_db > MIX_PEAK_CEILING_DB:
            limited = self.paths.audio_dir / "mix_limited.wav"
            limit_peak(
                working,
                limited,
                ceiling_db=MIX_PEAK_CEILING_DB,
                sample_rate=self.sample_rate,
                channels=self.channels,
            )
            corrections.append(
                {
                    "kind": "peak",
                    "ceiling_db": MIX_PEAK_CEILING_DB,
                    "before_peak_db": round(measured.true_peak_db, 2),
                }
            )
            self.issues.warn(
                "mix_peak_limited",
                f"true peak {measured.true_peak_db:+.2f} dBFS exceeds the "
                f"{MIX_PEAK_CEILING_DB:.1f} dBFS ceiling; limited",
            )
            limited.replace(working)
            measured = measure_loudness(working, target_lufs=target_lufs)

        self.progress(
            f"loudness   : {measured.integrated_lufs:.1f} LUFS "
            f"(target {target_lufs:.1f}), peak "
            f"{measured.true_peak_db:+.2f} dBFS"
        )
        return working, measured, corrections

    # -- report -----------------------------------------------------------

    def _build_report(
        self,
        *,
        timeline: dict,
        target_s: float,
        target_source: str,
        placements: list[SfxPlacement],
        music: Path | None,
        sfx_stem: Path | None,
        mixed_s: float,
        loudness: LoudnessInfo,
        corrections: list[dict],
        stem_count: int,
    ) -> dict:
        placed = [placement for placement in placements if placement.placed]
        config = self.script.audio_config

        return {
            "stage": "mix",
            "status": self.issues.status,
            "settings": {
                "background_music": config.background_music,
                "background_volume": config.background_volume,
                "master_volume": config.master_volume,
                "fade_in_seconds": config.fade_in_seconds,
                "fade_out_seconds": config.fade_out_seconds,
                "skip_music": self.skip_music,
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "peak_ceiling_db": MIX_PEAK_CEILING_DB,
            },
            "totals": {
                "duration_s": round(mixed_s, 4),
                "target_duration_s": round(target_s, 4),
                "duration_delta_s": round(mixed_s - target_s, 4),
                "target_source": target_source,
                "stems": stem_count,
                "scenes": len(timeline.get("scenes") or []),
                "sfx_scripted": len(placements),
                "sfx_placed": len(placed),
                "sfx_dropped": len(placements) - len(placed),
                "music_used": music is not None,
                "sfx_used": sfx_stem is not None,
                "loudness": loudness.to_dict(),
                "loudness_target_lufs": config.master_volume,
                "corrections": corrections,
                "warnings": len(self.issues.warnings),
                "errors": len(self.issues.errors),
            },
            "stems": [
                {"role": "voice", "file": str(self.paths.voiceover_path)},
                *(
                    [{"role": "music", "file": str(music)}]
                    if music is not None
                    else []
                ),
                *(
                    [{"role": "sfx", "file": str(sfx_stem)}]
                    if sfx_stem is not None
                    else []
                ),
            ],
            "output": str(self.paths.mix_path),
            "sfx": [placement.to_dict() for placement in placements],
            "warnings": [issue.to_dict() for issue in self.issues.warnings],
            "errors": [issue.to_dict() for issue in self.issues.errors],
        }
