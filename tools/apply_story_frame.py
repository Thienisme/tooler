#!/usr/bin/env python3
"""
Apply the storytelling-frame layout to a project's script.json.

Writes (or replaces) the top-level `story_frame` block:

    "story_frame": {
        "use": "story_host",
        "x": 0.04, "y": 0.06, "width": 0.66, "height": 0.88
    }

`--use` names a registry key (assets/characters/characters.json); the
narrator's artwork, mouth flap and poses ride in from there.  Explicit
keys in the block always win over the registry -- the same merge rule the
schema applies when it reads the script back.

    python3 tools/apply_story_frame.py projects/<id>/script.json \
        [--use story_host] [--style border|none] [--remove] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULTS = {
    "x": 0.04,
    "y": 0.06,
    "width": 0.66,
    "height": 0.88,
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path, help="path to script.json")
    parser.add_argument("--use", default="story_host",
                        help="registry key of the narrator")
    parser.add_argument("--style", choices=("border", "none"), default="border",
                        help="how the frame's edge is drawn")
    parser.add_argument("--remove", action="store_true",
                        help="strip the story_frame block instead")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the block without writing it")
    args = parser.parse_args()

    data = load(args.script)

    if args.remove:
        if "story_frame" in data:
            del data["story_frame"]
            if not args.dry_run:
                save(args.script, data)
            print("removed story_frame")
        else:
            print("no story_frame to remove")
        return

    block = {"use": args.use, "style": args.style, **DEFAULTS}
    if args.dry_run:
        print(json.dumps({"story_frame": block}, ensure_ascii=False, indent=2))
        return

    data["story_frame"] = block
    save(args.script, data)
    print(f"story_frame -> {args.script}: {json.dumps(block)}")


if __name__ == "__main__":
    sys.exit(main())
