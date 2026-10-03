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
    DEFAULT_TALK_PERIOD_S,
    CharacterIdle,
    CharacterMouth,
    CharacterMotion,
    CharacterOverlay,
    HostLayoutConfig,
    Impact,
    Script,
)

# A pose or a talk frame shown for less than this reads as a flicker,
# not as a change of mind.
MIN_VARIANT_S = 0.12

# A pause no longer than this is the narrator breathing, not a stop: the
# mouth flap keeps going across it instead of snapping shut for a couple
# of frames.  It also has to stay above MIN_VARIANT_S, because the
# closed-mouth holds it leaves behind must be long enough to schedule.
MICRO_PAUSE_S = 0.15

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
    # Scene-local windows the sprite must stand down for.  Only the
    # persistent host carries these: a scene character landing in the
    # host's corner suppresses it instead of compositing underneath.  The
    # planner forwards them onto the base layers, whose enable expression
    # already knows how to subtract a window.
    hide_windows: tuple[tuple[float, float], ...] = ()
    # The mouth-patch spec, carried so the baker can composite the patch
    # without a second lookup: anchor/size are fractions of the trimmed
    # artwork, and the baker needs them at bake time, not at resolve time.
    mouth: "CharacterMouth | None" = None

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
            "hide_windows": [
                [round(s, 4), round(e, 4)] for s, e in self.hide_windows
            ],
            "mouth": (
                {
                    "images": len(self.mouth.images),
                    "anchor": [round(self.mouth.x, 4), round(self.mouth.y, 4)],
                    "size": [round(self.mouth.size[0], 4), round(self.mouth.size[1], 4)],
                    "period_s": round(self.mouth.period_s, 4),
                }
                if self.mouth is not None
                else None
            ),
        }


@dataclass(frozen=True)
class CharacterVariant:
    """
    One frame swap inside a resolved cue: an image over a window.

    `kind` says which machinery produced the window -- "pose" for a
    scripted stance change, "talk" for the body-swap mouth-flap cycle and
    "mouth" for a mouth-patch flap -- so the report can explain where an
    odd-looking swap came from.  `mouth_file` is set only for "mouth": it
    is the opaque patch composited *onto* the resting sprite for this
    window, never a replacement for it.  Every window is scene-local
    seconds, exactly like the cue's own `start_s`/`end_s`.
    """

    image_file: str
    start_s: float
    end_s: float
    kind: str
    flip: bool
    mouth_file: str | None = None
    # True for a slice that exists because the narration is *not* speaking:
    # the closed mouth held through a measured pause.  The renderer
    # composites it like any other swap; the flag only explains in a
    # report why a patch sat still for half a second.
    silence: bool = False

    def to_dict(self) -> dict:
        return {
            "image_file": self.image_file,
            "start_s": round(self.start_s, 4),
            "end_s": round(self.end_s, 4),
            "kind": self.kind,
            "flip": self.flip,
            "mouth_file": self.mouth_file,
            "silence": self.silence,
        }


@dataclass(frozen=True)
class CharacterPlan:
    """Every cue in the video, plus the things that had to be explained."""

    cues: dict[int, tuple[CharacterCue, ...]] = field(default_factory=dict)
    warnings: tuple[dict, ...] = ()
    variants: dict[int, dict[int, tuple[CharacterVariant, ...]]] = field(
        default_factory=dict
    )

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

    def variants_for(self, scene_id: int, cue_index: int) -> tuple[CharacterVariant, ...]:
        return self.variants.get(scene_id, {}).get(cue_index, ())


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


