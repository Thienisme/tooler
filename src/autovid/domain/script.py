"""
Script schema for the autovid explainer-video pipeline.

`script.json` is the data contract between the script writer and the
pipeline.  Every field is parsed into a frozen dataclass so that later
stages never touch raw dicts and never guess types.

Parsing is *error accumulating*: a malformed script reports every problem
at once instead of dying on the first one.  Structural problems (missing
key, wrong type, out-of-range number, unknown enum value) are raised here.
Semantic problems that depend on the filesystem or on other scenes (asset
existence, section-break indices, sfx density, ...) belong to the
validator in `autovid.application.validate`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Allowed enum values
# --------------------------------------------------------------------------

KEN_BURNS_TYPES = frozenset(
    {
        "zoom_in",
        "zoom_out",
        "pan_left",
        "pan_right",
        "pan_up",
        "pan_down",
        "none",
    }
)

TRANSITION_TYPES = frozenset(
    {
        "cut",
        "fade",
        "fade_fast",
        "fade_slow",
        "dissolve",
        "slide_left",
        "slide_right",
        "slide_up",
        "slide_down",
        "wipe_left",
        "wipe_right",
        "wipe_up",
        "wipe_down",
        "zoom",
        "whip_pan",
        # Punchy, comedy-usable joins: a hard geometric wipe, a blur sweep,
        # a square-block cut, an iris, a full-frame flash and a diagonal.
        "pixelize",
        "blur",
        "circle_open",
        "circle_close",
        "squeeze_h",
        "squeeze_v",
        "flash_white",
        "flash_black",
        "diag_tl",
        "diag_br",
    }
)

TEXT_ANIMATIONS = frozenset(
    {"fade_in", "pop", "typewriter", "slide_in", "none"}
)

# --------------------------------------------------------------------------
# Character layers (a cartoon host composited over the background)
# --------------------------------------------------------------------------

# How a character arrives.  "pop" and "zoom_in" grow in size, "drop_bounce"
# falls and lands, "fly_in"/"slide_in" travel in from an edge, "spin_in"
# rotates while it arrives.
CHARACTER_ENTER_TYPES = frozenset(
    {
        "none",
        "fade_in",
        "pop",
        "fly_in",
        "slide_in",
        "drop_bounce",
        "spin_in",
        "zoom_in",
    }
)

CHARACTER_EXIT_TYPES = frozenset(
    {
        "none",
        "fade_out",
        "shrink_out",
        "fly_out",
        "slide_out",
        "drop_out",
        "spin_out",
        "zoom_out",
    }
)

# What a character does while it is on screen.  These are all oscillation
# of position, because a size cannot be animated inside one filtergraph
# (see infrastructure/video/sprites.py): "talk" is the quick bob that
# stands in for speech, "bob_sway" is the general "alive" setting.
CHARACTER_IDLE_TYPES = frozenset(
    {"none", "bob", "sway", "bob_sway", "shake", "talk"}
)

CHARACTER_EDGES = frozenset(
    {
        "left",
        "right",
        "top",
        "bottom",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
        "center",
    }
)

# A named comedy bundle: it fills in enter/idle/exit/sfx for whatever the
# script left out, so a scene can say "preset": "boing" and get a whole bit.
CHARACTER_PRESETS = frozenset(
    {"none", "pop", "boing", "whoosh", "ta_da", "sneak", "ninja"}
)

CHARACTER_PRESET_DEFAULTS: dict[str, dict] = {
    "pop": {
        "enter": {"type": "pop", "duration_ms": 320},
        "idle": {"type": "bob", "amplitude_px": 10, "period_s": 2.2},
        "exit": {"type": "fade_out", "duration_ms": 300},
        "sfx": "data/sfx/pop.mp3",
    },
    "boing": {
        "enter": {"type": "drop_bounce", "from": "top", "duration_ms": 900},
        "idle": {"type": "bob_sway", "amplitude_px": 16, "period_s": 2.4},
        "exit": {"type": "shrink_out", "duration_ms": 350},
        "sfx": "data/sfx/boing.mp3",
    },
    "whoosh": {
        "enter": {"type": "fly_in", "from": "left", "duration_ms": 550},
        "idle": {"type": "sway", "amplitude_px": 12, "period_s": 3.0},
        "exit": {"type": "fly_out", "to": "right", "duration_ms": 450},
        "sfx": "data/sfx/whoosh.mp3",
    },
    "ta_da": {
        "enter": {"type": "zoom_in", "duration_ms": 420},
        "idle": {"type": "talk", "amplitude_px": 8, "period_s": 0.45},
        "exit": {"type": "fade_out", "duration_ms": 350},
        "sfx": "data/sfx/ta_da.mp3",
    },
    "sneak": {
        "enter": {"type": "slide_in", "from": "bottom", "duration_ms": 700},
        "idle": {"type": "shake", "amplitude_px": 6, "period_s": 0.4},
        "exit": {"type": "slide_out", "to": "bottom", "duration_ms": 600},
        "sfx": "data/sfx/sneak.mp3",
    },
    "ninja": {
        "enter": {"type": "spin_in", "from": "right", "duration_ms": 700},
        "idle": {"type": "none"},
        "exit": {"type": "spin_out", "to": "left", "duration_ms": 600},
        "sfx": "data/sfx/whoosh.mp3",
    },
}

# A cutaway "reaction" panel: the same character compositing, positioned by
# fraction of the frame instead of as a host standing on the floor.
CHARACTER_FLASHES = frozenset({"none", "white", "black"})

MAX_CHARACTER_HEIGHT_FRACTION = 1.0
MIN_CHARACTER_HEIGHT_FRACTION = 0.05
MAX_CHARACTER_IDLE_AMPLITUDE_PX = 120
MAX_IMPACT_INTENSITY = 0.35
MAX_IMPACT_SHAKE_PX = 60

TEXT_POSITIONS = frozenset(
    {
        "center",
        "top",
        "bottom",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
    }
)

TTS_ENGINES = frozenset({"vieneu"})

# "sentence" synthesizes each sentence separately so that the auto-pause
# engine can place a real pause after every sentence.  "scene" synthesizes
# the whole scene in one call (better prosody, coarser pacing control).
TTS_GRANULARITIES = frozenset({"sentence", "scene"})

RESOLUTION_PATTERN = re.compile(r"^(\d+)x(\d+)$")

# --------------------------------------------------------------------------
# Hard limits (spec §4 / §10)
# --------------------------------------------------------------------------

MAX_PAUSE_AFTER_MS = 10_000
KEN_BURNS_MIN_SCALE = 1.0
KEN_BURNS_MAX_SCALE = 1.5
MAX_SFX_VOLUME = 0.8
MIN_TTS_SPEED = 0.5
MAX_TTS_SPEED = 2.0


class ScriptSchemaError(Exception):
    """Raised when script.json violates the schema contract."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        joined = "\n".join(f"  - {message}" for message in errors)
        super().__init__(
            f"script.json is invalid ({len(errors)} error(s)):\n{joined}"
        )


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VideoMetadata:
    title: str
    author: str
    resolution: str
    fps: int
    language: str
    # Runtime envelope, in minutes, that the validator measures the
    # estimate against.  Defaults to the 8-20 minute explainer format; a
    # Short declares its own so a 60s video is not warned as "too short".
    target_minutes: tuple[float, float] = (8.0, 20.0)

    @property
    def width(self) -> int:
        return int(RESOLUTION_PATTERN.match(self.resolution).group(1))

    @property
    def height(self) -> int:
        return int(RESOLUTION_PATTERN.match(self.resolution).group(2))


