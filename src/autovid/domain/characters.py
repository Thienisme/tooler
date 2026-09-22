"""
Character cues: turning a script block into a window on the real timeline.

A character cue is deliberately absolute by the time the pipeline touches
it.  The script is allowed to be vague -- "appear when the second sentence
starts, stay for three sentences" -- but a compositor cannot be: it needs
seconds relative to the clip, and those seconds depend on narration that
was only measured during stage 2.

So the resolution happens once, here, and both consumers use the result:

* stage 4 composites the sprite inside its window;
* stage 5 places the entrance/exit sound effects at the same instants.

If the two resolved the timing separately they would eventually disagree,
and the sound of a character landing would arrive in a different scene from
the character.

Sentence windows come from the stage-2 artifacts rather than from the
timeline, because `timeline.json` records only a *count* of units per
scene.  The arithmetic is the one the staging stage itself uses:

    narration = sum(unit durations) + sum(pauses except the scene's last)

which is why the pause after a sentence (and not the scene's trailing
pause) is the gap that separates sentence i from sentence i+1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autovid.domain.script import (
    CharacterIdle,
    CharacterMotion,
    CharacterOverlay,
    Impact,
    Script,
)

# A character that is on screen for less than this reads as a glitch rather
# than as a beat, so such a cue is dropped with an explanation instead.
MIN_ON_SCREEN_S = 0.4

# An entrance or exit squeezed below this is not a motion any more; the
# window is simply too short for the effect that was asked for.
MIN_MOTION_S = 0.12

# Entrances and departures never take more than this share of the window,
# so a long animation cannot swallow the moment it was meant to introduce.
MAX_MOTION_SHARE = 0.45

# Default share of the frame height a host occupies, matching the schema.
DEFAULT_HEIGHT_FRACTION = 0.45


@dataclass(frozen=True)
class CharacterCue:
    """One character, on one scene, with the timing already resolved."""

    scene_id: int
    index: int
    image_file: str
    start_s: float
    end_s: float
    x: float
    y: float
    height: float
    flip: bool
    enter: CharacterMotion
    idle: CharacterIdle
    exit: CharacterMotion
    sfx: str | None
    sfx_volume: float
    timing_source: str
    sentence_index: int | None = None
    dropped: bool = False
    note: str = ""

    @property
    def duration_s(self) -> float:
        return max(self.end_s - self.start_s, 0.0)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "image_file": self.image_file,
            "start_s": round(self.start_s, 4),
            "end_s": round(self.end_s, 4),
            "duration_s": round(self.duration_s, 4),
            "timing_source": self.timing_source,
            "sentence_index": self.sentence_index,
            "position": {
                "x": round(self.x, 4),
                "y": round(self.y, 4),
                "height": round(self.height, 4),
                "flip": self.flip,
            },
            "enter": {
                "type": self.enter.type,
                "edge": self.enter.edge,
                "duration_s": round(self.enter.duration_ms / 1000.0, 4),
            },
            "idle": {
                "type": self.idle.type,
                "amplitude_px": self.idle.amplitude_px,
                "period_s": self.idle.period_s,
            },
            "exit": {
                "type": self.exit.type,
                "edge": self.exit.edge,
                "duration_s": round(self.exit.duration_ms / 1000.0, 4),
            },
            "sfx": self.sfx,
            "dropped": self.dropped,
            "note": self.note,
        }


@dataclass(frozen=True)
class CharacterPlan:
    """Every cue in the video, plus the things that had to be explained."""

    cues: dict[int, tuple[CharacterCue, ...]] = field(default_factory=dict)
    warnings: tuple[dict, ...] = ()

    @property
    def total(self) -> int:
        return sum(len(scene_cues) for scene_cues in self.cues.values())

    @property
    def active(self) -> int:
        return sum(
            1
            for scene_cues in self.cues.values()
            for cue in scene_cues
            if not cue.dropped
        )

    def for_scene(self, scene_id: int) -> tuple[CharacterCue, ...]:
        return self.cues.get(scene_id, ())


def sentence_windows(
    tts_report: dict | None, pacing_report: dict | None
) -> dict[int, list[tuple[float, float]]]:
    """
    Scene-local start/end seconds for every narrated sentence.

    Returns `{}` when the reports are missing, which is what makes a cue
    anchored to a sentence fall back to its millisecond offset instead of
    silently landing at zero.
    """
    if not isinstance(tts_report, dict):
        return {}

    pauses_by_scene: dict[int, list[float]] = {}
    if isinstance(pacing_report, dict):
        for scene in pacing_report.get("scenes") or []:
            if not isinstance(scene, dict):
                continue
            pauses_by_scene[int(scene.get("id", 0))] = [
                (sentence.get("pause_ms") or 0) / 1000.0
                for sentence in scene.get("sentences") or []
                if isinstance(sentence, dict)
            ]

    windows: dict[int, list[tuple[float, float]]] = {}
    for scene in tts_report.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        units = scene.get("units") or []
        pauses = pauses_by_scene.get(int(scene.get("id", 0)), [])

        cursor = 0.0
        scene_windows: list[tuple[float, float]] = []
        for position, unit in enumerate(units):
            duration = float(unit.get("duration_s") or 0.0)
            scene_windows.append((cursor, cursor + duration))
            pause = pauses[position] if position < len(pauses) else 0.0
            cursor += duration + pause

        if scene_windows:
            windows[int(scene.get("id", 0))] = scene_windows

    return windows


def _clip_seconds(timeline: dict | None) -> dict[int, float]:
    """How long each scene's clip runs, from stage 2's own timeline."""
    durations: dict[int, float] = {}
    if not isinstance(timeline, dict):
        return durations
    for scene in timeline.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        start = float(scene.get("start_s") or 0.0)
        end = float(scene.get("end_s") or 0.0)
        if end > start:
            durations[int(scene.get("id", 0))] = end - start
    return durations


def _resolve_window(
    character: CharacterOverlay,
    *,
    scene_id: int,
    index: int,
    clip_s: float | None,
    windows: list[tuple[float, float]],
) -> tuple[float, float, str, int | None, list[dict]]:
    """Scene-local [start, end] for one cue, with the reason it landed there."""
    warnings: list[dict] = []

    limit = clip_s if clip_s is not None else None
    start: float | None = None
    end: float | None = None
    source = "offset"
    sentence_index: int | None = None

    if character.at_sentence is not None:
        position = character.at_sentence - 1
        if not windows:
            # Nothing has been measured at all: the reports stage 2 writes
            # are missing, which is a different problem from a sentence
            # index that does not exist, and the fix is different too.
            source = "offset_fallback"
            warnings.append(
                {
                    "code": "character_timing_unmeasured",
                    "message": (
                        f"character {index} is anchored to sentence "
                        f"{character.at_sentence} but the tts/pacing reports "
                        "are not available; its offset was used and will not "
                        "track the voice"
                    ),
                    "scene_id": scene_id,
                }
            )
        elif position < len(windows):
            start = windows[position][0]
            source = "sentence"
            sentence_index = character.at_sentence
            if character.for_sentences is not None:
                last = min(position + character.for_sentences - 1, len(windows) - 1)
                end = windows[last][1]
        else:
            warnings.append(
                {
                    "code": "character_sentence_missing",
                    "message": (
                        f"character {index} asks to appear at sentence "
                        f"{character.at_sentence} but the scene narrates only "
                        f"{len(windows)} sentence(s); its offset was used "
                        "instead"
                    ),
                    "scene_id": scene_id,
                }
            )

    if start is None:
        start = character.start_offset_ms / 1000.0
    if end is None:
        if character.end_offset_ms is not None:
            end = character.end_offset_ms / 1000.0
        elif limit is not None:
            end = limit
        else:
            # Nothing to measure against: hold the cue for a plausible
            # default rather than for zero seconds.
            end = start + 4.0

    if limit is not None:
        start = min(start, max(limit - MIN_ON_SCREEN_S, 0.0))
        end = min(end, limit)
    start = max(start, 0.0)

    if end - start < MIN_ON_SCREEN_S:
        if limit is not None and limit >= MIN_ON_SCREEN_S:
            end = min(start + MIN_ON_SCREEN_S, limit)
        else:
            end = start + MIN_ON_SCREEN_S

    return start, end, source, sentence_index, warnings


def _fit_motion(motion: CharacterMotion, window_s: float, *, label: str,
                scene_id: int, index: int) -> tuple[CharacterMotion, list[dict]]:
    """Shrink an entrance/exit so it cannot eat the whole window."""
    warnings: list[dict] = []
    if motion.type == "none" or motion.duration_ms <= 0:
        return motion, warnings

    requested_s = motion.duration_ms / 1000.0
    allowed_s = max(window_s * MAX_MOTION_SHARE, MIN_MOTION_S)
    if requested_s <= allowed_s:
        return motion, warnings

    warnings.append(
        {
            "code": "character_motion_clamped",
            "message": (
                f"character {index} {label} was shortened from "
                f"{motion.duration_ms}ms to {allowed_s * 1000:.0f}ms so it "
                f"fits a {window_s:.2f}s cue"
            ),
            "scene_id": scene_id,
        }
    )
    return (
        CharacterMotion(
            type=motion.type,
            duration_ms=int(round(allowed_s * 1000)),
            edge=motion.edge,
        ),
        warnings,
    )


def resolve_character_cues(
    script: Script,
    *,
    timeline: dict | None = None,
    tts_report: dict | None = None,
    pacing_report: dict | None = None,
) -> CharacterPlan:
    """
    Resolve every character block into a compositable cue.

    Cues that cannot be placed at all (no usable window) are dropped and
    reported rather than rendered as a zero-frame flash.
    """
    windows = sentence_windows(tts_report, pacing_report)
    clip_seconds = _clip_seconds(timeline)

    cues: dict[int, tuple[CharacterCue, ...]] = {}
    warnings: list[dict] = []

    for scene in script.scenes:
        if not scene.characters:
            continue

        scene_cues: list[CharacterCue] = []
        for index, character in enumerate(scene.characters):
            clip_s = clip_seconds.get(scene.id)
            start, end, source, sentence_index, cue_warnings = _resolve_window(
                character,
                scene_id=scene.id,
                index=index,
                clip_s=clip_s,
                windows=windows.get(scene.id, []),
            )
            warnings.extend(cue_warnings)

            window_s = end - start
            enter, enter_warnings = _fit_motion(
                character.enter, window_s, label="entrance",
                scene_id=scene.id, index=index,
            )
            exit_motion, exit_warnings = _fit_motion(
                character.exit, window_s, label="exit",
                scene_id=scene.id, index=index,
            )
            warnings.extend(enter_warnings)
            warnings.extend(exit_warnings)

            note = (
                "no sentence timings from stage 2"
                if source == "offset_fallback"
                else ""
            )
            # A cue with no measurable window is dropped rather than drawn:
            # zero frames of a character is a flicker, not an appearance.
            dropped = window_s <= 0.0
            if dropped:
                note = note or "zero-length window"

            scene_cues.append(
                CharacterCue(
                    scene_id=scene.id,
                    index=index,
                    image_file=character.image_file,
                    start_s=start,
                    end_s=end,
                    x=character.x,
                    y=character.y,
                    height=character.height,
                    flip=character.flip,
                    enter=enter,
                    idle=character.idle,
                    exit=exit_motion,
                    sfx=character.sfx,
                    sfx_volume=character.sfx_volume,
                    timing_source=source,
                    sentence_index=sentence_index,
                    dropped=dropped,
                    note=note,
                )
            )

        if scene_cues:
            cues[scene.id] = tuple(scene_cues)

    return CharacterPlan(cues=cues, warnings=tuple(warnings))


def impact_offset_s(
    impact: Impact | None, *, windows: list[tuple[float, float]]
) -> float:
    """
    When a punch-in fires, in scene-local seconds.

    A punch-in is almost always on a word, so it is anchored to a sentence
    when one is given and to a millisecond offset otherwise.
    """
    if impact is None:
        return 0.0

    if impact.at_sentence is not None:
        position = impact.at_sentence - 1
        if 0 <= position < len(windows):
            return windows[position][0]
        if windows:
            return windows[-1][0]

    return impact.at_offset_ms / 1000.0


def character_sfx_cues(plan: CharacterPlan) -> list[dict]:
    """
    The sound each cue needs, ready for stage 5.

    An arrival is heard at landing, not at the start of its travel, so the
    effect is placed at the end of the entrance for a falling or spinning
    arrival and at the start for a slide or a fade.  ``edge`` decides.
    """
    placements: list[dict] = []
    for scene_cues in plan.cues.values():
        for cue in scene_cues:
            if cue.dropped or not cue.sfx:
                continue

            offset_s = cue.start_s
            if cue.enter.type in {"drop_bounce", "pop", "zoom_in", "spin_in"}:
                offset_s = cue.start_s + cue.enter.duration_ms / 1000.0

            placements.append(
                {
                    "scene_id": cue.scene_id,
                    "file": cue.sfx,
                    "volume": cue.sfx_volume,
                    "offset_s": max(offset_s, 0.0),
                    "source": f"character {cue.index}",
                    # Character slots number from the cue, scripted effects
                    # from the scene's own list; `source` is what tells the
                    # two apart in the report.
                    "offset_index": cue.index,
                }
            )

    return placements
