from pathlib import Path

from ai_mystery_story.infrastructure.tts.edge_tts_provider import (
    EdgeTTSProvider,
)


OUTPUT_PATH = Path(
    "test_edge_tts/test.mp3"
)


provider = EdgeTTSProvider()

duration = provider.generate(
    text=(
        "Minh giật mình tỉnh giấc. "
        "Đúng ba giờ mười ba phút sáng, "
        "một tiếng kéo ghế vang lên trên trần nhà."
    ),
    output_path=OUTPUT_PATH,
)

print("Edge TTS generated successfully")
print(f"Voice: {provider.voice}")
print(f"Output: {OUTPUT_PATH}")
print(f"Duration: {duration:.2f} seconds")

assert OUTPUT_PATH.exists()
assert OUTPUT_PATH.stat().st_size > 0
assert duration > 0

print("TEST PASSED")