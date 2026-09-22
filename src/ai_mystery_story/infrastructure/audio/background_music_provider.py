"""
Background Music Provider - Generate or load ambient background music.

Supports:
1. Auto-generated ambient mystery music using ffmpeg synthesis
2. Loading custom music files (mp3, wav, etc.)
3. Looping to match narration duration
"""

import subprocess
import tempfile
from pathlib import Path


# Resolve ffmpeg binary - prefer project's bundled binary
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_FFMPEG_BIN = _PROJECT_ROOT / "bin" / "ffmpeg"
_FFMPEG = str(_FFMPEG_BIN) if _FFMPEG_BIN.exists() else "ffmpeg"
_FFPROBE_BIN = _PROJECT_ROOT / "bin" / "ffprobe"
_FFPROBE = str(_FFPROBE_BIN) if _FFPROBE_BIN.exists() else "ffprobe"


# Default music settings for mystery/horror atmosphere
DEFAULT_MUSIC_CONFIG = {
    "style": "dark_ambient",
    "volume": 0.12,  # Background music volume (0.0 - 1.0)
    "fade_in_seconds": 3.0,
    "fade_out_seconds": 5.0,
}


class BackgroundMusicProvider:
    """
    Generate or load background music for mystery story videos.

    Priority order:
    1. Custom music file (if music_path is provided)
    2. Music from data/music/ directory (random selection)
    3. Auto-generated ambient music using ffmpeg synthesis
    """

    # Supported audio formats
    AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}

    def __init__(
        self,
        music_path: Path | None = None,
        volume: float = 0.12,
        fade_in_seconds: float = 3.0,
        fade_out_seconds: float = 5.0,
    ):
        self.music_path = music_path
        self.volume = volume
        self.fade_in_seconds = fade_in_seconds
        self.fade_out_seconds = fade_out_seconds

    def get_music(
        self,
        duration_seconds: float,
        output_path: Path,
    ) -> Path:
        """
        Get background music matching the specified duration.

        Priority:
        1. Custom music file (if provided)
        2. Random track from data/music/ directory
        3. Auto-generated ambient music

        Args:
            duration_seconds: Required duration in seconds
            output_path: Where to save the output music file

        Returns:
            Path to the music file
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Priority 1: Custom music file
        if self.music_path and self.music_path.exists():
            return self._process_custom_music(
                duration_seconds, output_path
            )

        # Priority 2: Music from data/music/ directory
        bundled_music = self._find_bundled_music()
        if bundled_music:
            return self._process_custom_music(
                duration_seconds, output_path, music_file=bundled_music
            )

        # Priority 3: Generate ambient music
        return self._generate_ambient_music(
            duration_seconds, output_path
        )

    def _find_bundled_music(self) -> Path | None:
        """
        Find a random music file from data/music/ directory.

        Returns:
            Path to a random music file, or None if directory is empty.
        """
        import random

        music_dir = _PROJECT_ROOT / "data" / "music"
        if not music_dir.exists():
            return None

        music_files = [
            f for f in music_dir.iterdir()
            if f.is_file() and f.suffix.lower() in self.AUDIO_EXTENSIONS
        ]

        if not music_files:
            return None

        selected = random.choice(music_files)
        print(f"  🎵 Selected bundled music: {selected.name}")
        return selected

    def _generate_ambient_music(
        self,
        duration_seconds: float,
        output_path: Path,
    ) -> Path:
        """
        Generate dark ambient mystery music using ffmpeg synthesis.

        Creates layered sine waves with slow modulation to produce
        an eerie, atmospheric background sound.
        """
        print("  Generating ambient mystery music...")

        # Add extra seconds for fade in/out handling
        total_duration = duration_seconds + 2.0

        # Layer 1: Deep bass drone (very low frequency)
        # Layer 2: Mid-range mysterious tone
        # Layer 3: High eerie whisper tone
        # Layer 4: Subtle noise texture

        # Generate two sine waves and mix them for dark ambient
        # Note: amix averages inputs, so we need significant boost
        filter_complex = (
            # Layer 1: Deep bass drone at 55Hz (full volume)
            f"sine=frequency=55:duration={total_duration},"
            f"volume=1.0,"
            f"tremolo=f=0.1:d=0.4[bass];"

            # Layer 2: Eerie mid tone at 220Hz (lower volume for depth)
            f"sine=frequency=220:duration={total_duration},"
            f"volume=0.5,"
            f"tremolo=f=0.15:d=0.6[mid];"

            # Mix bass + mid (amix averages → divide by 2)
            f"[bass][mid]amix=inputs=2:duration=first[mixed];"

            # amix averages inputs (÷2), lowpass reduces further
            # We need significant boost to reach audible levels (-15 dB target)
            f"[mixed]volume={self.volume * 30},"
            f"lowpass=f=800,"
            f"afade=t=in:st=0:d={self.fade_in_seconds},"
            f"afade=t=out:st={duration_seconds - self.fade_out_seconds}:d={self.fade_out_seconds}"
        )

        command = [
            _FFMPEG,
            "-y",
            "-filter_complex", filter_complex,
            "-t", str(duration_seconds),
            "-ar", "44100",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(output_path.with_suffix('.wav')),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to generate ambient music:\n"
                f"{result.stderr}"
            )

        # Convert WAV to MP3
        wav_path = output_path.with_suffix('.wav')
        convert_cmd = [
            _FFMPEG, "-y",
            "-i", str(wav_path),
            "-c:a", "libmp3lame", "-b:a", "128k",
            str(output_path),
        ]
        subprocess.run(convert_cmd, capture_output=True, text=True, check=True)
        wav_path.unlink(missing_ok=True)

        print(f"  ✅ Ambient music generated: {output_path.name}")
        return output_path

    def _process_custom_music(
        self,
        duration_seconds: float,
        output_path: Path,
        music_file: Path | None = None,
    ) -> Path:
        """
        Process music file: loop or trim to match duration,
        apply volume and fade effects.

        Args:
            duration_seconds: Required duration in seconds
            output_path: Where to save the output
            music_file: Specific file to use (defaults to self.music_path)
        """
        # Use provided music_file or fall back to self.music_path
        source_music = music_file if music_file else self.music_path
        print(f"  Processing music: {source_music.name}")

        filter_complex = (
            # Loop the music to fill the duration
            f"aloop=loop=-1:size=2e+09,"
            # Trim to exact duration
            f"atrim=0:{duration_seconds},"
            # Apply volume
            f"volume={self.volume},"
            # Fade in at start
            f"afade=t=in:st=0:d={self.fade_in_seconds},"
            # Fade out at end
            f"afade=t=out:st={duration_seconds - self.fade_out_seconds}:d={self.fade_out_seconds}"
        )

        command = [
            _FFMPEG,
            "-y",
            "-stream_loop", "-1",
            "-i", str(source_music),
            "-filter_complex", filter_complex,
            "-t", str(duration_seconds),
            "-ar", "44100",
            "-ac", "2",
            "-c:a", "libmp3lame",
            "-b:a", "128k",
            str(output_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to process music:\n"
                f"{result.stderr}"
            )

        print(f"  ✅ Music processed: {output_path.name}")
        return output_path


class AudioMixer:
    """
    Mix narration audio with background music using ffmpeg.

    Produces a final audio track with:
    - Narration at full volume
    - Background music at reduced volume
    - Proper mixing and normalization
    """

    def __init__(
        self,
        narration_volume: float = 1.0,
        music_volume: float = 0.12,
    ):
        self.narration_volume = narration_volume
        self.music_volume = music_volume

    def mix(
        self,
        narration_path: Path,
        music_path: Path,
        output_path: Path,
    ) -> float:
        """
        Mix narration and background music.

        Args:
            narration_path: Path to narration audio
            music_path: Path to background music
            output_path: Where to save mixed audio

        Returns:
            Duration of mixed audio in seconds
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        print("  Mixing narration + background music...")

        # Mix narration + music using amix filter, output directly to MP3
        filter_complex = (
            f"[0:a]volume={self.narration_volume}[narr];"
            f"[1:a]volume={self.music_volume}[music];"
            f"[narr][music]amix=inputs=2:duration=first:dropout_transition=3,"
            f"pan=stereo|c0=c0|c1=c0"
        )

        command = [
            _FFMPEG,
            "-y",
            "-i", str(narration_path),
            "-i", str(music_path),
            "-filter_complex", filter_complex,
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            "-ar", "44100",
            str(output_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to mix audio:\n{result.stderr}"
            )

        # Get duration of output
        duration = self._get_duration(output_path)
        print(f"  ✅ Audio mixed: {output_path.name} ({duration:.1f}s)")
        return duration

    def _get_duration(self, audio_path: Path) -> float:
        """Get audio duration using ffprobe."""
        command = [
            _FFPROBE,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"ffprobe failed:\n{result.stderr}"
            )

        return float(result.stdout.strip())
