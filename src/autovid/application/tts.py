"""
Stage 2 — voiceover.

This is the stage everything else is measured against, so it is built
around one idea: *measure, never guess*.  Every sentence is synthesized on
its own, its exact duration and its natural silences are measured from the
file that was actually produced, and the pauses are computed from those
measurements.  The timeline that later stages consume is therefore a record
of what happened, not an estimate of what should happen.

Outputs
-------
    audio/scene_001.wav ... scene_NNN.wav   per-scene narration
    audio/voiceover_raw.wav                  concatenated, pre-normalisation
    audio/voiceover_full.wav                 master, normalised to target LUFS
    output/timeline.json                     exact scene timings for stage 4
    output/tts_report.json                   measurements, cache hits, warnings
    output/pacing_report.json                every pause and its breakdown
    output/state.json                        resume manifest

Caching
-------
Sentence audio is cached under `cache/tts/` keyed by a hash of the text,
voice, speed and granularity.  Editing a sentence therefore invalidates
only that sentence, and re-running the stage after a crash re-synthesizes
nothing that already succeeded.

The key is the *text*, not the scene, so repeated sentences are also
synthesized once.  That means `units_cached` counts both "already on disk
from an earlier run" and "same sentence seen earlier in this run"; the
distinction does not matter for cost, only for reading the report.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from autovid.application.issues import Issue, IssueCollector
from autovid.domain.pacing import (
    PauseBreakdown,
    PacingEngine,
    ScenePacing,
    build_pacing_result,
)
from autovid.domain.script import Script, Scene
from autovid.infrastructure.audio.tools import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    AudioError,
    LoudnessInfo,
    SilenceInfo,
    concatenate,
    limit_peak,
    measure_loudness,
    measure_silence,
    normalize_loudness,
    silence_file,
)
from autovid.infrastructure.ffmpeg import probe_duration
from autovid.infrastructure.tts.backend import (
    DEFAULT_CHARS_PER_SECOND,
    TTSBackend,
)
from autovid.paths import Paths, read_json, resolve_asset, write_json

DEFAULT_MAX_RETRIES = 3

# A unit shorter than this is a synthesis failure, not a short sentence.
MIN_UNIT_SECONDS = 0.15

# Measured duration below this share of the estimate suggests truncation.
SHORT_UNIT_RATIO = 0.45

# Measured duration above this multiple of the estimate suggests the model
# ran away; both bounds are warnings, not errors, because TTS pacing is not
# deterministic.
LONG_UNIT_RATIO = 3.0

# Leading silence longer than this delays the words noticeably.
MAX_LEADING_SILENCE_MS = 1500

# If the master differs from the sum of its parts by more than this, the
# timeline would drift, so it is worth shouting about.
TIMELINE_TOLERANCE_S = 0.06

TIMELINE_VERSION = 1


class SynthesisFailed(RuntimeError):
    """Raised when a text unit cannot be synthesized after every retry."""


@dataclass
class UnitResult:
    """One synthesized text unit."""

    index: int
    text: str
    key: str
    file: Path
    duration_s: float
    silence: SilenceInfo
    loudness: LoudnessInfo
    attempts: int
    cached: bool

    @property
    def chars(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "chars": self.chars,
            "text": self.text,
            "file": str(self.file),
            "duration_s": round(self.duration_s, 4),
            "cached": self.cached,
            "attempts": self.attempts,
            "silence": self.silence.to_dict(),
            "loudness": self.loudness.to_dict(),
        }


@dataclass
class SceneResult:
    """One scene's narration plus its pacing."""

    scene_id: int
    index: int
    audio_file: Path
    units: list[UnitResult]
    pacing: ScenePacing
    narration_s: float
    start_s: float = 0.0

    @property
    def pause_after_s(self) -> float:
        """Extra silence to insert after this scene's audio."""
        return self.pacing.pause_ms / 1000.0

    @property
    def natural_tail_s(self) -> float:
        """Trailing silence the voice model left inside `narration_s`."""
        if not self.units:
            return 0.0
        return self.units[-1].silence.trailing_ms / 1000.0

    @property
    def boundary_silence_s(self) -> float:
        """
        Total silence at the scene boundary.

        This is what the pacing engine sizes, and what the pause policies
        in `pacing.py` are expressed in: the requested `pause_after_ms` is
        the *total* gap, of which `natural_tail_s` already exists in the
        scene audio, so only the remainder is inserted.
        """
        return self.natural_tail_s + self.pause_after_s

    @property
    def end_s(self) -> float:
        return self.start_s + self.narration_s + self.pause_after_s

    def to_timeline(self, transition: dict, ken_burns: dict) -> dict:
        """
        The entry stage 4 renders from.

        Transition and Ken Burns settings are copied in because a
        transition overlaps two clips, so it changes the arithmetic of
        where each scene physically starts.

        Three separate figures describe the boundary, because conflating
        them is how out-of-sync videos happen:

        * `narration_s`   — length of the scene's audio file, which already
          ends in `natural_tail_s` of silence.
        * `pause_after_s` — silence to insert after that file.  Holding the
          image for `narration_s + pause_after_s` makes the next image
          appear exactly when the next sentence starts.
        * `boundary_silence_s` — the audible gap the writer asked for
          (`natural_tail_s + pause_after_s`).
        """
        return {
            "id": self.scene_id,
            "index": self.index,
            "start_s": round(self.start_s, 4),
            "narration_s": round(self.narration_s, 4),
            "pause_after_s": round(self.pause_after_s, 4),
            "natural_tail_s": round(self.natural_tail_s, 4),
            "boundary_silence_s": round(self.boundary_silence_s, 4),
            "end_s": round(self.end_s, 4),
            "audio_file": str(self.audio_file),
            "unit_count": len(self.units),
            "transition_in": transition,
            "ken_burns": ken_burns,
        }


