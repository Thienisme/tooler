from pathlib import Path

from ai_mystery_story.application.audio_assembly_service import (
    AudioAssemblyService,
)
from ai_mystery_story.application.tts_service import (
    TTSService,
)
from ai_mystery_story.domain.audio.audio_track import (
    AudioTrack,
)
from ai_mystery_story.domain.story.mystery_story import MysteryStory


class AudioGenerator:

    def __init__(
        self,
        tts_service: TTSService,
        audio_assembly_service: AudioAssemblyService,
    ):
        self.tts_service = tts_service
        self.audio_assembly_service = audio_assembly_service

    def generate(
        self,
        story: MysteryStory,
        output_dir: Path,
    ) -> AudioTrack:

        if story.narration is None:
            raise RuntimeError(
                "Cannot generate audio: narration is empty."
            )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        segments_dir = output_dir / "segments"

        segments = self.tts_service.generate(
            story=story,
            output_dir=segments_dir,
        )

        full_audio_path = (
            output_dir / "narration_full.mp3"
        )

        duration = self.audio_assembly_service.assemble(
            segments=segments,
            output_path=full_audio_path,
        )

        audio = AudioTrack(
            segments=segments,
            full_audio_path=full_audio_path,
            duration_seconds=duration,
        )

        story.audio = audio

        return audio