def _pose_windows(
    *,
    poses: tuple,
    talk_images: tuple[str, ...],
    auto_pose_s: float | None,
    talk_period_s: float,
    mouth: "CharacterMouth | None" = None,
    start_s: float = 0.0,
    end_s: float = 0.0,
    enter_s: float = 0.0,
    exit_s: float = 0.0,
    clip_s: float | None = None,
    windows: list[tuple[float, float]] | None = None,
    scene_id: int = 0,
    index: int = 0,
) -> tuple[tuple[CharacterVariant, ...], list[dict]]:
    """
    Every frame swap for one cue, with the reasons it landed there.

    Swaps live only in the *motion-free* zone: after the entrance has
    finished travelling and before the exit starts moving.  A pose that
    replaced the sprite mid-flight would composite two characters -- the
    moving one and the posed one -- so the zone is a hard boundary, not a
    preference.

    Poses with an explicit window come first; whatever they do not cover
    leaves room for the auto-rotation of the poses that stayed unanchored
    -- or, when none is left, of the talk frames themselves.

    A `mouth` block owns the speech: the flap tiles every stretch of the
    motion-free zone no pose occupies and holds the closed patch through
    the pauses.  Sentence-anchored poses keep their windows -- the body
    swaps while the flap covers the rest -- and the unanchored pose pool
    rotates through the pauses, where a stance change reads as listening
    rather than as talking.  Talk frames are not used beside a flap: the
    patch already is the mouth.
    """
    warnings: list[dict] = []
    windows = windows if windows is not None else []
    zone_start = start_s + max(enter_s, 0.0)
    zone_end = end_s - max(exit_s, 0.0)
    if clip_s is not None:
        zone_end = min(zone_end, clip_s)
    if zone_end - zone_start < MIN_VARIANT_S:
        return (), warnings

    def clamp(start: float, end: float) -> tuple[float, float] | None:
        start = max(start, zone_start)
        end = min(end, zone_end)
        if end - start < MIN_VARIANT_S:
            return None
        return start, end

    variants: list[CharacterVariant] = []
    covered: list[tuple[float, float]] = []
    dropped_images: set[str] = set()
    # Unanchored poses: rotation-pool members with no window of their own.
    pool: list[str] = []

    for pose in poses:
        pose_start: float | None = None
        pose_end: float | None = None
        anchored = False

        if pose.from_sentence is not None and windows:
            first = pose.from_sentence - 1
            last = (pose.to_sentence or pose.from_sentence) - 1
            if first < len(windows):
                pose_start = windows[first][0]
                pose_end = windows[min(last, len(windows) - 1)][1]
                anchored = True
            else:
                warnings.append(
                    {
                        "code": "character_pose_sentence_missing",
                        "message": (
                            f"character {index} asks for a pose at sentence "
                            f"{pose.from_sentence} but the scene narrates only "
                            f"{len(windows)} sentence(s); its offset was used "
                            "instead"
                        ),
                        "scene_id": scene_id,
                    }
                )
        elif pose.from_sentence is not None and not windows:
            warnings.append(
                {
                    "code": "character_timing_unmeasured",
                    "message": (
                        f"character {index} anchors a pose to sentence "
                        f"{pose.from_sentence} but the tts/pacing reports are "
                        "not available; its offset was used and will not "
                        "track the voice"
                    ),
                    "scene_id": scene_id,
                }
            )

        if pose_start is None:
            pose_start = start_s + pose.start_offset_ms / 1000.0
        if pose_end is None:
            if pose.end_offset_ms is not None:
                pose_end = start_s + pose.end_offset_ms / 1000.0
            elif not anchored:
                # An unanchored pose with no end is a rotation-pool member,
                # not a window of its own: giving each one "until the next
                # sentence" would overlap its siblings and itself.
                pool.append(pose.image_file)
                continue
            else:
                # A sentence-anchored pose the reports could not measure
                # holds from its offset to the end of the cue.
                pose_end = end_s
            if pose_end <= pose_start:
                pose_end = pose_start + MIN_VARIANT_S

        clamped = clamp(pose_start, pose_end)
        if clamped is None:
            dropped_images.add(pose.image_file)
            warnings.append(
                {
                    "code": "character_pose_dropped",
                    "message": (
                        f"character {index} pose ({pose.image_file}) lands "
                        "outside its cue and was dropped"
                    ),
                    "scene_id": scene_id,
                }
            )
            continue

        variants.append(
            CharacterVariant(
                image_file=pose.image_file,
                start_s=clamped[0],
                end_s=clamped[1],
                kind="pose",
                flip=pose.flip,
            )
        )
        covered.append(clamped)

    # Auto-rotation fills whatever the scripted poses left open.  It only
    # rotates something: the poses that have no window of their own.  A
    # single unanchored pose cannot rotate (nothing to alternate with), so
    # the rotation falls back to the talk frames -- which keeps the resting
    # look as the character's identity and uses the cadence the author
    # already chose for speaking.  The unused-pose warning explains how to
    # put a lone pose to work instead.
    if mouth is not None and mouth.images:
        # The flap and the poses share the cue instead of one excluding the
        # other.  The unanchored pose pool claims the pauses first -- a
        # stance change while nobody speaks reads as listening -- then the
        # flap tiles everything left: cycling the patches inside the speech
        # segments and holding the closed patch (`images[0]`) through the
        # pauses the poses did not take.  Slice boundaries align with the
        # speech segments themselves, so a pause is never covered by a
        # flapping slice, and the schedule stays contiguous.
        period = max(mouth.period_s, 2 * MIN_VARIANT_S)
        closed_patch = mouth.images[0]
        speech = _speech_segments(windows, zone_start, zone_end)
        occupied = list(covered)

        if auto_pose_s is not None and pool:
            pose_period = max(auto_pose_s, 2 * MIN_VARIANT_S)
            slot = 0
            for pause_start, pause_end in _subtract_windows(
                covered + speech, zone_start, zone_end
            ):
                cursor = pause_start
                while cursor < pause_end - MIN_VARIANT_S:
                    slice_end = min(cursor + pose_period, pause_end)
                    if slice_end - cursor >= MIN_VARIANT_S:
                        variants.append(
                            CharacterVariant(
                                image_file=pool[slot % len(pool)],
                                start_s=cursor,
                                end_s=slice_end,
                                kind="pose",
                                flip=False,
                            )
                        )
                        occupied.append((cursor, slice_end))
                        slot += 1
                    cursor = slice_end

        # The flap follows the voice: cycle the patches through the speech
        # no pose occupies.
        for seg_start, seg_end in speech:
            for free_start, free_end in _subtract_windows(
                occupied, seg_start, seg_end
            ):
                cursor = free_start
                slot = 0
                while cursor < free_end - MIN_VARIANT_S:
                    slice_end = min(cursor + period, free_end)
                    if slice_end - cursor >= MIN_VARIANT_S:
                        variants.append(
                            CharacterVariant(
                                image_file="",
                                start_s=cursor,
                                end_s=slice_end,
                                kind="mouth",
                                flip=False,
                                mouth_file=mouth.images[slot % len(mouth.images)],
                                silence=False,
                            )
                        )
                        slot += 1
                    cursor = slice_end

        # The pauses the poses left: the closed patch holds, so the mouth
        # stays shut while the narrator breathes.  A hold is one variant
        # per contiguous gap -- the patch never animates, so slicing it on
        # the flap period would only bake identical frames.
        for pause_start, pause_end in _subtract_windows(
            occupied + speech, zone_start, zone_end
        ):
            if pause_end - pause_start < MIN_VARIANT_S:
                continue
            variants.append(
                CharacterVariant(
                    image_file="",
                    start_s=pause_start,
                    end_s=pause_end,
                    kind="mouth",
                    flip=False,
                    mouth_file=closed_patch,
                    silence=True,
                )
            )

    elif auto_pose_s is not None:
        pool_kinds = ["pose"] * len(pool)
        if len(pool) < 2 and len(talk_images) >= 2:
            pool = list(talk_images)
            pool_kinds = ["talk"] * len(pool)

        if len(pool) >= 2:
            # The rotation only fills the motion-free zone: an entrance or
            # an exit needs the resting sprite on screen the whole time it
            # is moving, and a swap there would composite two characters.
            gaps = _subtract_windows(covered, zone_start, zone_end)
            period = max(auto_pose_s, 2 * MIN_VARIANT_S)
            # A talk-frame pool is a mouth in disguise, so it follows the
            # voice like the mouth flap does; a pose pool is a stance, and
            # a stance may legitimately outlive a pause.
            follows_voice = "talk" in pool_kinds
            for gap_start, gap_end in gaps:
                bounds = (
                    _speech_segments(windows, gap_start, gap_end)
                    if follows_voice
                    else [(gap_start, gap_end)]
                )
                for bound_start, bound_end in bounds:
                    cursor = bound_start
                    slot = 0
                    while cursor < bound_end - MIN_VARIANT_S:
                        slice_end = min(cursor + period, bound_end)
                        if slice_end - cursor >= MIN_VARIANT_S:
                            variants.append(
                                CharacterVariant(
                                    image_file=pool[slot % len(pool)],
                                    start_s=cursor,
                                    end_s=slice_end,
                                    kind=pool_kinds[slot % len(pool_kinds)],
                                    flip=False,
                                )
                            )
                            slot += 1
                        cursor = slice_end

    elif len(poses) == 0 and talk_images:
        # No auto-rotation and no poses: the talk frames cycle on the mouth
        # cadence, inside the motion-free zone *and* inside the speech.
        # Between sentences nothing is scheduled, so the resting look holds
        # while the narrator breathes.
        period = max(talk_period_s, 2 * MIN_VARIANT_S)
        for seg_start, seg_end in _speech_segments(windows, zone_start, zone_end):
            cursor = seg_start
            slot = 0
            while cursor < seg_end - MIN_VARIANT_S:
                slice_end = min(cursor + period, seg_end)
                if slice_end - cursor >= MIN_VARIANT_S:
                    variants.append(
                        CharacterVariant(
                            image_file=talk_images[slot % len(talk_images)],
                            start_s=cursor,
                            end_s=slice_end,
                            kind="talk",
                            flip=False,
                        )
                    )
                    slot += 1
                cursor = slice_end

    variants.sort(key=lambda v: (v.start_s, v.end_s))

    # A pose that never made it onto the screen is almost always a missing
    # `auto_pose_s` on a rotation-only pool, or a window that fell outside
    # the cue; say so instead of letting the artwork sit unused.
    shown = {v.image_file for v in variants if v.kind == "pose"}
    for pose in poses:
        if pose.image_file in shown or pose.image_file in dropped_images:
            continue
        warnings.append(
            {
                "code": "character_pose_unused",
                "message": (
                    f"character {index} pose ({pose.image_file}) was never "
                    "shown: give it a sentence anchor or an end_offset_ms, "
                    "or set auto_pose_s so the unanchored poses rotate"
                ),
                "scene_id": scene_id,
            }
        )

    return tuple(variants), warnings