@dataclass(frozen=True)
class TTSConfig:
    engine: str
    voice: str
    speed: float
    pitch: float
    emotion: str
    granularity: str
    max_chars_per_chunk: int


@dataclass(frozen=True)
class AudioConfig:
    background_music: str | None
    background_volume: float
    master_volume: float
    fade_in_seconds: float
    fade_out_seconds: float
    # Duck the bed under the narration.  A fixed volume has to be quiet
    # enough for the loudest passage, which leaves the music inaudible
    # everywhere else; ducking lets it sit higher between sentences.
    ducking: bool


@dataclass(frozen=True)
class AutoPauseConfig:
    enabled: bool
    per_sentence: bool
    min_pause_ms: int
    max_pause_ms: int
    respect_tts_natural_pause: bool


@dataclass(frozen=True)
class PacingConfig:
    auto_pause: AutoPauseConfig
    section_breaks: tuple[int, ...]
    custom_pauses: dict[int, int]


@dataclass(frozen=True)
class KenBurns:
    enabled: bool
    type: str
    start_scale: float
    end_scale: float


@dataclass(frozen=True)
class TransitionIn:
    type: str
    duration: float


@dataclass(frozen=True)
class TextOverlay:
    text: str
    font: str
    font_size: int
    color: str
    stroke_color: str
    stroke_width: int
    position: str
    start_offset_ms: int
    end_offset_ms: int
    animation: str
    animation_duration_ms: int


@dataclass(frozen=True)
class SFX:
    file: str
    time_offset_ms: int
    volume: float


@dataclass(frozen=True)
class CharacterMotion:
    """An arrival or a departure: what, how long, and from/to which edge."""

    type: str
    duration_ms: int
    edge: str


