"""
Auto-pause engine — the rhythm layer (spec §6).

How a pause is decided
----------------------
Every pause is built from four additive contributions, so the report can
show exactly where the milliseconds came from:

    final = punctuation + semantic + context - natural_tts

then clamped, unless the writer overrode it.

1. **punctuation** — the sentence's closing mark picks a base value
   (`.` 700ms, `?` 1500ms, `!` 1000ms, `...` 2000ms).
2. **semantic** — a discourse marker starting the sentence shifts that
   base by a fixed amount.  The most specific marker wins, so
   "nhưng khoan đã" counts as suspense rather than matching the twist
   marker "nhưng", and the bonuses never stack.
3. **context** — where the pause sits in the video: the opening scene caps
   it, the closing scene and section breaks raise a floor, and any SFX or
   overlay still running at the scene boundary raises the floor further.
4. **natural_tts** — silence the voice model already left at the end of
   the sentence, subtracted so the pause is not doubled.

Two deliberate deviations from the written spec, both to keep the numbers
predictable and the report truthful:

* The spec multiplies its three layers (`punctuation x semantic x
  context`).  Multipliers compound: 1500ms x 1.5 x 1.5 is already 3375ms,
  and everything that reaches the cap ends up identical to everything else
  at the cap, which destroys the rhythm the engine exists to create.
  Additive contributions preserve ordering and make the breakdown add up.
* The spec computes one pause per scene, which makes the punctuation and
  semantic layers meaningless in a scene holding several sentences.  Every
  sentence gets its own pause; the last sentence of a scene additionally
  carries the scene-level (context) pause.

Ordering of operations
----------------------
The scene-closing pause needs to know how long the scene's audio actually
is, but that length includes the inter-sentence pauses.  Callers therefore
use the two-step API: `inter_sentence_pauses()` first (punctuation and
semantics only, no audio length needed), then `scene_pause()` once the
real length is known.  `compute()` composes both for callers who already
know the audio length.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from autovid.domain.sentences import (
    ELLIPSIS,
    EXCLAMATION,
    NONE,
    PERIOD,
    QUESTION,
    classify_ending,
    split_sentences,
)
from autovid.domain.script import AutoPauseConfig, Scene

# --------------------------------------------------------------------------
# Layer 1 — punctuation (spec §6.1)
# --------------------------------------------------------------------------

PUNCTUATION_PAUSE_MS: dict[str, int] = {
    QUESTION: 1500,
    ELLIPSIS: 2000,
    EXCLAMATION: 1000,
    PERIOD: 700,
    # No closing punctuation reads like a trailing-off clause; keep it
    # short so unterminated fragments do not stall the video.
    NONE: 300,
}

# --------------------------------------------------------------------------
# Layer 2 — semantic markers (spec §6.2)
#
# Markers only match at the *start* of a sentence.  "Nhưng" appears in the
# middle of ordinary Vietnamese sentences constantly, so matching anywhere
# would fire the twist bonus all the time and mean nothing.
# --------------------------------------------------------------------------

TWIST_MARKERS = (
    "nhưng",
    "tuy nhiên",
    "thế nhưng",
    "trái lại",
    "ngược lại",
)

SUSPENSE_MARKERS = (
    "nhưng khoan",
    "đợi đã",
    "và đây là điều thú vị",
    "bí mật là",
    "sự thật là",
    "không ai ngờ",
    "hóa ra",
    "hoá ra",
)

CONCLUSION_MARKERS = (
    "tóm lại",
    "vậy nên",
    "kết luận là",
    "nói cách khác",
)

ENUMERATION_MARKERS = (
    "thứ nhất",
    "thứ hai",
    "thứ ba",
    "tiếp theo",
    "sau đó",
)

# Checked for equal-length ties in this order.
SEMANTIC_LAYERS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("twist", TWIST_MARKERS, 800),
    ("suspense", SUSPENSE_MARKERS, 500),
    ("conclusion", CONCLUSION_MARKERS, 300),
    ("enumeration", ENUMERATION_MARKERS, -250),
)

# --------------------------------------------------------------------------
# Layer 3 — context (spec §6.3)
# --------------------------------------------------------------------------

FIRST_SCENE_MAX_MS = 500
LAST_SCENE_MIN_MS = 2000
SECTION_BREAK_MIN_MS = 2500
RHETORICAL_QUESTION_BONUS_MS = 400

# --------------------------------------------------------------------------
# Pause classification for the report (spec §6.5).  The spec's table has
# overlapping ranges; these buckets are disjoint so every pause lands in
# exactly one.
# --------------------------------------------------------------------------

PAUSE_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("micro", 0, 399),
    ("comedic", 400, 799),
    ("thinking", 800, 1499),
    ("transitional", 1500, 1999),
    ("dramatic", 2000, 1_000_000),
)


def bucket_for(pause_ms: int) -> str:
    for name, low, high in PAUSE_BUCKETS:
        if low <= pause_ms <= high:
            return name
    return "dramatic"


def semantic_bonus(sentence: str) -> tuple[str, int]:
    """
    Return (layer name, ms bonus) for the sentence's strongest marker.

    The most specific marker wins, so "nhưng khoan đã" counts as suspense
    even though it also starts with the twist marker "nhưng".  Equally long
    matches fall back to the layer order in SEMANTIC_LAYERS.
    """
    head = sentence.lstrip("\"'“”‘’(-— ").lower()

    best: tuple[str, int] | None = None
    best_key: tuple[int, int] | None = None

    for priority, (name, markers, bonus) in enumerate(SEMANTIC_LAYERS):
        for marker in markers:
            if not head.startswith(marker):
                continue
            key = (len(marker), -priority)
            if best_key is None or key > best_key:
                best_key = key
                best = (name, bonus)

    return best if best is not None else ("none", 0)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PauseBreakdown:
    """Where every millisecond of a pause came from."""

    punctuation_ms: int
    semantic_ms: int
    context_ms: int
    natural_ms: int
    final_ms: int
    semantic_layer: str = "none"

    def to_dict(self) -> dict:
        return {
            "punctuation_ms": self.punctuation_ms,
            "semantic_ms": self.semantic_ms,
            "semantic_layer": self.semantic_layer,
            "context_ms": self.context_ms,
            "natural_tts_ms": self.natural_ms,
            "final_ms": self.final_ms,
        }


@dataclass(frozen=True)
class SentencePause:
    index: int
    text: str
    ending: str
    is_last: bool
    target_ms: int
    pause_ms: int
    breakdown: PauseBreakdown

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "ending": self.ending,
            "is_last_of_scene": self.is_last,
            "target_ms": self.target_ms,
            "pause_ms": self.pause_ms,
            "breakdown": self.breakdown.to_dict(),
        }


@dataclass(frozen=True)
class ScenePacing:
    scene_id: int
    source: str
    sentences: tuple[SentencePause, ...]
    pause_ms: int
    breakdown: PauseBreakdown

    @property
    def inter_sentence_pause_ms(self) -> int:
        return sum(
            sentence.pause_ms
            for sentence in self.sentences
            if not sentence.is_last
        )

    def to_dict(self) -> dict:
        return {
            "id": self.scene_id,
            "source": self.source,
            "pause_after_ms": self.pause_ms,
            "breakdown": self.breakdown.to_dict(),
            "sentences": [sentence.to_dict() for sentence in self.sentences],
        }


@dataclass(frozen=True)
class PacingResult:
    scenes: tuple[ScenePacing, ...]
    distribution: dict[str, int]
    total_pause_ms: int
    average_pause_ms: int

    def to_dict(self, settings: dict | None = None) -> dict:
        return {
            "settings": settings or {},
            "total_scenes": len(self.scenes),
            "total_pause_ms": self.total_pause_ms,
            "average_pause_ms": self.average_pause_ms,
            "pause_distribution": self.distribution,
            "scenes": [scene.to_dict() for scene in self.scenes],
        }


EMPTY_BREAKDOWN = PauseBreakdown(0, 0, 0, 0, 0)


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


class PacingEngine:
    """
    Computes every pause in the video.

    Pure by design: it never touches audio files, it only consumes
    measurements the caller already took.  That makes the whole rhythm
    layer testable without rendering anything.
    """

    def __init__(
        self,
        config: AutoPauseConfig,
        section_breaks: tuple[int, ...],
        *,
        scene_count: int,
    ) -> None:
        self.config = config
        self.section_breaks = set(section_breaks)
        self.scene_count = scene_count

    # -- public API -------------------------------------------------------

    @staticmethod
    def units(scene: Scene) -> list[str]:
        """The text units that are synthesized and paused separately."""
        sentences = split_sentences(scene.text)
        if sentences:
            return sentences
        stripped = scene.text.strip()
        return [stripped] if stripped else []

    def inter_sentence_pauses(
        self,
        scene: Scene,
        *,
        natural_trailing_ms: list[int] | None = None,
        units: list[str] | None = None,
    ) -> list[SentencePause]:
        """
        Pauses for every unit except the scene's last one.

        Only the punctuation and semantic layers apply here, so this can
        run before the scene's real audio length is known — which it must,
        because that length depends on these very pauses.

        `units` overrides the split, which is how a whole-scene granularity
        is expressed (one unit, so no pauses inside the scene).
        """
        sentences = units if units is not None else self.units(scene)
        naturals = natural_trailing_ms or []

        pauses: list[SentencePause] = []
        for position, sentence in enumerate(sentences[:-1]):
            natural = naturals[position] if position < len(naturals) else 0
            target, breakdown = self._sentence_pause(sentence, natural_ms=natural)
            pauses.append(
                SentencePause(
                    index=position,
                    text=sentence,
                    ending=classify_ending(sentence),
                    is_last=False,
                    target_ms=target,
                    pause_ms=breakdown.final_ms,
                    breakdown=breakdown,
                )
            )
        return pauses

    def scene_pause(
        self,
        scene: Scene,
        *,
        index: int,
        natural_ms: int = 0,
        scene_audio_ms: int = 0,
        sfx_end_ms: int = 0,
        overlay_end_ms: int = 0,
        explicit_pause_ms: int | None = None,
        units: list[str] | None = None,
    ) -> tuple[int, PauseBreakdown]:
        """
        The pause that closes a scene (spec §6.3).

        `scene_audio_ms` is the scene's measured narration length, including
        the inter-sentence pauses.  `sfx_end_ms` / `overlay_end_ms` are the
        furthest points any SFX or overlay reaches inside the scene.
        """
        sentences = units if units is not None else self.units(scene)
        if not sentences:
            return 0, EMPTY_BREAKDOWN

        return self._scene_pause(
            sentences[-1],
            index=index,
            natural_ms=natural_ms,
            scene_audio_ms=scene_audio_ms,
            sfx_end_ms=sfx_end_ms,
            overlay_end_ms=overlay_end_ms,
            explicit_pause_ms=explicit_pause_ms,
        )

    def source_for(
        self, scene: Scene, explicit_pause_ms: int | None
    ) -> str:
        if explicit_pause_ms is not None:
            return (
                "override_scene"
                if scene.pause_after_ms is not None
                else "override_custom"
            )
        return "auto" if self.config.enabled else "disabled"

    def assemble_scene(
        self,
        scene: Scene,
        *,
        index: int,
        inter_sentence: list[SentencePause],
        scene_pause_ms: int,
        scene_breakdown: PauseBreakdown,
        explicit_pause_ms: int | None,
        units: list[str] | None = None,
    ) -> ScenePacing:
        """
        Combine the computed halves into the full scene pacing.

        When auto-pause is off, everything the engine computed is zeroed
        while an explicit writer override is preserved.
        """
        sentences = units if units is not None else self.units(scene)

        # `scene_pause_ms` is the target before natural silence is
        # subtracted; the pause actually inserted is `breakdown.final_ms`.
        # Using the target here would double up with the silence the voice
        # model already left at the end of the scene.
        last = SentencePause(
            index=len(sentences) - 1,
            text=sentences[-1] if sentences else "",
            ending=classify_ending(sentences[-1]) if sentences else NONE,
            is_last=True,
            target_ms=scene_pause_ms,
            pause_ms=scene_breakdown.final_ms,
            breakdown=scene_breakdown,
        )

        items = [*inter_sentence, last]

        if not self.config.enabled and explicit_pause_ms is None:
            items = [
                replace(
                    item,
                    target_ms=0,
                    pause_ms=0,
                    breakdown=replace(item.breakdown, final_ms=0),
                )
                for item in items
            ]

        final = items[-1]
        return ScenePacing(
            scene_id=scene.id,
            source=self.source_for(scene, explicit_pause_ms),
            sentences=tuple(items),
            pause_ms=final.pause_ms,
            breakdown=final.breakdown,
        )

    def compute(
        self,
        scene: Scene,
        *,
        index: int,
        natural_trailing_ms: list[int] | None = None,
        scene_audio_ms: int = 0,
        sfx_end_ms: int = 0,
        overlay_end_ms: int = 0,
        explicit_pause_ms: int | None = None,
        units: list[str] | None = None,
    ) -> ScenePacing:
        """
        Full pacing for one scene in a single call.

        Convenience wrapper over `inter_sentence_pauses` + `scene_pause`.
        The TTS stage calls those two halves directly, because a scene's
        real length is not known until its sentences are synthesized.
        """
        sentences = units if units is not None else self.units(scene)
        if not sentences:
            return ScenePacing(scene.id, "empty", (), 0, EMPTY_BREAKDOWN)

        naturals = natural_trailing_ms or []
        inter = self.inter_sentence_pauses(
            scene, natural_trailing_ms=naturals, units=sentences
        )
        target, breakdown = self.scene_pause(
            scene,
            index=index,
            natural_ms=naturals[-1] if naturals else 0,
            scene_audio_ms=scene_audio_ms,
            sfx_end_ms=sfx_end_ms,
            overlay_end_ms=overlay_end_ms,
            explicit_pause_ms=explicit_pause_ms,
            units=sentences,
        )

        return self.assemble_scene(
            scene,
            index=index,
            inter_sentence=inter,
            scene_pause_ms=target,
            scene_breakdown=breakdown,
            explicit_pause_ms=explicit_pause_ms,
            units=sentences,
        )

    # -- internal layers --------------------------------------------------

    def _sentence_pause(
        self, sentence: str, *, natural_ms: int
    ) -> tuple[int, PauseBreakdown]:
        """A pause inside a scene: punctuation and semantics, no context."""
        punctuation, semantic, layer = self._base(sentence)
        breakdown = self._assemble(
            punctuation_ms=punctuation,
            semantic_ms=semantic,
            layer=layer,
            context_delta=0,
            natural_ms=natural_ms,
            clamp=True,
        )
        return breakdown.final_ms, breakdown

    def _scene_pause(
        self,
        sentence: str,
        *,
        index: int,
        natural_ms: int,
        scene_audio_ms: int,
        sfx_end_ms: int,
        overlay_end_ms: int,
        explicit_pause_ms: int | None,
    ) -> tuple[int, PauseBreakdown]:
        """The pause that closes a scene: everything plus context."""
        punctuation, semantic, layer = self._base(sentence)
        base = punctuation + semantic

        # Context is expressed as a delta so the breakdown still adds up.
        context = 0
        is_first = index == 0
        is_last = index == self.scene_count - 1

        # The opening scene stays snappy; the floor rules below still win
        # over this cap, so nothing that is still playing gets cut off.
        if is_first:
            context += min(0, FIRST_SCENE_MAX_MS - base)
        if is_last:
            context += max(0, LAST_SCENE_MIN_MS - (base + context))
        if index in self.section_breaks:
            context += max(0, SECTION_BREAK_MIN_MS - (base + context))
        if classify_ending(sentence) == QUESTION:
            # A question thrown at the viewer needs room to land.
            context += RHETORICAL_QUESTION_BONUS_MS

        for end_ms in (sfx_end_ms, overlay_end_ms):
            if end_ms > scene_audio_ms:
                context += max(0, end_ms - scene_audio_ms - context)

        if explicit_pause_ms is not None:
            # A writer override replaces the computed target and bypasses
            # the clamp, but natural silence is still respected so the
            # audible pause matches what was asked for.
            natural = natural_ms if self.config.respect_tts_natural_pause else 0
            natural = min(natural, explicit_pause_ms)
            return explicit_pause_ms, PauseBreakdown(
                punctuation_ms=punctuation,
                semantic_ms=semantic,
                context_ms=context,
                natural_ms=-natural,
                final_ms=max(0, explicit_pause_ms - natural),
                semantic_layer=layer,
            )

        breakdown = self._assemble(
            punctuation_ms=punctuation,
            semantic_ms=semantic,
            layer=layer,
            context_delta=context,
            natural_ms=natural_ms,
            clamp=True,
        )
        # Derive the target back out of the breakdown so that the reported
        # target always equals punctuation + semantic + context, even when
        # the configured floor/ceiling moved it.
        return (
            breakdown.punctuation_ms
            + breakdown.semantic_ms
            + breakdown.context_ms,
            breakdown,
        )

    @staticmethod
    def _base(sentence: str) -> tuple[int, int, str]:
        """Return (punctuation_ms, semantic_ms, semantic_layer)."""
        ending = classify_ending(sentence)
        punctuation = PUNCTUATION_PAUSE_MS.get(
            ending, PUNCTUATION_PAUSE_MS[PERIOD]
        )
        layer, bonus = semantic_bonus(sentence)
        return punctuation, bonus, layer

    def _assemble(
        self,
        *,
        punctuation_ms: int,
        semantic_ms: int,
        layer: str,
        context_delta: int,
        natural_ms: int,
        clamp: bool,
    ) -> PauseBreakdown:
        context_ms = context_delta
        target = punctuation_ms + semantic_ms + context_ms

        # The writer's floor/ceiling applies to the computed value, before
        # natural silence is subtracted.
        if clamp:
            clamped = min(
                max(target, self.config.min_pause_ms), self.config.max_pause_ms
            )
            context_ms += clamped - target
            target = clamped

        natural = natural_ms if self.config.respect_tts_natural_pause else 0
        if natural > target:
            natural = target

        return PauseBreakdown(
            punctuation_ms=punctuation_ms,
            semantic_ms=semantic_ms,
            context_ms=context_ms,
            natural_ms=-natural,
            final_ms=max(0, target - natural),
            semantic_layer=layer,
        )


def build_pacing_result(scene_pacings: list[ScenePacing]) -> PacingResult:
    """Aggregate per-scene pacings into the report payload."""
    distribution = {name: 0 for name, _, _ in PAUSE_BUCKETS}
    total = 0
    count = 0

    for scene in scene_pacings:
        for sentence in scene.sentences:
            bucket = bucket_for(sentence.pause_ms)
            distribution[bucket] = distribution.get(bucket, 0) + 1
            total += sentence.pause_ms
            count += 1

    return PacingResult(
        scenes=tuple(scene_pacings),
        distribution=distribution,
        total_pause_ms=total,
        average_pause_ms=round(total / count) if count else 0,
    )
