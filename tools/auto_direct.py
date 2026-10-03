#!/usr/bin/env python3
"""
Auto-director: stage the characters of a script so the author does not have to.

The part of script.json that repeats scene after scene -- where a character
stands, which way it faces, when it walks on and off -- is mechanical.  This
tool derives it from what only the author can know: which characters appear
in which scene.

    python3 tools/auto_direct.py projects/topics-002/script.json
    python3 tools/auto_direct.py projects/*/script.json --dry-run
    python3 tools/auto_direct.py projects/topics-002/script.json --reflow

What it does, per scene
-----------------------
* Resolves each character block through the registry
  (assets/characters/characters.json): the script names `"use": "my_host"`
  (or just the resting image) and the tool expands the frame set, the same
  merge `apply_character_registry.py` performs.
* Assigns x positions from free floor slots.  Two cues that stand in
  different slots are far enough apart that idle sway cannot make them
  read as one blob, which is the visual form of the overlap `validate`
  warns about (`characters_overlap`) -- caught here, before the render,
  with the fix already applied.
* Auto-flips a two-character stage into a face-off: the right-hand
  character mirrors to face the left-hand one, unless the author set
  `flip` themselves.
* Staggers a cast bigger than the stage: once every slot is taken the
  next character appears on its own sentence (`at_sentence` +
  `for_sentences` 1) instead of piling onto an occupied slot.

The tool only fills gaps.  A block with an explicit `x` keeps it (and
fences the slots left for everyone else); explicit `flip`, timing,
preset, sfx and pose choices always win.  `y`/`height` are never touched.

Modes
-----
default    fill gaps on the blocks the script already lists.
--reflow   drop each block's placement (`x`/`y`/timing) first and re-stage
           the whole cast, keeping non-placement overrides (preset, sfx,
           poses, talk).  For scripts staged by hand that now want the
           tool's layout instead.
--dry-run  print the planned staging like a shot list, write nothing.

Scenes sitting in a story frame get tightened slots so characters stay
inside the panel the frame draws; a scene can opt out per scene with
`"story_frame": false` exactly as the renderer expects.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
# Unconditional front insert.  The guarded idiom other tools use silently
# skips the insert because the editable install's .pth already put src at
# the *end* of sys.path -- behind the project root, where the repo's
# autovid.py entry point shadows the package when cwd is the repo root
# (unittest, IDE run buttons) and the import then explodes.
sys.path.insert(0, str(SRC_DIR))

from autovid.domain.sentences import count_sentences  # noqa: E402

REGISTRY_PATH = PROJECT_ROOT / "assets" / "characters" / "characters.json"

# Floor slots, as x fractions of the frame.  Two characters read as a
# conversation when they face each other with daylight between them; three
# is what a scene can hold before the validator calls the stage crowded.
SLOT_X = {"left": 0.30, "center": 0.50, "right": 0.72}
SLOT_ORDER = ("left", "center", "right")

# Story frames own most of the canvas for the art panel, so scene
# characters move in and tighten.  The defaults keep them inside the
# standard frame window (x 0.04 .. 0.70).
STORY_FRAME_SLOTS = {"left": 0.14, "center": 0.32, "right": 0.52}

# Two characters closer than this on the floor read as one blob once idle
# sway is added; the slot tables above keep every pair further apart.
MIN_SLOT_GAP = 0.18

# A staggered appearance lasts one sentence -- long enough to land the
# joke, short enough to hand the stage over.
STAGGER_FOR_SENTENCES = 1


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    """Read the character registry, skipping `_readme` blocks."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        name: entry
        for name, entry in raw.items()
        if not name.startswith("_") and isinstance(entry, dict)
    }


def resolve_registry_entry(
    character: dict, registry: dict
) -> tuple[str | None, dict]:
    """
    Find the registry entry a script character block names.

    `"use"` wins; otherwise the resting image is matched, exactly like
    apply_character_registry.py does.  Returns (key, entry); (None, {})
    when the block names nothing in the registry -- a standalone sprite
    that still gets staged, just without frame swaps to inject.
    """
    use = str(character.get("use") or "").strip()
    if use and use in registry:
        return use, registry[use]
    image = str(character.get("image_file") or "").strip()
    if image:
        for name, entry in registry.items():
            if entry.get("image_file") == image:
                return name, entry
    return None, {}


