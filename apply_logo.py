"""
📌 Cover Gemini Watermark (✦) with Logo

Usage:
    python apply_logo.py <topic-id> [--tutien] [--dola] [--scale 0.25]
    python apply_logo.py --image <image-path> [--logo <logo-path>] [--dola] [--scale 0.25]

Example:
    python apply_logo.py mystery-002
    python apply_logo.py mystery-003
    python apply_logo.py horror-001 --tutien
    python apply_logo.py --image path/to/any/image.jpg
    python apply_logo.py --image path/to/dola-image.png --dola
    python apply_logo.py --image path/to/dola-image.png --dola --scale 0.3
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ai_mystery_story.infrastructure.image.logo_overlay import (
    apply_logo_to_topic_images,
    apply_logo_to_topic_thumbnail,
)


def main() -> None:
    args = sys.argv[1:]
    tutien = "--tutien" in args
    dola = "--dola" in args
    watermark = "dola" if dola else "gemini"
    args = [a for a in args if a not in ("--tutien", "--dola")]

    scale_ratio: float | None = None
    if watermark == "dola":
        scale_ratio = 0.25  # hard default for Dola mode (override with --scale)
    if "--scale" in args:
        s_idx = args.index("--scale")
        if s_idx + 1 >= len(args):
            print("❌ --scale requires a value, e.g. --scale 0.22")
            sys.exit(1)
        try:
            scale_ratio = float(args[s_idx + 1])
        except ValueError:
            print("❌ --scale must be a number, e.g. --scale 0.22")
            sys.exit(1)
        if not 0.05 <= scale_ratio <= 0.9:
            print("❌ --scale must be between 0.05 and 0.9")
            sys.exit(1)
        del args[s_idx:s_idx + 2]

    # --- Single arbitrary image mode ---
    if "--image" in args:
        idx = args.index("--image")
        image_path = Path(args[idx + 1]) if idx + 1 < len(args) else None
        if image_path is None:
            print("❌ --image requires a path to an image file.")
            sys.exit(1)
        if image_path.is_dir():
            print(f"❌ Path is a directory, not an image: {image_path}")
            print('   Tip: quote paths containing spaces:')
            print('        --image "path/with space/img.png"')
            sys.exit(1)
        if not image_path.exists():
            print(f"❌ Image not found: {image_path}")
            sys.exit(1)

        logo_path: Path | None = None
        if "--logo" in args:
            logo_idx = args.index("--logo")
            if logo_idx + 1 < len(args):
                logo_path = Path(args[logo_idx + 1])
        if logo_path is None:
            logo_path = (
                PROJECT_ROOT / "projects" / "logo" / "logo-laoto-bip.png"
                if tutien
                else PROJECT_ROOT / "projects" / "logo" / "logo.png"
            )
        if not logo_path.exists():
            print(f"❌ Logo not found: {logo_path}")
            sys.exit(1)

        print()
        print("=" * 60)
        print("📌 COVERING WATERMARK (✦) WITH LOGO")
        print("=" * 60)
        print(f"Image    : {image_path}")
        print(f"Logo     : {logo_path}")
        print()

        from ai_mystery_story.infrastructure.image.logo_overlay import LogoOverlay

        overlay_kwargs = {"logo_path": logo_path, "watermark": watermark}
        if scale_ratio is not None:
            overlay_kwargs["scale_ratio"] = scale_ratio
        overlay = LogoOverlay(**overlay_kwargs)
        overlay.apply_to_thumbnail(
            thumbnail_path=image_path,
            logo_path=logo_path,
        )

        print()
        print("=" * 60)
        print("✅ DONE. Logo applied successfully.")
        print("=" * 60)
        print()
        return

    if len(args) < 1:
        print("Usage:")
        print("  python apply_logo.py <topic-id> [--tutien] [--dola] [--scale 0.25]")
        print("  python apply_logo.py --image <image-path> [--logo <logo-path>] [--dola] [--scale 0.25]")
        print("\nExamples:")
        print("  python apply_logo.py mystery-002")
        print("  python apply_logo.py horror-001 --tutien")
        print("  python apply_logo.py --image path/to/any/image.jpg")
        print("  python apply_logo.py --image path/to/dola-image.png --dola --scale 0.3")
        sys.exit(1)

    topic_id = args[0]
    projects_dir = PROJECT_ROOT / "projects"

    if tutien:
        logo_path = projects_dir / "logo" / "logo-laoto-bip.png"
    else:
        logo_path = projects_dir / "logo" / "logo.png"

    if not logo_path.exists():
        print(f"❌ Logo not found: {logo_path}")
        sys.exit(1)

    print()
    print("=" * 60)
    print("📌 COVERING WATERMARK (✦) WITH LOGO")
    print("=" * 60)
    print(f"Topic ID : {topic_id}")
    print(f"Logo     : {logo_path}")
    print()

    any_success = False
    errors: list[str] = []

    # --- Thumbnail ---
    try:
        print("▶ Thumbnail:")
        updated_path = apply_logo_to_topic_thumbnail(
            topic_id=topic_id,
            projects_dir=projects_dir,
            logo_path=logo_path,
            watermark=watermark,
            scale_ratio=scale_ratio,
        )
        print(f"  File Size: {updated_path.stat().st_size // 1024} KB")
        any_success = True
    except Exception as exc:
        print(f"  ⚠ Skipped thumbnail: {exc}")
        errors.append(str(exc))

    print()

    # --- Images directory ---
    try:
        print("▶ Images:")
        updated_images = apply_logo_to_topic_images(
            topic_id=topic_id,
            projects_dir=projects_dir,
            logo_path=logo_path,
            watermark=watermark,
            scale_ratio=scale_ratio,
        )
        if updated_images:
            for img_path in updated_images:
                print(f"  File Size: {img_path.stat().st_size // 1024} KB")
            any_success = True
        else:
            print("  ℹ No images found in images/ directory — skipped.")
    except Exception as exc:
        print(f"  ⚠ Skipped images: {exc}")
        errors.append(str(exc))

    print()
    print("=" * 60)

    if any_success:
        print("✅ DONE. Logo applied successfully.")
    else:
        print("❌ FAILED. No files were processed.")
        for err in errors:
            print(f"   {err}")
        sys.exit(1)

    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