def _subtract_windows(
    covered: list[tuple[float, float]], start_s: float, end_s: float
) -> list[tuple[float, float]]:
    """
    The parts of [start_s, end_s] no sentence-anchored pose occupies.

    Sorted, non-overlapping, and each at least `MIN_VARIANT_S` long, because
    a sliver shorter than that cannot be shown without flickering and the
    rotation simply stays on the resting frame there.
    """
    clipped = sorted(
        (max(s, start_s), min(e, end_s)) for s, e in covered
    )
    gaps: list[tuple[float, float]] = []
    cursor = start_s
    for s, e in clipped:
        if e <= cursor or s >= end_s:
            continue
        if s > cursor:
            gaps.append((cursor, min(s, end_s)))
        cursor = max(cursor, e)
    if cursor < end_s:
        gaps.append((cursor, end_s))
    return [
        (s, e)
        for s, e in gaps
        if e - s >= MIN_VARIANT_S
    ]


def _speech_segments(
    windows: list[tuple[float, float]],
    zone_start: float,
    zone_end: float,
) -> list[tuple[float, float]]:
    """
    The parts of [zone_start, zone_end] where the narration is speaking.

    Windows are stage 2's measured sentence spans, scene-local like the
    zone itself.  A pause no longer than `MICRO_PAUSE_S` is breath rather
    than a stop, so neighbours merge across it: a flap that closed for a
    couple of frames would read as a twitch, and a silence hold shorter
    than `MIN_VARIANT_S` could not be scheduled at all.  Every returned
    segment is therefore at least schedulable, and the gaps between them
    are where the closed mouth goes.

    With nothing measured at all the whole zone speaks -- the behaviour
    before the voice-follow gating existed -- so a cue rendered without
    stage-2 reports keeps flapping exactly as it used to.  But when speech
    *was* measured and none of it overlaps the zone (a cue parked in the
    trailing pause), the result is empty: the mouth stays closed.
    """
    if not windows:
        return [(zone_start, zone_end)]

    clipped = sorted(
        (max(start, zone_start), min(end, zone_end))
        for start, end in windows
        if min(end, zone_end) > max(start, zone_start)
    )
    merged: list[tuple[float, float]] = []
    for start, end in clipped:
        if merged and start - merged[-1][1] <= MICRO_PAUSE_S:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# The persistent host is composited *above* the scene characters (it is
