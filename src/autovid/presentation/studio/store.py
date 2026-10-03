"""
In-memory editor state for the video studio, backed by script.json.

The UI never hands a half-built document to the pipeline: it edits this
store, and only `save()` writes a file.  Reads are tolerant -- a project
written by hand may be missing whole sections -- so an author can point the
studio at an existing script.json and carry on rather than starting over.
Writes go through `to_dict()`, which emits exactly the keys the schema
knows, so a round trip cannot invent fields the validator would reject.
"""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The keys the UI owns.  Anything else in the file (tts_config, audio_config,
# pacing) is preserved verbatim so opening a project in the studio and saving
# it back does not quietly drop settings the author made elsewhere.
PRESERVED_TOP_LEVEL = ("tts_config", "audio_config", "pacing")

DEFAULT_RESOLUTION = (1920, 1080)

# The schema requires a narration voice.  The studio does not edit audio,
# but it must not hand back a file the validator rejects, so a project
# written without one gets this default rather than an error the author
# has no way to fix from this window.
DEFAULT_TTS_CONFIG = {
    "engine": "vieneu",
    "voice": "Minh Quân Pro",
    "granularity": "sentence",
}


def _as_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


@dataclass
class Motion:
    """Ken Burns: a slow move over the still so it does not read as a freeze."""

    enabled: bool = True
    type: str = "zoom_in"
    start_scale: float = 1.0
    end_scale: float = 1.12

    @classmethod
    def from_dict(cls, data: Any) -> Motion:
        data = data if isinstance(data, dict) else {}
        enabled = data.get("enabled", True)
        kind = str(data.get("type") or "zoom_in")
        start = _as_float(data.get("start_scale"), 1.0)
        end = _as_float(data.get("end_scale"), 1.12)
        # A zoom that starts and ends at the same scale reads as a frozen
        # image, which the assembler warns about; nudging the end is kinder
        # than handing the author a silent no-op.  A pan is different: it
        # slides the window at a constant scale, so equal values are exactly
        # what it should be told.
        if enabled and kind == "zoom_in" and abs(end - start) < 1e-3:
            end = start + 0.12
        elif enabled and kind == "zoom_out" and abs(end - start) < 1e-3:
            end = max(0.5, start - 0.12)
        return cls(bool(enabled), kind, start, round(end, 4))

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "type": self.type,
            "start_scale": round(self.start_scale, 4),
            "end_scale": round(self.end_scale, 4),
        }

    def scale_at(self, progress: float) -> float:
        if not self.enabled or self.type == "none":
            return 1.0
        return self.start_scale + (self.end_scale - self.start_scale) * progress


