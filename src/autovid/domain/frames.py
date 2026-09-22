"""
Frame-exact reconciliation: audio seconds -> video frames.

Two traps live in this conversion, and both are how "out of sync" videos
happen.

**Rounding.**  Audio is continuous, video is discrete.  Rounding each
scene's duration to whole frames accumulates error: 48 scenes at 30fps can
drift by up to 0.8s, eight times the tolerance this project is built to.
So boundaries are computed *cumulatively* instead — scene i's end frame is
`round(end_s * fps)` and its start frame is the previous scene's end frame.
The frames then tile the output with no gaps, and the frame counts sum to
`round(total_duration * fps)` exactly, whatever the individual errors are.

**Transitions.**  An `xfade` overlaps two clips, so a chain of them
shortens the video by the transition length at every boundary: ten 0.4s
dissolves would pull the video 4 seconds ahead of the voiceover.  Each
segment is therefore rendered `transition_frames` longer than its slot, so
the overlap consumes the extra instead of the timeline.  That restores the
total duration *and* keeps every scene's local time zero aligned with the
moment its narration starts — which is what the whole pipeline is for.
"""

from __future__ import annotations

from dataclasses import dataclass

# Schema transition name -> ffmpeg xfade transition name.
# "cut" is not a transition: it costs nothing and stays frame exact.
# Every name here was checked against `ffmpeg -h filter=xfade` on the
# bundled build, because an unknown transition name is rejected at render
# time rather than at parse time.
TRANSITION_XFADE_NAMES: dict[str, str] = {
    "fade": "fade",
    # ffmpeg's own "fast"/"slow" fades are six-frame and one-second ramps,
    # which is exactly the difference between a punchy beat and a sombre one.
    "fade_fast": "fadefast",
    "fade_slow": "fadeslow",
    "dissolve": "dissolve",
    "slide_left": "slideleft",
    "slide_right": "slideright",
    "slide_up": "slideup",
    "slide_down": "slidedown",
    "wipe_left": "wipeleft",
    "wipe_right": "wiperight",
    "wipe_up": "wipeup",
    "wipe_down": "wipedown",
    "zoom": "zoomin",
    # ffmpeg has no whip pan; smoothleft is the closest high-speed slide.
    "whip_pan": "smoothleft",
    "pixelize": "pixelize",
    "blur": "hblur",
    "circle_open": "circleopen",
    "circle_close": "circleclose",
    "squeeze_h": "squeezeh",
    "squeeze_v": "squeezev",
    "flash_white": "fadewhite",
    "flash_black": "fadeblack",
    "diag_tl": "diagtl",
    "diag_br": "diagbr",
}

CUT_TRANSITIONS = frozenset({"cut", "none", ""})

# A transition longer than this cannot be expressed by the schema anyway.
MAX_TRANSITION_SECONDS = 5.0


@dataclass(frozen=True)
class PlanWarning:
    code: str
    message: str
    scene_id: int | None = None


@dataclass(frozen=True)
class SceneFrames:
    """Frame-level timing for one scene."""

    scene_id: int
    index: int
    start_frame: int
    narration_frames: int
    pause_frames: int
    segment_frames: int
    transition_after: str | None
    transition_after_frames: int
    transition_requested_frames: int

    @property
    def narration_end_frame(self) -> int:
        return self.start_frame + self.narration_frames

    @property
    def end_frame(self) -> int:
        return self.narration_end_frame + self.pause_frames

    @property
    def transition_clamped(self) -> bool:
        return self.transition_after_frames < self.transition_requested_frames

    def to_dict(self, fps: int) -> dict:
        return {
            "id": self.scene_id,
            "index": self.index,
            "start_frame": self.start_frame,
            "narration_frames": self.narration_frames,
            "pause_frames": self.pause_frames,
            "end_frame": self.end_frame,
            "segment_frames": self.segment_frames,
            "segment_duration_s": round(self.segment_frames / fps, 4),
            "transition_after": self.transition_after,
            "transition_after_frames": self.transition_after_frames,
            "transition_after_s": round(self.transition_after_frames / fps, 4),
        }


@dataclass(frozen=True)
class FramePlan:
    fps: int
    width: int
    height: int
    total_frames: int
    total_duration_s: float
    scenes: tuple[SceneFrames, ...]
    warnings: tuple[PlanWarning, ...] = ()

    @property
    def has_transitions(self) -> bool:
        return any(scene.transition_after for scene in self.scenes)

    @property
    def rendered_frames(self) -> int:
        """Frames actually encoded, before xfade overlaps are removed."""
        return sum(scene.segment_frames for scene in self.scenes)

    @property
    def overlapped_frames(self) -> int:
        return sum(
            scene.transition_after_frames for scene in self.scenes
        )

    def transition_offsets(self) -> list[dict]:
        """
        The xfade chain: each entry starts a transition on a scene boundary.

        With the compensation above, the offset for the transition after
        scene i is simply that boundary, so the crossfade runs through the
        pause rather than over the narration.
        """
        chain = []
        for scene in self.scenes:
            if not scene.transition_after:
                continue
            chain.append(
                {
                    "scene_id": scene.scene_id,
                    "type": scene.transition_after,
                    "duration_s": round(
                        scene.transition_after_frames / self.fps, 4
                    ),
                    "offset_s": round(scene.end_frame / self.fps, 4),
                }
            )
        return chain

    def to_dict(self) -> dict:
        return {
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "total_frames": self.total_frames,
            "total_duration_s": round(self.total_duration_s, 4),
            "rendered_frames": self.rendered_frames,
            "overlapped_frames": self.overlapped_frames,
            "has_transitions": self.has_transitions,
            "transitions": self.transition_offsets(),
            "scenes": [scene.to_dict(self.fps) for scene in self.scenes],
        }


