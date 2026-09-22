import sys
from pathlib import Path

from dotenv import load_dotenv
from mutagen.mp3 import MP3

from ai_mystery_story.application.audio.audio_generator import (
    AudioGenerator,
)
from ai_mystery_story.application.audio_assembly_service import (
    AudioAssemblyService,
)
from ai_mystery_story.application.tts_service import (
    TTSService,
)
from ai_mystery_story.domain.audio.audio_segment import (
    AudioSegment,
)
from ai_mystery_story.infrastructure.audio.ffmpeg_audio_assembler import (
    FFmpegAudioAssembler,
)
from ai_mystery_story.infrastructure.repositories.json_story_repository import (
    JsonStoryRepository,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> None:

    # Accept:  generate_audio.py <provider> <topic-id> [--tutien] [--no-intro-outro]
    args = sys.argv[1:]
    tutien = "--tutien" in args
    no_intro_outro = "--no-intro-outro" in args
    args = [a for a in args if a not in ("--tutien", "--no-intro-outro")]

    if len(args) != 2:
        print(
            "Usage: "
            "python generate_audio.py [fake|edge|fish] <topic-id> [--tutien] [--no-intro-outro]"
        )
        sys.exit(1)

    provider_name = args[0]
    topic_id = args[1]

    if provider_name not in {"fake", "edge", "fish", "vieneu"}:
        print(
            f"Unsupported TTS provider: "
            f"{provider_name}"
        )
        sys.exit(1)

    load_dotenv()

    repository = JsonStoryRepository(
        projects_dir=PROJECT_ROOT / "projects",
    )

    story = repository.get_by_topic_id(topic_id)

    if story is None:
        print(f"Story not found for topic: {topic_id}")
        sys.exit(1)

    print(
        f"Story: "
        f"{story.blueprint.title}"
    )

    if story.narration is None:
        print("Narration is empty.")
        print(
            "Run generate_narration.py first."
        )
        sys.exit(1)

    print(
        f"Narration segments: "
        f"{len(story.narration.segments)}"
    )

    if provider_name == "fake":
        from ai_mystery_story.infrastructure.tts.fakes.fake_tts_provider import (
            FakeTTSProvider,
        )

        provider = FakeTTSProvider()
    elif provider_name == "fish":
        from ai_mystery_story.infrastructure.tts.fish_speech_provider import (
            FishSpeechProvider,
        )

        provider = FishSpeechProvider()
    elif provider_name == "vieneu":
        from ai_mystery_story.infrastructure.tts.vieneu_tts_provider import (
            VieNeuTTSProvider,
        )

        provider = VieNeuTTSProvider()
    else:
        from ai_mystery_story.infrastructure.tts.edge_tts_provider import (
            EdgeTTSProvider,
        )

        provider = EdgeTTSProvider(
            rate="-2%" if tutien else None,
        )

    print(f"Provider: {provider_name}")
    if tutien:
        print("Intro/Outro: tu tien (_intro_tutien / _outro_tutien)")
        print("Edge TTS rate: -2% (tu tien override)")
    else:
        print("Intro/Outro: default (_intro / _outro)")

    tts_service = TTSService(
        provider=provider,
    )

    audio_assembly_service = AudioAssemblyService(
        assembler=FFmpegAudioAssembler(),
    )

    audio_generator = AudioGenerator(
        tts_service=tts_service,
        audio_assembly_service=audio_assembly_service,
    )

    output_dir = (
        PROJECT_ROOT
        / "projects"
        / topic_id
        / "audio"
    )

    # Pick intro/outro folder based on --tutien flag
    if tutien:
        intro_path = PROJECT_ROOT / "projects" / "_intro_tutien" / "intro.mp3"
        outro_path = PROJECT_ROOT / "projects" / "_outro_tutien" / "outro.mp3"
    else:
        intro_path = PROJECT_ROOT / "projects" / "_intro" / "intro.mp3"
        outro_path = PROJECT_ROOT / "projects" / "_outro" / "outro.mp3"

    if not no_intro_outro:
        if not intro_path.is_file() or not outro_path.is_file():
            missing = []
            if not intro_path.is_file():
                missing.append(str(intro_path))
            if not outro_path.is_file():
                missing.append(str(outro_path))
            print(f"Không có intro/outro: {', '.join(missing)}")
            sys.exit(1)

    audio = audio_generator.generate(
        story=story,
        output_dir=output_dir,
    )

    narration_segment = AudioSegment(
        order=1,
        title="Narration",
        source_text=story.narration.full_text(),
        file_path=audio.full_audio_path,
        duration_seconds=audio.duration_seconds,
    )

    if no_intro_outro:
        print("Skipping intro/outro (--no-intro-outro)")
    else:
        intro_segment = AudioSegment(
            order=0,
            title="Intro",
            source_text="",
            file_path=intro_path,
            duration_seconds=float(MP3(intro_path).info.length),
        )
        outro_segment = AudioSegment(
            order=2,
            title="Outro",
            source_text="",
            file_path=outro_path,
            duration_seconds=float(MP3(outro_path).info.length),
        )

        combined_audio_path = output_dir / "narration_full_combined.mp3"
        audio.duration_seconds = audio_assembly_service.assemble(
            segments=[
                intro_segment,
                narration_segment,
                outro_segment,
            ],
            output_path=combined_audio_path,
        )
        combined_audio_path.replace(audio.full_audio_path)

    repository.save(story, topic_id=topic_id)

    print("Audio generated successfully")
    print(
        f"Segments: "
        f"{len(audio.segments)}"
    )
    print(
        f"Duration: "
        f"{audio.duration_seconds:.2f} seconds"
    )
    print(
        f"Full audio: "
        f"{audio.full_audio_path}"
    )


if __name__ == "__main__":
    main()
