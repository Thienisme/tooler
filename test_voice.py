import asyncio
from pathlib import Path

import edge_tts


TEXT = """
Ba năm trước, hắn bị toàn gia tộc khinh thường.
Không ai biết rằng, trong cơ thể hắn...
một viên ngọc cổ đã thức tỉnh.
"""


VOICE = "vi-VN-HoaiMyNeural"

TESTS = {
    "D": {
        "rate": "-3%",
        "pitch": "+0Hz",
    },
    "E": {
        "rate": "-4%",
        "pitch": "+1Hz",
    },
    "F": {
        "rate": "-5%",
        "pitch": "+0Hz",
    },
}

async def generate(
    name: str,
    rate: str,
    pitch: str | None,
    output_path: Path,
):
    communicate = edge_tts.Communicate(
        text=TEXT,
        voice=VOICE,
        rate=rate,
        pitch=pitch,
    )

    await communicate.save(
        str(output_path)
    )


async def main():
    output_dir = Path("voice_tests")
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print("=" * 60)
    print("EDGE TTS VOICE TEST")
    print("=" * 60)
    print()
    print(f"Voice: {VOICE}")
    print()

    for name, config in TESTS.items():
        output_path = (
            output_dir
            / f"voice_test_{name}.mp3"
        )

        print(
            f"Generating Test {name}: "
            f"rate={config['rate']}, "
            f"pitch={config['pitch']}"
        )

        await generate(
            name=name,
            rate=config["rate"],
            pitch=config["pitch"],
            output_path=output_path,
        )

        print(
            f"Saved: {output_path}"
        )
        print()

    print("=" * 60)
    print("DONE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
