#!/usr/bin/env python3
"""
Apply a character registry to script.json files.

A registry declares one character's frame set once -- the resting image,
the mouth-flap frames, the poses and the rotation timer -- so a script only
has to name the character instead of repeating the same four image paths
in every scene:

    {
      "my_host": {
        "image_file": "assets/characters/my_host_01.png",
        "talk": ["assets/characters/my_host_02.png",
                 "assets/characters/my_host_03.png"],
        "poses": ["assets/characters/my_host_04.png"],
        "auto_pose_s": 2.5
      }
    }

    python3 tools/apply_character_registry.py \\
        --registry assets/characters/characters.json \\
        projects/topics-002/script.json

Every `characters[]` entry whose `image_file` matches the registry's
`image_file` (or names the registry key in `use`) is upgraded in place.
Explicit `talk`/`poses`/`auto_pose_s` already written in the script win --
the registry is a default, not a takeover.

The registry keeps working after the merge: `use` entries are left in the
JSON untouched, because the pipeline itself ignores unknown keys and the
tool can re-apply the latest registry to the same scripts at any time.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def find_workspace(start: Path) -> Path:
    """Walk up until a directory containing `autovid.py` shows up."""
    for candidate in (start, *start.parents):
        if (candidate / "autovid.py").exists():
            return candidate
    return start


def load_registry(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise SystemExit(f"{path}: expected a non-empty object of characters")
    for name, entry in raw.items():
        # Keys starting with an underscore are notes for humans, not
        # characters -- the registry ships with a `_readme` block.
        if name.startswith("_"):
            continue
        if not isinstance(entry, dict) or "image_file" not in entry:
            raise SystemExit(
                f"{path}: '{name}' needs at least an 'image_file'"
            )
    return raw


def apply_to_character(entry: dict, character: dict) -> bool:
    """Merge one registry entry into one script character block."""
    changed = False

    # An explicit block in the script wins over the registry default.
    for key in ("talk", "poses", "auto_pose_s", "mouth", "idle"):
        if key in character:
            continue
        if key in entry:
            character[key] = entry[key]
            changed = True

    if "talk_period_s" in entry and "talk" not in character:
        character.setdefault("talk", {})
        if isinstance(character["talk"], dict):
            character["talk"].setdefault("period_s", entry["talk_period_s"])
            changed = True

    return changed


def apply_to_script(script_path: Path, registry: dict, *, dry_run: bool) -> bool:
    raw = json.loads(script_path.read_text(encoding="utf-8"))
    changed = False

    for scene in raw.get("scenes") or []:
        for character in scene.get("characters") or []:
            image = character.get("image_file", "")
            use = character.get("use")

            entry = None
            if use and use in registry:
                entry = registry[use]
            else:
                for name, candidate in registry.items():
                    if name.startswith("_") or not isinstance(candidate, dict):
                        continue
                    if candidate.get("image_file") == image:
                        entry = candidate
                        break

            if entry is not None and apply_to_character(entry, character):
                changed = True

    if changed and not dry_run:
        script_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply a character registry (talk frames, poses, auto-rotation) "
            "to every matching character in the given script.json files."
        )
    )
    parser.add_argument(
        "scripts",
        nargs="+",
        type=Path,
        help="script.json file(s) to upgrade in place",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("assets/characters/characters.json"),
        help="registry file (default: assets/characters/characters.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing anything",
    )
    args = parser.parse_args()

    registry = load_registry(args.registry)
    workspace = find_workspace(Path(__file__).resolve().parent)

    touched = 0
    for script_path in args.scripts:
        full = script_path if script_path.is_absolute() else workspace / script_path
        if not full.exists():
            print(f"SKIP {script_path}: file not found", file=sys.stderr)
            continue
        if apply_to_script(full, registry, dry_run=args.dry_run):
            touched += 1
            verb = "would update" if args.dry_run else "updated"
            print(f"{verb}: {full}")
        else:
            print(f"no change: {full}")

    if args.dry_run:
        print(f"\n{touched} file(s) would change")
    else:
        print(f"\n{touched} file(s) updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