# the last sprite in every scene's graph), so its cue index is the highest
# slot.  A scene's own cues number from 0.
def host_slot(scene_cue_count: int) -> int:
    """The cue index the injected host takes in a scene."""
    return scene_cue_count


# Horizontal half-spans for the host-overlap test, in frame fractions.  A
# cue is a standing character, so its box is roughly a third of its height
# wide; the host is a corner talking head.  The spans are generous on
# purpose: suppressing the host one beat too early reads far better than
# two heads overlapping in the corner.
HOST_HALF_SPAN = 0.08
CUE_HALF_SPAN = 0.1

# The storytelling-frame narrator is bigger than a corner host, and stands
# in the right-hand strip beside the frame, so its overlap span is wider.
STORY_HOST_HALF_SPAN = 0.15

# A cue counts as "inside the story frame" when its x span sits within the
# panel and its top clears the panel's top; its FEET may hang a little
# below the panel's bottom edge, because scene characters stand on the
# screen floor (y ~ 0.95) while the panel stops at ~0.94.  That much
# overshoot still reads as "standing in the frame" -- only a cue that
# sticks out sideways or upwards reaches into the narrator's strip.
FRAME_FOOT_SLACK = 0.08

# A scene cue whose window ends starts the host again this much early (and
# puts it down this much late): a narrator snapping back on the exact frame
# a guest leaves reads as a flicker.
SUPPRESSION_MARGIN_S = 0.06


