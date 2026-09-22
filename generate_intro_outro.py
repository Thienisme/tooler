import sys
from pathlib import Path

from dotenv import load_dotenv
from mutagen.mp3 import MP3

from ai_mystery_story.domain.audio.audio_segment import AudioSegment
from ai_mystery_story.infrastructure.tts.tts_provider import TTSProvider


PROJECT_ROOT = Path(__file__).resolve().parent

INTRO_TEXT = (
    "he lô hê sờ ly ly, chào mừng bạn đến với kênh Sợ ma studio. "
    "hãy tắt đèn đi… và chúng ta sẽ bắt đầu câu chuyện nha hi hi hi hi..."
)

OUTRO_TEXT = (
    "Câu chuyện đến đây là kết thúc, nhưng những điều đáng sợ, "
    "có lẽ vẫn chưa dừng lại. "
    "Nếu bạn thích những câu chuyện như thế này, "
    "nhớ like và đăng ký Sợ ma studio nhé. "
    "Hẹn gặp lại bạn trong câu chuyện tiếp theo. "
    "Hôn môi nha… chụt chụt."
)


def create_provider(provider_name: str) -> TTSProvider:
    if provider_name == "fake":
        from ai_mystery_story.infrastructure.tts.fakes.fake_tts_provider import (
            FakeTTSProvider,
        )

        return FakeTTSProvider()

    if provider_name == "fish":
        from ai_mystery_story.infrastructure.tts.fish_speech_provider import (
            FishSpeechProvider,
        )

        return FishSpeechProvider()

    if provider_name == "edge":
        from ai_mystery_story.infrastructure.tts.edge_tts_provider import (
            EdgeTTSProvider,
        )

        return EdgeTTSProvider()

    if provider_name == "vieneu":
        from ai_mystery_story.infrastructure.tts.vieneu_tts_provider import (
            VieNeuTTSProvider,
        )

        # Kênh ma/kinh dị → Anh Khôi
        return VieNeuTTSProvider(voice="Anh Khôi")

    raise ValueError(
        f"Unsupported TTS provider: {provider_name}"
    )


def generate_intro_outro(
    provider: TTSProvider,
    projects_dir: Path,
) -> tuple[AudioSegment, AudioSegment]:
    """Create the shared intro/outro clips, preserving valid existing files."""
    intro_dir = projects_dir / "_intro"
    outro_dir = projects_dir / "_outro"

    intro = _generate_clip(
        provider=provider,
        output_path=intro_dir / "intro.mp3",
        order=0,
        title="Intro",
        text=INTRO_TEXT,
    )
    outro = _generate_clip(
        provider=provider,
        output_path=outro_dir / "outro.mp3",
        order=0,
        title="Outro",
        text=OUTRO_TEXT,
    )

    return intro, outro


def load_intro_outro(
    projects_dir: Path,
) -> tuple[AudioSegment, AudioSegment]:
    """Load the shared clips without creating or regenerating them."""
    intro_path = projects_dir / "_intro" / "intro.mp3"
    outro_path = projects_dir / "_outro" / "outro.mp3"

    missing_paths = [
        path
        for path in (intro_path, outro_path)
        if not path.exists() or path.stat().st_size == 0
    ]
    if missing_paths:
        raise FileNotFoundError("Không có in/outro")

    return (
        AudioSegment(
            order=0,
            title="Intro",
            source_text=INTRO_TEXT,
            file_path=intro_path,
            duration_seconds=float(MP3(intro_path).info.length),
        ),
        AudioSegment(
            order=0,
            title="Outro",
            source_text=OUTRO_TEXT,
            file_path=outro_path,
            duration_seconds=float(MP3(outro_path).info.length),
        ),
    )


def _generate_clip(
    provider: TTSProvider,
    output_path: Path,
    order: int,
    title: str,
    text: str,
) -> AudioSegment:
    if output_path.exists() and output_path.stat().st_size > 0:
        print(f"{title} already exists. Skipping TTS.")
        duration_seconds = float(MP3(output_path).info.length)
    else:
        print(f"Generating {title.lower()}...")
        duration_seconds = provider.generate(
            text=text,
            output_path=output_path,
        )

    return AudioSegment(
        order=order,
        title=title,
        source_text=text,
        file_path=output_path,
        duration_seconds=duration_seconds,
    )


def main() -> None:
    if len(sys.argv) != 2:
        print(
            "Usage: python generate_intro_outro.py "
            "[fake|edge|fish|vieneu]"
        )
        sys.exit(1)

    provider_name = sys.argv[1]

    load_dotenv()

    try:
        provider = create_provider(provider_name)
    except ValueError as error:
        print(error)
        sys.exit(1)

    intro, outro = generate_intro_outro(
        provider=provider,
        projects_dir=PROJECT_ROOT / "projects",
    )

    print("Intro and outro generated successfully")
    print(f"Intro: {intro.file_path}")
    print(f"Outro: {outro.file_path}")


if __name__ == "__main__":
    main()
