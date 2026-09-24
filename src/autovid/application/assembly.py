"""
Stage 4 — assembly: one clip per scene, then one video.

Each scene is encoded on its own into `cache/segments/scene_NNN.mp4`, which
is what makes this stage resumable: a clip that exists and whose inputs are
unchanged is reused, so re-running after a crash re-renders at most the
scenes that actually changed.  Splitting also keeps every ffmpeg call small
and short, instead of one 13-minute filtergraph that fails as a whole.

Timing comes from `domain.frames.build_frame_plan`, never from arithmetic
done here.  The plan converts the stage-2 timeline into whole frames,
extends each scene by its incoming transition so the overlap eats the extra
rather than the timeline, and refuses to return a plan whose frames do not
add up.  This stage then renders exactly `segment_frames` frames per scene,
holds ffmpeg to it with `-frames:v`, and measures the result with ffprobe.

Text overlays are pre-rendered PIL layers composited with `overlay`, because
the bundled ffmpeg has no `drawtext` (see `infrastructure/video/text.py`).
Overlay offsets are scene-relative, so each layer's window is clamped to the
clip; one that would fall outside is reported and dropped rather than
encoded as a no-op.

Characters are composited in the same pass but *under* the text.  Their
timing is resolved once, in `domain.characters`, from the narration stage 2
measured -- which is why this stage reads `tts_report.json` and
`pacing_report.json` back off disk as well as the timeline: a cue anchored
to "the third sentence" can only be placed by the report that says when the
third sentence actually starts.

The output here is silent.  Audio is muxed once, at the end, from the single
normalised master -- so there is exactly one place where video and audio
meet and can be checked, instead of 48.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from autovid.application.issues import Issue, IssueCollector
from autovid.domain.characters import (
    CharacterCue,
    CharacterPlan,
    impact_offset_s,
    resolve_character_cues,
    sentence_windows,
)
from autovid.domain.frames import FramePlan, SceneFrames, build_frame_plan
from autovid.domain.script import Script, Scene
from autovid.infrastructure.ffmpeg import (
    ffmpeg_available,
    probe_duration,
    probe_streams,
    run,
)
from autovid.infrastructure.image.fonts import (
    fit_overlay_font_size,
    resolve_font,
)
from autovid.infrastructure.video.filters import (
    ImpactPunch,
    OverlayLayer,
    build_scene_filtergraph,
    inspect_motion,
)
from autovid.infrastructure.video.sprites import (
    CharacterLayer,
    SpritePlanner,
    sprite_transparency,
)
from autovid.infrastructure.video.text import (
    render_text_layer,
    render_typewriter_layers,
)
from autovid.paths import Paths, read_json, resolve_asset, write_json

# Encoder settings for the per-scene clips: the spec's delivery target.
DEFAULT_PRESET = "medium"
DEFAULT_CRF = 18

# Joining with transitions costs one more encode.  Doing it near-losslessly
# keeps the delivery encode from being the second lossy generation of the
# same frames.
CONCAT_PRESET = "veryfast"
CONCAT_CRF = 12

# An overlay visible for less than this cannot be read, so it is a script
# mistake rather than something to render.
MIN_OVERLAY_SECONDS = 0.4

# A typewriter reveal shorter or longer than this reads wrong: too fast to
# see, or so slow the viewer waits for text that is already there.
MIN_TYPEWRITER_SECONDS = 0.35
TYPEWRITER_SPAN_FRACTION = 0.8

# Prefix count for a typewriter reveal.  Beyond this it stops reading as
# typing and just costs inputs and encode time.
TYPEWRITER_STEPS = 12

# Planned versus encoded length, in frames.  One frame is rounding.
FRAME_TOLERANCE = 1

# A character sprite is a composited input, and inputs cost render time.
# Beyond this many layers in one scene the graph is almost certainly a
# mistake rather than an effect.
MAX_CHARACTER_LAYERS_PER_SCENE = 24

# Frame-length of an impact flash, as a share of the impact's own length.
FLASH_DURATION_SHARE = 0.6


CLIP_VERSION = 1


@dataclass(frozen=True)
class OverlaySpan:
    """
    One composited layer and the exact time window it is visible for.

    Spans are the unit the filtergraph consumes, and they are explicit
    rather than derived from the script: a typewriter reveal becomes many
    spans over one overlay, each with its own window, and nothing
    downstream needs to know why.
    """

    path: Path
    start_s: float
    end_s: float
    animation: str
    animation_duration_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass
class OverlayPlan:
    """One scripted overlay and the spans it became."""

    index: int
    text: str
    animation: str
    start_s: float
    end_s: float
    font: str
    font_size: int
    spans: list[OverlaySpan] = field(default_factory=list)
    dropped: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        payload = {
            "index": self.index,
            "text": self.text,
            "animation": self.animation,
            "window_s": [round(self.start_s, 3), round(self.end_s, 3)],
            "font": self.font,
            "font_size": self.font_size,
            "layers": [str(span.path) for span in self.spans],
            "span_count": len(self.spans),
        }
        if self.dropped:
            payload["dropped"] = True
            payload["note"] = self.note
        return payload


@dataclass
class SceneClipResult:
    scene_id: int
    index: int
    clip: Path
    frames: int
    planned_duration_s: float
    measured_duration_s: float
    transition_after: str | None
    motion: dict
    overlays: list[OverlayPlan]
    cached: bool
    rendered: bool
    render_seconds: float
    # Character layers and the punch-in, both of which are inputs the
    # filtergraph consumes rather than properties of the script.
    characters: list[CharacterLayer] = field(default_factory=list)
    cues: list[CharacterCue] = field(default_factory=list)
    impact: ImpactPunch | None = None

    @property
    def drift_s(self) -> float:
        return self.measured_duration_s - self.planned_duration_s

    def to_dict(self) -> dict:
        payload = {
            "id": self.scene_id,
            "index": self.index,
            "clip": str(self.clip),
            "frames": self.frames,
            "planned_duration_s": round(self.planned_duration_s, 4),
            "measured_duration_s": round(self.measured_duration_s, 4),
            "cached": self.cached,
            "rendered": self.rendered,
            "render_seconds": round(self.render_seconds, 2),
            "transition_after": self.transition_after,
            "ken_burns": self.motion,
            "overlays": [overlay.to_dict() for overlay in self.overlays],
        }
        if self.cues or self.characters:
            payload["characters"] = {
                "cues": [cue.to_dict() for cue in self.cues],
                "layers": [layer.to_dict() for layer in self.characters],
            }
        if self.impact is not None:
            payload["impact"] = {
                "offset_s": round(self.impact.offset_s, 4),
                "intensity": self.impact.intensity,
                "shake_px": self.impact.shake_px,
                "duration_s": round(self.impact.duration_s, 4),
                "flash": self.impact.flash,
            }
        return payload


@dataclass
class AssemblyStageResult:
    status: str
    plan: FramePlan | None
    clips: list[SceneClipResult]
    preview: Path | None
    report: dict
    issues: list[Issue] = field(default_factory=list)


class AssemblyStage:
    """
    Renders scene clips and joins them into a silent preview video.

    The timeline is read from disk rather than passed in: this stage must
    render from the narration stage 2 actually measured, and reading it back
    is what keeps `state.json` an honest record of what happened.
    """

    def __init__(
        self,
        script: Script,
        paths: Paths,
        *,
        preset: str = DEFAULT_PRESET,
        crf: int = DEFAULT_CRF,
        force: bool = False,
        dry_run: bool = False,
        skip_characters: bool = False,
        progress=None,
    ) -> None:
        self.script = script
        self.paths = paths
        self.preset = preset
        self.crf = crf
        self.force = force
        self.dry_run = dry_run
        self.skip_characters = skip_characters
        self.progress = progress or (lambda message: None)
        self.issues = IssueCollector()

        self.overlay_cache = paths.cache_dir / "overlays"
        self.sprite_cache = paths.cache_dir / "sprites"
        self.sprite_planner = SpritePlanner(self.sprite_cache)
        self.character_plan: CharacterPlan = CharacterPlan()
        self.fps = script.video_metadata.fps
        self.frame_size = (
            script.video_metadata.width,
            script.video_metadata.height,
        )
        self._scenes_by_id = {scene.id: scene for scene in script.scenes}

    # -- entry point ------------------------------------------------------

    def run(self) -> AssemblyStageResult:
        if not ffmpeg_available():
            self.issues.error(
                "ffmpeg_missing",
                "ffmpeg not found; assembly needs a static binary in "
                "bin/ffmpeg or an ffmpeg on PATH",
            )
            return self._abort()

        timeline = read_json(self.paths.output_dir / "timeline.json")
        if not isinstance(timeline, dict):
            self.issues.error(
                "timeline_missing",
                "output/timeline.json not found; run the tts stage first so "
                "clips are cut to measured narration rather than to guesswork",
            )
            return self._abort()

        self.paths.create()
        self.overlay_cache.mkdir(parents=True, exist_ok=True)

        if not self.skip_characters and any(
            scene.characters for scene in self.script.scenes
        ):
            self._resolve_characters(timeline)

        try:
            plan = build_frame_plan(
                timeline,
                fps=self.fps,
                width=self.frame_size[0],
                height=self.frame_size[1],
            )
        except ValueError as error:
            self.issues.error("frame_plan_failed", str(error))
            return self._abort()

        for warning in plan.warnings:
            self.issues.warn(
                warning.code, warning.message, scene_id=warning.scene_id
            )

        self._cross_check_script(plan)

        clips: list[SceneClipResult] = []
        for planned in plan.scenes:
            scene = self._scenes_by_id.get(planned.scene_id)
            if scene is None:
                self.issues.warn(
                    "scene_not_in_script",
                    "the timeline has a scene that script.json does not; its "
                    "clip cannot be rendered",
                    scene_id=planned.scene_id,
                )
                continue

            result = self._render_scene(scene, planned)
            if result is not None:
                clips.append(result)

        preview: Path | None = None
        if not self.issues.errors and not self.dry_run:
            preview = self._join(clips, plan)

        if not self.dry_run and not self.issues.errors:
            if len(clips) != len(plan.scenes):
                self.issues.error(
                    "clips_incomplete",
                    f"rendered {len(clips)} of {len(plan.scenes)} clips; the "
                    "joined video would be missing scenes",
                )

        report = self._build_report(plan, clips, preview)
        write_json(self.paths.output_dir / "assembly_report.json", report)

        return AssemblyStageResult(
            status=self.issues.status,
            plan=plan,
            clips=clips,
            preview=preview,
            report=report,
            issues=self.issues.issues,
        )

    def _abort(self) -> AssemblyStageResult:
        return AssemblyStageResult(
            status="fail",
            plan=None,
            clips=[],
            preview=None,
            report={"stage": "assembly", "status": "fail", "scenes": []},
            issues=self.issues.issues,
        )

    def _resolve_characters(self, timeline: dict) -> None:
        """
        Turn every character block into a cue with real seconds.

        This is the only place the resolution happens, and the mix stage
        reuses its output, so a landing sound and the landing itself cannot
        disagree about when the landing was.
        """
        self.character_plan = resolve_character_cues(
            self.script,
            timeline=timeline,
            tts_report=read_json(self.paths.output_dir / "tts_report.json"),
            pacing_report=read_json(self.paths.output_dir / "pacing_report.json"),
        )

        for warning in self.character_plan.warnings:
            self.issues.warn(
                warning["code"], warning["message"], scene_id=warning.get("scene_id")
            )

    def _sprite_source(self, cue: CharacterCue) -> Path | None:
        """The character artwork, checked before anything is baked from it."""
        source = resolve_asset(cue.image_file, self.paths.workspace)
        if source is None:
            self.issues.error(
                "character_image_missing",
                f"character {cue.index} needs '{cue.image_file}' and it does "
                "not exist; the character would be missing from the video",
                scene_id=cue.scene_id,
                unit_index=cue.index,
            )
            return None

        try:
            has_alpha, transparent_share = sprite_transparency(source)
        except OSError as error:
            self.issues.error(
                "character_image_unreadable",
                f"could not read character {cue.index} ({cue.image_file}): {error}",
                scene_id=cue.scene_id,
                unit_index=cue.index,
            )
            return None

        if not has_alpha:
            self.issues.error(
                "character_image_opaque",
                f"character {cue.index} ({source.name}) has no alpha channel; "
                "it will composite as a solid rectangle over the scene. "
                "Export the character as a PNG with a transparent background",
                scene_id=cue.scene_id,
                unit_index=cue.index,
            )
        elif transparent_share < 0.02:
            self.issues.warn(
                "character_fully_opaque",
                f"character {cue.index} ({source.name}) has an alpha channel "
                f"but only {transparent_share * 100:.1f}% of it is "
                "transparent, so it will still cover the scene as a block",
                scene_id=cue.scene_id,
                unit_index=cue.index,
            )
        return source

    def _build_characters(
        self, scene: Scene, clip_duration_s: float
    ) -> tuple[list[CharacterLayer], list[CharacterCue], ImpactPunch | None]:
        """Bake every character layer for one scene, in composite order."""
        cues = [
            cue
            for cue in self.character_plan.for_scene(scene.id)
            if not cue.dropped and cue.start_s < clip_duration_s
        ]

        layers: list[CharacterLayer] = []
        for cue in cues:
            source = self._sprite_source(cue)
            if source is None:
                continue

            cue_layers = self.sprite_planner.plan(
                cue,
                source=source,
                frame_size=self.frame_size,
                fps=self.fps,
            )
            for layer in cue_layers:
                if layer.frame.box_w > self.frame_size[0]:
                    self.issues.warn(
                        "character_wider_than_frame",
                        f"character {cue.index} is "
                        f"{layer.frame.content_w}px wide on a "
                        f"{self.frame_size[0]}px frame and will be cropped",
                        scene_id=scene.id,
                        unit_index=cue.index,
                    )
            layers.extend(cue_layers)

        if len(layers) > MAX_CHARACTER_LAYERS_PER_SCENE:
            self.issues.warn(
                "character_layers_dense",
                f"{len(layers)} character layers in one scene; every layer is "
                "an input the encoder has to hold open, so this scene will "
                "render slowly",
                scene_id=scene.id,
            )

        impact = self._impact_for(scene, clip_duration_s)
        return layers, cues, impact

    def _impact_for(
        self, scene: Scene, clip_duration_s: float
    ) -> ImpactPunch | None:
        """The punch-in for one scene, or None when it has none."""
        spec = scene.impact
        if spec is None or not spec.enabled:
            return None

        windows = self._sentence_windows(scene.id)
        offset_s = impact_offset_s(spec, windows=windows)
        if offset_s >= clip_duration_s:
            self.issues.warn(
                "impact_outside_clip",
                f"the punch-in is at {offset_s:.2f}s but the scene runs "
                f"{clip_duration_s:.2f}s; it was dropped",
                scene_id=scene.id,
            )
            return None

        return ImpactPunch(
            offset_s=max(offset_s, 0.0),
            intensity=spec.intensity,
            shake_px=spec.shake_px,
            duration_s=min(
                spec.duration_ms / 1000.0, max(clip_duration_s - offset_s, 0.1)
            ),
            flash=spec.flash,
        )

    def _sentence_windows(self, scene_id: int) -> list[tuple[float, float]]:
        return sentence_windows(
            read_json(self.paths.output_dir / "tts_report.json"),
            read_json(self.paths.output_dir / "pacing_report.json"),
        ).get(scene_id, [])

    def _cross_check_script(self, plan: FramePlan) -> None:
        planned_ids = {scene.scene_id for scene in plan.scenes}
        for scene in self.script.scenes:
            if scene.id not in planned_ids:
                self.issues.warn(
                    "scene_not_in_timeline",
                    "this scene has no timeline entry, so it would not appear "
                    "in the video; re-run the tts stage",
                    scene_id=scene.id,
                )

    # -- one scene --------------------------------------------------------

    def _render_scene(
        self, scene: Scene, planned: SceneFrames
    ) -> SceneClipResult | None:
        image = self._prepared_image(scene)
        if image is None:
            return None

        frames = planned.segment_frames
        duration_s = frames / self.fps

        overlays = self._build_overlays(scene, duration_s)
        spans = [span for plan in overlays for span in plan.spans]
        character_layers, character_cues, impact = (
            ([], [], None)
            if self.skip_characters
            else self._build_characters(scene, duration_s)
        )

        motion = inspect_motion(
            _motion_dict(scene), frames=frames, frame_size=self.frame_size
        )
        if motion["enabled"] and motion.get("too_slow"):
            self.issues.warn(
                "ken_burns_imperceptible",
                f"{motion['type']} moves {motion['travel_px']:.0f}px across "
                f"{frames} frames ({motion['travel_px_per_frame']:.3f}px per "
                "frame), which will read as a frozen image; widen the scale "
                "range or drop the effect",
                scene_id=scene.id,
            )

        clip = self.paths.segments_dir / f"scene_{scene.id:03d}.mp4"
        signature = _scene_signature(
            scene,
            planned,
            image,
            spans,
            motion,
            character_layers,
            impact,
            fps=self.fps,
            frame_size=self.frame_size,
            preset=self.preset,
            crf=self.crf,
        )
        signature_path = clip.with_suffix(".json")

        if self.dry_run:
            self.progress(
                f"scene {scene.id}: would render {frames} frames "
                f"({duration_s:.2f}s)"
            )
            return SceneClipResult(
                scene_id=scene.id,
                index=planned.index,
                clip=clip,
                frames=frames,
                planned_duration_s=duration_s,
                measured_duration_s=duration_s,
                transition_after=planned.transition_after,
                motion=motion,
                overlays=overlays,
                cached=False,
                rendered=False,
                render_seconds=0.0,
                characters=character_layers,
                cues=character_cues,
                impact=impact,
            )

        if (
            not self.force
            and clip.exists()
            and clip.stat().st_size > 0
            and read_json(signature_path) == signature
        ):
            return self._measure_clip(
                scene, planned, clip, duration_s, overlays, motion,
                character_layers, character_cues, impact,
                cached=True, rendered=False, render_seconds=0.0, verify=False,
            )

        graph = build_scene_filtergraph(
            frames=frames,
            frame_size=self.frame_size,
            fps=self.fps,
            motion=_motion_dict(scene),
            overlays=[
                OverlayLayer(
                    path=span.path,
                    start_s=span.start_s,
                    end_s=span.end_s,
                    animation=span.animation,
                    animation_duration_s=span.animation_duration_s,
                )
                for span in spans
            ],
            sprites=character_layers,
            impact=impact,
        )

        # The still is a single frame, which `zoompan` turns into the whole
        # clip.  Every layer -- character or text -- is looped into a real
        # stream instead, with a frame per output frame, because otherwise
        # the fade filters only ever see t=0 and the layer never appears
        # (see filters.py).  A character layer needs this for the same
        # reason: its alpha ramps and its `enable` window are both time.
        inputs: list[str] = ["-i", str(image)]
        for layer in character_layers:
            inputs.extend(
                [
                    "-loop",
                    "1",
                    "-framerate",
                    str(self.fps),
                    "-t",
                    f"{duration_s:.4f}",
                    "-i",
                    str(layer.frame.path),
                ]
            )
        for span in spans:
            inputs.extend(
                [
                    "-loop",
                    "1",
                    "-framerate",
                    str(self.fps),
                    "-t",
                    f"{duration_s:.4f}",
                    "-i",
                    str(span.path),
                ]
            )

        if impact is not None and impact.flash != "none":
            # A punch-in flash is a colour plate that fades out from the
            # beat, not a `fade` filter: a fade-in would paint everything
            # *before* the beat in the flash colour (measured).
            colour = "white" if impact.flash == "white" else "black"
            flash_s = max(impact.duration_s * FLASH_DURATION_SHARE, 0.05)
            inputs.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={colour}:s={self.frame_size[0]}x{self.frame_size[1]}"
                    f":r={self.fps}:d={flash_s:.4f}",
                ]
            )

        started = time.monotonic()
        try:
            run(
                [
                    "-y",
                    *inputs,
                    "-filter_complex",
                    graph,
                    "-map",
                    "[out]",
                    "-frames:v",
                    str(frames),
                    "-c:v",
                    "libx264",
                    "-preset",
                    self.preset,
                    "-crf",
                    str(self.crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    str(self.fps),
                    # A keyframe every two seconds keeps the later join and
                    # any seeking predictable.
                    "-g",
                    str(self.fps * 2),
                    "-an",
                    "-movflags",
                    "+faststart",
                    str(clip),
                ]
            )
        except RuntimeError as error:
            self.issues.error(
                "scene_render_failed",
                f"ffmpeg could not render this scene: {error}",
                scene_id=scene.id,
            )
            return None

        render_seconds = time.monotonic() - started
        write_json(signature_path, signature)

        return self._measure_clip(
            scene, planned, clip, duration_s, overlays, motion,
            character_layers, character_cues, impact,
            cached=False, rendered=True,
            render_seconds=render_seconds, verify=True,
        )

    def _measure_clip(
        self,
        scene: Scene,
        planned: SceneFrames,
        clip: Path,
        duration_s: float,
        overlays: list[OverlayPlan],
        motion: dict,
        characters: list[CharacterLayer],
        cues: list[CharacterCue],
        impact: ImpactPunch | None,
        *,
        cached: bool,
        rendered: bool,
        render_seconds: float,
        verify: bool,
    ) -> SceneClipResult | None:
        try:
            measured = probe_duration(clip)
        except (RuntimeError, FileNotFoundError) as error:
            self.issues.error(
                "clip_unreadable",
                f"could not probe the rendered clip: {error}",
                scene_id=scene.id,
            )
            return None

        drift_frames = round((measured - duration_s) * self.fps)
        if abs(drift_frames) > FRAME_TOLERANCE:
            self.issues.warn(
                "clip_duration_drift",
                f"the clip is {measured:.3f}s but the plan asked for "
                f"{duration_s:.3f}s ({drift_frames:+d} frames); this scene "
                "would drift against the voiceover",
                scene_id=scene.id,
            )

        if verify:
            self._check_clip_streams(scene, clip)

        return SceneClipResult(
            scene_id=scene.id,
            index=planned.index,
            clip=clip,
            frames=planned.segment_frames,
            planned_duration_s=duration_s,
            measured_duration_s=measured,
            transition_after=planned.transition_after,
            motion=motion,
            overlays=overlays,
            characters=characters,
            cues=cues,
            impact=impact,
            cached=cached,
            rendered=rendered,
            render_seconds=render_seconds,
        )

    def _check_clip_streams(self, scene: Scene, clip: Path) -> None:
        """Confirm the encoder wrote what the plan assumed it would."""
        try:
            streams = probe_streams(clip)
        except RuntimeError as error:
            self.issues.warn(
                "clip_probe_failed",
                f"could not inspect the clip: {error}",
                scene_id=scene.id,
            )
            return

        videos = [
            stream
            for stream in streams.get("streams", [])
            if stream.get("codec_type") == "video"
        ]
        if not videos:
            self.issues.error(
                "clip_has_no_video",
                "the rendered clip has no video stream",
                scene_id=scene.id,
            )
            return

        stream = videos[0]
        size = (stream.get("width"), stream.get("height"))
        if size != self.frame_size:
            self.issues.error(
                "clip_wrong_size",
                f"the clip is {size[0]}x{size[1]} but the video is "
                f"{self.frame_size[0]}x{self.frame_size[1]}",
                scene_id=scene.id,
            )

        pixel_format = stream.get("pix_fmt")
        if pixel_format not in (None, "yuv420p"):
            self.issues.warn(
                "clip_pixel_format",
                f"the clip is {pixel_format} rather than yuv420p, which can "
                "break the join",
                scene_id=scene.id,
            )

    def _prepared_image(self, scene: Scene) -> Path | None:
        """
        The stage-3 frame, or the original artwork if stage 3 has not run.

        Falling back to the source keeps this stage usable on its own; the
        frame is scaled to the output size by the filtergraph either way.
        """
        prepared = self.paths.prepared_images_dir / f"scene_{scene.id:03d}.png"
        if prepared.exists():
            return prepared

        if scene.image_file:
            resolved = resolve_asset(scene.image_file, self.paths.workspace)
            if resolved is not None:
                self.issues.warn(
                    "prepared_image_missing",
                    "no prepared frame; using the source image, so Ken Burns "
                    "headroom and stage 3's checks are missing (run the "
                    "images stage for a cleaner result)",
                    scene_id=scene.id,
                )
                return resolved

        self.issues.error(
            "image_missing",
            f"no prepared frame and no usable image_file "
            f"({scene.image_file!r}); run the images stage after adding the "
            "artwork",
            scene_id=scene.id,
        )
        return None

    # -- overlays ---------------------------------------------------------

    def _build_overlays(
        self, scene: Scene, clip_duration_s: float
    ) -> list[OverlayPlan]:
        """Turn every scripted overlay into the spans that will be composited."""
        plans: list[OverlayPlan] = []

        for index, overlay in enumerate(scene.text_overlays):
            plan = OverlayPlan(
                index=index,
                text=overlay.text,
                animation=overlay.animation,
                start_s=overlay.start_offset_ms / 1000.0,
                end_s=overlay.end_offset_ms / 1000.0,
                font=overlay.font,
                font_size=overlay.font_size,
            )

            font = resolve_font(overlay.font, self.paths.workspace)
            if font.path is None:
                plan.dropped = True
                plan.note = "no usable font"
                self.issues.error(
                    "overlay_font_unavailable",
                    f"overlay {index} needs '{overlay.font}' and no fallback "
                    "font exists; the text would be missing from the video",
                    scene_id=scene.id,
                    unit_index=index,
                )
                plans.append(plan)
                continue

            if font.used_fallback:
                self.issues.warn(
                    "overlay_font_fallback",
                    f"overlay {index} requested '{overlay.font}'; rendering "
                    f"with {font.path.name} instead",
                    scene_id=scene.id,
                    unit_index=index,
                )

            # A single line of text wider than its placement area runs off
            # the frame edge mid-word.  The images stage reports the size
            # that would fit; shrinking here is what makes that report true
            # on screen instead of only on paper.
            fitted = fit_overlay_font_size(
                overlay.text,
                font.path,
                requested_size=overlay.font_size,
                position=overlay.position,
                frame_size=self.frame_size,
                stroke_width=overlay.stroke_width,
            )
            if fitted < overlay.font_size:
                self.issues.warn(
                    "overlay_font_shrunk",
                    f"overlay {index} '{overlay.text}' does not fit at "
                    f"{overlay.font_size}px in the {overlay.position} area; "
                    f"rendered at {fitted}px instead",
                    scene_id=scene.id,
                    unit_index=index,
                )
                plan.font_size = fitted

            # An overlay cannot outlive its scene, and a negative offset is
            # the same as starting at zero.
            plan.start_s = max(0.0, plan.start_s)
            plan.end_s = min(plan.end_s, clip_duration_s)
            visible_s = plan.end_s - plan.start_s

            if visible_s < MIN_OVERLAY_SECONDS:
                plan.dropped = True
                plan.note = (
                    f"window is {visible_s:.2f}s inside a "
                    f"{clip_duration_s:.2f}s scene"
                )
                self.issues.warn(
                    "overlay_outside_clip",
                    f"overlay {index} '{overlay.text}' would be visible for "
                    f"{max(visible_s, 0):.2f}s inside a "
                    f"{clip_duration_s:.2f}s scene; dropped",
                    scene_id=scene.id,
                    unit_index=index,
                )
                plans.append(plan)
                continue

            key = _overlay_key(
                scene, index, font.path, self.frame_size, plan.font_size
            )
            requested_window = min(
                overlay.animation_duration_ms / 1000.0, visible_s / 2
            )

            if overlay.animation == "typewriter":
                plan.spans = self._typewriter_spans(
                    overlay=overlay,
                    plan=plan,
                    font_path=font.path,
                    key=key,
                    visible_s=visible_s,
                )
                plans.append(plan)
                continue

            destination = self.overlay_cache / f"{key}.png"
            layer = render_text_layer(
                text=overlay.text,
                font_path=font.path,
                font_size=plan.font_size,
                colour=overlay.color,
                stroke_colour=overlay.stroke_color,
                stroke_width=overlay.stroke_width,
                position=overlay.position,
                frame_size=self.frame_size,
                destination=destination,
            )
            plan.spans = [
                OverlaySpan(
                    path=layer.path,
                    start_s=plan.start_s,
                    end_s=plan.end_s,
                    animation=overlay.animation,
                    animation_duration_s=requested_window,
                )
            ]
            plans.append(plan)

        return plans

    def _typewriter_spans(
        self,
        *,
        overlay,
        plan: OverlayPlan,
        font_path: Path,
        key: str,
        visible_s: float,
    ) -> list[OverlaySpan]:
        """
        A typewriter reveal as a stack of held prefixes.

        The reveal occupies its own window at the start of the overlay, then
        the finished line holds for the remainder.  Without that split, an
        overlay whose animation duration is 300ms but which stays on screen
        for three seconds would keep typing for three seconds.
        """
        reveal_s = min(
            max(overlay.animation_duration_ms / 1000.0, MIN_TYPEWRITER_SECONDS),
            visible_s * TYPEWRITER_SPAN_FRACTION,
        )

        layers = render_typewriter_layers(
            text=overlay.text,
            font_path=font_path,
            font_size=plan.font_size,
            colour=overlay.color,
            stroke_colour=overlay.stroke_color,
            stroke_width=overlay.stroke_width,
            position=overlay.position,
            frame_size=self.frame_size,
            steps=min(TYPEWRITER_STEPS, max(2, len(overlay.text))),
            destination_dir=self.overlay_cache,
            stem=key,
        )

        reveal_end = plan.start_s + reveal_s
        step_s = reveal_s / len(layers)

        spans: list[OverlaySpan] = []
        for position, layer in enumerate(layers):
            is_last = position == len(layers) - 1
            start = plan.start_s + step_s * position
            # The final prefix is the whole line, so it holds to the end.
            end = plan.end_s if is_last else plan.start_s + step_s * (position + 1)
            spans.append(
                OverlaySpan(
                    path=layer.path,
                    start_s=start,
                    end_s=end,
                    # Prefixes are replaced instantly, so the only visible
                    # change is the added characters; the finished line
                    # fades out like any other overlay.
                    animation="cut_in" if is_last else "none",
                    animation_duration_s=min(
                        max(overlay.animation_duration_ms / 1000.0, 0.05),
                        max(plan.end_s - reveal_end, 0.05) / 2,
                    ),
                )
            )
        return spans

    # -- joining ----------------------------------------------------------

    def _join(
        self, clips: list[SceneClipResult], plan: FramePlan
    ) -> Path | None:
        """
        Join the clips, applying transitions where the plan asked for them.

        With no transitions this is a stream copy: instant and lossless.
        With transitions the boundaries must be re-encoded, because `xfade`
        reads two streams at once and cannot be done by copying.
        """
        if not clips:
            self.issues.error("nothing_to_join", "no clips were rendered")
            return None

        preview = self.paths.output_dir / "preview_video.mp4"
        transitions = plan.transition_offsets()

        if not transitions:
            self._concat_copy([clip.clip for clip in clips], preview)
        else:
            self._concat_xfade(clips, transitions, preview, plan)

        self._verify_join(preview, plan)
        return preview

    def _concat_copy(self, files: list[Path], destination: Path) -> None:
        """Join with the concat demuxer, without touching a single frame."""
        listing = self.paths.cache_dir / "concat_list.txt"
        listing.write_text(
            "".join(f"file '{_escape_concat_path(path)}'\n" for path in files),
            encoding="utf-8",
        )
        try:
            run(
                [
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(listing),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(destination),
                ]
            )
        except RuntimeError as error:
            destination.unlink(missing_ok=True)
            self.issues.error(
                "join_failed", f"could not join the scene clips: {error}"
            )
        finally:
            listing.unlink(missing_ok=True)

    def _concat_xfade(
        self,
        clips: list[SceneClipResult],
        transitions: list[dict],
        destination: Path,
        plan: FramePlan,
    ) -> None:
        """
        Apply every transition in a single filtergraph.

        Offsets are the plan's, in absolute timeline seconds, which is also
        what `xfade` wants when the chain runs left to right: the offset is
        measured from the start of the accumulated stream.  Because each
        clip was rendered with its overlap already included, the crossfade
        consumes the extra rather than the timeline, so total length and
        per-scene start times both survive.

        Boundaries the script left as cuts are joined with `concat` inside
        the same graph: mixing the two keeps one encode and one pass, where
        the older batched approach re-encoded the whole video per batch and
        lost the transitions at every batch seam.
        """
        by_scene = {entry["scene_id"]: entry for entry in transitions}

        statements: list[str] = []

        # Every join filter needs both sides to agree on timebase *and* on a
        # defined frame rate, and the producers here agree on neither: a
        # decoded clip arrives on the stream's own timebase, while `concat`
        # outputs 1/1000000 and reports its frame rate as the useless "1/0".
        # Mixing cuts and transitions in one graph then fails with "First
        # input link main timebase do not match" followed by "inputs needs to
        # be a constant frame rate" -- graphs that look correct on paper and
        # that ffmpeg rejects.  Both sides of every step are normalised, so
        # the chain holds whatever order the script mixes cuts and fades in.
        for index in range(len(clips)):
            statements.append(_normalise(f"{index}:v", f"in{index}", plan.fps))

        previous = "in0"
        for index in range(1, len(clips)):
            left = f"a{index}"
            statements.append(_normalise(previous, left, plan.fps))

            entry = by_scene.get(clips[index - 1].scene_id)

            if entry is None:
                label = f"c{index}"
                statements.append(
                    f"[{left}][in{index}]concat=n=2:v=1:a=0[{label}]"
                )
                previous = label
                continue

            # Quantise the offset to whole frames so the transition starts
            # on a frame boundary rather than between two.
            offset_frames = round(entry["offset_s"] * plan.fps)
            label = f"x{index}"
            statements.append(
                f"[{left}][in{index}]xfade=transition={entry['type']}"
                f":duration={entry['duration_s']:.4f}"
                f":offset={offset_frames / plan.fps:.4f}[{label}]"
            )
            previous = label

        statements.append(f"[{previous}]format=yuv420p[out]")


        inputs: list[str] = []
        for clip in clips:
            inputs.extend(["-i", str(clip.clip)])

        try:
            run(
                [
                    "-y",
                    *inputs,
                    "-filter_complex",
                    ";\n".join(statements),
                    "-map",
                    "[out]",
                    "-c:v",
                    "libx264",
                    "-preset",
                    CONCAT_PRESET,
                    "-crf",
                    str(CONCAT_CRF),
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    str(plan.fps),
                    "-an",
                    "-movflags",
                    "+faststart",
                    str(destination),
                ],
                timeout=None,
            )
        except RuntimeError as error:
            # A failed encode leaves a headerless file behind, which would
            # otherwise look like a preview to every later stage.
            destination.unlink(missing_ok=True)
            self.issues.error(
                "transition_join_failed",
                f"the transition pass failed: {error}",
            )

    def _verify_join(self, preview: Path, plan: FramePlan) -> None:
        """
        Check the joined video against the plan.

        The last cheap chance to catch a join that dropped or duplicated
        frames, and far cheaper than discovering it after the audio mix.
        """
        if not preview.exists() or preview.stat().st_size == 0:
            self.issues.error(
                "preview_missing", "the joined video was not written"
            )
            return

        try:
            measured = probe_duration(preview)
        except (RuntimeError, FileNotFoundError) as error:
            self.issues.error(
                "preview_unreadable", f"could not probe the join: {error}"
            )
            return

        drift = measured - plan.total_duration_s
        tolerance_s = FRAME_TOLERANCE / plan.fps
        if abs(drift) > tolerance_s:
            self.issues.error(
                "preview_duration_mismatch",
                f"the joined video is {measured:.3f}s but the timeline is "
                f"{plan.total_duration_s:.3f}s ({drift:+.3f}s); audio mixed "
                "against it would not line up",
            )
        else:
            self.progress(
                f"joined {measured:.3f}s (delta {drift * 1000:+.0f}ms)"
            )

        try:
            streams = probe_streams(preview)
        except RuntimeError:
            return

        for stream in streams.get("streams", []):
            if stream.get("codec_type") != "video":
                continue
            size = (stream.get("width"), stream.get("height"))
            if size != self.frame_size:
                self.issues.error(
                    "preview_wrong_size",
                    f"the joined video is {size[0]}x{size[1]} but the script "
                    f"asks for {self.frame_size[0]}x{self.frame_size[1]}",
                )

    # -- report -----------------------------------------------------------

    def _build_report(
        self,
        plan: FramePlan,
        clips: list[SceneClipResult],
        preview: Path | None,
    ) -> dict:
        transitions = plan.transition_offsets()
        drift = [abs(clip.drift_s) for clip in clips]

        motion_types: dict[str, int] = {}
        for clip in clips:
            kind = (
                clip.motion.get("type", "none")
                if clip.motion.get("enabled")
                else "none"
            )
            motion_types[kind] = motion_types.get(kind, 0) + 1

        # A failed join can leave a file behind that has no moov atom, so
        # the probe is part of the report rather than an assumption.  Letting
        # it raise here would replace a precise join error with a traceback.
        preview_duration_s: float | None = None
        preview_info = None
        if preview is not None and preview.exists():
            try:
                preview_duration_s = round(probe_duration(preview), 4)
            except (RuntimeError, FileNotFoundError):
                preview_duration_s = None
        if preview_duration_s is not None and preview is not None:
            preview_info = {
                "file": str(preview),
                "duration_s": preview_duration_s,
                "has_audio": False,
                "size_mb": round(preview.stat().st_size / (1024 * 1024), 1),
            }

        return {
            "stage": "assembly",
            "status": self.issues.status,
            "settings": {
                "preset": self.preset,
                "crf": self.crf,
                "fps": plan.fps,
                "frame": f"{plan.width}x{plan.height}",
                "concat_preset": CONCAT_PRESET,
                "concat_crf": CONCAT_CRF,
                "dry_run": self.dry_run,
                "force": self.force,
                "characters": not self.skip_characters,
            },
            "plan": plan.to_dict(),
            "totals": {
                "scenes": len(clips),
                "clips_cached": sum(1 for clip in clips if clip.cached),
                "clips_rendered": sum(1 for clip in clips if clip.rendered),
                # The video's length, and separately how much of it had to be
                # encoded on this run: a fully cached re-run produces the same
                # video while encoding nothing.
                "frames_total": sum(clip.frames for clip in clips),
                "frames_rendered": sum(
                    clip.frames for clip in clips if clip.rendered
                ),
                "transitions": len(transitions),
                "transition_types": sorted(
                    {entry["type"] for entry in transitions}
                ),
                "overlays": sum(len(clip.overlays) for clip in clips),
                "overlay_spans": sum(
                    len(plan_.spans) for clip in clips for plan_ in clip.overlays
                ),
                "dropped_overlays": sum(
                    1
                    for clip in clips
                    for plan_ in clip.overlays
                    if plan_.dropped
                ),
                "motion_types": motion_types,
                "characters": sum(len(clip.cues) for clip in clips),
                "character_layers": sum(
                    len(clip.characters) for clip in clips
                ),
                "character_enters": _tally(
                    cue.enter.type
                    for clip in clips
                    for cue in clip.cues
                    if cue.enter.type != "none"
                ),
                "character_exits": _tally(
                    cue.exit.type
                    for clip in clips
                    for cue in clip.cues
                    if cue.exit.type != "none"
                ),
                "character_idles": _tally(
                    cue.idle.type
                    for clip in clips
                    for cue in clip.cues
                    if cue.idle.type != "none"
                ),
                "character_sfx": sum(
                    1
                    for clip in clips
                    for cue in clip.cues
                    if cue.sfx
                ),
                "punch_ins": sum(1 for clip in clips if clip.impact),
                "render_seconds": round(
                    sum(clip.render_seconds for clip in clips), 2
                ),
                "max_clip_drift_s": round(max(drift), 4) if drift else 0.0,
                "warnings": len(self.issues.warnings),
                "errors": len(self.issues.errors),
            },
            "preview": preview_info,
            # Planned against actual: the number that says whether the join
            # kept the timeline, in the report rather than only in a log.
            "join": {
                "planned_duration_s": round(plan.total_duration_s, 4),
                "measured_duration_s": preview_duration_s,
                "delta_s": (
                    round(preview_duration_s - plan.total_duration_s, 4)
                    if preview_duration_s is not None
                    else None
                ),
            },
            "scenes": [clip.to_dict() for clip in clips],
            "warnings": [issue.to_dict() for issue in self.issues.warnings],
            "errors": [issue.to_dict() for issue in self.issues.errors],
        }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _tally(values) -> dict[str, int]:
    """Count occurrences, for the report's "what effects are in this video"."""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _motion_dict(scene: Scene) -> dict:
    motion = scene.ken_burns
    return {
        "enabled": motion.enabled,
        "type": motion.type,
        "start_scale": motion.start_scale,
        "end_scale": motion.end_scale,
    }