@dataclass
class TTSStageResult:
    status: str
    scenes: list[SceneResult]
    timeline: dict
    report: dict
    issues: list[Issue] = field(default_factory=list)


class TTSStage:
    """
    Synthesizes the whole voiceover and lays out the timeline.

    The backend is borrowed, not owned: the caller creates it (so a loaded
    model can be reused between runs) and the caller closes it.
    """

    def __init__(
        self,
        script: Script,
        paths: Paths,
        backend: TTSBackend,
        *,
        force: bool = False,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        normalize_each_scene: bool = False,
        progress=None,
    ) -> None:
        self.script = script
        self.paths = paths
        self.backend = backend
        self.force = force
        self.max_retries = max(1, max_retries)
        self.sample_rate = sample_rate
        self.channels = channels
        self.normalize_each_scene = normalize_each_scene
        self.progress = progress or (lambda message: None)

        self.tts_cache = paths.cache_dir / "tts"
        self.silence_cache = paths.cache_dir / "silence"
        self.issues = IssueCollector()

        self.engine = PacingEngine(
            script.pacing.auto_pause,
            script.pacing.section_breaks,
            scene_count=len(script.scenes),
        )

        self.retry_count = 0
        self.cache_hits = 0

    # -- entry point ------------------------------------------------------

    def run(self) -> TTSStageResult:
        self.paths.create()
        self.tts_cache.mkdir(parents=True, exist_ok=True)
        self.silence_cache.mkdir(parents=True, exist_ok=True)

        sfx_end_ms = self._sfx_end_ms_by_scene()

        scene_results: list[SceneResult] = []
        cursor = 0.0

        for index, scene in enumerate(self.script.scenes):
            result = self._process_scene(
                scene, index=index, sfx_end_ms=sfx_end_ms.get(scene.id, 0)
            )
            result.start_s = cursor
            cursor = result.end_s
            scene_results.append(result)
            self.progress(
                f"scene {scene.id}/{len(self.script.scenes)} "
                f"{result.narration_s:.1f}s + {result.pause_after_s:.2f}s pause"
            )

        master = self._build_master(scene_results)

        # The master is built by concatenating exactly these parts, so the
        # two durations must agree; a mismatch means the timeline that
        # stage 4 renders from would drift.
        parts_total = sum(
            scene.narration_s + scene.pause_after_s for scene in scene_results
        )
        delta = master["duration_s"] - parts_total
        if abs(delta) > TIMELINE_TOLERANCE_S:
            self.issues.warn(
                "master_duration_mismatch",
                f"the master is {master['duration_s']:.3f}s but the scene "
                f"parts add up to {parts_total:.3f}s (delta {delta:+.3f}s); "
                "scene timings may drift",
            )

        timeline = self._build_timeline(scene_results, master)
        pacing_payload = build_pacing_result(
            [scene.pacing for scene in scene_results]
        ).to_dict(self._pacing_settings())

        report = self._build_report(scene_results, master, delta)

        write_json(self.paths.output_dir / "timeline.json", timeline)
        write_json(self.paths.output_dir / "pacing_report.json", pacing_payload)
        write_json(self.paths.output_dir / "tts_report.json", report)

        return TTSStageResult(
            status=self.issues.status,
            scenes=scene_results,
            timeline=timeline,
            report=report,
            issues=self.issues.issues,
        )

    # -- per scene --------------------------------------------------------

    def _process_scene(
        self, scene: Scene, *, index: int, sfx_end_ms: int
    ) -> SceneResult:
        units = self._units_for(scene)
        results = [
            self._synthesize(scene, position, text)
            for position, text in enumerate(units)
        ]

        naturals = [unit.silence.trailing_ms for unit in results]

        # Step one: pauses inside the scene.  These need no knowledge of
        # the scene's total length, which is what breaks the circular
        # dependency (the length depends on them).
        inter = self.engine.inter_sentence_pauses(
            scene, natural_trailing_ms=naturals, units=units
        )
        narration_ms = int(
            round(sum(unit.duration_s for unit in results) * 1000)
        ) + sum(pause.pause_ms for pause in inter)

        # Step two: the pause that closes the scene, now that the real
        # length is known.
        explicit = self._explicit_pause_ms(scene)
        target, breakdown = self.engine.scene_pause(
            scene,
            index=index,
            natural_ms=naturals[-1] if naturals else 0,
            scene_audio_ms=narration_ms,
            sfx_end_ms=sfx_end_ms,
            overlay_end_ms=self._overlay_end_ms(scene),
            explicit_pause_ms=explicit,
            units=units,
        )

        pacing = self.engine.assemble_scene(
            scene,
            index=index,
            inter_sentence=inter,
            scene_pause_ms=target,
            scene_breakdown=breakdown,
            explicit_pause_ms=explicit,
            units=units,
        )

        audio_file = self._render_scene_audio(scene, results, inter)
        return SceneResult(
            scene_id=scene.id,
            index=index,
            audio_file=audio_file,
            units=results,
            pacing=pacing,
            narration_s=probe_duration(audio_file),
        )

    def _units_for(self, scene: Scene) -> list[str]:
        """Text units to synthesize: sentences, or the scene as one blob."""
        if self.script.tts_config.granularity == "scene":
            stripped = scene.text.strip()
            return [stripped] if stripped else []
        return self.engine.units(scene)

    def _explicit_pause_ms(self, scene: Scene) -> int | None:
        """Writer override.  Per-scene wins over the pacing map."""
        if scene.pause_after_ms is not None:
            return scene.pause_after_ms
        return self.script.pacing.custom_pauses.get(scene.id)

    @staticmethod
    def _overlay_end_ms(scene: Scene) -> int:
        if not scene.text_overlays:
            return 0
        return max(overlay.end_offset_ms for overlay in scene.text_overlays)

    def _render_scene_audio(
        self,
        scene: Scene,
        units: list[UnitResult],
        inter: list,
    ) -> Path:
        """
        Concatenate a scene from its sentence audio plus the pauses between
        them.  The pause that closes the scene is *not* included here: it
        belongs to the gap between scenes.
        """
        parts: list[Path] = []
        for position, unit in enumerate(units):
            parts.append(unit.file)
            if position < len(inter) and inter[position].pause_ms > 0:
                parts.append(
                    silence_file(
                        self.silence_cache,
                        inter[position].pause_ms,
                        sample_rate=self.sample_rate,
                        channels=self.channels,
                    )
                )

        destination = self.paths.scene_audio_path(scene.id)

        if self.normalize_each_scene:
            raw = destination.with_name(f"{destination.stem}_raw.wav")
            concatenate(
                parts,
                raw,
                sample_rate=self.sample_rate,
                channels=self.channels,
                work_dir=self.paths.cache_dir,
            )
            try:
                normalize_loudness(
                    raw,
                    destination,
                    target_lufs=self.script.audio_config.master_volume,
                    sample_rate=self.sample_rate,
                    channels=self.channels,
                )
            finally:
                raw.unlink(missing_ok=True)
            return destination

        concatenate(
            parts,
            destination,
            sample_rate=self.sample_rate,
            channels=self.channels,
            work_dir=self.paths.cache_dir,
        )
        return destination

    # -- synthesis --------------------------------------------------------

    def _synthesize(
        self, scene: Scene, position: int, text: str
    ) -> UnitResult:
        key = self._cache_key(text)
        wav = self.tts_cache / f"{key}.wav"
        meta_path = self.tts_cache / f"{key}.json"

        cached_meta = read_json(meta_path)
        if (
            not self.force
            and wav.exists()
            and wav.stat().st_size > 0
            and self._meta_is_current(cached_meta, text)
        ):
            self.cache_hits += 1
            silence = _silence_from_meta(cached_meta)
            return UnitResult(
                index=position,
                text=text,
                key=key,
                file=wav,
                duration_s=float(cached_meta["duration_s"]),
                silence=silence,
                loudness=_loudness_from_meta(cached_meta),
                attempts=int(cached_meta.get("attempts", 1)),
                cached=True,
            )

        attempts = self._synthesize_with_retry(
            text, wav, scene_id=scene.id, unit_index=position
        )
        silence = measure_silence(wav)
        loudness = measure_loudness(wav)

        if loudness.clipping:
            self.issues.warn(
                "unit_clipped",
                f"true peak {loudness.true_peak_db:+.2f} dBFS; limiting to -1 dB",
                scene_id=scene.id,
                unit_index=position,
            )
            limited = wav.with_name(f"{key}_limited.wav")
            limit_peak(
                wav,
                limited,
                sample_rate=self.sample_rate,
                channels=self.channels,
            )
            limited.replace(wav)
            loudness = measure_loudness(wav)
            silence = measure_silence(wav)

        self._check_quality(
            scene, position, text, silence, loudness, attempts
        )

        write_json(
            meta_path,
            {
                "version": 1,
                "key": key,
                "text": text,
                "voice": self.script.tts_config.voice,
                "speed": self.script.tts_config.speed,
                "granularity": self.script.tts_config.granularity,
                "backend": self.backend.name,
                "attempts": attempts,
                "duration_s": silence.duration_s,
                "silence": {
                    "leading_ms": silence.leading_ms,
                    "trailing_ms": silence.trailing_ms,
                    "internal": [list(pair) for pair in silence.internal],
                },
                "loudness": loudness.to_dict(),
            },
        )

        return UnitResult(
            index=position,
            text=text,
            key=key,
            file=wav,
            duration_s=silence.duration_s,
            silence=silence,
            loudness=loudness,
            attempts=attempts,
            cached=False,
        )

    def _synthesize_with_retry(
        self, text: str, destination: Path, *, scene_id: int, unit_index: int
    ) -> int:
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                self.backend.synthesize(text, destination)

                if (
                    not destination.exists()
                    or destination.stat().st_size == 0
                ):
                    raise SynthesisFailed("the backend wrote an empty file")

                duration = probe_duration(destination)
                if duration < MIN_UNIT_SECONDS:
                    raise SynthesisFailed(
                        f"produced {duration:.3f}s of audio, which is too short "
                        "to be a sentence"
                    )
                return attempt

            except Exception as error:  # noqa: BLE001 - retried below
                last_error = error
                self.retry_count += 1
                if attempt < self.max_retries:
                    self.issues.warn(
                        "synthesis_retry",
                        f"attempt {attempt}/{self.max_retries} failed "
                        f"({error}); retrying",
                        scene_id=scene_id,
                        unit_index=unit_index,
                    )

        raise SynthesisFailed(
            f"Could not synthesize after {self.max_retries} attempts: "
            f"{last_error}"
        ) from last_error

    def _check_quality(
        self,
        scene: Scene,
        position: int,
        text: str,
        silence: SilenceInfo,
        loudness: LoudnessInfo,
        attempts: int,
    ) -> None:
        estimated = _estimate_seconds(
            text, self.script.tts_config.speed, self.backend
        )
        measured = silence.duration_s

        if measured < estimated * SHORT_UNIT_RATIO:
            self.issues.warn(
                "unit_truncated",
                f"measured {measured:.2f}s against an estimate of "
                f"{estimated:.2f}s; the sentence may be cut short",
                scene_id=scene.id,
                unit_index=position,
            )
        elif measured > estimated * LONG_UNIT_RATIO:
            self.issues.warn(
                "unit_too_long",
                f"measured {measured:.2f}s against an estimate of "
                f"{estimated:.2f}s; consider splitting this sentence",
                scene_id=scene.id,
                unit_index=position,
            )

        if silence.leading_ms > MAX_LEADING_SILENCE_MS:
            self.issues.warn(
                "unit_leading_silence",
                f"{silence.leading_ms}ms of silence before the first word "
                "will delay the image change",
                scene_id=scene.id,
                unit_index=position,
            )

        if silence.internal_count > 0:
            total_internal = sum(
                end - start for start, end in silence.internal
            )
            self.issues.warn(
                "unit_internal_silence",
                f"{silence.internal_count} silence(s) totalling "
                f"{total_internal:.2f}s inside one unit; the punctuation "
                "layer cannot govern pauses the model already made",
                scene_id=scene.id,
                unit_index=position,
            )

        if loudness.integrated_lufs < -35:
            self.issues.warn(
                "unit_very_quiet",
                f"integrated loudness {loudness.integrated_lufs:.1f} LUFS is "
                "far below the rest of the narration",
                scene_id=scene.id,
                unit_index=position,
            )

    def _cache_key(self, text: str) -> str:
        """
        Cache identity of one text unit.

        The granularity is part of the key on purpose: the same text reads
        differently when synthesized as part of a longer passage, so a
        change in granularity must not silently reuse stale audio.
        """
        config = self.script.tts_config
        payload = "|".join(
            (
                self.backend.name,
                config.voice,
                f"{config.speed:.4f}",
                config.granularity,
                text.strip(),
            )
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _meta_is_current(self, meta: dict | None, text: str) -> bool:
        if not isinstance(meta, dict):
            return False
        config = self.script.tts_config
        return (
            meta.get("text") == text
            and meta.get("voice") == config.voice
            and abs(float(meta.get("speed", -1)) - config.speed) < 1e-9
            and meta.get("granularity") == config.granularity
            and meta.get("backend") == self.backend.name
            and float(meta.get("duration_s", 0)) > 0
        )

    # -- composition ------------------------------------------------------

    def _build_master(self, scenes: list[SceneResult]) -> dict:
        parts: list[Path] = []
        for position, scene in enumerate(scenes):
            parts.append(scene.audio_file)
            pause_ms = scene.pacing.pause_ms
            # The final pause is kept: it gives the closing scene room to
            # breathe before the fade-out instead of cutting off abruptly.
            if pause_ms > 0:
                parts.append(
                    silence_file(
                        self.silence_cache,
                        pause_ms,
                        sample_rate=self.sample_rate,
                        channels=self.channels,
                    )
                )

        raw = self.paths.audio_dir / "voiceover_raw.wav"
        raw_duration = concatenate(
            parts,
            raw,
            sample_rate=self.sample_rate,
            channels=self.channels,
            work_dir=self.paths.cache_dir,
        )

        target_lufs = self.script.audio_config.master_volume
        master = self.paths.voiceover_path
        before = normalize_loudness(
            raw,
            master,
            target_lufs=target_lufs,
            sample_rate=self.sample_rate,
            channels=self.channels,
        )
        after = measure_loudness(
            master, target_lufs=target_lufs
        )

        return {
            "file": master,
            "raw_file": raw,
            "duration_s": probe_duration(master),
            "raw_duration_s": raw_duration,
            "target_lufs": target_lufs,
            "loudness_before": before,
            "loudness_after": after,
        }

    def _build_timeline(
        self, scenes: list[SceneResult], master: dict
    ) -> dict:
        entries = []
        for scene_result, scene in zip(scenes, self.script.scenes):
            entries.append(
                scene_result.to_timeline(
                    transition={
                        "type": scene.transition_in.type,
                        "duration": scene.transition_in.duration,
                    },
                    ken_burns={
                        "enabled": scene.ken_burns.enabled,
                        "type": scene.ken_burns.type,
                        "start_scale": scene.ken_burns.start_scale,
                        "end_scale": scene.ken_burns.end_scale,
                    },
                )
            )

        metadata = self.script.video_metadata
        return {
            "version": TIMELINE_VERSION,
            "title": metadata.title,
            "resolution": metadata.resolution,
            "fps": metadata.fps,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "voiceover_file": str(master["file"]),
            "voiceover_duration_s": round(master["duration_s"], 4),
            # end_s already accumulates start_s, so the last scene's end is
            # the total; summing every end_s would double-count.
            "total_duration_s": round(scenes[-1].end_s, 4) if scenes else 0.0,
            "scenes": entries,
        }

    def _pacing_settings(self) -> dict:
        config = self.script.pacing.auto_pause
        return {
            "enabled": config.enabled,
            "per_sentence": config.per_sentence,
            "min_pause_ms": config.min_pause_ms,
            "max_pause_ms": config.max_pause_ms,
            "respect_tts_natural_pause": config.respect_tts_natural_pause,
            "section_breaks": list(self.script.pacing.section_breaks),
            "granularity": self.script.tts_config.granularity,
        }

    def _build_report(
        self,
        scenes: list[SceneResult],
        master: dict,
        delta: float,
    ) -> dict:
        durations = [unit.duration_s for scene in scenes for unit in scene.units]
        units_total = len(durations)

        # Note: a scene WAV already contains its own in-scene pauses, so
        # `scene_audio_s` must not be added to `inter_sentence_pauses_s`.
        # The two figures that do add up are `scene_audio_s` +
        # `scene_pauses_s` = `parts_total_s`.
        scene_audio_s = sum(scene.narration_s for scene in scenes)
        scene_pauses_s = sum(scene.pause_after_s for scene in scenes)
        inter_sentence_s = sum(
            scene.pacing.inter_sentence_pause_ms for scene in scenes
        ) / 1000.0
        parts_total = scene_audio_s + scene_pauses_s

        return {
            "stage": "tts",
            "status": self.issues.status,
            "settings": {
                "backend": self.backend.name,
                "voice": self.script.tts_config.voice,
                "speed": self.script.tts_config.speed,
                "granularity": self.script.tts_config.granularity,
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "max_retries": self.max_retries,
                "force": self.force,
                "normalize_each_scene": self.normalize_each_scene,
            },
            "totals": {
                "scenes": len(scenes),
                "units": units_total,
                "units_cached": self.cache_hits,
                "units_synthesized": units_total - self.cache_hits,
                "retries": self.retry_count,
                "scene_audio_s": round(scene_audio_s, 3),
                "inter_sentence_pauses_s": round(inter_sentence_s, 3),
                "scene_pauses_s": round(scene_pauses_s, 3),
                "parts_total_s": round(parts_total, 3),
                "master_duration_s": round(master["duration_s"], 3),
                "master_delta_s": round(delta, 4),
                "master_loudness": master["loudness_after"].to_dict(),
                "master_loudness_before": master["loudness_before"].to_dict(),
            },
            "unit_stats": {
                "duration_min_s": round(min(durations), 4) if durations else 0,
                "duration_max_s": round(max(durations), 4) if durations else 0,
                "duration_avg_s": (
                    round(sum(durations) / len(durations), 4)
                    if durations
                    else 0
                ),
            },
            "scenes": [
                {
                    "id": scene.scene_id,
                    "index": scene.index,
                    "audio_file": str(scene.audio_file),
                    "narration_s": round(scene.narration_s, 4),
                    "pause_after_s": round(scene.pause_after_s, 4),
                    "pacing_source": scene.pacing.source,
                    "units": [unit.to_dict() for unit in scene.units],
                }
                for scene in scenes
            ],
            "warnings": [issue.to_dict() for issue in self.issues.warnings],
            "errors": [issue.to_dict() for issue in self.issues.errors],
        }

    # -- helpers ----------------------------------------------------------

    def _sfx_end_ms_by_scene(self) -> dict[int, int]:
        """
        The furthest point any SFX reaches inside each scene.

        The scene-closing pause must outlast an SFX that is still playing,
        otherwise the sound is cut off by the next scene's image.
        """
        ends: dict[int, int] = {}

        for scene in self.script.scenes:
            furthest = 0
            for sfx in scene.sfx:
                asset = resolve_asset(sfx.file, self.paths.workspace)
                if asset is None:
                    # Stage 1 already reported this; skip rather than crash.
                    continue
                try:
                    duration_ms = int(round(probe_duration(asset) * 1000))
                except (AudioError, RuntimeError) as error:
                    self.issues.warn(
                        "sfx_unreadable",
                        f"could not read {sfx.file}: {error}",
                        scene_id=scene.id,
                    )
                    continue
                furthest = max(furthest, sfx.time_offset_ms + duration_ms)
            if furthest:
                ends[scene.id] = furthest

        return ends


def _estimate_seconds(text: str, speed: float, backend: TTSBackend) -> float:
    """Rough speech length, using the fake backend's own rate when known."""
    rate = getattr(backend, "chars_per_second", DEFAULT_CHARS_PER_SECOND)
    return max(len(text.strip()) / max(rate * speed, 1.0), 0.05)


def _silence_from_meta(meta: dict) -> SilenceInfo:
    payload = meta.get("silence") or {}
    internal = payload.get("internal") or []
    return SilenceInfo(
        duration_s=float(meta.get("duration_s", 0.0)),
        leading_ms=int(payload.get("leading_ms", 0)),
        trailing_ms=int(payload.get("trailing_ms", 0)),
        internal=tuple((float(pair[0]), float(pair[1])) for pair in internal),
    )


def _loudness_from_meta(meta: dict) -> LoudnessInfo:
    payload = meta.get("loudness") or {}
    return LoudnessInfo(
        integrated_lufs=float(payload.get("integrated_lufs", 0.0)),
        true_peak_db=float(payload.get("true_peak_db", 0.0)),
        lra=float(payload.get("lra", 0.0)),
    )
