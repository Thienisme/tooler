from ai_mystery_story.domain.narration.narration import Narration
from ai_mystery_story.domain.narration.narration_segment import NarrationSegment
from ai_mystery_story.domain.story.story_blueprint import StoryBlueprint
from ai_mystery_story.infrastructure.ai.ai_provider import AIProvider


class NarrationGenerator:
    """Generate one canonical full-story narration in a single AI request."""

    def __init__(
        self,
        provider: AIProvider,
        request_delay_seconds: int = 0,
    ):
        self.provider = provider
        # Retained for backwards-compatible construction; unused because there
        # is exactly one full-story request.
        self.request_delay_seconds = request_delay_seconds

    def generate(
        self,
        blueprint: StoryBlueprint,
    ) -> Narration:
        if blueprint.case_file is None:
            raise ValueError(
                "This story has no case_file. Run generate_story.py again "
                "before generating narration."
            )

        full_text = self.provider.generate(
            self._build_full_story_prompt(blueprint)
        ).strip()
        if not full_text:
            raise RuntimeError("Gemini returned empty full narration")

        return Narration(
            segments=[
                NarrationSegment(
                    order=1,
                    title="Full narration",
                    text=full_text,
                )
            ]
        )

    def _build_full_story_prompt(
        self,
        blueprint: StoryBlueprint,
    ) -> str:
        case_file = blueprint.case_file
        beats = "\n".join(
            (
                f"{beat.order}. {beat.title}\n"
                f"Events: {beat.summary}\n"
                f"Purpose: {beat.purpose}\n"
                f"Target duration: {beat.target_duration_minutes} minutes"
            )
            for beat in blueprint.beats
        )

        return f"""
You are an expert Vietnamese detective-fiction writer.

Write the COMPLETE Vietnamese mystery narration from beginning to end in ONE
continuous draft. This is one request and one canonical story, not separately
written chapters. The narration will be split mechanically after generation,
so it must remain coherent even if paragraph breaks move between audio files.

AUDIO DURATION & NARRATION LENGTH GUIDANCE:

- Target audio narration duration: 50 to 75+ minutes (minimum around 45-50 minutes).
- Minimum word count: 7,000 Vietnamese words. Aim for 8,000 to 10,000+ words.
- DO NOT stop writing until you have reached at least 7,000 words. If you finish the story before that, expand scenes, add internal monologue, deepen dialogue, and enrich atmosphere until the minimum is met.
- PRIORITY: Write as much rich, immersive, atmospheric, and detailed prose as possible! Focus on expanding dialogues, character motivations, physical clue analysis, atmospheric tension, and step-by-step police investigation.
- DO NOT artificially cap or restrict the text. Let the narrative flow naturally while prioritizing maximum depth and audio duration.
- Preferred target: 8,000–10,000+ Vietnamese words.
- The story must remain coherent and logically consistent from beginning to end. Do not add filler solely to increase word count; expansions should contribute to atmosphere, character development, clues, investigation, suspense, or plot progression.

PRIVATE, IMMUTABLE CASE FILE

Title: {blueprint.title}
Premise: {blueprint.premise}
Setting: {blueprint.setting}
Crime type: {blueprint.crime_type}
Investigation type: {blueprint.investigation_type}
Protagonist: {blueprint.protagonist}
Central mystery: {blueprint.central_mystery}
Twist: {blueprint.twist_type}
Ending: {blueprint.ending_type}

Characters:
{chr(10).join(f'- {character}' for character in blueprint.characters)}

Culprit: {case_file.culprit}
Victim: {case_file.victim}
Motive: {case_file.motive}
Method: {case_file.method}
PRIVATE real timeline (never state it as narration before its planned reveal):
{chr(10).join(f'- {item}' for item in case_file.real_timeline)}
Initial false timeline / audience belief:
{chr(10).join(f'- {item}' for item in case_file.false_timeline)}
Initial police belief: {case_file.initial_belief}
Alibi: {case_file.alibi}
Fair-play clues:
{chr(10).join(f'- {item}' for item in case_file.clues)}
Red herrings:
{chr(10).join(f'- {item}' for item in case_file.red_herrings)}
Investigation solution: {case_file.investigation_solution}
Twist: {case_file.twist}
Final resolution: {case_file.final_resolution}
Facts that must never change:
{chr(10).join(f'- {item}' for item in case_file.immutable_facts)}
Planned information-reveal order:
{chr(10).join(
    f'- {reveal.order}. Beat {reveal.beat}: {reveal.event}. '
    f'Reveal: {reveal.reveals}. '
    f'Audience belief after: {reveal.audience_belief_after}'
    for reveal in case_file.reveal_order
) or '- This legacy blueprint has no explicit reveal schedule; follow the beats.'}

REQUIRED STORY PROGRESSION
{beats}

NON-NEGOTIABLE CONTINUITY & LEGAL SAFETY RULES

- LEGAL SAFETY & FICTIONAL NAMES: Real provinces, cities, districts, streets, and public bridges (Hà Nội, Cầu Giấy, cầu Nhật Tân...) ARE ALLOWED. However, ALL company names, building/residential estate names, brand names, and character names MUST BE 100% FICTIONAL (e.g. NEVER use real company or corporate names like Vinhomes, Viettel, FPT, etc.). All characters are completely fictional.
- The case file is the only source of truth for identities, timeline, motives,
  method, alibi, clues, red herrings, twist, and final solution.
- Do not invent or change a material fact, suspect, route, device, time,
  weapon, clue, or explanation.
- Before writing, silently verify that every event can occur in the exact
  timeline and that every later revelation agrees with earlier scenes.
- Treat the real timeline, culprit, motive, method, and final solution as
  private author knowledge. Reveal them only through the planned information
  reveals and their corresponding beats; do not narrate them as omniscient fact.
- Preserve the false timeline as the investigator's and audience's plausible
  working belief until a planned reveal disproves it.

WRITING REQUIREMENTS

- Write natural, immersive Vietnamese prose for audio narration.
- Show scenes, dialogue, investigation, deductions, and evidence rather than
  merely summarizing the beats.
- Keep names, locations, motivations, and physical details consistent.
- End with the requested resolution.

OUTPUT

Return ONLY the complete Vietnamese narration. Do not include titles,
chapter/segment headings, beat labels, JSON, Markdown, or commentary.
""".strip()