def _host_suppression_windows(
    scene_cues: tuple[CharacterCue, ...],
    *,
    host_x: float,
    host_half_span: float = HOST_HALF_SPAN,
    frame_box: tuple[float, float, float, float] | None = None,
    margin_s: float = 0.0,
) -> tuple[tuple[float, float], ...]:
    """
    The slices of the scene the host must stand down for.

    A scene character whose x span overlaps the host's would composite
    underneath it -- two heads in one corner -- so the host switches off
    for exactly those cues and returns when they end.  The returned windows
    ride on the host cue's `hide_windows`, which the filtergraph already
    subtracts from the layer's enable, so no new plumbing is needed.

    With a storytelling frame active, `frame_box` is the frame's (x, y, w, h)
    in frame fractions: a cue that lands fully *inside* the panel is the
    story content the frame exists to show, so the narrator outside keeps
    talking through it -- suppression is for cues that stick out into the
    narrator's strip.  `margin_s` pads each window so the narrator eases
    back in instead of snapping on the exact frame a guest leaves.
    """
    host_left = host_x - host_half_span
    host_right = host_x + host_half_span
    windows: list[tuple[float, float]] = []
    for cue in scene_cues:
        if cue.dropped:
            continue
        left = cue.x - CUE_HALF_SPAN
        right = cue.x + CUE_HALF_SPAN
        if frame_box is not None:
            frame_x, frame_y, frame_w, frame_h = frame_box
            cue_top = cue.y - cue.height
            inside = (
                left >= frame_x
                and right <= frame_x + frame_w
                and cue_top >= frame_y
                and cue.y <= frame_y + frame_h + FRAME_FOOT_SLACK
            )
            if inside:
                continue
        if right > host_left and left < host_right:
            windows.append((cue.start_s, cue.end_s))
    merged: list[tuple[float, float]] = []
    for start, end in sorted(windows):
        if merged and start - merged[-1][1] <= 0.05:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    if margin_s > 0:
        merged = [
            (max(start - margin_s, 0.0), end + margin_s)
            for start, end in merged
        ]
    return tuple(merged)


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

    A `host_layout` turns into one more cue in *every* scene: the persistent
    presenter with no enter and no exit, whose only motion is its idle.  A
    scene character that would land under the host suppresses the host for
    its window instead of stacking underneath it.
    """
    windows = sentence_windows(tts_report, pacing_report)
    clip_seconds = _clip_seconds(timeline)

    cues: dict[int, tuple[CharacterCue, ...]] = {}
    variants_by_scene: dict[int, dict[int, tuple[CharacterVariant, ...]]] = {}
    warnings: list[dict] = []

    # The storytelling frame owns the host when it is on: its narrator
    # replaces host_layout's, and the layout carries the suppression rules
    # (guests fully inside the panel are the story; guests sticking out
    # suppress the narrator).  host_layout keeps its own corner semantics.
    host_config = script.host_layout
    frame_config = script.story_frame
    host_enabled = (
        host_config.enabled
        and host_config.image_file is not None
        and bool(script.scenes)
    )
    frame_enabled = (
        frame_config.enabled
        and frame_config.image_file is not None
        and bool(script.scenes)
    )
    if frame_enabled:
        host_enabled = frame_config.show_narrator
        if frame_config.show_narrator:
            host_config = HostLayoutConfig(
                enabled=True,
                use=frame_config.use,
                image_file=frame_config.image_file,
                x=frame_config.host_x,
                y=frame_config.host_y,
                height=frame_config.host_height,
                flip=frame_config.host_flip,
                idle=frame_config.idle,
                mouth=frame_config.mouth,
                poses=frame_config.poses,
                auto_pose_s=frame_config.auto_pose_s,
            )

    for scene in script.scenes:
        scene_frame_enabled = (
            frame_enabled and scene.story_frame_enabled is not False
        )
        scene_host_enabled = host_enabled and (
            not frame_enabled or scene_frame_enabled
        )

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
                    mouth=character.mouth,
                )
            )

            if not dropped:
                # Only a motion that actually moves (or fades) the sprite
                # reserves its time; a `none` enter/exit still carries its
                # default duration but reserves nothing.
                enter_freeze_s = (
                    enter.duration_ms / 1000.0 if enter.type != "none" else 0.0
                )
                exit_freeze_s = (
                    exit_motion.duration_ms / 1000.0
                    if exit_motion.type != "none"
                    else 0.0
                )
                cue_variants, variant_warnings = _pose_windows(
                    poses=character.poses,
                    talk_images=character.talk_images,
                    auto_pose_s=character.auto_pose_s,
                    talk_period_s=character.talk_period_s,
                    mouth=character.mouth,
                    start_s=start,
                    end_s=end,
                    enter_s=enter_freeze_s,
                    exit_s=exit_freeze_s,
                    clip_s=clip_s,
                    windows=windows.get(scene.id, []),
                    scene_id=scene.id,
                    index=index,
                )
                warnings.extend(variant_warnings)
                if cue_variants:
                    variants_by_scene.setdefault(scene.id, {})[index] = cue_variants

        # The persistent host joins every scene, last in composite order, so
        # it sits above the scene characters in the filtergraph.  Its window
        # is the whole scene; the only motion is its idle, which is the
        # point of the layout -- the host is always there, breathing.
        # A scene that already lists the host's own artwork is the author
        # choreographing that entrance by hand, so the injection stands
        # down there instead of stacking a second host.
        author_placed_host = bool(
            host_config.image_file
            and any(
                cue.image_file == host_config.image_file
                for cue in scene_cues
            )
        )
        if scene_host_enabled and not author_placed_host:
            clip_s = clip_seconds.get(scene.id)
            host_start = 0.0
            host_end = clip_s if clip_s is not None else 6.0
            host_cue = CharacterCue(
                scene_id=scene.id,
                index=host_slot(len(scene_cues)),
                image_file=host_config.image_file or "",
                start_s=host_start,
                end_s=host_end,
                x=host_config.x,
                y=host_config.y,
                height=host_config.height,
                flip=host_config.flip,
                enter=CharacterMotion(type="none", duration_ms=0, edge="bottom"),
                idle=host_config.idle,
                exit=CharacterMotion(type="none", duration_ms=0, edge="bottom"),
                sfx=None,
                sfx_volume=0.5,
                timing_source="host_layout",
                # The mouth spec must ride on the cue itself: the variants
                # carry only which patch shows when, while the anchor and
                # size live here -- without it the baker drops every mouth
                # variant and the narrator never moves its lips.
                mouth=host_config.mouth,
                note=(
                    "storytelling-frame narrator"
                    if scene_frame_enabled
                    else "persistent host"
                ),
                # A scene character standing in the host's corner (or poking
                # out of the story frame) suppresses the host for its
                # window: the hide rides on hide_windows, the same machinery
                # a pose swap uses to gate a base layer.
                hide_windows=(
                    _host_suppression_windows(
                        tuple(scene_cues),
                        host_x=host_config.x,
                        host_half_span=STORY_HOST_HALF_SPAN,
                        frame_box=(
                            frame_config.x,
                            frame_config.y,
                            frame_config.width,
                            frame_config.height,
                        ),
                        margin_s=SUPPRESSION_MARGIN_S,
                    )
                    if scene_frame_enabled
                    else _host_suppression_windows(
                        tuple(scene_cues), host_x=host_config.x
                    )
                ),
            )
            scene_cues.append(host_cue)
            cues[scene.id] = tuple(scene_cues)

            # The host's registry frames keep working in the chair: the
            # mouth flap follows the measured narration and the pose pool
            # rotates through the pauses, exactly like a per-scene
            # character.  Talk frames stay out -- beside a flap the patch
            # already is the mouth.
            host_variants, host_variant_warnings = _pose_windows(
                poses=host_config.poses,
                talk_images=(),
                auto_pose_s=host_config.auto_pose_s,
                talk_period_s=DEFAULT_TALK_PERIOD_S,
                mouth=host_config.mouth,
                start_s=host_start,
                end_s=host_end,
                enter_s=0.0,
                exit_s=0.0,
                clip_s=clip_s,
                windows=windows.get(scene.id, []),
                scene_id=scene.id,
                index=host_cue.index,
            )
            warnings.extend(host_variant_warnings)
            if host_variants:
                variants_by_scene.setdefault(scene.id, {})[host_cue.index] = (
                    host_variants
                )
        elif scene_cues:
            cues[scene.id] = tuple(scene_cues)

    return CharacterPlan(cues=cues, warnings=tuple(warnings), variants=variants_by_scene)


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
