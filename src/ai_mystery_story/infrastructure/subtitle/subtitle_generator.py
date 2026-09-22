"""
Subtitle Generator - Generate SRT subtitle files from narration text.

Supports:
1. Generating SRT from narration segments with timing
2. Word-level timing using edge-tts timestamps
3. Sentence-level fallback timing
"""

import re
import subprocess
from pathlib import Path


# Resolve ffmpeg binary - prefer project's bundled binary
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_FFMPEG_BIN = _PROJECT_ROOT / "bin" / "ffmpeg"
_FFMPEG = str(_FFMPEG_BIN) if _FFMPEG_BIN.exists() else "ffmpeg"


class SubtitleGenerator:
    """
    Generate SRT subtitle files for video overlay.

    Creates subtitles synchronized with narration audio.
    """

    def __init__(
        self,
        max_chars_per_line: int = 42,
        max_lines: int = 2,
        font_size: int = 24,
    ):
        self.max_chars_per_line = max_chars_per_line
        self.max_lines = max_lines
        self.font_size = font_size

    def generate_from_segments(
        self,
        segments: list[dict],
        output_path: Path,
    ) -> Path:
        """
        Generate SRT file from narration segments.

        Args:
            segments: List of dicts with keys:
                - text: str (narration text)
                - start_time: float (start time in seconds)
                - end_time: float (end time in seconds)
            output_path: Where to save the SRT file

        Returns:
            Path to the SRT file
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        srt_entries = []
        entry_index = 1

        for segment in segments:
            text = segment.get("text", "")
            start_time = segment.get("start_time", 0.0)
            end_time = segment.get("end_time", 0.0)

            if not text.strip():
                continue

            # Split text into subtitle chunks
            chunks = self._split_into_subtitle_chunks(text)

            # Calculate timing for each chunk
            total_chars = sum(len(c) for c in chunks)
            if total_chars == 0:
                continue

            current_time = start_time
            for chunk in chunks:
                # Proportional timing based on character count
                chunk_duration = (
                    (end_time - start_time)
                    * len(chunk)
                    / total_chars
                )
                chunk_end = min(
                    current_time + chunk_duration,
                    end_time
                )

                srt_entries.append({
                    "index": entry_index,
                    "start": current_time,
                    "end": chunk_end,
                    "text": chunk,
                })

                entry_index += 1
                current_time = chunk_end

        # Write SRT file
        self._write_srt(srt_entries, output_path)

        print(f"  ✅ Subtitles generated: {output_path.name} "
              f"({len(srt_entries)} entries)")
        return output_path

    def generate_from_full_text(
        self,
        text: str,
        audio_duration: float,
        output_path: Path,
    ) -> Path:
        """
        Generate SRT from full narration text with estimated timing.

        Used when segment-level timing is not available.
        """
        segments = self._split_text_into_segments(text)

        # Distribute timing evenly across segments
        total_chars = sum(len(s) for s in segments)
        if total_chars == 0:
            return output_path

        current_time = 0.0
        segment_dicts = []

        for seg_text in segments:
            seg_duration = (
                audio_duration * len(seg_text) / total_chars
            )
            segment_dicts.append({
                "text": seg_text,
                "start_time": current_time,
                "end_time": current_time + seg_duration,
            })
            current_time += seg_duration

        return self.generate_from_segments(
            segment_dicts, output_path
        )

    def generate_from_word_timestamps(
        self,
        words: list[dict],
        output_path: Path,
    ) -> Path:
        """
        Generate SRT from word-level timestamps (from edge-tts).

        Uses sentence-first grouping:
        1. Group words into sentences (by punctuation)
        2. If sentence fits in max_chars_per_line → 1 line
        3. If too long → split by commas/clauses into 2 lines

        Args:
            words: List of dicts with keys:
                - text: str
                - start: float (seconds)
                - end: float (seconds)
            output_path: Where to save the SRT file
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not words:
            return output_path

        # Step 1: Group words into sentences by punctuation
        sentences = self._words_to_sentences(words)

        # Step 2: Create SRT entries from sentences
        srt_entries = []
        entry_index = 1

        for sentence_words in sentences:
            if not sentence_words:
                continue

            sentence_text = " ".join(w["text"] for w in sentence_words)
            sentence_start = sentence_words[0]["start"]
            sentence_end = sentence_words[-1]["end"]
            sentence_len = len(sentence_text)

            # If sentence fits in one line → use as-is
            if sentence_len <= self.max_chars_per_line:
                srt_entries.append({
                    "index": entry_index,
                    "start": sentence_start,
                    "end": sentence_end,
                    "text": sentence_text,
                })
                entry_index += 1
            else:
                # Split long sentence into multiple lines (recursive)
                lines = self._split_sentence_into_lines(
                    sentence_words, sentence_start, sentence_end
                )
                for line_text, line_start, line_end in lines:
                    if line_text.strip():
                        srt_entries.append({
                            "index": entry_index,
                            "start": line_start,
                            "end": line_end,
                            "text": line_text.strip(),
                        })
                        entry_index += 1

        self._write_srt(srt_entries, output_path)

        print(f"  ✅ Subtitles generated (word-level): "
              f"{output_path.name} ({len(srt_entries)} entries)")
        return output_path

    def _words_to_sentences(self, words: list[dict]) -> list[list[dict]]:
        """
        Group words into sentences by punctuation marks.

        Sentence endings: . ! ? 。 ！ ？
        Clause breaks (for splitting long sentences): , ; : —
        """
        SENTENCE_ENDS = {".", "!", "?", "。", "！", "？"}
        CLAUSE_BREAKS = {",", ";", ":", "—"}

        sentences = []
        current_sentence = []

        for word_info in words:
            current_sentence.append(word_info)

            # Check if this word ends a sentence
            word_clean = word_info["text"].rstrip()
            if word_clean and word_clean[-1] in SENTENCE_ENDS:
                sentences.append(current_sentence)
                current_sentence = []

        # Add remaining words as last sentence
        if current_sentence:
            sentences.append(current_sentence)

        return sentences

    def _split_sentence_into_lines(
        self,
        sentence_words: list[dict],
        sentence_start: float,
        sentence_end: float,
    ) -> list[tuple[str, float, float]]:
        """
        Recursively split a long sentence into lines that fit max_chars_per_line.

        Each line should be ≤ max_chars_per_line characters.
        Tries to split at clause breaks (, ; : —) first.

        Returns list of (text, start, end) tuples.
        """
        CLAUSE_BREAKS = {",", ";", ":", "—"}

        # Build full text
        full_text = " ".join(w["text"] for w in sentence_words)

        # If fits in one line, return as-is
        if len(full_text) <= self.max_chars_per_line:
            return [(full_text, sentence_start, sentence_end)]

        # Try to find a good split point (at clause break, near midpoint)
        best_split_idx = None
        best_split_pos = len(full_text) // 2  # Default: midpoint

        for i, word_info in enumerate(sentence_words):
            word_clean = word_info["text"].rstrip()
            if word_clean and word_clean[-1] in CLAUSE_BREAKS:
                text_before = " ".join(w["text"] for w in sentence_words[:i + 1])
                if abs(len(text_before) - len(full_text) // 2) < abs(best_split_pos - len(full_text) // 2):
                    best_split_idx = i + 1
                    best_split_pos = len(text_before)

        # If no good clause break found, split at midpoint
        if best_split_idx is None:
            char_count = 0
            for i, word_info in enumerate(sentence_words):
                char_count += len(word_info["text"]) + 1
                if char_count >= len(full_text) // 2:
                    best_split_idx = i + 1
                    break
            if best_split_idx is None:
                best_split_idx = max(1, len(sentence_words) // 2)

        # Split into two halves
        left_words = sentence_words[:best_split_idx]
        right_words = sentence_words[best_split_idx:]

        # Recursively split each half if still too long
        left_lines = self._split_sentence_into_lines(
            left_words, sentence_start, left_words[-1]["end"]
        ) if left_words else []

        right_lines = self._split_sentence_into_lines(
            right_words, right_words[0]["start"], sentence_end
        ) if right_words else []

        return left_lines + right_lines

    def _split_into_subtitle_chunks(
        self, text: str
    ) -> list[str]:
        """Split text into subtitle-sized chunks."""
        # First split by sentences
        sentences = re.split(
            r'(?<=[.!?。！？])\s+', text.strip()
        )

        chunks = []
        current_chunk = ""

        for sentence in sentences:
            if not sentence.strip():
                continue

            # If single sentence is too long, split by commas
            if len(sentence) > self.max_chars_per_line * 2:
                sub_chunks = self._split_long_sentence(sentence)
                chunks.extend(sub_chunks)
                continue

            # Check if adding this sentence exceeds limit
            test = (
                f"{current_chunk} {sentence}".strip()
                if current_chunk else sentence
            )

            if len(test) <= self.max_chars_per_line * self.max_lines:
                current_chunk = test
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk)

        return chunks if chunks else [text[:self.max_chars_per_line]]

    def _split_long_sentence(self, sentence: str) -> list[str]:
        """Split a long sentence by commas/clauses."""
        # Split by commas, semicolons, or clause markers
        parts = re.split(r'[,;]\s*', sentence)

        chunks = []
        current_chunk = ""

        for part in parts:
            part = part.strip()
            if not part:
                continue

            test = (
                f"{current_chunk}, {part}".strip()
                if current_chunk else part
            )

            if len(test) <= self.max_chars_per_line * self.max_lines:
                current_chunk = test
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = part

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _split_text_into_segments(
        self, text: str
    ) -> list[str]:
        """Split full text into timed segments."""
        # Split by paragraphs first
        paragraphs = [
            p.strip()
            for p in text.split("\n\n")
            if p.strip()
        ]

        if len(paragraphs) <= 1:
            # Split by sentences
            return re.split(
                r'(?<=[.!?。！？])\s+', text.strip()
            )

        return paragraphs

    def _format_srt_time(self, seconds: float) -> str:
        """Format seconds to SRT timestamp (HH:MM:SS,mmm)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)

        return (
            f"{hours:02d}:{minutes:02d}:{secs:02d},"
            f"{millis:03d}"
        )

    def _write_srt(
        self, entries: list[dict], output_path: Path
    ) -> None:
        """Write SRT file from entries."""
        lines = []

        for entry in entries:
            start = self._format_srt_time(entry["start"])
            end = self._format_srt_time(entry["end"])
            text = entry["text"]

            lines.append(str(entry["index"]))
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")  # Blank line separator

        output_path.write_text(
            "\n".join(lines), encoding="utf-8"
        )


def burn_subtitles_into_video(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    font_size: int = 24,
    font_color: str = "white",
    outline_color: str = "black",
    outline_width: int = 2,
    position: str = "bottom",
) -> Path:
    """
    Burn SRT subtitles into video using ffmpeg.

    Args:
        video_path: Input video file
        srt_path: SRT subtitle file
        output_path: Output video with subtitles
        font_size: Subtitle font size
        font_color: Subtitle text color
        outline_color: Text outline color
        outline_width: Text outline width
        position: Subtitle position (bottom, center, top)

    Returns:
        Path to output video
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Calculate margin based on position
    if position == "top":
        margin_v = 30
    elif position == "center":
        margin_v = 0
    else:  # bottom
        margin_v = 30

    # Escape path for ffmpeg subtitles filter
    srt_escaped = str(srt_path).replace("\\", "\\\\").replace(":", "\\:")

    subtitle_filter = (
        f"subtitles='{srt_escaped}'"
        f":force_style='FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"  # White
        f"OutlineColour=&H00000000,"  # Black outline
        f"Outline={outline_width},"
        f"Shadow=1,"
        f"MarginV={margin_v},"
        f"Alignment=2'"  # Bottom center
    )

    command = [
        _FFMPEG,
        "-y",
        "-i", str(video_path),
        "-vf", subtitle_filter,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "copy",
        str(output_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to burn subtitles:\n{result.stderr}"
        )

    print(f"  ✅ Subtitles burned into video: {output_path.name}")
    return output_path