def merge_registry_entry(character: dict, entry: dict) -> dict:
    """Fill the registry's frame-set keys into a block; script keys win."""
    merged = dict(character)
    for key in ("talk", "poses", "auto_pose_s", "mouth", "idle"):
        if key not in merged and key in entry:
            merged[key] = copy.deepcopy(entry[key])
    if "talk_period_s" in entry and "talk" not in merged:
        merged["talk"] = copy.deepcopy(entry["talk"])
        if isinstance(merged["talk"], dict):
            merged["talk"].setdefault("period_s", entry["talk_period_s"])
    return merged


# --------------------------------------------------------------------------
# staging
# --------------------------------------------------------------------------


def _uses_story_frame(script: dict, scene: dict) -> bool:
    """A scene sits inside the story frame unless it opts out per scene."""
    if scene.get("story_frame") is False:
        return False
    frame = script.get("story_frame")
    return isinstance(frame, dict) and bool(
        frame.get("use") or frame.get("image_file")
    )


def slots_for(script: dict, scene: dict) -> dict:
    """The slot table a scene's floor uses."""
    return (
        STORY_FRAME_SLOTS
        if _uses_story_frame(script, scene)
        else SLOT_X
    )


def _label(character: dict, key: str | None) -> str:
    return key or str(character.get("image_file") or "?").split("/")[-1]


def stage_scene(
    script: dict,
    scene: dict,
    registry: dict,
    *,
    reflow: bool = False,
) -> tuple[dict, list[str]]:
    """
    Stage one scene's cast.  Returns (scene, notes).

    Notes are human-readable lines describing every decision, so a dry run
    can be reviewed like a shot list.
    """
    notes: list[str] = []
    raw_cast = scene.get("characters") or []
    characters = [dict(entry) for entry in raw_cast if isinstance(entry, dict)]
    if not characters:
        return scene, notes

    sentence_count = max(count_sentences(str(scene.get("text") or "")), 1)
    slots = slots_for(script, scene)

    if reflow:
        for character in characters:
            for key in (
                "x",
                "y",
                "at_sentence",
                "for_sentences",
                "start_offset_ms",
                "end_offset_ms",
            ):
                character.pop(key, None)
        notes.append(
            f"scene {scene.get('id')}: reflow -- placement re-derived, "
            "overrides kept"
        )

    # -- pass 1: expand the registry, respect what the author pinned ------
    expanded: list[tuple[dict, str | None]] = []
    for index, character in enumerate(characters):
        key, entry = resolve_registry_entry(character, registry)
        if entry:
            merged = merge_registry_entry(character, entry)
            if merged != character:
                notes.append(
                    f"scene {scene.get('id')} character {index} "
                    f"({_label(character, key)}): registry frame set applied"
                )
            character = merged
        expanded.append((character, key))

    # -- pass 2: assign slots ---------------------------------------------
    # x-pinned blocks keep their spot and fence the slots around them;
    # everyone else takes the first slot that keeps MIN_SLOT_GAP daylight.
    taken: list[float] = [
        float(character["x"])
        for character, _key in expanded
        if "x" in character
    ]
    free = [
        name
        for name in SLOT_ORDER
        if all(abs(slots[name] - other) >= MIN_SLOT_GAP for other in taken)
    ]

    assigned: dict[int, dict] = {}
    # A solo cast takes centre stage -- a single speaker parked on the left
    # slot reads as waiting for a partner who never comes.
    unplaced = [index for index, (character, _key) in enumerate(expanded) if "x" not in character]
    if len(unplaced) == 1 and len(taken) == 0 and "center" in free:
        free.remove("center")
        free.insert(0, "center")
    for index, (character, key) in enumerate(expanded):
        if "x" in character:
            continue
        plan = dict(character)
        if free:
            slot = free.pop(0)
            notes.append(
                f"scene {scene.get('id')} character {index} "
                f"({_label(plan, key)}) -> slot {slot} (x={slots[slot]})"
            )
        else:
            # The stage is full: this cue gets its own sentence instead of
            # an occupied slot.  Clamped into the scene's sentence count.
            slot = SLOT_ORDER[index % len(SLOT_ORDER)]
            plan["at_sentence"] = min(index + 1, sentence_count)
            plan["for_sentences"] = STAGGER_FOR_SENTENCES
            notes.append(
                f"scene {scene.get('id')} character {index} "
                f"({_label(plan, key)}) -> slot {slot} staggered to "
                f"sentence {plan['at_sentence']} (stage full)"
            )
        plan["x"] = slots[slot]
        taken.append(plan["x"])
        assigned[index] = plan

    # -- pass 3: face-off flip ---------------------------------------------
    # Exactly two characters on stage turn towards each other unless the
    # author flipped one deliberately.  Sprites drawn facing camera just
    # mirror, which is harmless.
    standing = sorted(
        assigned, key=lambda index: float(assigned[index]["x"])
    )
    if len(standing) == 2 and len(assigned) == len(expanded):
        left_index, right_index = standing
        left_plan = assigned[left_index]
        right_plan = assigned[right_index]
        flipped = []
        for index, plan in (
            (left_index, left_plan),
            (right_index, right_plan),
        ):
            if "flip" not in expanded[index][0]:
                plan["flip"] = index == right_index
                flipped.append(str(index))
        if flipped:
            notes.append(
                f"scene {scene.get('id')}: face-off flip on character "
                f"{' and '.join(flipped)}"
            )

    # -- write back ---------------------------------------------------------
    for index, (character, _key) in enumerate(expanded):
        characters[index] = assigned.get(index, character)
    scene["characters"] = characters
    return scene, notes


