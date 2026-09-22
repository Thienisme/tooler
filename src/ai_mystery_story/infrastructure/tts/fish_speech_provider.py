"""
Fish Speech TTS Provider - Using Fish Audio SDK

Uses Fish Audio's free s2.1-pro-free model with Vietnamese voice models.
Supports Prosody control (speed, volume) for expressive storytelling.

IMPORTANT: Fish Speech s2.1-pro does NOT support bracket emotion tags
like [whisper]. Emotion is controlled via the voice model chosen and
natural language prosody settings.
"""

import os
import re
import tempfile
import time
import unicodedata
from pathlib import Path

from mutagen.mp3 import MP3

from ai_mystery_story.infrastructure.tts.tts_provider import TTSProvider

# Vietnamese voice models from Fish Audio marketplace (verified via API)
VIETNAMESE_VOICES = {
    # Original voices
    "Giọng Kể Chuyện Ấm Áp": "d41790c431e541d9ab846925b556f5d1",
    "NỮ KỂ CHUYỆN": "6d66039b0da146a9a76188459c8f3a0e",
    # New voices (added 2026-09-10, tested via test_fish_voices.py)
    "Nam Kể Kinh Dị (gầm nhẹ)": "218d3f0bc04241e7ae0dc0ac9c21d67d",
    "Giọng Trẻ Trò Chuyện": "b933d09fa29a4ed2b41c186f65218253",
    "Nữ Dịu Dàng Ngọt Ngào": "9ac6a11757ed4662aff7cb64bc33e868",
    "Nữ Truyền Cảm Hứng": "b3ac05235aba4ac9a9e143e95c9fd4a9",
    "Nam Hải Thanh Thể Thao": "50141ad70c20402a850780e63eee803b",
}

DEFAULT_VOICE_NAME = "Giọng Kể Chuyện Ấm Áp"