def _overlay_key(
    scene: Scene,
    index: int,
    font_path: Path,
    frame_size: tuple[int, int],
    font_size: int,
) -> str:
    """Cache identity of a rendered text layer, at the size it was drawn."""
    overlay = scene.text_overlays[index]
    payload = json.dumps(
        {
            "version": CLIP_VERSION,
            "text": overlay.text,
            "font": str(font_path),
            "font_size": font_size,
            "colour": overlay.color,
            "stroke_colour": overlay.stroke_color,
            "stroke_width": overlay.stroke_width,
            "position": overlay.position,
            "frame": list(frame_size),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _scene_signature(
    scene: Scene,
    planned: SceneFrames,
    image: Path,
    spans: list[OverlaySpan],
    motion: dict,
    characters: list[CharacterLayer],
    impact: ImpactPunch | None,
    *,
    fps: int,
    frame_size: tuple[int, int],
    preset: str,
    crf: int,
) -> dict:
    """
    Everything that changes a rendered clip, and nothing else.

    The artwork is identified by size and mtime rather than by hash: that is
    enough to notice a regenerated frame without reading a multi-megabyte
    PNG on every run.
    """
    stat = image.stat()
    return {
        "version": CLIP_VERSION,
        "frames": planned.segment_frames,
        "fps": fps,
        "size": list(frame_size),
        "preset": preset,
        "crf": crf,
        "image": {
            "path": str(image),
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        },
        "ken_burns": motion,
        "overlays": [
            {
                "path": str(span.path),
                "start_s": round(span.start_s, 4),
                "end_s": round(span.end_s, 4),
                "animation": span.animation,
                "animation_duration_s": round(span.animation_duration_s, 4),
            }
            for span in spans
        ],
        # Every layer is listed with the expressions it produced, because a
        # changed motion expression changes the rendered frames just as much
        # as a changed image does, and the cache has to notice either.
        "characters": [layer.to_dict() for layer in characters],
        "impact": (
            {
                "offset_s": round(impact.offset_s, 4),
                "intensity": impact.intensity,
                "shake_px": impact.shake_px,
                "duration_s": round(impact.duration_s, 4),
                "flash": impact.flash,
            }
            if impact is not None
            else None
        ),
    }


def _normalise(source: str, label: str, fps: int) -> str:
    """
    Make a stream safe to feed into `concat` or `xfade`.

    Both filters need their two inputs to agree on a declared frame rate
    *and* on a timebase, and the producers here agree on neither: a decoded
    clip arrives on its own stream timebase, while `concat` outputs
    1/1000000 and reports its frame rate as the unusable "1/0".  So every
    stream entering a join, on either side, is normalised.

    The order is the whole point, and it is not the obvious one.  `fps`
    declares the frame rate and the output timebase, so it must come
    **last**: putting `setpts` after it silently resets the frame rate
    back to 1/0, and the graph then fails with "The inputs needs to be a
    constant frame rate" from inside `xfade`.  `setpts` goes first, where
    it anchors the stream at zero -- which is also what makes an xfade
    `offset` mean "seconds into this stream".

    Verified against ffmpeg 7.0.2 for cuts, transitions and every mixture
    of the two (see the stage 4 tests).
    """
    return f"[{source}]setpts=PTS-STARTPTS,fps={fps}[{label}]"


def _escape_concat_path(path: Path) -> str:
    """
    Escape a path for ffmpeg's concat demuxer.

    The demuxer treats single quotes as delimiters, so a quote in a path
    has to be closed, escaped and reopened.  Rare, but a workspace under a
    directory with an apostrophe in the name would otherwise fail with a
    confusing error.
    """
    return str(path.resolve()).replace("'", r"'\''")