def _transition_for(entry: dict) -> tuple[str | None, dict]:
    payload = entry.get("transition_in") or {}
    raw_type = str(payload.get("type", "cut")).strip().lower()
    if raw_type in CUT_TRANSITIONS:
        return None, payload
    return TRANSITION_XFADE_NAMES.get(raw_type), payload


def build_frame_plan(
    timeline: dict, *, fps: int, width: int, height: int
) -> FramePlan:
    """
    Turn the stage-2 timeline into a frame-exact render plan.

    Raises `ValueError` when the timeline is unusable, because a bad plan
    silently produces a desynchronised video rather than an error.
    """
    entries = timeline.get("scenes") or []
    if not entries:
        raise ValueError("timeline.json has no scenes")
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")

    warnings: list[PlanWarning] = []

    def to_frame(seconds: float) -> int:
        return int(round(float(seconds) * fps))

    # Cumulative boundaries, so the rounding error never accumulates.
    boundaries: list[int] = []
    for entry in entries:
        boundaries.append(to_frame(entry["end_s"]))

    total_frames = to_frame(timeline.get("total_duration_s") or boundaries[-1])
    if total_frames <= 0:
        total_frames = boundaries[-1]

    boundaries[-1] = total_frames

    # Monotonic repair: a rounding hiccup must not produce a zero-length or
    # negative scene, which would crash the encoder.  Index 0 is included:
    # a first scene shorter than half a frame rounds its boundary to zero,
    # which would leave the window empty and the pause frames negative.
    for index in range(len(boundaries)):
        floor = boundaries[index - 1] if index > 0 else 0
        if boundaries[index] <= floor:
            boundaries[index] = floor + 1
            warnings.append(
                PlanWarning(
                    "scene_window_repaired",
                    f"scene {entries[index].get('id')} had a non-positive "
                    "frame window and was padded to one frame",
                    entries[index].get("id"),
                )
            )
    total_frames = boundaries[-1]

    scenes: list[SceneFrames] = []
    for index, entry in enumerate(entries):
        start = boundaries[index - 1] if index > 0 else 0
        end = boundaries[index]
        nominal = end - start

        narration_end = to_frame(
            float(entry["start_s"]) + float(entry["narration_s"])
        )
        narration_frames = narration_end - start
        # Keep narration inside its own window; the pause absorbs rounding.
        narration_frames = max(1, min(narration_frames, nominal))
        pause_frames = nominal - narration_frames

        scenes.append(
            SceneFrames(
                scene_id=int(entry["id"]),
                index=index,
                start_frame=start,
                narration_frames=narration_frames,
                pause_frames=pause_frames,
                segment_frames=nominal,
                transition_after=None,
                transition_after_frames=0,
                transition_requested_frames=0,
            )
        )

    # Transitions use the *incoming* transition of the next scene, and are
    # clamped to the pause so a crossfade never runs over narration.
    for index in range(len(scenes) - 1):
        name, payload = _transition_for(entries[index + 1])
        if name is None:
            continue

        requested = to_frame(min(float(payload.get("duration", 0.0)), MAX_TRANSITION_SECONDS))
        current = scenes[index]
        allowed = min(requested, current.pause_frames, current.narration_frames)
        # At least one frame, and never the whole pause if the pause is the
        # only thing that lets the scene read.
        allowed = max(1, allowed)

        if allowed < requested:
            warnings.append(
                PlanWarning(
                    "transition_clamped",
                    f"transition into scene {entries[index + 1].get('id')} was "
                    f"shortened from {requested} to {allowed} frame(s) so it "
                    "fits inside the pause",
                    current.scene_id,
                )
            )

        scenes[index] = SceneFrames(
            scene_id=current.scene_id,
            index=current.index,
            start_frame=current.start_frame,
            narration_frames=current.narration_frames,
            pause_frames=current.pause_frames,
            segment_frames=current.segment_frames + allowed,
            transition_after=name,
            transition_after_frames=allowed,
            transition_requested_frames=requested,
        )

    plan = FramePlan(
        fps=fps,
        width=width,
        height=height,
        total_frames=total_frames,
        total_duration_s=total_frames / fps,
        scenes=tuple(scenes),
        warnings=tuple(warnings),
    )

    # The invariant the whole design rests on: after the overlaps are
    # removed, the rendered frames are exactly the timeline's frames.
    if plan.rendered_frames - plan.overlapped_frames != plan.total_frames:
        raise ValueError(
            "frame plan does not reconcile: "
            f"{plan.rendered_frames} rendered - {plan.overlapped_frames} "
            f"overlapped != {plan.total_frames} total"
        )

    return plan