class FishSpeechProvider(TTSProvider):
    """
    Fish Audio TTS Provider with Vietnamese voice support.

    Uses the free s2.1-pro-free model with a pre-selected Vietnamese
    narrator voice for accurate pronunciation.

    Emotion is achieved through:
    - Voice model selection (narrator vs warm vs calm)
    - Prosody settings (speed, volume)
    - Text punctuation and formatting (ellipses, dashes, periods)
    """

    def __init__(
        self,
        api_key: str | None = None,
        reference_id: str | None = None,
        voice_name: str | None = None,
        model: str | None = None,
        speed: float | None = None,
        volume: float | None = None,
        max_chars: int | None = None,
        max_retries: int = 3,
        timeout_seconds: int | None = None,
    ):
        self.api_key = api_key or os.getenv("FISH_API_KEY", "")
        self.model = model or os.getenv("FISH_MODEL", "s2.1-pro-free")
        self.speed = speed or float(os.getenv("FISH_SPEED", "1.0"))
        self.volume = volume or float(os.getenv("FISH_VOLUME", "0"))
        self.max_chars = max_chars or int(os.getenv("FISH_MAX_CHARS", "2000"))
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds or int(
            os.getenv("FISH_TIMEOUT_SECONDS", "120")
        )

        # Resolve reference_id: explicit > voice_name > env > default
        if reference_id:
            self.reference_id = reference_id
        elif voice_name and voice_name in VIETNAMESE_VOICES:
            self.reference_id = VIETNAMESE_VOICES[voice_name]
        elif voice_name:
            # User passed a custom reference_id via voice_name
            self.reference_id = voice_name
        else:
            env_ref = os.getenv("FISH_REFERENCE_ID", "")
            if env_ref:
                self.reference_id = env_ref
            else:
                self.reference_id = VIETNAMESE_VOICES[DEFAULT_VOICE_NAME]

        if not self.api_key:
            raise ValueError(
                "FISH_API_KEY is required. "
                "Get free key at https://fish.audio/app/api-keys"
            )

        print(
            f"Fish Speech: model={self.model}, "
            f"voice={self.reference_id[:12]}..., "
            f"speed={self.speed}, volume={self.volume}"
        )

    def generate(
        self,
        text: str,
        output_path: Path,
    ) -> float:
        """
        Generate audio from text.

        Returns:
            Audio duration in seconds
        """
        text = text.strip()
        if not text:
            raise ValueError("Cannot generate TTS from empty text.")

        # Clean text: remove non-Vietnamese chars, emotion tags, etc.
        text = self._clean_text(text)

        if not text:
            raise ValueError("Text is empty after cleaning.")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Split text into chunks
        chunks = self._split_text(text, self.max_chars)
        print(f"Fish Speech: {len(chunks)} chunk(s), max {self.max_chars} chars")

        if len(chunks) == 1:
            self._generate_with_retry(chunks[0], output_path)
        else:
            self._generate_and_merge(chunks, output_path)

        return self._get_duration(output_path)

    def _clean_text(self, text: str) -> str:
        """
        Clean + preprocess text for Fish Speech:
        1. Normalize to UTF-8 NFC (precomposed)
        2. Preprocess: numbers to words, abbreviations, diacritics tricks
        3. Remove emotion tags, non-Vietnamese chars
        4. Normalize whitespace
        """
        # Step 1: Normalize to NFC (precomposed)
        text = unicodedata.normalize('NFC', text)

        # Step 2: Text preprocessing for better Vietnamese pronunciation
        text = self._preprocess_text(text)

        # Remove bracket-style emotion tags: [whisper], [pause], etc.
        text = re.sub(r'\[[^\]]*\]', '', text)

        # Remove parenthesis-style emotion tags: (whisper), (pause), etc.
        text = re.sub(r'\([a-zA-Z\s]+\)', '', text)

        # Remove Chinese/Japanese/Korean characters
        text = re.sub(
            r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]',
            ' ',
            text,
        )

        # Keep only: Vietnamese chars, Latin chars, digits, basic punctuation
        allowed = re.compile(
            r"[a-zA-Zàáãạảăắằẳẵặâấầẩẫậèéẹẻẽêềếểễệđ"
            r"ìíĩỉịòóõọỏôốồổỗộơớờởỡợùúũụủưứừửữự"
            r"ỳýỹỷỵÀÁÃẠẢĂẮẰẲẴẶÂẤẦẨẪẬÈÉẸẺẼÊỀẾỂỄỆĐ"
            r"ÌÍĨỈỊÒÓÕỌỎÔỐỒỔỖỘƠỚỜỞỠỢÙÚŨỤỦƯỨỪỬỮỰ"
            r"ỲÝỸỶỴ0-9.,;:!?…–—\-\"\'() \n\r\t]",
        )
        text = ''.join(c for c in text if allowed.match(c))

        # Normalize multiple spaces/newlines
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r' +\n', '\n', text)
        text = re.sub(r'\n +', '\n', text)

        # Final NFC normalization
        text = unicodedata.normalize('NFC', text)

        return text.strip()

    def _preprocess_text(self, text: str) -> str:
        """
        Preprocess text for better Vietnamese pronunciation.
        Applies spelling tricks and text-to-word conversion.
        """
        # ── 1. Convert numbers to words ──────────────────────
        # Percentages: 10% -> mười phần trăm
        text = re.sub(
            r'(\d+)\s*%',
            lambda m: self._number_to_words(int(m.group(1))) + ' phần trăm',
            text,
        )

        # Currency: $5 -> năm đô la, 100k -> một trăm nghìn
        text = re.sub(
            r'\$(\d+)',
            lambda m: self._number_to_words(int(m.group(1))) + ' đô la',
            text,
        )

        # Standalone numbers in sentences (convert to words)
        text = re.sub(
            r'(?<=[\s,.])\d+(?=[\s,.])',
            lambda m: self._number_to_words(int(m.group())),
            text,
        )

        # ── 2. Common abbreviations -> Vietnamese pronunciation ──
        abbreviations = {
            'AI': 'ai',
            'TTS': 'ti ti ét',
            'STT': 'ét ti ti',
            'API': 'a p i',
            'GPS': 'gi pi ét',
            'UV': 'u vê',
            'OK': 'ô kê',
            'VPN': 'vê pê en',
            'SMS': 'ét em ét',
            'GPS': 'gi pi ét',
            'CEO': 'xi yi ô',
            'CTO': 'xi ti ô',
            'DNA': 'di en a',
            'USB': 'u ét be',
            'LCD': 'ét xê di',
            'LED': 'ét li di',
            'HD': 'hây di',
            '4K': 'bốn cà',
            'AI': 'ai',
        }
        for abbr, viet in abbreviations.items():
            # Match abbreviation with word boundaries
            text = re.sub(
                r'\b' + re.escape(abbr) + r'\b',
                viet,
                text,
                flags=re.IGNORECASE,
            )

        # ── 3. Compound words: add dash for clarity ───────────
        # Từ láy: đằng đẵng -> đằng - đẵng
        compound_pairs = [
            ('đằng đẵng', 'đằng - đẵng'),
            ('chập chùng', 'chập - chùng'),
            ('lấp lánh', 'lấp - lánh'),
            ('lung linh', 'lung - linh'),
            ('lung linh', 'lung - linh'),
            ('nhấp nhô', 'nhấp - nhô'),
            ('đ=-=-=-=-=-=', 'đ=-=-=-=-=-='),
        ]
        for old, new in compound_pairs:
            text = text.replace(old, new)

        return text

    @staticmethod
    def _number_to_words(n: int) -> str:
        """Convert number to Vietnamese words."""
        if n == 0:
            return 'không'

        ones = [
            '', 'một', 'hai', 'ba', 'bốn', 'năm',
            'sáu', 'bảy', 'tám', 'chín'
        ]
        tens = [
            '', 'mười', 'hai mươi', 'ba mươi', 'bốn mươi',
            'năm mươi', 'sáu mươi', 'bảy mươi', 'tám mươi', 'chín mươi'
        ]

        if n < 10:
            return ones[n]
        elif n < 20:
            if n == 10:
                return 'mười'
            return f'mười {ones[n - 10]}'
        elif n < 100:
            t = n // 10
            o = n % 10
            if o == 0:
                return tens[t]
            return f'{tens[t]} {ones[o]}'
        elif n < 1000:
            h = n // 100
            r = n % 100
            if r == 0:
                return f'{ones[h]} trăm'
            return f'{ones[h]} trăm {FishSpeechProvider._number_to_words(r)}'
        elif n < 1000000:
            t = n // 1000
            r = n % 1000
            if r == 0:
                return f'{FishSpeechProvider._number_to_words(t)} nghìn'
            return f'{FishSpeechProvider._number_to_words(t)} nghìn {FishSpeechProvider._number_to_words(r)}'
        else:
            return str(n)  # Fallback for very large numbers

    def _generate_with_retry(
        self,
        text: str,
        output_path: Path,
    ) -> None:
        """Generate audio with retry logic."""
        from fishaudio import FishAudio

        client = FishAudio(api_key=self.api_key)

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                print(
                    f"  Generating TTS (attempt {attempt}/{self.max_retries})..."
                )

                # Build TTS config with optimized parameters for Vietnamese
                from fishaudio.types import TTSConfig, Prosody

                prosody = Prosody(speed=self.speed, volume=self.volume)

                config = TTSConfig(
                    reference_id=self.reference_id,
                    format="mp3",
                    prosody=prosody,
                    temperature=0.3,          # Low = more accurate pronunciation
                    repetition_penalty=1.2,    # Prevent swallowing end consonants
                    chunk_length=100,          # Small chunks = better focus on diacritics
                    top_p=0.7,                 # Conservative token selection
                )

                # Generate audio
                audio_data = client.tts.convert(
                    text=text,
                    model=self.model,
                    config=config,
                )

                # Save to file
                with open(output_path, "wb") as f:
                    f.write(audio_data)

                if (
                    not output_path.exists()
                    or output_path.stat().st_size == 0
                ):
                    raise RuntimeError("Fish Speech generated an empty file.")

                return

            except Exception as exc:
                last_error = exc
                print(
                    f"  Fish Speech attempt {attempt} failed: "
                    f"{type(exc).__name__}: {exc}"
                )

                if output_path.exists():
                    output_path.unlink()

                if attempt < self.max_retries:
                    delay = 2 ** (attempt - 1)
                    print(f"  Retrying in {delay}s...")
                    time.sleep(delay)

        raise RuntimeError(
            f"Fish Speech failed after {self.max_retries} attempts: {last_error}"
        )

    def _generate_and_merge(
        self,
        chunks: list[str],
        output_path: Path,
    ) -> None:
        """Generate multiple chunks and merge them."""
        from pydub import AudioSegment

        with tempfile.TemporaryDirectory(prefix="fish_tts_") as temp_dir:
            temp_path = Path(temp_dir)
            chunk_paths = []

            for i, chunk in enumerate(chunks, 1):
                chunk_path = temp_path / f"chunk_{i:03d}.mp3"
                print(f"  Chunk {i}/{len(chunks)} ({len(chunk)} chars)")

                self._generate_with_retry(chunk, chunk_path)
                chunk_paths.append(chunk_path)

                # Delay between requests to avoid rate limiting
                if i < len(chunks):
                    time.sleep(2)

            # Merge chunks with pauses
            combined = AudioSegment.empty()
            pause = AudioSegment.silent(duration=300)  # 300ms pause between chunks

            for i, chunk_path in enumerate(chunk_paths):
                audio = AudioSegment.from_mp3(chunk_path)
                if i > 0:
                    combined += pause
                combined += audio

            combined.export(output_path, format="mp3", bitrate="128k")

    def _split_text(self, text: str, max_chars: int) -> list[str]:
        """Split text into chunks, respecting sentence boundaries."""
        if len(text) <= max_chars:
            return [text]

        # Split by double newlines first (paragraphs)
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current = ""

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            if len(paragraph) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(self._split_by_sentences(paragraph, max_chars))
                continue

            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = paragraph

        if current:
            chunks.append(current)

        return chunks

    def _split_by_sentences(self, text: str, max_chars: int) -> list[str]:
        """Split text by Vietnamese/Latin sentence endings."""
        # Match Vietnamese/Latin sentence endings
        sentences = re.split(
            r'(?<=[.!?。！？…])\s+', text
        )
        chunks: list[str] = []
        current = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            if len(sentence) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(self._split_by_words(sentence, max_chars))
                continue

            candidate = f"{current} {sentence}" if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sentence

        if current:
            chunks.append(current)

        return chunks

    def _split_by_words(self, text: str, max_chars: int) -> list[str]:
        """Split text by words (last resort)."""
        words = text.split()
        chunks: list[str] = []
        current = ""

        for word in words:
            candidate = f"{current} {word}" if current else word
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = word

        if current:
            chunks.append(current)

        return chunks

    def _get_duration(self, path: Path) -> float:
        """Get audio duration in seconds."""
        audio = MP3(path)
        return float(audio.info.length)

    @staticmethod
    def get_available_voices() -> dict[str, str]:
        """Get available Vietnamese voice models."""
        return dict(VIETNAMESE_VOICES)

    @staticmethod
    def get_emotion_tags() -> dict[str, str]:
        """
        Get text formatting tips for emotion control.
        Fish Speech s2.1-pro uses natural text formatting for emotion,
        NOT bracket tags like [whisper].
        """
        return {
            "Thì thầm (dùng dấu ...)": "...",
            "Nhấn mạnh (dùng _underscore_)": "_nội dung_",
            "Tạm dừng (dùng dấu ... hoặc xuống dòng)": "...\n\n",
            "Thở dài (thêm dấu chấm than)": "...!",
            "Giọng thấp (giãn cách từ)": "Từ    cách    xa    nhau",
            "Giọng bình thường": "Text không cần format đặc biệt",
        }
