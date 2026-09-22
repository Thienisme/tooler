"""
Sentence-level helpers shared by validation and the pacing engine.

The pacing engine needs to know where sentences end (to place a real
pause) and which punctuation closed them (to size the pause), so both
rules live here as part of the domain instead of being reimplemented in
each stage.
"""

from __future__ import annotations

import re

# Split on whitespace that follows sentence-final punctuation.  Keeping
# the punctuation attached to the sentence means "…" and "..." survive
# intact, and decimals like "1.5" never split because no space follows.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])[\s\u00a0]+")

# Punctuation that can close a sentence, including common closing quotes.
_SENTENCE_MARKS = ".!?…"
_CLOSING_CHARS = "\"')”’»"

# Ending classes, in the order they are tested.
QUESTION = "question"
EXCLAMATION = "exclamation"
ELLIPSIS = "ellipsis"
PERIOD = "period"
NONE = "none"


def split_sentences(text: str) -> list[str]:
    """
    Split text into sentences, keeping trailing punctuation attached.

    Empty fragments are dropped so callers never synthesize silence.
    """
    stripped = text.strip()
    if not stripped:
        return []
    return [
        fragment.strip()
        for fragment in _SENTENCE_SPLIT.split(stripped)
        if fragment.strip()
    ]


def count_sentences(text: str) -> int:
    return len(split_sentences(text))


def classify_ending(sentence: str) -> str:
    """
    Classify how a sentence ends.

    Returns one of 'question', 'exclamation', 'ellipsis', 'period', 'none'.
    Trailing quotes/brackets are ignored so “…?” still reads as a question.
    """
    stripped = sentence.rstrip().rstrip(_CLOSING_CHARS).rstrip()

    if not stripped:
        return NONE
    if stripped.endswith("..."):
        return ELLIPSIS

    last = stripped[-1]
    if last == "?":
        return QUESTION
    if last == "!":
        return EXCLAMATION
    if last == ".":
        return PERIOD
    if last == "…":
        return ELLIPSIS
    return NONE
