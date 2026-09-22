"""assemble_audio.py

Ghép intro + narration + outro thành một file MP3 hoàn chỉnh.

Giữ nguyên file narration gốc, tạo thêm file output mới.

Usage:
    python assemble_audio.py <project-id> [options]

Examples:
    # Mặc định: lấy narration_vieneu.mp3, intro/outro từ _intro/_outro
    python assemble_audio.py horror-007

    # Dùng intro/outro tu tiên
    python assemble_audio.py horror-007 --tutien

    # Chỉ định file narration khác
    python assemble_audio.py horror-007 --narration narration_full.mp3

    # Chỉ định output khác
    python assemble_audio.py horror-007 --output final.mp3

Defaults:
    --narration  narration_vieneu.mp3
    --output     narration_full.mp3
    intro        projects/_intro/intro.mp3
    outro        projects/_outro/outro.mp3
"""

import sys
from pathlib import Path

from mutagen.mp3 import MP3

PROJECT_ROOT = Path(__file__).resolve().parent

SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ai_mystery_story.application.audio_assembly_service import AudioAssemblyService
from ai_mystery_story.domain.audio.audio_segment import AudioSegment
from ai_mystery_story.infrastructure.audio.ffmpeg_audio_assembler import FFmpegAudioAssembler


def get_duration(path: Path) -> float:
    return float(MP3(str(path)).info.length)


def main() -> None:
    # ----------------------------------------------------------------
    # Parse args manually (no argparse dep needed for this simple CLI)
    # ----------------------------------------------------------------
    args = sys.argv[1:]
    tutien      = "--tutien" in args
    args        = [a for a in args if a != "--tutien"]

    narration_name = "narration_vieneu.mp3"
    output_name    = "narration_full.mp3"

    # --narration <filename>
    if "--narration" in args:
        idx = args.index("--narration")
        narration_name = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    # --output <filename>
    if "--output" in args:
        idx = args.index("--output")
        output_name = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if not args:
        print("Usage:")
        print("  python assemble_audio.py <project-id> [--tutien]")
        print("         [--narration <filename>] [--output <filename>]")
        print()
        print("Examples:")
        print("  python assemble_audio.py horror-007")
        print("  python assemble_audio.py horror-007 --tutien")
        print("  python assemble_audio.py horror-007 --narration narration_vieneu.mp3")
        sys.exit(1)

    project_id = args[0]

    # ----------------------------------------------------------------
    # Resolve paths
    # ----------------------------------------------------------------
    projects_dir   = PROJECT_ROOT / "projects"
    audio_dir      = projects_dir / project_id / "audio"
    narration_path = audio_dir / narration_name
    output_path    = audio_dir / output_name

    if tutien:
        intro_path = projects_dir / "_intro_tutien" / "intro.mp3"
        outro_path = projects_dir / "_outro_tutien" / "outro.mp3"
    else:
        intro_path = projects_dir / "_intro" / "intro.mp3"
        outro_path = projects_dir / "_outro" / "outro.mp3"

    # ----------------------------------------------------------------
    # Validate
    # ----------------------------------------------------------------
    errors = []
    if not narration_path.exists():
        errors.append(f"Narration not found : {narration_path}")
    if not intro_path.exists():
        errors.append(f"Intro not found     : {intro_path}")
    if not outro_path.exists():
        errors.append(f"Outro not found     : {outro_path}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        sys.exit(1)

    # ----------------------------------------------------------------
    # Print summary
    # ----------------------------------------------------------------
    print()
    print("=" * 60)
    print("ASSEMBLING AUDIO")
    print("=" * 60)
    print(f"Project   : {project_id}")
    print(f"Narration : {narration_path.name}  ({get_duration(narration_path):.1f}s)")
    print(f"Intro     : {intro_path}  ({get_duration(intro_path):.1f}s)")
    print(f"Outro     : {outro_path}  ({get_duration(outro_path):.1f}s)")
    print(f"Output    : {output_path.name}")
    print()

    # ----------------------------------------------------------------
    # Assemble
    # ----------------------------------------------------------------
    service = AudioAssemblyService(assembler=FFmpegAudioAssembler())

    segments = [
        AudioSegment(
            order=0,
            title="Intro",
            source_text="",
            file_path=intro_path,
            duration_seconds=get_duration(intro_path),
        ),
        AudioSegment(
            order=1,
            title="Narration",
            source_text="",
            file_path=narration_path,
            duration_seconds=get_duration(narration_path),
        ),
        AudioSegment(
            order=2,
            title="Outro",
            source_text="",
            file_path=outro_path,
            duration_seconds=get_duration(outro_path),
        ),
    ]

    duration = service.assemble(segments=segments, output_path=output_path)

    # ----------------------------------------------------------------
    # Done
    # ----------------------------------------------------------------
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"Done!")
    print(f"Output   : {output_path}")
    print(f"Duration : {duration:.1f}s  ({duration / 60:.1f} min)")
    print(f"Size     : {size_mb:.1f} MB")
    print()


if __name__ == "__main__":
    main()