@dataclass
class Transition:
    """How this scene arrives: the cut that opens the video."""

    type: str = "fade"
    duration: float = 0.6

    @classmethod
    def from_dict(cls, data: Any) -> Transition:
        data = data if isinstance(data, dict) else {}
        return cls(
            str(data.get("type") or "fade"),
            max(0.0, _as_float(data.get("duration"), 0.6)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "duration": round(self.duration, 3)}


@dataclass
class Placement:
    """One character on the stage: where it stands and how it moves."""

    image_file: str
    x: float = 0.5
    y: float = 0.86
    height: float = 0.3
    flip: bool = False
    enter_type: str = "none"
    enter_duration_ms: int = 0
    enter_from: str = "bottom"
    idle_type: str = "none"
    idle_amplitude_px: int = 4
    idle_period_s: float = 3.0
    exit_type: str = "none"
    exit_duration_ms: int = 300
    exit_to: str = "bottom"
    at_sentence: int | None = None
    for_sentences: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Placement:
        enter = data.get("enter") or {}
        idle = data.get("idle") or {}
        exit_ = data.get("exit") or {}
        at_sentence = data.get("at_sentence")
        for_sentences = data.get("for_sentences")
        return cls(
            image_file=str(data.get("image_file") or ""),
            x=_as_float(data.get("x"), 0.5),
            y=_as_float(data.get("y"), 0.86),
            height=_as_float(data.get("height"), 0.3),
            flip=bool(data.get("flip", False)),
            enter_type=str(enter.get("type") or "none"),
            enter_duration_ms=int(enter.get("duration_ms") or 0),
            enter_from=str(enter.get("from") or "bottom"),
            idle_type=str(idle.get("type") or "none"),
            idle_amplitude_px=int(idle.get("amplitude_px") or 4),
            idle_period_s=_as_float(idle.get("period_s"), 3.0),
            exit_type=str(exit_.get("type") or "none"),
            exit_duration_ms=int(exit_.get("duration_ms") or 300),
            exit_to=str(exit_.get("to") or "bottom"),
            at_sentence=int(at_sentence) if at_sentence is not None else None,
            for_sentences=(
                int(for_sentences) if for_sentences is not None else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "image_file": self.image_file,
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "height": round(self.height, 4),
        }
        if self.flip:
            data["flip"] = True
        if self.at_sentence is not None:
            data["at_sentence"] = self.at_sentence
        if self.for_sentences is not None:
            data["for_sentences"] = self.for_sentences
        data["enter"] = {"type": self.enter_type}
        if self.enter_type != "none":
            data["enter"]["duration_ms"] = self.enter_duration_ms
            if self.enter_type in ("slide_in", "fly_in"):
                data["enter"]["from"] = self.enter_from
        data["idle"] = {
            "type": self.idle_type,
            "amplitude_px": self.idle_amplitude_px,
            "period_s": round(self.idle_period_s, 2),
        }
        data["exit"] = {"type": self.exit_type}
        if self.exit_type != "none":
            data["exit"]["duration_ms"] = self.exit_duration_ms
            if self.exit_type in ("slide_out", "fly_out"):
                data["exit"]["to"] = self.exit_to
        return data


@dataclass
class StoryFrame:
    """The fixed panel that holds the picture, with a host beside it."""

    enabled: bool = False
    style: str = "border"
    use: str | None = None
    show_narrator: bool = True
    x: float = 0.02
    y: float = 0.1
    width: float = 0.58
    height: float = 0.8
    host_x: float = 0.78
    host_y: float = 0.985
    host_height: float = 0.4
    host_flip: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> StoryFrame:
        data = data if isinstance(data, dict) else {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            style=str(data.get("style") or "border"),
            use=data.get("use"),
            show_narrator=bool(data.get("show_narrator", True)),
            x=_as_float(data.get("x"), 0.02),
            y=_as_float(data.get("y"), 0.1),
            width=_as_float(data.get("width"), 0.58),
            height=_as_float(data.get("height"), 0.8),
            host_x=_as_float(data.get("host_x"), 0.78),
            host_y=_as_float(data.get("host_y"), 0.985),
            host_height=_as_float(data.get("host_height"), 0.4),
            host_flip=bool(data.get("host_flip", False)),
        )

    def to_dict(self) -> dict[str, Any] | None:
        """
        The `story_frame` block, or None when the frame is off.

        The schema requires the block to name a narrator (`use` or
        `image_file`) whenever it is present at all, so a project with the
        frame switched off writes no block rather than an empty one.
        """
        if not self.enabled:
            return None
        data: dict[str, Any] = {
            "enabled": True,
            "style": self.style,
            "show_narrator": self.show_narrator,
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "width": round(self.width, 4),
            "height": round(self.height, 4),
        }
        if self.use:
            data["use"] = self.use
        if self.show_narrator:
            data["host_x"] = round(self.host_x, 4)
            data["host_y"] = round(self.host_y, 4)
            data["host_height"] = round(self.host_height, 4)
            if self.host_flip:
                data["host_flip"] = True
        return data

    @property
    def right_edge(self) -> float:
        return self.x + self.width

    def host_footprint(self, width: int, height: int) -> tuple[float, float]:
        """
        The narrator's horizontal span in frame fractions.

        The host's width follows from its height and the sprite's aspect
        ratio, which the store does not know, so the caller supplies the
        measured ratio.  Used to warn when the panel leaves no clear strip.
        """
        return (self.host_x, self.host_height)


@dataclass
class TextOverlay:
    """
    A caption burned onto the picture: the scene's own title card.

    Position names a spot on the frame rather than a coordinate, so the
    overlay lands in the same place at every resolution.
    """

    text: str = ""
    font: str = "assets/fonts/handwriting.ttf"
    font_size: int = 72
    color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: int = 3
    position: str = "bottom"
    start_offset_ms: int = 0
    end_offset_ms: int = 4000
    animation: str = "fade_in"
    animation_duration_ms: int = 400

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextOverlay:
        start = int(data.get("start_offset_ms") or 0)
        end = int(data.get("end_offset_ms") or 0)
        return cls(
            text=str(data.get("text") or ""),
            font=str(data.get("font") or "assets/fonts/handwriting.ttf"),
            font_size=int(data.get("font_size") or 72),
            color=str(data.get("color") or "#FFFFFF"),
            stroke_color=str(data.get("stroke_color") or "#000000"),
            stroke_width=int(data.get("stroke_width") if data.get("stroke_width") is not None else 3),
            position=str(data.get("position") or "bottom"),
            start_offset_ms=start,
            # The schema requires end > start; a half-written overlay gets a
            # usable window rather than a validator error on save.
            end_offset_ms=end if end > start else start + 4000,
            animation=str(data.get("animation") or "fade_in"),
            animation_duration_ms=int(
                data.get("animation_duration_ms") or 400
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "font": self.font,
            "font_size": self.font_size,
            "color": self.color,
            "stroke_color": self.stroke_color,
            "stroke_width": self.stroke_width,
            "position": self.position,
            "start_offset_ms": self.start_offset_ms,
            "end_offset_ms": self.end_offset_ms,
            "animation": self.animation,
            "animation_duration_ms": self.animation_duration_ms,
        }


@dataclass
class Impact:
    """
    A punch-in on the whole scene: a zoom spike, a shake, a flash.

    Time is anchored either to a sentence or to a millisecond offset,
    whichever the author finds easier to place.
    """

    enabled: bool = False
    at_sentence: int | None = None
    at_offset_ms: int = 0
    intensity: float = 0.12
    shake_px: int = 0
    duration_ms: int = 450
    flash: str = "none"

    @classmethod
    def from_dict(cls, data: Any) -> Impact:
        """
        Parse the block, treating an absent one as "no punch".

        A script without an `impact` block means the scene has no punch, so
        a missing dict must not read as `enabled: true` -- that would fire a
        zoom spike into every scene the author never asked for.
        """
        if not isinstance(data, dict):
            return cls(enabled=False)
        at_sentence = data.get("at_sentence")
        return cls(
            enabled=bool(data.get("enabled", True)),
            at_sentence=int(at_sentence) if at_sentence is not None else None,
            at_offset_ms=int(data.get("at_offset_ms") or 0),
            intensity=_as_float(data.get("intensity"), 0.12),
            shake_px=int(data.get("shake_px") or 0),
            duration_ms=int(data.get("duration_ms") or 450),
            flash=str(data.get("flash") or "none"),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"enabled": self.enabled}
        if self.at_sentence is not None:
            data["at_sentence"] = self.at_sentence
        else:
            data["at_offset_ms"] = self.at_offset_ms
        data["intensity"] = round(self.intensity, 4)
        if self.shake_px:
            data["shake_px"] = self.shake_px
        data["duration_ms"] = self.duration_ms
        if self.flash and self.flash != "none":
            data["flash"] = self.flash
        return data


@dataclass
class Scene:
    """One shot: a background, a move, a transition and a cast."""

    id: int
    text: str = ""
    image_file: str = ""
    motion: Motion = field(default_factory=Motion)
    transition: Transition = field(default_factory=Transition)
    characters: list[Placement] = field(default_factory=list)
    overlays: list[TextOverlay] = field(default_factory=list)
    impact: Impact = field(default_factory=Impact)
    story_frame_enabled: bool | None = None
    pause_after_ms: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scene:
        placements = [
            Placement.from_dict(item)
            for item in data.get("characters") or []
            if isinstance(item, dict) and item.get("image_file")
        ]
        overlays = [
            TextOverlay.from_dict(item)
            for item in data.get("text_overlays") or []
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        frame_on = data.get("story_frame")
        return cls(
            id=int(data.get("id") or 0),
            text=str(data.get("text") or ""),
            image_file=str(data.get("image_file") or ""),
            motion=Motion.from_dict(data.get("ken_burns")),
            transition=Transition.from_dict(data.get("transition_in")),
            characters=placements,
            overlays=overlays,
            impact=Impact.from_dict(data.get("impact")),
            story_frame_enabled=(
                bool(frame_on) if isinstance(frame_on, bool) else None
            ),
            pause_after_ms=int(data.get("pause_after_ms") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "text": self.text,
            "image_file": self.image_file,
            "transition_in": self.transition.to_dict(),
            "ken_burns": self.motion.to_dict(),
            "characters": [item.to_dict() for item in self.characters],
        }
        if self.overlays:
            data["text_overlays"] = [item.to_dict() for item in self.overlays]
        if self.impact.enabled:
            data["impact"] = self.impact.to_dict()
        if self.pause_after_ms:
            data["pause_after_ms"] = self.pause_after_ms
        if self.story_frame_enabled is not None:
            data["story_frame"] = self.story_frame_enabled
        return data


@dataclass
class Project:
    """One explainer video: its scenes plus the look they share."""

    name: str
    path: Path
    title: str = "Video moi"
    author: str = ""
    resolution: tuple[int, int] = DEFAULT_RESOLUTION
    fps: int = 30
    language: str = "vi"
    target_minutes: tuple[float, float] = (8.0, 20.0)
    story_frame: StoryFrame = field(default_factory=StoryFrame)
    scenes: list[Scene] = field(default_factory=list)
    preserved: dict[str, Any] = field(default_factory=dict)
    dirty: bool = False

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """The script.json document, keys in the order the schema reads."""
        document: dict[str, Any] = {
            "video_metadata": {
                "title": self.title,
                "author": self.author,
                "resolution": f"{self.resolution[0]}x{self.resolution[1]}",
                "fps": self.fps,
                "language": self.language,
                "target_minutes": list(self.target_minutes),
            }
        }
        for key in PRESERVED_TOP_LEVEL:
            if key in self.preserved:
                document[key] = copy.deepcopy(self.preserved[key])
        frame = self.story_frame.to_dict()
        if frame is not None:
            document["story_frame"] = frame
        document["scenes"] = [scene.to_dict() for scene in self.scenes]
        return document

    def renumber(self) -> None:
        """Scene ids are 1..N and contiguous; the schema relies on that."""
        for index, scene in enumerate(self.scenes, start=1):
            scene.id = index

    def next_scene_id(self) -> int:
        return len(self.scenes) + 1

    def mark_dirty(self) -> None:
        self.dirty = True


class ProjectStore:
    """
    Loads and saves projects.

    `open` prefers the existing file, falling back to a blank project when
    the path does not exist yet -- which is what "New project" means.  Every
    `save` keeps the previous file as `<name>.bak`, so an author who edits
    a script in the studio can always get the hand-written one back.
    """

    @staticmethod
    def open(path: Path) -> Project:
        path = Path(path)
        if not path.exists():
            return ProjectStore.blank(path)

        raw = json.loads(path.read_text(encoding="utf-8"))
        metadata = raw.get("video_metadata") or {}
        resolution = ProjectStore._parse_resolution(
            metadata.get("resolution"), DEFAULT_RESOLUTION
        )
        target = metadata.get("target_minutes")
        if not (isinstance(target, (list, tuple)) and len(target) == 2):
            target = (8.0, 20.0)

        project = Project(
            name=ProjectStore._name_for(path),
            path=path,
            title=str(metadata.get("title") or path.stem),
            author=str(metadata.get("author") or ""),
            resolution=resolution,
            fps=int(metadata.get("fps") or 30),
            language=str(metadata.get("language") or "vi"),
            target_minutes=(
                _as_float(target[0], 8.0),
                _as_float(target[1], 20.0),
            ),
            story_frame=StoryFrame.from_dict(raw.get("story_frame")),
            preserved={
                key: raw[key] for key in PRESERVED_TOP_LEVEL if key in raw
            },
        )
        for item in raw.get("scenes") or []:
            if isinstance(item, dict):
                project.scenes.append(Scene.from_dict(item))
        if not isinstance(project.preserved.get("tts_config"), dict) or not project.preserved[
            "tts_config"
        ].get("voice"):
            project.preserved["tts_config"] = copy.deepcopy(DEFAULT_TTS_CONFIG)
        project.renumber()
        return project

    @staticmethod
    def blank(path: Path) -> Project:
        """
        A new project, seeded so the file is schema-valid the moment it is
        saved: the schema requires an author, a narration voice and non-empty
        scene text, and an author who has not written either yet should not
        be met by a validator error before they have typed anything.
        """
        path = Path(path)
        return Project(
            name=ProjectStore._name_for(path),
            path=path,
            title=path.stem,
            author="autovid studio",
            story_frame=StoryFrame(),
            scenes=[Scene(id=1, text="Viet noi dung phan trinh bay")],
            preserved={
                "tts_config": copy.deepcopy(DEFAULT_TTS_CONFIG),
                "audio_config": {"master_volume": -14.0},
            },
        )

    @staticmethod
    def _name_for(path: Path) -> str:
        """
        The project's display name: the workspace folder, not the file.

        Every workspace holds `script.json`, so naming projects after the
        file would leave a list of identical entries called "script".
        """
        folder = path.parent.name
        return folder if folder and folder != "." else path.stem

    @staticmethod
    def _parse_resolution(value: Any, fallback: tuple[int, int]) -> tuple[int, int]:
        if not isinstance(value, str) or "x" not in value.lower():
            return fallback
        left, _, right = value.lower().partition("x")
        try:
            width, height = int(left), int(right)
        except ValueError:
            return fallback
        if width <= 0 or height <= 0:
            return fallback
        return (width, height)

    @staticmethod
    def save(project: Project) -> Path:
        """Write the project, keeping the previous file as a `.bak`."""
        project.renumber()
        project.path.parent.mkdir(parents=True, exist_ok=True)
        if project.path.exists():
            shutil.copy2(project.path, project.path.with_suffix(".bak"))
        project.path.write_text(
            json.dumps(project.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        project.dirty = False
        return project.path