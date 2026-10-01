#!/usr/bin/env python3
"""
Turn the persistent-host layout on (or off) for whole scripts.

The host is one character injected into every scene -- the channel's
presenter, parked in a corner, breathing, flapping to the narration.  The
frame set comes from the character registry, so a script only names it:

    python3 tools/apply_host_layout.py projects/topics-002/script.json
    python3 tools/apply_host_layout.py projects/*/script.json --dry-run
    python3 tools/apply_host_layout.py projects/topics-002/script.json --remove

Equivalent JSON this writes:

    "host_layout": { "use": "my_host" }

Anything already under `host_layout` is replaced, so re-running with
different flags is idempotent.  Position/idle overrides belong in the
script afterwards -- the tool deliberately writes the smallest block that
expresses a decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

VALID_KEYS = {"use", "enabled", "image_file", "x", "y", "height", "flip", "idle"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Add (or remove) a persistent-host layout to script.json files: "
            "one presenter composited into every scene."
        )
    )
    parser.add_argument(
        "scripts",
        nargs="+",
        type=Path,
        help="script.json file(s) to update in place",
    )
    parser.add_argument(
        "--use",
        default="my_host",
        help="registry key of the host character (default: my_host)",
    )
    parser.add_argument(
        "--corner",
        choices=("right", "left"),
        default="right",
        help="which upper corner the host sits in (default: right)",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="delete the host_layout block instead",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing anything",
    )
    args = parser.parse_args()

    workspace = PROJECT_ROOT
    touched = 0
    for script_path in args.scripts:
        full = script_path if script_path.is_absolute() else workspace / script_path
        if not full.exists():
            print(f"SKIP {script_path}: not found", file=sys.stderr)
            continue
        raw = json.loads(full.read_text(encoding="utf-8"))

        if args.remove:
            if "host_layout" not in raw:
                print(f"no change: {full}")
                continue
            del raw["host_layout"]
            verb = "would remove" if args.dry_run else "removed"
            if not args.dry_run:
                full.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            print(f"{verb} host_layout: {full}")
            touched += 1
            continue

        x = 0.15 if args.corner == "left" else 0.85
        block = {"use": args.use, "x": x}
        current = raw.get("host_layout")
        if isinstance(current, dict):
            # Keep human overrides that the tool does not manage.
            for key in set(current) & VALID_KEYS - set(block):
                block[key] = current[key]
        if current == block:
            print(f"no change: {full}")
            continue
        raw["host_layout"] = block
        verb = "would update" if args.dry_run else "updated"
        if not args.dry_run:
            full.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"{verb} host_layout -> {block}: {full}")
        touched += 1

    print(f"\n{touched} file(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
