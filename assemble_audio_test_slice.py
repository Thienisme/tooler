"""assemble_audio_test_slice.py

Tạo file MP3 test ngắn (~92 giây) để kiểm tra âm lượng
đồng bộ giữa intro / narration / outro (tu tien):

    Intro (18.7s) + Narration slice (60s) + Outro (13.6s)

Output: projects/tt-lathien-21-40/audio/narration_full_SYNC_TEST.mp3

Dùng cùng AudioAssemblyService + FFmpegAudioAssembler như
generate_audio.py / assemble_audio.py, nên kết quả loudness
giống hệt khi render thật.
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


PROJECT_ID = "tt-lathien-21-40"

audio_dir = PROJECT_ROOT / "projects" / PROJECT_ID / "audio"

intro_path = PROJECT_ROOT / "projects" / "_intro_tutien" / "intro.mp3"
outro_path = PROJECT_ROOT / "projects" / "_outro_tutien" / "outro.mp3"
narration_path = audio_dir / "narration_test_slice.mp3"
output_path = audio_dir / "narration_full_SYNC_TEST.mp3"

for p in (intro_path, outro_path, narration_path):
    if not p.is_file():
        print(f"ERROR: Không tìm thấy file: {p}")
        sys.exit(1)

print()
print("=" * 60)
print("ASSEMBLING SYNC TEST CLIP (tu tien)")
print("=" * 60)
print(f"Intro     : {intro_path}  ({MP3(str(intro_path)).info.length:.1f}s)")
print(f"Narration : {narration_path.name}  ({MP3(str(narration_path)).info.length:.1f}s)")
print(f"Outro     : {outro_path}  ({MP3(str(outro_path)).info.length:.1f}s)")
print(f"Output    : {output_path.name}")
print()

service = AudioAssemblyService(assembler=FFmpegAudioAssembler())

segments = [
    AudioSegment(
        order=0,
        title="Intro",
        source_text="",
        file_path=intro_path,
        duration_seconds=MP3(str(intro_path)).info.length,
    ),
    AudioSegment(
        order=1,
        title="Narration",
        source_text="",
        file_path=narration_path,
        duration_seconds=MP3(str(narration_path)).info.length,
    ),
    AudioSegment(
        order=2,
        title="Outro",
        source_text="",
        file_path=outro_path,
        duration_seconds=MP3(str(outro_path)).info.length,
    ),
]

duration = service.assemble(segments=segments, output_path=output_path)

size_mb = output_path.stat().st_size / 1024 / 1024

print("Done!")
print(f"Output   : {output_path}")
print(f"Duration : {duration:.1f}s")
print(f"Size     : {size_mb:.1f} MB")
print()
print("Nghe thử các mốc thời gian để kiểm tra volume:")
print(f"  0:00 - 0:18   Intro")
print(f"  0:18 - 1:18   Narration (chuyển volume tại 0:18)")
print(f"  1:18 - 1:32   Outro (chuyển volume tại 1:18)")
print()
