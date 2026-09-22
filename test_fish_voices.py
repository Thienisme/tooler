"""
Test Fish Audio Vietnamese voices.

Generates a short horror-style sample with each candidate voice from the
Fish Audio marketplace so the quality can be verified by ear before the
voices are added to FishSpeechProvider.

Usage:
    python test_fish_voices.py            # test all candidate voices
    python test_fish_voices.py 2          # test only voice #2
    python test_fish_voices.py --list     # list voices without generating
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from ai_mystery_story.infrastructure.tts.fish_speech_provider import (
    VIETNAMESE_VOICES,
)

load_dotenv()

# Horror/mystery sample text (TTS-friendly, like the app's narration style)
SAMPLE_TEXT = (
    "Đêm đó, gió thổi rít qua khe cửa, căn nhà cũ kỹ chìm trong bóng tối. "
    "Từ tầng hầm, một tiếng động lạ vang lên, chậm rãi, như bước chân của "
    "ai đó đang tiến lại gần..."
)

OUTPUT_DIR = Path("test_fish_voices_output")


def list_voices() -> None:
    print("\nDANH SACH GIONG DOC:")
    print("=" * 60)
    for index, (name, voice_id) in enumerate(VIETNAMESE_VOICES.items(), 1):
        print(f"  {index}. {name}")
        print(f"     {voice_id}")


def test_voice(name: str, voice_id: str) -> bool:
    """Generate a sample with one voice. Returns True on success."""
    from fishaudio import FishAudio
    from fishaudio.types import Prosody, TTSConfig

    api_key = os.getenv("FISH_API_KEY", "")
    if not api_key:
        print("ERROR: FISH_API_KEY not set in .env")
        return False

    client = FishAudio(api_key=api_key)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = name.lower().replace(" ", "_")
    output_path = OUTPUT_DIR / f"voice_{safe_name}.mp3"

    config = TTSConfig(
        reference_id=voice_id,
        format="mp3",
        prosody=Prosody(speed=1.0, volume=0),
        temperature=0.3,
        repetition_penalty=1.2,
        chunk_length=100,
        top_p=0.7,
    )

    print(f"\n[{name}] Generating...")
    start = time.time()

    try:
        audio_data = client.tts.convert(
            text=SAMPLE_TEXT,
            model="s2.1-pro-free",
            config=config,
        )
        with open(output_path, "wb") as f:
            f.write(audio_data)
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return False

    elapsed = time.time() - start
    size_kb = output_path.stat().st_size / 1024
    print(f"  OK -> {output_path} ({size_kb:.0f} KB, {elapsed:.1f}s)")
    return True


def main() -> None:
    if "--list" in sys.argv:
        list_voices()
        return

    only = None
    for arg in sys.argv[1:]:
        if arg.isdigit():
            only = int(arg)
            break

    voices = list(VIETNAMESE_VOICES.items())

    print("=" * 60)
    print("FISH AUDIO VIETNAMESE VOICE TEST")
    print("=" * 60)
    print(f"Sample text: {SAMPLE_TEXT[:50]}...")

    results: dict[str, bool] = {}
    for index, (name, voice_id) in enumerate(voices, 1):
        if only is not None and index != only:
            continue
        results[name] = test_voice(name, voice_id)
        time.sleep(2)  # rate-limit courtesy between requests

    print("\n" + "=" * 60)
    print("KET QUA:")
    print("=" * 60)
    for name, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  [{status}] {name}")
    print(f"\nNghe thu cac file trong: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
