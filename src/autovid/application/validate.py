"""
Stage 1 — validate input.

Nothing is rendered until every precondition passes.  The checks fall into
two families:

* **errors** — the pipeline cannot possibly produce a correct video
  (missing image, unknown sfx file, out-of-range section break, ffmpeg
  absent, not enough disk).
* **warnings** — the video will render but probably disappoint (no visible
  Ken Burns motion, overlay too short to read, SFX too dense, estimated
  runtime far from the 10-15 minute target).

`--strict` promotes warnings to errors for a release build.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from autovid.application.issues import Issue
from autovid.domain.sentences import count_sentences
from autovid.domain.script import (
    MAX_SFX_VOLUME,
    Script,
    Scene,
)
from autovid.infrastructure import ffmpeg as ffmpeg_tools
from autovid.infrastructure.video.sprites import sprite_transparency
from autovid.paths import Paths, read_json, resolve_asset

# --------------------------------------------------------------------------
# Estimation heuristics
#
# These exist so stage 1 can estimate runtime and disk usage before a
# single byte of audio is generated.  They are deliberately crude and
# documented rather than clever: real durations come from stage 2.
# --------------------------------------------------------------------------

# Vietnamese TTS at speed 1.0 lands around 13-15 characters per second.
CHARS_PER_SECOND = 14.0

# x264 CRF 18 at 1920x1080@30 measures roughly 5.5 Mbps for this kind of
# slow-moving still-image content.
BASE_VIDEO_BITRATE_MBPS = 5.5
BASE_PIXELS = 1920 * 1080
BASE_FPS = 30

AUDIO_BITRATE_KBPS = 320

# Final video plus per-scene temp segments, image cache and audio stems.
DISK_HEADROOM_FACTOR = 2.5

# Default runtime envelope for the long explainer format, in minutes.  A
# script can override it with `video_metadata.target_minutes` (a Short does,
# so it is not warned as "too short").
DEFAULT_TARGET_MINUTES = (8.0, 20.0)

# Design rules from the spec.
MIN_OVERLAY_READABLE_MS = 2000
MAX_CONCURRENT_OVERLAYS = 2
MAX_TRANSITION_SECONDS = 0.5
MIN_OVERLAY_FONT_SIZE_1080P = 40
MIN_SFX_GAP_SECONDS = 5.0
HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# A pan moves the crop window inside an enlarged image, so the image must
# be scaled up by at least this much for the movement to be visible.
MIN_PAN_HEADROOM = 0.02

ALLOWED_FPS = frozenset({24, 25, 30, 50, 60})
# Delivery frames are at least 720p on the shorter side and carry at least
# 720p worth of pixels.  Stated this way, 1920x1080 (landscape) and
# 1080x1920 (vertical Shorts) both pass while a genuinely small frame does
# not; the old width/height pair rejected every vertical frame.
MIN_SHORT_SIDE = 720
MIN_PIXELS = 1280 * 720

# Characters: two on screen is a conversation, three is a crowd.
MAX_CHARACTERS_PER_SCENE = 2
MAX_CHARACTER_HEIGHT_FRACTION = 0.85
MIN_CHARACTER_HEIGHT_FRACTION = 0.12
# Below this a cue is a flicker rather than an appearance.
MIN_CHARACTER_VISIBLE_MS = 600


@dataclass
class ValidationReport:
    status: str = "pass"
    issues: list[Issue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)

    def error(self, code: str, message: str, scene_id: int | None = None) -> None:
        self.add(Issue(code, message, "error", scene_id))

    def warn(self, code: str, message: str, scene_id: int | None = None) -> None:
        self.add(Issue(code, message, "warning", scene_id))

    def to_dict(self, *, strict: bool = False) -> dict:
        return {
            "stage": "validate",
            "status": self.status,
            "strict": strict,
            "stats": self.stats,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def _engine_voice_names() -> set[str]:
    """
    Voice names from the installed VieNeu engine, without loading the model.

    The SDK ships its presets as `assets/voices_v3_turbo.json`, so the names
    are available before — and independently of — the 282MB of weights.  An
    empty set means the engine (or that file) is not present.
    """
    try:
        import json
        from pathlib import Path

        import vieneu
    except Exception:
        return set()

    try:
        asset_dir = Path(vieneu.__file__).resolve().parent / "assets"
    except (AttributeError, TypeError):
        return set()

    names: set[str] = set()
    for filename in ("voices_v3_turbo.json", "voices_v3_nano.json"):
        candidate = asset_dir / filename
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for name, entry in (payload.get("presets") or {}).items():
            names.add(name)
            # Aliases are accepted by `infer` too (renamed presets keep
            # working), so they belong in the catalog the validator checks.
            for alias in (entry or {}).get("aliases") or []:
                names.add(alias)
    return names


def estimate_scene_seconds(scene: Scene, speed: float) -> float:
    """Rough narration length for one scene, in seconds."""
    effective_rate = max(CHARS_PER_SECOND * speed, 1.0)
    return len(scene.text) / effective_rate


def estimate_total_seconds(script: Script) -> float:
    """
    Rough total narration length, including the pauses we already know
    about.  Auto-computed pauses are unknown at this point, so they are
    approximated by a flat per-sentence allowance.
    """
    seconds = 0.0
    for scene in script.scenes:
        seconds += estimate_scene_seconds(scene, script.tts_config.speed)
        explicit = scene.pause_after_ms
        if explicit is None:
            explicit = script.pacing.custom_pauses.get(scene.id)
        if explicit is not None:
            seconds += explicit / 1000.0
        else:
            # Reserve a rough per-sentence pause so the estimate is not
            # wildly optimistic (real values come from the pacing engine).
            auto_ms = 700 if script.pacing.auto_pause.per_sentence else 900
            sentences = max(count_sentences(scene.text) - 1, 0)
            seconds += sentences * auto_ms / 1000.0
    return seconds


def estimate_disk_mb(total_seconds: float, script: Script) -> float:
    metadata = script.video_metadata
    scale = (
        metadata.width * metadata.height * metadata.fps
    ) / (BASE_PIXELS * BASE_FPS)
    video_mbps = BASE_VIDEO_BITRATE_MBPS * scale
    video_mb = total_seconds * video_mbps / 8.0
    audio_mb = total_seconds * AUDIO_BITRATE_KBPS / 8.0 / 1024.0
    return (video_mb + audio_mb) * DISK_HEADROOM_FACTOR


class ScriptValidator:
    """Runs every stage-1 check and reports all problems at once."""

    def __init__(self, script: Script, paths: Paths) -> None:
        self.script = script
        self.paths = paths
        self.report = ValidationReport()

    # -- entry point ------------------------------------------------------

    def run(
        self, *, strict: bool = False, skip_engine_check: bool = False
    ) -> ValidationReport:
        self._check_video_metadata()
        self._check_tts_config()
        self._check_audio_config()
        self._check_pacing()
        self._check_scene_sequence()
        self._check_scenes()
        self._check_sfx_density()
        self._check_toolchain(skip_engine_check=skip_engine_check)
        self._check_disk()
        self._collect_stats()

        if strict and not self.report.errors and self.report.warnings:
            self.report.status = "fail"
            self.report.error(
                "strict_warnings",
                f"--strict is on and {len(self.report.warnings)} warning(s) "
                "must be resolved first",
            )
        elif self.report.errors:
            self.report.status = "fail"

        return self.report

    # -- checks -----------------------------------------------------------

    def _check_video_metadata(self) -> None:
        metadata = self.script.video_metadata

        if metadata.width % 2 or metadata.height % 2:
            self.report.error(
                "resolution_odd_dimensions",
                f"resolution {metadata.resolution} has odd dimensions; H.264 "
                "requires even width and height",
            )

        short_side = min(metadata.width, metadata.height)
        if short_side < MIN_SHORT_SIDE or (
            metadata.width * metadata.height < MIN_PIXELS
        ):
            self.report.warn(
                "resolution_low",
                f"resolution {metadata.resolution} is below {MIN_SHORT_SIDE}p "
                "on its shorter side (or too few pixels); text overlays will "
                "be hard to read",
            )

        if metadata.fps not in ALLOWED_FPS:
            self.report.warn(
                "fps_unusual",
                f"fps {metadata.fps} is unusual for YouTube delivery "
                f"(expected one of {sorted(ALLOWED_FPS)})",
            )

        if not metadata.title.strip():
            self.report.warn("title_empty", "video_metadata.title is empty")

    def _check_tts_config(self) -> None:
        config = self.script.tts_config

        if not config.voice.strip():
            self.report.error(
                "tts_voice_missing",
                "tts_config.voice is empty; every scene needs a voice",
            )
            return

        known = self._known_voices()
        if known is None:
            self.report.warn(
                "tts_voice_unverified",
                "could not import the VieNeu voice catalog; the configured "
                f"voice '{config.voice}' was not verified",
            )
        elif config.voice not in known:
            self.report.warn(
                "tts_voice_unknown",
                f"voice '{config.voice}' is not in the VieNeu catalog "
                f"(available: {', '.join(sorted(known))})",
            )

        if config.max_chars_per_chunk < 120:
            self.report.warn(
                "tts_chunk_small",
                f"max_chars_per_chunk={config.max_chars_per_chunk} forces very "
                "small chunks; prosody may suffer",
            )

    @staticmethod
    def _known_voices() -> set[str] | None:
        """
        VieNeu voice names, or None when no catalog can be found.

        The installed engine's own catalog wins: it is read from the JSON the
        SDK ships, so it costs a file read rather than a model load, and it is
        the list `infer` will actually accept.  The story pipeline's static
        8-voice map is only a fallback for an install that predates it, since
        treating a stale list as authoritative reports valid voices (the
        newer presets) as unknown.
        """
        names = _engine_voice_names()
        if names:
            return names

        try:
            from ai_mystery_story.infrastructure.tts.vieneu_tts_provider import (
                VIENEU_VOICES,
            )
        except Exception:
            return None
        return set(VIENEU_VOICES.values())

    def _check_audio_config(self) -> None:
        config = self.script.audio_config

        if config.background_music:
            if resolve_asset(config.background_music, self.paths.workspace) is None:
                self.report.error(
                    "bgm_missing",
                    f"background music not found: {config.background_music}",
                )
        elif config.background_volume > 0:
            self.report.warn(
                "bgm_volume_without_file",
                "background_volume is set but no background_music is declared",
            )

        if config.background_volume > 0.3:
            self.report.warn(
                "bgm_too_loud",
                f"background_volume={config.background_volume} will fight the "
                "voiceover; the spec recommends 0.12",
            )

    def _check_pacing(self) -> None:
        pacing = self.script.pacing
        scene_ids = {scene.id for scene in self.script.scenes}
        scene_count = len(self.script.scenes)

        seen_breaks: set[int] = set()
        previous_break = 0
        for index, value in enumerate(pacing.section_breaks):
            if value >= scene_count:
                self.report.error(
                    "section_break_out_of_range",
                    f"section_breaks[{index}]={value} is past the last scene "
                    f"(valid range 1..{scene_count - 1})",
                )
                continue
            if value in seen_breaks:
                self.report.warn(
                    "section_break_duplicate",
                    f"section_breaks contains {value} more than once",
                )
            if value <= previous_break:
                self.report.warn(
                    "section_break_unsorted",
                    f"section_breaks[{index}]={value} is not greater than the "
                    f"previous break {previous_break}; segment boundaries "
                    "will be ambiguous",
                )
            seen_breaks.add(value)
            previous_break = value

        for scene_id, pause in pacing.custom_pauses.items():
            if scene_id not in scene_ids:
                self.report.error(
                    "custom_pause_unknown_scene",
                    f"pacing.custom_pauses references scene {scene_id}, which "
                    "does not exist",
                    scene_id=scene_id,
                )
            elif pause > pacing.auto_pause.max_pause_ms:
                self.report.warn(
                    "custom_pause_above_max",
                    f"custom pause {pause}ms for scene {scene_id} exceeds "
                    f"auto_pause.max_pause_ms={pacing.auto_pause.max_pause_ms}ms",
                    scene_id=scene_id,
                )

    def _check_scene_sequence(self) -> None:
        seen: set[int] = set()
        previous = 0
        for scene in self.script.scenes:
            if scene.id in seen:
                self.report.error(
                    "scene_id_duplicate",
                    f"scene id {scene.id} appears more than once",
                    scene_id=scene.id,
                )
            elif scene.id <= previous:
                self.report.error(
                    "scene_id_not_increasing",
                    f"scene id {scene.id} does not come after {previous}; ids "
                    "must increase so render order is unambiguous",
                    scene_id=scene.id,
                )
            seen.add(scene.id)
            previous = scene.id

    def _check_scenes(self) -> None:
        metadata = self.script.video_metadata
        fonts: dict[str, int] = {}

        for scene in self.script.scenes:
            self._check_scene_image(scene)
            self._check_ken_burns(scene)
            self._check_overlays(scene, metadata)
            self._check_characters(scene, metadata)
            self._check_impact(scene)
            self._check_sfx(scene, fonts)
            self._check_text_length(scene)

        for font, count in sorted(fonts.items()):
            self.report.warn(
                "font_missing",
                f"font not found: {font} (used by {count} overlay(s)); the "
                "pipeline will fall back to a default font",
            )

    def _check_scene_image(self, scene: Scene) -> None:
        if scene.image_file:
            if resolve_asset(scene.image_file, self.paths.workspace) is None:
                self.report.error(
                    "image_missing",
                    f"image not found: {scene.image_file}",
                    scene_id=scene.id,
                )
            return

        self.report.warn(
            "image_not_generated",
            "scene has an image_prompt but no image_file; the image must be "
            "generated before stage 3 can prepare it",
            scene_id=scene.id,
        )

    def _check_ken_burns(self, scene: Scene) -> None:
        motion = scene.ken_burns

        if not motion.enabled:
            return

        if motion.type == "none":
            self.report.warn(
                "ken_burns_type_none",
                "ken_burns.enabled is true but type is 'none'; the image will "
                "sit completely still",
                scene_id=scene.id,
            )
            return

        # A zoom needs the scale to actually change; a pan instead needs
        # headroom to move the crop window inside the enlarged image, so an
        # unchanged scale is correct there.
        if motion.type in ("zoom_in", "zoom_out"):
            if abs(motion.end_scale - motion.start_scale) < 1e-9:
                self.report.warn(
                    "ken_burns_no_motion",
                    f"{motion.type} with start_scale == end_scale == "
                    f"{motion.start_scale}; there is no visible movement",
                    scene_id=scene.id,
                )
        elif max(motion.start_scale, motion.end_scale) < 1.0 + MIN_PAN_HEADROOM:
            self.report.warn(
                "ken_burns_no_room_to_pan",
                f"{motion.type} at scale {max(motion.start_scale, motion.end_scale)} "
                "leaves no room to move the crop window; set a scale of at "
                f"least {1.0 + MIN_PAN_HEADROOM:.2f} so panning is visible",
                scene_id=scene.id,
            )

    def _check_overlays(self, scene: Scene, metadata) -> None:
        if len(scene.text_overlays) > MAX_CONCURRENT_OVERLAYS:
            self.report.warn(
                "overlay_count",
                f"{len(scene.text_overlays)} text overlays on one scene; the "
                f"spec allows at most {MAX_CONCURRENT_OVERLAYS} at a time",
                scene_id=scene.id,
            )

        estimated_ms = estimate_scene_seconds(
            scene, self.script.tts_config.speed
        ) * 1000

        for index, overlay in enumerate(scene.text_overlays):
            duration_ms = overlay.end_offset_ms - overlay.start_offset_ms
            if duration_ms < MIN_OVERLAY_READABLE_MS:
                self.report.warn(
                    "overlay_too_short",
                    f"overlay {index} is on screen for {duration_ms}ms; at "
                    f"least {MIN_OVERLAY_READABLE_MS}ms is needed to read it",
                    scene_id=scene.id,
                )

            if overlay.end_offset_ms > estimated_ms:
                self.report.warn(
                    "overlay_past_scene",
                    f"overlay {index} ends at {overlay.end_offset_ms}ms but "
                    f"the scene narrates for roughly {estimated_ms:.0f}ms",
                    scene_id=scene.id,
                )

            if overlay.animation_duration_ms > duration_ms:
                self.report.error(
                    "overlay_animation_too_long",
                    f"overlay {index} animates for "
                    f"{overlay.animation_duration_ms}ms but is only visible "
                    f"for {duration_ms}ms",
                    scene_id=scene.id,
                )

            for label, color in (
                ("color", overlay.color),
                ("stroke_color", overlay.stroke_color),
            ):
                if not HEX_COLOR.match(color):
                    self.report.warn(
                        "overlay_color_format",
                        f"overlay {index} {label}='{color}' is not a #RGB or "
                        "#RRGGBB hex colour",
                        scene_id=scene.id,
                    )

            # Scale the minimum readable size with the shorter side, which
            # is the text's binding constraint on both a wide and a tall
            # frame.  Using the height made every vertical frame demand
            # oversized text.
            reference = min(metadata.width, metadata.height)
            min_size = MIN_OVERLAY_FONT_SIZE_1080P * reference / 1080
            if overlay.font_size < min_size:
                self.report.warn(
                    "overlay_font_small",
                    f"overlay {index} font_size={overlay.font_size} is below "
                    f"{min_size:.0f}px for a {metadata.resolution} frame; it "
                    "will be hard to read on a phone",
                    scene_id=scene.id,
                )

    def _check_characters(self, scene: Scene, metadata) -> None:
        """
        Character layers: the artwork, where it stands, and when it is on.

        Everything here is checkable without rendering, which is the point
        of having a validator: a missing sprite or a cue that falls outside
        its scene is a one-line fix before a render rather than something
        noticed in a 13-minute video.
        """
        if not scene.characters:
            return

        if len(scene.characters) > MAX_CHARACTERS_PER_SCENE:
            self.report.warn(
                "character_count",
                f"{len(scene.characters)} characters on one scene; more than "
                f"{MAX_CHARACTERS_PER_SCENE} is hard to follow",
                scene_id=scene.id,
            )

        estimated_ms = estimate_scene_seconds(
            scene, self.script.tts_config.speed
        ) * 1000
        sentence_count = self._sentence_count(scene.id)

        for index, character in enumerate(scene.characters):
            asset = resolve_asset(character.image_file, self.paths.workspace)
            aspect = 1.0
            if asset is None:
                self.report.error(
                    "character_image_missing",
                    f"character {index} image not found: {character.image_file}",
                    scene_id=scene.id,
                )
            else:
                has_alpha, transparent_share = sprite_transparency(asset)
                if not has_alpha:
                    self.report.error(
                        "character_image_opaque",
                        f"character {index} ({asset.name}) has no alpha "
                        "channel; it will cover the scene as a rectangle. "
                        "Export the character as a transparent PNG",
                        scene_id=scene.id,
                    )
                elif transparent_share < 0.02:
                    self.report.warn(
                        "character_fully_opaque",
                        f"character {index} ({asset.name}) is only "
                        f"{transparent_share * 100:.1f}% transparent; check "
                        "that its background was really removed",
                        scene_id=scene.id,
                    )
                aspect = self._sprite_aspect(asset)

            height_px = character.height * metadata.height
            width_px = height_px * aspect
            left = character.x * metadata.width - width_px / 2
            right = left + width_px
            if left < -0.02 * metadata.width or right > 1.02 * metadata.width:
                self.report.warn(
                    "character_off_frame",
                    f"character {index} is {width_px:.0f}px wide at x="
                    f"{character.x:.2f} and would extend outside the "
                    "frame; move it inward or reduce height",
                    scene_id=scene.id,
                )

            if character.height > MAX_CHARACTER_HEIGHT_FRACTION:
                self.report.warn(
                    "character_too_large",
                    f"character {index} height={character.height} fills more "
                    f"than {MAX_CHARACTER_HEIGHT_FRACTION} of the frame and "
                    "leaves no room for the scene",
                    scene_id=scene.id,
                )
            elif character.height < MIN_CHARACTER_HEIGHT_FRACTION:
                self.report.warn(
                    "character_too_small",
                    f"character {index} height={character.height} is under "
                    f"{MIN_CHARACTER_HEIGHT_FRACTION} of the frame; details "
                    "will not read on a phone",
                    scene_id=scene.id,
                )

            if character.y < 0.3:
                self.report.warn(
                    "character_floating",
                    f"character {index} stands at y={character.y} (its feet), "
                    "which is high in the frame; characters usually stand in "
                    "the lower half unless they are flying",
                    scene_id=scene.id,
                )

            if (
                character.at_sentence is not None
                and sentence_count
                and character.at_sentence > sentence_count
            ):
                self.report.warn(
                    "character_sentence_missing",
                    f"character {index} waits for sentence "
                    f"{character.at_sentence} but this scene has "
                    f"{sentence_count}; its offset will be used instead",
                    scene_id=scene.id,
                )

            if character.end_offset_ms is not None:
                if character.end_offset_ms > estimated_ms:
                    self.report.warn(
                        "character_past_scene",
                        f"character {index} leaves at "
                        f"{character.end_offset_ms}ms but the scene narrates "
                        f"for roughly {estimated_ms:.0f}ms; it will be cut",
                        scene_id=scene.id,
                    )
                visible_ms = character.end_offset_ms - character.start_offset_ms
                if visible_ms < MIN_CHARACTER_VISIBLE_MS:
                    self.report.warn(
                        "character_too_brief",
                        f"character {index} is on screen for {visible_ms}ms; "
                        f"under {MIN_CHARACTER_VISIBLE_MS}ms reads as a flash "
                        "of something rather than as an appearance",
                        scene_id=scene.id,
                    )

            if character.sfx and resolve_asset(
                character.sfx, self.paths.workspace
            ) is None:
                self.report.error(
                    "character_sfx_missing",
                    f"character {index} sound not found: {character.sfx}",
                    scene_id=scene.id,
                )

        self._check_character_overlap(scene)

    def _check_character_overlap(self, scene: Scene) -> None:
        """
        Two characters whose windows overlap land on top of each other.

        Only cues with explicit millisecond windows can be compared here: a
        cue anchored to a sentence has no known window until stage 2 has
        measured the narration, and comparing the two clocks would invent a
        conflict rather than find one.
        """
        windows: list[tuple[int, float, float | None]] = []
        for index, character in enumerate(scene.characters):
            if character.at_sentence is not None:
                continue
            windows.append(
                (
                    index,
                    float(character.start_offset_ms),
                    (
                        float(character.end_offset_ms)
                        if character.end_offset_ms is not None
                        else None
                    ),
                )
            )

        for position in range(len(windows)):
            for other in range(position + 1, len(windows)):
                first, second = windows[position], windows[other]
                # An open-ended cue runs to the end of the scene, so it
                # overlaps anything that starts after it.
                if first[2] is None or second[2] is None:
                    overlapping = True
                else:
                    overlapping = min(first[2], second[2]) > max(
                        first[1], second[1]
                    )
                if overlapping:
                    self.report.warn(
                        "characters_overlap",
                        f"characters {first[0]} and {second[0]} are on screen "
                        "at the same time; place them at x positions far "
                        "enough apart or stagger their timing",
                        scene_id=scene.id,
                    )

    def _sentence_count(self, scene_id: int) -> int:
        """Sentences stage 2 measured for a scene, or 0 when it has not run."""
        report = read_json(self.paths.output_dir / "tts_report.json")
        if not isinstance(report, dict):
            return 0
        for scene in report.get("scenes") or []:
            if isinstance(scene, dict) and scene.get("id") == scene_id:
                return len(scene.get("units") or [])
        return 0

    def _sprite_aspect(self, asset: Path) -> float:  # noqa: D401
        """Width over height of a sprite's visible pixels."""
        try:
            with Image.open(asset) as image:
                sprite = image.convert("RGBA")
                box = sprite.getbbox()
                if box is not None:
                    sprite = sprite.crop(box)
                if sprite.height <= 0:
                    return 1.0
                return sprite.width / sprite.height
        except OSError:
            return 1.0

    def _check_impact(self, scene: Scene) -> None:
        """Punch-ins: on a beat that exists, and not fighting a flash."""
        impact = scene.impact
        if impact is None or not impact.enabled:
            return

        estimated_ms = estimate_scene_seconds(
            scene, self.script.tts_config.speed
        ) * 1000
        sentence_count = self._sentence_count(scene.id)

        if (
            impact.at_sentence is not None
            and sentence_count
            and impact.at_sentence > sentence_count
        ):
            self.report.warn(
                "impact_sentence_missing",
                f"the punch-in waits for sentence {impact.at_sentence} but "
                f"this scene has {sentence_count}; it will fire at the start "
                "of the scene instead",
                scene_id=scene.id,
            )
        elif impact.at_sentence is None and impact.at_offset_ms > estimated_ms:
            self.report.warn(
                "impact_outside_scene",
                f"the punch-in is at {impact.at_offset_ms}ms but the scene "
                f"narrates for roughly {estimated_ms:.0f}ms",
                scene_id=scene.id,
            )

        # Two white flashes in a row is a strobe, not an edit: the scene
        # transition and the punch-in would both fire at the same instant.
        if (
            impact.flash == "white"
            and scene.transition_in.type in ("flash_white", "fade_fast")
            and impact.at_sentence in (None, 1)
            and impact.at_offset_ms < 400
        ):
            self.report.warn(
                "impact_flashes_twice",
                "this scene already arrives on a white transition and the "
                "punch-in flashes at the same moment; move the punch-in to "
                "a later beat or drop one of the two",
                scene_id=scene.id,
            )

    def _check_sfx(self, scene: Scene, missing_fonts: dict[str, int]) -> None:
        for index, overlay in enumerate(scene.text_overlays):
            if resolve_asset(overlay.font, self.paths.workspace) is None:
                missing_fonts[overlay.font] = (
                    missing_fonts.get(overlay.font, 0) + 1
                )

        for index, sfx in enumerate(scene.sfx):
            if resolve_asset(sfx.file, self.paths.workspace) is None:
                self.report.error(
                    "sfx_missing",
                    f"sfx not found: {sfx.file}",
                    scene_id=scene.id,
                )
            if sfx.volume > MAX_SFX_VOLUME:
                self.report.warn(
                    "sfx_too_loud",
                    f"sfx {index} volume={sfx.volume} exceeds the "
                    f"{MAX_SFX_VOLUME} ceiling; SFX must stay under the voice",
                    scene_id=scene.id,
                )

    def _check_text_length(self, scene: Scene) -> None:
        length = len(scene.text)
        if length < 20:
            self.report.warn(
                "text_too_short",
                f"scene text is only {length} characters; the image will "
                "change too fast to register",
                scene_id=scene.id,
            )
            return

        # One scene is one breath: much more than ~60s of narration means
        # the scene should probably be split.
        seconds = estimate_scene_seconds(scene, self.script.tts_config.speed)
        if seconds > 60:
            self.report.warn(
                "scene_too_long",
                f"scene narrates for roughly {seconds:.0f}s; consider splitting "
                "it so images keep moving",
                scene_id=scene.id,
            )

    def _check_sfx_density(self) -> None:
        """Spec §10.2: at most one SFX per 5-10 seconds."""
        for scene in self.script.scenes:
            if len(scene.sfx) < 2:
                continue
            offsets = sorted(sfx.time_offset_ms for sfx in scene.sfx)
            for previous, current in zip(offsets, offsets[1:]):
                gap = (current - previous) / 1000.0
                if gap < MIN_SFX_GAP_SECONDS:
                    self.report.warn(
                        "sfx_too_dense",
                        f"two SFX are {gap:.1f}s apart (minimum "
                        f"{MIN_SFX_GAP_SECONDS:.0f}s); they will read as noise",
                        scene_id=scene.id,
                    )

    def _check_toolchain(self, *, skip_engine_check: bool = False) -> None:
        if not ffmpeg_tools.ffmpeg_available():
            self.report.error(
                "ffmpeg_missing",
                "ffmpeg not found. Add a static binary to bin/ffmpeg or "
                "install ffmpeg and put it on PATH.",
            )
        if not ffmpeg_tools.ffprobe_available():
            self.report.error(
                "ffprobe_missing",
                "ffprobe not found. Add a static binary to bin/ffprobe or "
                "install ffmpeg and put it on PATH.",
            )

        if skip_engine_check:
            return

        try:
            import vieneu  # noqa: F401
        except Exception:
            self.report.error(
                "tts_engine_missing",
                "the VieNeu TTS engine is not installed. Run: pip install vieneu. "
                "Pass --skip-engine-check to validate everything else without it.",
            )

    def _check_disk(self) -> None:
        try:
            usage = shutil.disk_usage(self.paths.workspace)
        except OSError as error:
            self.report.warn(
                "disk_unreadable",
                f"could not read free disk space for {self.paths.workspace}: "
                f"{error}",
            )
            return

        needed_mb = estimate_disk_mb(
            estimate_total_seconds(self.script), self.script
        )
        free_mb = usage.free / (1024 * 1024)

        if free_mb < needed_mb:
            self.report.error(
                "disk_full",
                f"need about {needed_mb:.0f} MB but only {free_mb:.0f} MB is "
                f"free on {self.paths.workspace}",
            )
        elif free_mb < needed_mb * 1.5:
            self.report.warn(
                "disk_tight",
                f"{free_mb:.0f} MB free against an estimated {needed_mb:.0f} MB "
                "requirement; leave more headroom",
            )

    # -- stats ------------------------------------------------------------

    def _collect_stats(self) -> None:
        script = self.script
        scenes = script.scenes
        lengths = [len(scene.text) for scene in scenes]

        images_found = 0
        images_missing = 0
        for scene in scenes:
            if not scene.image_file:
                continue
            if resolve_asset(scene.image_file, self.paths.workspace) is None:
                images_missing += 1
            else:
                images_found += 1

        total_seconds = estimate_total_seconds(script)
        target_min_minutes, target_max_minutes = self._target_minutes()
        self.report.stats = {
            "scene_count": len(scenes),
            "total_text_chars": sum(lengths),
            "total_sentences": sum(count_sentences(scene.text) for scene in scenes),
            "text_chars_min": min(lengths),
            "text_chars_max": max(lengths),
            "text_chars_avg": round(sum(lengths) / len(lengths), 1),
            "images_found": images_found,
            "images_missing": images_missing,
            "images_to_generate": sum(1 for scene in scenes if not scene.image_file),
            "text_overlays": sum(len(scene.text_overlays) for scene in scenes),
            "sfx_count": sum(len(scene.sfx) for scene in scenes),
            "characters": sum(len(scene.characters) for scene in scenes),
            "character_presets": sorted(
                {
                    character.preset
                    for scene in scenes
                    for character in scene.characters
                    if character.preset != "none"
                }
            ),
            "punch_ins": sum(
                1
                for scene in scenes
                if scene.impact is not None and scene.impact.enabled
            ),
            "estimated_narration_seconds": round(
                sum(
                    estimate_scene_seconds(scene, script.tts_config.speed)
                    for scene in scenes
                ),
                1,
            ),
            "estimated_total_seconds": round(total_seconds, 1),
            "estimated_total_minutes": round(total_seconds / 60, 1),
            "estimated_disk_mb": round(
                estimate_disk_mb(total_seconds, script), 1
            ),
            "target_minutes": [target_min_minutes, target_max_minutes],
        }

        minutes = total_seconds / 60
        if minutes < target_min_minutes:
            self.report.warn(
                "runtime_short",
                f"estimated runtime is {minutes:.1f} min, well under the "
                f"{target_min_minutes:g}-{target_max_minutes:g} min target "
                "for this format",
            )
        elif minutes > target_max_minutes:
            self.report.warn(
                "runtime_long",
                f"estimated runtime is {minutes:.1f} min, above the "
                f"{target_max_minutes:g} min target for this format",
            )

    def _target_minutes(self) -> tuple[float, float]:
        """Runtime envelope from the script, falling back to the format default."""
        target = getattr(self.script.video_metadata, "target_minutes", None)
        if (
            isinstance(target, (tuple, list))
            and len(target) == 2
            and target[0] <= target[1]
        ):
            return (float(target[0]), float(target[1]))
        return DEFAULT_TARGET_MINUTES