@dataclass(frozen=True)
class CharacterIdle:
    """What a character does while it is standing there."""

    type: str
    amplitude_px: int
    period_s: float


@dataclass(frozen=True)
class CharacterOverlay:
    """One character layer inside one scene."""

    image_file: str
    preset: str
    x: float
    y: float
    height: float
    flip: bool
    # `at_sentence` is 1-based and resolved against the measured narration,
    # so a cue can say "appear when the third sentence starts" instead of
    # guessing a millisecond offset that TTS is free to invalidate.
    at_sentence: int | None
    start_offset_ms: int
    end_offset_ms: int | None
    for_sentences: int | None
    enter: CharacterMotion
    idle: CharacterIdle
    exit: CharacterMotion
    sfx: str | None
    sfx_volume: float


@dataclass(frozen=True)
class Impact:
    """A punch-in: a fast zoom spike, an optional camera shake, a flash."""

    enabled: bool
    at_sentence: int | None
    at_offset_ms: int
    intensity: float
    shake_px: int
    duration_ms: int
    flash: str


@dataclass(frozen=True)
class Scene:
    id: int
    text: str
    image_file: str | None
    image_prompt: str | None
    pause_after_ms: int | None
    transition_in: TransitionIn
    ken_burns: KenBurns
    text_overlays: tuple[TextOverlay, ...]
    sfx: tuple[SFX, ...]
    characters: tuple[CharacterOverlay, ...] = ()
    impact: Impact | None = None


@dataclass(frozen=True)
class Script:
    video_metadata: VideoMetadata
    tts_config: TTSConfig
    audio_config: AudioConfig
    pacing: PacingConfig
    scenes: tuple[Scene, ...]

    @property
    def scene_count(self) -> int:
        return len(self.scenes)

    @property
    def total_text_chars(self) -> int:
        return sum(len(scene.text) for scene in self.scenes)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


# Sentinel for "key absent", distinct from an explicit JSON null.
_MISSING = object()