def direct_script(
    script: dict,
    registry: dict | None = None,
    *,
    reflow: bool = False,
) -> tuple[dict, list[str]]:
    """Stage every scene of a script dict.  Returns (script, notes)."""
    if registry is None:
        registry = load_registry()
    all_notes: list[str] = []
    for scene in script.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        _scene, notes = stage_scene(
            script, scene, registry, reflow=reflow
        )
        all_notes.extend(notes)
    return script, all_notes


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stage a script's characters automatically: registry frame "
            "sets, floor slots, face-off flip, sentence staggering."
        )
    )
    parser.add_argument(
        "scripts",
        nargs="+",
        type=Path,
        help="script.json file(s) to stage in place",
    )
    parser.add_argument(
        "--reflow",
        action="store_true",
        help=(
            "re-derive placement for every character block, keeping "
            "non-placement overrides (preset, sfx, poses, talk)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned staging without writing anything",
    )
    args = parser.parse_args()

    registry = load_registry()
    workspace = PROJECT_ROOT
    touched = 0
    for script_path in args.scripts:
        full = (
            script_path if script_path.is_absolute() else workspace / script_path
        )
        if not full.exists():
            print(f"SKIP {script_path}: not found", file=sys.stderr)
            continue
        raw = json.loads(full.read_text(encoding="utf-8"))
        directed, notes = direct_script(raw, registry, reflow=args.reflow)
        scenes = [
            scene
            for scene in directed.get("scenes") or []
            if isinstance(scene, dict)
        ]
        staged = sum(1 for scene in scenes if scene.get("characters"))
        mode = "reflow" if args.reflow else "fill"
        header = (
            f"{script_path}: {staged}/{len(scenes)} scene(s) with a cast, "
            f"mode={mode}"
        )
        if args.dry_run:
            print(f"DRY RUN {header}")
        else:
            full.write_text(
                json.dumps(directed, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"WROTE {header}")
        for note in notes:
            print(note)
        if not notes:
            print("  (nothing to decide -- casts absent or already placed)")
        touched += 1

    print(f"\n{touched} script(s) processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