class _Parser:
    """Accumulates every schema error instead of raising on the first."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def fail(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    # -- primitive readers ------------------------------------------------

    def obj(self, value: Any, where: str) -> dict | None:
        if value is _MISSING or value is None:
            return None
        if not isinstance(value, dict):
            self.fail(where, f"expected an object, got {type(value).__name__}")
            return None
        return value

    def required(
        self, data: dict, key: str, where: str
    ) -> Any:
        if key not in data or data[key] is None:
            self.fail(where, f"missing required key '{key}'")
            return _MISSING
        return data[key]

    def string(
        self,
        value: Any,
        where: str,
        *,
        default: str | None = None,
        allow_empty: bool = False,
    ) -> str | None:
        if value is _MISSING:
            return default
        if value is None:
            return default
        if not isinstance(value, str):
            self.fail(where, f"expected a string, got {type(value).__name__}")
            return default
        if not allow_empty and not value.strip():
            self.fail(where, "must not be empty")
            return default
        return value

    def number(
        self,
        value: Any,
        where: str,
        *,
        default: float | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float | None:
        if value is _MISSING or value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.fail(where, f"expected a number, got {type(value).__name__}")
            return default
        result = float(value)
        if minimum is not None and result < minimum:
            self.fail(where, f"must be >= {minimum}, got {result}")
            return default
        if maximum is not None and result > maximum:
            self.fail(where, f"must be <= {maximum}, got {result}")
            return default
        return result

    def integer(
        self,
        value: Any,
        where: str,
        *,
        default: int | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int | None:
        if value is _MISSING or value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            self.fail(where, f"expected an integer, got {type(value).__name__}")
            return default
        if minimum is not None and value < minimum:
            self.fail(where, f"must be >= {minimum}, got {value}")
            return default
        if maximum is not None and value > maximum:
            self.fail(where, f"must be <= {maximum}, got {value}")
            return default
        return value

    def boolean(
        self, value: Any, where: str, *, default: bool | None = None
    ) -> bool | None:
        if value is _MISSING or value is None:
            return default
        if not isinstance(value, bool):
            self.fail(where, f"expected a boolean, got {type(value).__name__}")
            return default
        return value

    def enum(
        self,
        value: Any,
        where: str,
        allowed: frozenset[str],
        *,
        default: str | None = None,
    ) -> str | None:
        if value is _MISSING or value is None:
            return default
        if not isinstance(value, str):
            self.fail(where, f"expected a string, got {type(value).__name__}")
            return default
        if value not in allowed:
            options = ", ".join(sorted(allowed))
            self.fail(where, f"unknown value '{value}' (allowed: {options})")
            return default
        return value

    def array(self, value: Any, where: str) -> list | None:
        if value is _MISSING or value is None:
            return []
        if not isinstance(value, list):
            self.fail(where, f"expected an array, got {type(value).__name__}")
            return []
        return value


def _parse_ken_burns(parser: _Parser, data: Any, where: str) -> KenBurns:
    raw = parser.obj(data, where) or {}
    return KenBurns(
        enabled=parser.boolean(
            raw.get("enabled", _MISSING), f"{where}.enabled", default=True
        ),
        type=parser.enum(
            raw.get("type", _MISSING),
            f"{where}.type",
            KEN_BURNS_TYPES,
            default="zoom_in",
        ),
        start_scale=parser.number(
            raw.get("start_scale", _MISSING),
            f"{where}.start_scale",
            default=KEN_BURNS_MIN_SCALE,
            minimum=KEN_BURNS_MIN_SCALE,
            maximum=KEN_BURNS_MAX_SCALE,
        ),
        end_scale=parser.number(
            raw.get("end_scale", _MISSING),
            f"{where}.end_scale",
            default=1.08,
            minimum=KEN_BURNS_MIN_SCALE,
            maximum=KEN_BURNS_MAX_SCALE,
        ),
    )


def _parse_transition(parser: _Parser, data: Any, where: str) -> TransitionIn:
    raw = parser.obj(data, where) or {}
    return TransitionIn(
        type=parser.enum(
            raw.get("type", _MISSING),
            f"{where}.type",
            TRANSITION_TYPES,
            default="cut",
        ),
        duration=parser.number(
            raw.get("duration", _MISSING),
            f"{where}.duration",
            default=0.0,
            minimum=0.0,
        ),
    )


def _parse_text_overlay(
    parser: _Parser, data: Any, where: str
) -> TextOverlay | None:
    raw = parser.obj(data, where)
    if raw is None:
        return None

    text = parser.string(
        parser.required(raw, "text", where), f"{where}.text"
    )
    if text is None:
        # Without text the overlay is meaningless; the error is recorded.
        return None

    start = parser.integer(
        raw.get("start_offset_ms", _MISSING),
        f"{where}.start_offset_ms",
        default=0,
        minimum=0,
    )
    end = parser.integer(
        raw.get("end_offset_ms", _MISSING),
        f"{where}.end_offset_ms",
        default=start + 2000,
        minimum=0,
    )

    if start is not None and end is not None and end <= start:
        parser.fail(
            f"{where}.end_offset_ms",
            f"must be greater than start_offset_ms ({start}), got {end}",
        )

    return TextOverlay(
        text=text,
        font=parser.string(
            raw.get("font", _MISSING),
            f"{where}.font",
            default="assets/fonts/handwriting.ttf",
        ),
        font_size=parser.integer(
            raw.get("font_size", _MISSING),
            f"{where}.font_size",
            default=72,
            minimum=8,
        ),
        color=parser.string(
            raw.get("color", _MISSING), f"{where}.color", default="#FFFFFF"
        ),
        stroke_color=parser.string(
            raw.get("stroke_color", _MISSING),
            f"{where}.stroke_color",
            default="#000000",
        ),
        stroke_width=parser.integer(
            raw.get("stroke_width", _MISSING),
            f"{where}.stroke_width",
            default=3,
            minimum=0,
        ),
        position=parser.enum(
            raw.get("position", _MISSING),
            f"{where}.position",
            TEXT_POSITIONS,
            default="center",
        ),
        start_offset_ms=start,
        end_offset_ms=end,
        animation=parser.enum(
            raw.get("animation", _MISSING),
            f"{where}.animation",
            TEXT_ANIMATIONS,
            default="fade_in",
        ),
        animation_duration_ms=parser.integer(
            raw.get("animation_duration_ms", _MISSING),
            f"{where}.animation_duration_ms",
            default=300,
            minimum=0,
        ),
    )


def _parse_sfx(parser: _Parser, data: Any, where: str) -> SFX | None:
    raw = parser.obj(data, where)
    if raw is None:
        return None

    file = parser.string(parser.required(raw, "file", where), f"{where}.file")
    if file is None:
        return None

    return SFX(
        file=file,
        time_offset_ms=parser.integer(
            raw.get("time_offset_ms", _MISSING),
            f"{where}.time_offset_ms",
            default=0,
            minimum=0,
        ),
        volume=parser.number(
            raw.get("volume", _MISSING),
            f"{where}.volume",
            default=MAX_SFX_VOLUME,
            minimum=0.0,
            maximum=MAX_SFX_VOLUME,
        ),
    )


def _parse_character(
    parser: _Parser, data: Any, where: str
) -> CharacterOverlay | None:
    """
    Parse one character layer, filling the gaps from its comedy preset.

    A preset is a starting point, not a mode: anything written out
    explicitly wins, so `"preset": "boing"` plus a custom `exit` gives the
    boing entrance and the author's own departure.
    """
    raw = parser.obj(data, where)
    if raw is None:
        return None

    preset = parser.enum(
        raw.get("preset", _MISSING),
        f"{where}.preset",
        CHARACTER_PRESETS,
        default="none",
    )
    defaults = CHARACTER_PRESET_DEFAULTS.get(preset or "none", {})

    image_file = parser.string(
        parser.required(raw, "image_file", where), f"{where}.image_file"
    )
    if image_file is None:
        return None

    enter_defaults: dict = defaults.get("enter", {})
    exit_defaults: dict = defaults.get("exit", {})
    idle_defaults: dict = defaults.get("idle", {})

    enter_raw = parser.obj(raw.get("enter", _MISSING), f"{where}.enter") or {}
    enter = CharacterMotion(
        type=parser.enum(
            enter_raw.get("type", _MISSING),
            f"{where}.enter.type",
            CHARACTER_ENTER_TYPES,
            default=enter_defaults.get("type", "fade_in"),
        ),
        duration_ms=parser.integer(
            enter_raw.get("duration_ms", _MISSING),
            f"{where}.enter.duration_ms",
            default=enter_defaults.get("duration_ms", 400),
            minimum=0,
        ),
        edge=parser.enum(
            enter_raw.get("from", _MISSING),
            f"{where}.enter.from",
            CHARACTER_EDGES,
            default=enter_defaults.get("from", "bottom"),
        ),
    )

    exit_raw = parser.obj(raw.get("exit", _MISSING), f"{where}.exit") or {}
    exit_motion = CharacterMotion(
        type=parser.enum(
            exit_raw.get("type", _MISSING),
            f"{where}.exit.type",
            CHARACTER_EXIT_TYPES,
            default=exit_defaults.get("type", "fade_out"),
        ),
        duration_ms=parser.integer(
            exit_raw.get("duration_ms", _MISSING),
            f"{where}.exit.duration_ms",
            default=exit_defaults.get("duration_ms", 400),
            minimum=0,
        ),
        edge=parser.enum(
            exit_raw.get("to", _MISSING),
            f"{where}.exit.to",
            CHARACTER_EDGES,
            default=exit_defaults.get("to", "bottom"),
        ),
    )

    idle_raw = parser.obj(raw.get("idle", _MISSING), f"{where}.idle") or {}
    idle = CharacterIdle(
        type=parser.enum(
            idle_raw.get("type", _MISSING),
            f"{where}.idle.type",
            CHARACTER_IDLE_TYPES,
            default=idle_defaults.get("type", "bob_sway"),
        ),
        amplitude_px=parser.integer(
            idle_raw.get("amplitude_px", _MISSING),
            f"{where}.idle.amplitude_px",
            default=idle_defaults.get("amplitude_px", 12),
            minimum=0,
            maximum=MAX_CHARACTER_IDLE_AMPLITUDE_PX,
        ),
        period_s=parser.number(
            idle_raw.get("period_s", _MISSING),
            f"{where}.idle.period_s",
            default=idle_defaults.get("period_s", 2.4),
            minimum=0.2,
        ),
    )

    sfx_raw = parser.obj(raw.get("sfx", _MISSING), f"{where}.sfx")
    sfx_file: str | None = None
    sfx_volume = 0.5
    if sfx_raw is None:
        sfx_file = defaults.get("sfx")
    else:
        sfx_file = parser.string(
            sfx_raw.get("file", _MISSING),
            f"{where}.sfx.file",
            default=defaults.get("sfx"),
        )
        sfx_volume = parser.number(
            sfx_raw.get("volume", _MISSING),
            f"{where}.sfx.volume",
            default=0.5,
            minimum=0.0,
            maximum=MAX_SFX_VOLUME,
        )

    start = parser.integer(
        raw.get("start_offset_ms", _MISSING),
        f"{where}.start_offset_ms",
        default=0,
        minimum=0,
    )
    end = parser.integer(
        raw.get("end_offset_ms", _MISSING),
        f"{where}.end_offset_ms",
        minimum=0,
    )
    if end is not None and start is not None and end <= start:
        parser.fail(
            f"{where}.end_offset_ms",
            f"must be greater than start_offset_ms ({start}), got {end}",
        )

    return CharacterOverlay(
        image_file=image_file,
        preset=preset or "none",
        x=parser.number(
            raw.get("x", _MISSING),
            f"{where}.x",
            default=0.5,
            minimum=0.0,
            maximum=1.0,
        ),
        y=parser.number(
            raw.get("y", _MISSING),
            f"{where}.y",
            default=0.9,
            minimum=-0.5,
            maximum=1.0,
        ),
        height=parser.number(
            raw.get("height", _MISSING),
            f"{where}.height",
            default=0.45,
            minimum=MIN_CHARACTER_HEIGHT_FRACTION,
            maximum=MAX_CHARACTER_HEIGHT_FRACTION,
        ),
        flip=parser.boolean(
            raw.get("flip", _MISSING), f"{where}.flip", default=False
        ),
        at_sentence=parser.integer(
            raw.get("at_sentence", _MISSING),
            f"{where}.at_sentence",
            minimum=1,
        ),
        start_offset_ms=start,
        end_offset_ms=end,
        for_sentences=parser.integer(
            raw.get("for_sentences", _MISSING),
            f"{where}.for_sentences",
            minimum=1,
        ),
        enter=enter,
        idle=idle,
        exit=exit_motion,
        sfx=sfx_file,
        sfx_volume=sfx_volume,
    )


def _parse_impact(parser: _Parser, data: Any, where: str) -> Impact:
    raw = parser.obj(data, where) or {}
    return Impact(
        enabled=parser.boolean(
            raw.get("enabled", _MISSING), f"{where}.enabled", default=True
        ),
        at_sentence=parser.integer(
            raw.get("at_sentence", _MISSING),
            f"{where}.at_sentence",
            minimum=1,
        ),
        at_offset_ms=parser.integer(
            raw.get("at_offset_ms", _MISSING),
            f"{where}.at_offset_ms",
            default=0,
            minimum=0,
        ),
        intensity=parser.number(
            raw.get("intensity", _MISSING),
            f"{where}.intensity",
            default=0.08,
            minimum=0.0,
            maximum=MAX_IMPACT_INTENSITY,
        ),
        shake_px=parser.integer(
            raw.get("shake_px", _MISSING),
            f"{where}.shake_px",
            default=8,
            minimum=0,
            maximum=MAX_IMPACT_SHAKE_PX,
        ),
        duration_ms=parser.integer(
            raw.get("duration_ms", _MISSING),
            f"{where}.duration_ms",
            default=450,
            minimum=50,
        ),
        flash=parser.enum(
            raw.get("flash", _MISSING),
            f"{where}.flash",
            CHARACTER_FLASHES,
            default="none",
        ),
    )


def _parse_scene(
    parser: _Parser, data: Any, index: int
) -> Scene | None:
    where = f"scenes[{index}]"
    raw = parser.obj(data, where)
    if raw is None:
        return None

    scene_id = parser.integer(
        parser.required(raw, "id", where), f"{where}.id", minimum=1
    )
    text = parser.string(
        parser.required(raw, "text", where), f"{where}.text"
    )

    image_file = parser.string(
        raw.get("image_file", _MISSING), f"{where}.image_file"
    )
    image_prompt = parser.string(
        raw.get("image_prompt", _MISSING), f"{where}.image_prompt"
    )
    if image_file is None and image_prompt is None:
        parser.fail(
            where,
            "needs 'image_file' (must exist on disk) or 'image_prompt' "
            "(image to be generated)",
        )

    if scene_id is None or text is None:
        return None

    overlays: list[TextOverlay] = []
    for overlay_index, overlay_data in enumerate(
        parser.array(raw.get("text_overlays", _MISSING), f"{where}.text_overlays")
        or []
    ):
        overlay = _parse_text_overlay(
            parser, overlay_data, f"{where}.text_overlays[{overlay_index}]"
        )
        if overlay is not None:
            overlays.append(overlay)

    sfx_list: list[SFX] = []
    for sfx_index, sfx_data in enumerate(
        parser.array(raw.get("sfx", _MISSING), f"{where}.sfx") or []
    ):
        sfx_item = _parse_sfx(parser, sfx_data, f"{where}.sfx[{sfx_index}]")
        if sfx_item is not None:
            sfx_list.append(sfx_item)

    characters: list[CharacterOverlay] = []
    for character_index, character_data in enumerate(
        parser.array(raw.get("characters", _MISSING), f"{where}.characters")
        or []
    ):
        character = _parse_character(
            parser, character_data, f"{where}.characters[{character_index}]"
        )
        if character is not None:
            characters.append(character)

    return Scene(
        id=scene_id,
        text=text,
        image_file=image_file,
        image_prompt=image_prompt,
        pause_after_ms=parser.integer(
            raw.get("pause_after_ms", _MISSING),
            f"{where}.pause_after_ms",
            minimum=0,
            maximum=MAX_PAUSE_AFTER_MS,
        ),
        transition_in=_parse_transition(
            parser, raw.get("transition_in", _MISSING), f"{where}.transition_in"
        ),
        ken_burns=_parse_ken_burns(
            parser, raw.get("ken_burns", _MISSING), f"{where}.ken_burns"
        ),
        text_overlays=tuple(overlays),
        sfx=tuple(sfx_list),
        characters=tuple(characters),
        # An absent `impact` block means no punch-in, so the parser is only
        # consulted when the key is actually there; otherwise every scene
        # would silently get one.
        impact=(
            _parse_impact(parser, impact_data, f"{where}.impact")
            if (impact_data := raw.get("impact", _MISSING)) is not _MISSING
            and impact_data is not None
            else None
        ),
    )


def _parse_video_metadata(
    parser: _Parser, data: Any
) -> VideoMetadata | None:
    where = "video_metadata"
    raw = parser.obj(data, where)
    if raw is None:
        parser.fail(where, "missing required object")
        return None

    resolution = parser.string(
        parser.required(raw, "resolution", where), f"{where}.resolution"
    )
    if resolution is not None and not RESOLUTION_PATTERN.match(resolution):
        parser.fail(
            f"{where}.resolution",
            f"expected 'WIDTHxHEIGHT' (e.g. '1920x1080'), got '{resolution}'",
        )

    title = parser.string(
        parser.required(raw, "title", where), f"{where}.title"
    )
    if None in (resolution, title):
        return None

    target_minutes = _parse_target_minutes(parser, raw, where)

    return VideoMetadata(
        title=title,
        author=parser.string(
            raw.get("author", _MISSING), f"{where}.author", default=""
        ),
        resolution=resolution,
        fps=parser.integer(
            parser.required(raw, "fps", where), f"{where}.fps", minimum=1
        ),
        language=parser.string(
            raw.get("language", _MISSING), f"{where}.language", default="vi"
        ),
        target_minutes=target_minutes,
    )


def _parse_target_minutes(
    parser: _Parser, raw: dict, where: str
) -> tuple[float, float]:
    """Optional [min, max] runtime envelope, in minutes."""
    value = raw.get("target_minutes", _MISSING)
    if value is _MISSING or value is None:
        return (8.0, 20.0)

    ok = (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in value)
        and value[0] >= 0
        and value[0] <= value[1]
    )
    if not ok:
        parser.fail(
            f"{where}.target_minutes",
            "expected [min_minutes, max_minutes] with 0 <= min <= max",
        )
        return (8.0, 20.0)
    return (float(value[0]), float(value[1]))


def _parse_tts_config(parser: _Parser, data: Any) -> TTSConfig:
    where = "tts_config"
    raw = parser.obj(data, where) or {}
    voice = parser.string(
        parser.required(raw, "voice", where), f"{where}.voice"
    )
    return TTSConfig(
        engine=parser.enum(
            raw.get("engine", _MISSING),
            f"{where}.engine",
            TTS_ENGINES,
            default="vieneu",
        ),
        voice=voice or "",
        speed=parser.number(
            raw.get("speed", _MISSING),
            f"{where}.speed",
            default=1.0,
            minimum=MIN_TTS_SPEED,
            maximum=MAX_TTS_SPEED,
        ),
        pitch=parser.number(
            raw.get("pitch", _MISSING), f"{where}.pitch", default=0.0
        ),
        emotion=parser.string(
            raw.get("emotion", _MISSING), f"{where}.emotion", default="neutral"
        ),
        granularity=parser.enum(
            raw.get("granularity", _MISSING),
            f"{where}.granularity",
            TTS_GRANULARITIES,
            default="sentence",
        ),
        max_chars_per_chunk=parser.integer(
            raw.get("max_chars_per_chunk", _MISSING),
            f"{where}.max_chars_per_chunk",
            default=1000,
            minimum=80,
        ),
    )


def _parse_audio_config(parser: _Parser, data: Any) -> AudioConfig:
    where = "audio_config"
    raw = parser.obj(data, where) or {}
    return AudioConfig(
        background_music=parser.string(
            raw.get("background_music", _MISSING), f"{where}.background_music"
        ),
        background_volume=parser.number(
            raw.get("background_volume", _MISSING),
            f"{where}.background_volume",
            default=0.12,
            minimum=0.0,
            maximum=1.0,
        ),
        master_volume=parser.number(
            raw.get("master_volume", _MISSING),
            f"{where}.master_volume",
            default=-14.0,
            minimum=-70.0,
            maximum=0.0,
        ),
        fade_in_seconds=parser.number(
            raw.get("fade_in_seconds", _MISSING),
            f"{where}.fade_in_seconds",
            default=2.0,
            minimum=0.0,
        ),
        fade_out_seconds=parser.number(
            raw.get("fade_out_seconds", _MISSING),
            f"{where}.fade_out_seconds",
            default=3.0,
            minimum=0.0,
        ),
        ducking=bool(
            parser.boolean(
                raw.get("ducking", _MISSING), f"{where}.ducking", default=True
            )
        ),
    )


def _parse_pacing_config(parser: _Parser, data: Any) -> PacingConfig:
    where = "pacing"
    raw = parser.obj(data, where) or {}

    auto_where = f"{where}.auto_pause"
    auto_raw = parser.obj(raw.get("auto_pause", _MISSING), auto_where) or {}

    min_pause = parser.integer(
        auto_raw.get("min_pause_ms", _MISSING),
        f"{auto_where}.min_pause_ms",
        default=300,
        minimum=0,
    )
    max_pause = parser.integer(
        auto_raw.get("max_pause_ms", _MISSING),
        f"{auto_where}.max_pause_ms",
        default=5000,
        minimum=0,
    )
    if (
        min_pause is not None
        and max_pause is not None
        and max_pause < min_pause
    ):
        parser.fail(
            f"{auto_where}.max_pause_ms",
            f"must be >= min_pause_ms ({min_pause}), got {max_pause}",
        )

    section_breaks: list[int] = []
    for break_index, value in enumerate(
        parser.array(
            raw.get("section_breaks", _MISSING), f"{where}.section_breaks"
        )
        or []
    ):
        parsed = parser.integer(
            value, f"{where}.section_breaks[{break_index}]", minimum=1
        )
        if parsed is not None:
            section_breaks.append(parsed)

    custom_pauses: dict[int, int] = {}
    custom_where = f"{where}.custom_pauses"
    custom_raw = parser.obj(raw.get("custom_pauses", _MISSING), custom_where) or {}
    for key, value in custom_raw.items():
        try:
            scene_key = int(key)
        except (TypeError, ValueError):
            parser.fail(
                f"{custom_where}.{key}",
                "key must be a scene id (integer)",
            )
            continue
        parsed_pause = parser.integer(
            value,
            f"{custom_where}.{key}",
            minimum=0,
            maximum=MAX_PAUSE_AFTER_MS,
        )
        if parsed_pause is not None:
            custom_pauses[scene_key] = parsed_pause

    return PacingConfig(
        auto_pause=AutoPauseConfig(
            enabled=parser.boolean(
                auto_raw.get("enabled", _MISSING),
                f"{auto_where}.enabled",
                default=True,
            ),
            per_sentence=parser.boolean(
                auto_raw.get("per_sentence", _MISSING),
                f"{auto_where}.per_sentence",
                default=True,
            ),
            min_pause_ms=min_pause,
            max_pause_ms=max_pause,
            respect_tts_natural_pause=parser.boolean(
                auto_raw.get("respect_tts_natural_pause", _MISSING),
                f"{auto_where}.respect_tts_natural_pause",
                default=True,
            ),
        ),
        section_breaks=tuple(section_breaks),
        custom_pauses=custom_pauses,
    )


def parse_script(data: Any) -> Script:
    """
    Parse a raw dict into a validated `Script`.

    Raises `ScriptSchemaError` listing every structural problem found.
    """
    parser = _Parser()

    if not isinstance(data, dict):
        raise ScriptSchemaError(["root: expected a JSON object"])

    video_metadata = _parse_video_metadata(
        parser, data.get("video_metadata", _MISSING)
    )
    tts_config = _parse_tts_config(parser, data.get("tts_config", _MISSING))
    audio_config = _parse_audio_config(
        parser, data.get("audio_config", _MISSING)
    )
    pacing = _parse_pacing_config(parser, data.get("pacing", _MISSING))

    raw_scenes = parser.array(data.get("scenes", _MISSING), "scenes")
    scenes: list[Scene] = []
    for index, scene_data in enumerate(raw_scenes or []):
        scene = _parse_scene(parser, scene_data, index)
        if scene is not None:
            scenes.append(scene)

    if not scenes:
        parser.fail("scenes", "must contain at least one scene")

    if parser.errors:
        raise ScriptSchemaError(parser.errors)

    return Script(
        video_metadata=video_metadata,
        tts_config=tts_config,
        audio_config=audio_config,
        pacing=pacing,
        scenes=tuple(scenes),
    )


def load_script(path: Path) -> Script:
    """Read and parse a script.json file."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ScriptSchemaError([f"{path}: file not found"]) from error
    except OSError as error:
        raise ScriptSchemaError([f"{path}: {error}"]) from error

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ScriptSchemaError(
            [f"{path}: invalid JSON at line {error.lineno}, col {error.colno}: {error.msg}"]
        ) from error

    return parse_script(data)
