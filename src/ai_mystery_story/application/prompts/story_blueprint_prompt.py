class StoryBlueprintPrompt:

    @staticmethod
    def _build_mechanisms_block(resolved_mechanisms: dict | None) -> str:
        """
        Build a human-readable guidance block from resolved mechanism objects.

        ``resolved_mechanisms`` has the shape::

            {
                "primary": {mechanism dict} | None,
                "secondary": [{mechanism dict}, ...],
            }
        """
        if not resolved_mechanisms:
            return ""

        lines: list[str] = []

        primary = resolved_mechanisms.get("primary")
        secondary = resolved_mechanisms.get("secondary", [])

        if not primary and not secondary:
            return ""

        lines.append("============================================================")
        lines.append("MECHANISM LIBRARY REFERENCES")
        lines.append("============================================================")
        lines.append("")
        lines.append(
            "The following mechanisms are abstract principles taken from a "
            "curated mystery library. Use them as structural blueprints — "
            "NOT as plot elements to copy. All characters, settings, "
            "timelines, and clue sequences must be designed independently."
        )
        lines.append("")

        if primary:
            lines.append(
                f"PRIMARY MECHANISM: [{primary['id']}] "
                f"{primary['name']} / {primary['name_vi']}"
            )
            lines.append(f"Category: {primary['category']}")
            lines.append(f"Description: {primary['description']}")
            lines.append(
                f"What it fools: {primary['what_it_fools']}"
            )
            lines.append(
                f"Typical false assumption to exploit: "
                f"{primary['typical_false_assumption']}"
            )
            clue_types = ", ".join(primary.get("fair_play_clue_types", []))
            if clue_types:
                lines.append(f"Suggested fair-play clue types: {clue_types}")
            avoid = primary.get("avoid_when", [])
            if avoid:
                lines.append(
                    "Avoid when: " + "; ".join(avoid)
                )

        if secondary:
            lines.append("")
            lines.append("SECONDARY MECHANISMS (supporting / layering):")
            for m in secondary:
                lines.append(
                    f"  [{m['id']}] {m['name']} / {m['name_vi']} "
                    f"— {m['description']}"
                )
                lines.append(
                    f"       False assumption: {m['typical_false_assumption']}"
                )

        lines.append("")
        lines.append(
            "Combine the primary and secondary mechanisms so that each "
            "serves a distinct layer of the deception. The primary mechanism "
            "drives the central false belief; secondary mechanisms add "
            "misdirection, reinforce the false picture, or complicate "
            "the investigation. Every mechanism used must have at least "
            "one corresponding fair-play clue planted before the reveal."
        )

        return "\n".join(lines)

    @staticmethod
    def build(
        title: str,
        premise: str,
        setting: str,
        crime_type: str,
        protagonist: str,
        ending_type: str,
        target_duration_minutes: int,
        mystery_difficulty: str = "medium",
        creative_constraints: dict[str, bool] | None = None,
        core_mechanism: str | None = None,
        fair_play_focus: str | None = None,
        false_assumption: str | None = None,
        misdirection_strategy: str | None = None,
        reveal_condition: str | None = None,
        resolved_mechanisms: dict | None = None,
    ) -> str:

        constraints = "\n".join(
            f"- {key}: {str(value).lower()}"
            for key, value in (creative_constraints or {}).items()
        ) or "- No additional constraints supplied."

        mechanism_guidance = core_mechanism or (
            "Not supplied. Invent the mechanism best suited to the premise."
        )
        fair_play_guidance = fair_play_focus or (
            "Not supplied. Choose fair-play clues that best suit the case."
        )

        false_assumption_guidance = false_assumption or (
            "Not supplied. Design the false assumption from the premise."
        )
        misdirection_guidance = misdirection_strategy or (
            "Not supplied. Design the misdirection strategy from the premise."
        )
        reveal_condition_guidance = reveal_condition or (
            "Not supplied. Design the reveal conditions from the story logic."
        )

        mechanisms_block = StoryBlueprintPrompt._build_mechanisms_block(
            resolved_mechanisms
        )

        return f"""
You are an expert mystery novelist, detective fiction writer,
crime story architect, and long-form audio storyteller.

Your task is to design a complete blueprint for a long-form
Vietnamese mystery / detective story intended for an audio duration
of approximately 30 to 60+ minutes (aiming for ~4,000 to 8,500+ Vietnamese words).

The story will later be expanded into a full Vietnamese narration
and converted into audio using text-to-speech.

This is NOT a horror story.

The story must primarily rely on:

- mystery
- investigation
- deduction
- clues
- evidence
- character motives
- contradictions
- deception
- red herrings
- hidden information
- logical revelations
- tension
- suspense
- a satisfying solution

The audience should continuously want to know:

"WHAT REALLY HAPPENED?"

and

"HOW CAN THE PROTAGONIST PROVE IT?"

============================================================
LEGAL SAFETY & FICTIONAL NAMING RULES (MANDATORY)
============================================================

To strictly eliminate legal risks, copyright/trademark issues, and defamation risks:

1. GEOGRAPHIC LOCATIONS (Real places allowed):
   - You MAY use real provinces, cities, districts, wards, streets, public bridges, and general geographic regions (e.g., Hà Nội, Cầu Giấy, Long Biên, Nam Định, đường Võ Nguyên Giáp, cầu Nhật Tân).

2. COMPANIES, BUILDINGS, RESIDENTIAL COMPLEXES, HOSPITALS, AND ORGANIZATIONS (MUST BE 100% UNIQUE & FICTIONAL):
   - You MUST NEVER use real company names, real brand names, real corporation names, real real-estate brands, or real commercial building names (e.g., NEVER use "Vinhomes", "Vincom", "Keangnam", "FPT", "Viettel", "Landmark", etc.).
   - DO NOT copy or repeat example placeholder names!
   - ALWAYS invent fresh, unique, 100% fictional, imaginative names for companies, factories, office buildings, private residential estates, and hospitals for every single story you create.

3. CHARACTER NAMES (MUST BE 100% FICTIONAL & RANDOMIZED):
   - ALL character names (investigators, victims, culprits, suspects, witnesses) MUST BE completely randomized and fictional.
   - NEVER use the real names, titles, or identities of real living or historical public figures or police officers. Any resemblance to real individuals must be purely coincidental.

============================================================
STORY TOPIC
============================================================

Title:
{title}

Premise:
{premise}

Setting:
{setting}

Crime type:
{crime_type}

Protagonist:
{protagonist}

Ending type:
{ending_type}

Design the investigation type, central mystery, culprit method, and twist
yourself. They must be the strongest fair-play solution for this particular
premise; do not reuse a fixed mystery formula.

Mystery difficulty:
{mystery_difficulty}

Creative constraints (mandatory):
{constraints}

Core mechanism guidance:
{mechanism_guidance}

Fair-play focus guidance:
{fair_play_guidance}

False assumption to exploit:
{false_assumption_guidance}

Misdirection strategy:
{misdirection_guidance}

Reveal condition (the solution must satisfy ALL of these):
{reveal_condition_guidance}

Treat the five fields above as high-level creative direction, not a complete
solution. Translate them into a specific physically plausible method, fair-play
clues, red herrings, and twist in case_file. Do not repeat their wording
mechanically or introduce facts that conflict with the premise.

{mechanisms_block}

============================================================
CORE STORY PRINCIPLE
============================================================

The story must be built around a solvable mystery.

There must be a real underlying truth.

The truth must already exist before the investigation begins.

The protagonist does not magically create the solution.

Instead, the protagonist gradually discovers the truth by:

- observing evidence
- questioning people
- identifying contradictions
- reconstructing timelines
- comparing statements
- discovering hidden relationships
- interpreting physical evidence
- testing hypotheses
- recognizing false assumptions
- connecting previously unrelated clues

The final solution must be logically supported by information
introduced earlier in the story.

The audience should be able to look back after the reveal and think:

"Everything was there. I simply misunderstood it."

============================================================
NO RANDOM TWISTS
============================================================

Do NOT create twists merely for shock value.

Every major twist must be supported by earlier information.

Do NOT suddenly introduce:

- a secret twin
- an unknown murderer
- a hidden room
- a random confession
- supernatural intervention
- a completely new character
- previously unmentioned evidence
- an unexplained coincidence

unless that element was properly established or foreshadowed earlier.

The final explanation must not depend on information that the audience
could not reasonably have known existed.

============================================================
MYSTERY STRUCTURE
============================================================

Build the story using a progression similar to:

Initial incident
        ↓
First investigation
        ↓
Initial theory
        ↓
Contradicting evidence
        ↓
New suspect / new possibility
        ↓
False lead / red herring
        ↓
Important hidden clue
        ↓
Reconstruction of events
        ↓
Major contradiction
        ↓
Breakthrough
        ↓
Final confrontation
        ↓
Solution
        ↓
Aftermath

The exact structure may vary depending on the case.

Do not make every story follow exactly the same sequence.

============================================================
THE ACTUAL CRIME
============================================================

Before designing the beats, internally determine:

1. What actually happened?
2. Who is responsible?
3. Why did they do it?
4. How was the crime committed?
5. When did each important event actually happen?
6. What evidence proves the truth?
7. What evidence initially points investigators in the wrong direction?
8. What mistake does the protagonist initially make?
9. What observation finally breaks the case?
10. Why could the crime initially appear impossible or confusing?

Do NOT output this planning separately.

Use it to construct a coherent story.

============================================================
CULPRIT LOGIC
============================================================

The culprit must have:

- a believable motive
- a believable opportunity
- a believable method
- a reason to hide the truth
- behavior consistent with their personality
- a realistic relationship to the victim or case

The culprit must not behave irrationally simply because
the plot requires them to be caught.

If the culprit makes a mistake, that mistake must be believable.

============================================================
CLUE DESIGN
============================================================

Create different categories of clues.

Use a mixture of:

1. Direct evidence
2. Circumstantial evidence
3. Timeline clues
4. Behavioral clues
5. Contradictions
6. Physical clues
7. Relationship clues
8. Financial or communication clues when appropriate
9. Misleading clues
10. Small seemingly insignificant details

Important clues should appear naturally inside the story.

Do NOT make every clue obviously important.

Some clues should initially appear meaningless.

Later, their significance should become clear.

============================================================
FAIR-PLAY MYSTERY
============================================================

The mystery should be fair to the audience.

The audience should have enough information to theoretically
reach the correct conclusion before the protagonist explains it.

Do not hide the entire solution until the final paragraph.

Instead, gradually provide enough evidence for the audience
to form and revise theories.

The protagonist may understand the significance of a clue later,
but the clue itself should have existed earlier.

============================================================
RED HERRINGS
============================================================

Use red herrings carefully.

A red herring should:

- appear plausible
- have some supporting evidence
- temporarily lead the investigation in the wrong direction
- eventually be disproved or reinterpreted

Do not create fake suspects simply to increase the number
of characters.

Prefer meaningful suspects with believable motives.

============================================================
SUSPECT DESIGN
============================================================

Create a small number of important characters.

Each major suspect should have:

- a relationship to the victim or case
- a believable motive
- something they are hiding
- information they know
- a reason to lie or conceal information

Not every suspicious character should be guilty.

Some characters may hide unrelated secrets.

This makes the investigation more realistic.

Do not create unnecessary characters.

============================================================
PROTAGONIST
============================================================

The protagonist is:

{protagonist}

Give the protagonist a clear reason to investigate the case.

The protagonist should actively investigate.

Do NOT allow the story to progress only because
other characters randomly reveal information.

The protagonist should make deductions.

The protagonist should sometimes make mistakes.

The protagonist should revise their theories when new evidence appears.

The protagonist should ultimately solve the central mystery
through reasoning and investigation.

============================================================
CENTRAL MYSTERY
============================================================

Design one precise central mystery from the premise. This question must remain
important throughout the story.

Every major beat should either:

- deepen the mystery
- provide evidence
- challenge an existing theory
- reveal character information
- increase the stakes
- narrow the possibilities
- or move the protagonist closer to the truth

Avoid scenes that exist only to fill time.

============================================================
INVESTIGATION TYPE
============================================================

Choose the investigation type that best fits the premise and make it
structurally important to solving the case.

For example, if the investigation type involves an impossible
timeline, the story should emphasize:

- timestamps
- movements
- alibis
- camera footage
- phone records
- travel times
- witness statements
- inconsistencies in the chronology

Do not merely mention the investigation type.

Make it structurally important to solving the case.

============================================================
CRIME TYPE
============================================================

The crime type is:

{crime_type}

The crime should remain consistent with this category.

The investigation, evidence, motives, consequences,
and final resolution should all support the crime type.

============================================================
TWIST
============================================================

Design a twist that is a major reinterpretation of the investigation.

The twist must:

- be surprising
- be logically possible
- be foreshadowed
- change the audience's understanding of earlier events
- help explain previously confusing evidence

The twist must NOT invalidate the entire investigation.

Instead, it should cause earlier clues to be understood differently.

============================================================
ENDING
============================================================

The requested ending type is:

{ending_type}

The ending must resolve the central mystery appropriately.

A fully resolved ending should clearly explain:

- what happened
- who was responsible
- why it happened
- how it happened
- how the protagonist discovered the truth

A bittersweet ending may resolve the mystery while leaving
meaningful emotional consequences.

Do not leave the central mystery unresolved unless the requested
ending type specifically requires ambiguity.

============================================================
AUDIO-FIRST STORYTELLING
============================================================

This story will be experienced primarily through audio narration.

Therefore:

- important information must be understandable when heard
- important clues must be describable verbally
- conversations should carry useful information
- locations should be distinguishable through narration
- characters should have clear identities and roles
- timestamps and sequences should be stated clearly when important
- evidence should be explained naturally
- avoid relying on visual diagrams
- avoid clues that require the audience to physically see an image

The story should remain compelling even without music or sound effects.

Sound effects may later enhance the production,
but the mystery itself must work through narration.

============================================================
PACING
============================================================

The target duration is exactly:

{target_duration_minutes} minutes.

Create approximately 8-12 meaningful story beats.

Do not create filler beats.

Each beat must represent a substantial section of the investigation.

The pacing should generally follow:

EARLY STORY

- strong opening incident
- introduce protagonist
- establish the case
- establish the central question
- introduce initial evidence
- introduce important suspects

MIDDLE STORY

- investigation deepens
- conflicting statements appear
- new evidence is discovered
- initial theories fail
- red herrings appear
- stakes increase
- hidden relationships are revealed
- timeline or evidence becomes increasingly difficult to explain

LATE STORY

- protagonist recognizes a major contradiction
- earlier clues are reinterpreted
- possible solution emerges
- protagonist tests the theory
- culprit or truth is confronted
- final explanation is revealed

ENDING

- central mystery is resolved
- consequences are shown
- earlier clues make sense
- final emotional impact is delivered

============================================================
DURATION RULE
============================================================

The sum of all beat durations MUST equal exactly:

{target_duration_minutes} minutes.

For example:

5 + 6 + 7 + 8 + 9 + 8 + 7 + 6 + 4 = 60

Before returning the JSON, internally calculate and verify the total.

DO NOT return the JSON until the total duration is exactly
{target_duration_minutes} minutes.

Do not exceed the target duration.

Do not fall below the target duration.

============================================================
CHARACTER REQUIREMENTS
============================================================

Create characters that are actually useful.

Each character description should communicate:

- name
- role
- relationship to the case
- relationship to the protagonist
- relevant personality
- relevant motivation
- potentially suspicious behavior when appropriate

Do not create unnecessary characters.

Keep the number of important characters manageable
for audio listeners.

============================================================
STORY BEAT REQUIREMENTS
============================================================

Each beat must contain:

- order
- title
- summary
- purpose
- target_duration_minutes

The summary must describe concrete events.

Do not write vague summaries such as:

"The investigation becomes more complicated."

Instead write what actually happens.

For example:

"The investigator compares the building's elevator logs
with the suspect's statement and discovers that the elevator
was used six minutes after the suspect claimed to have left."

The purpose should explain why the beat matters.

============================================================
CONTINUITY
============================================================

Maintain strict consistency for:

- names
- ages
- relationships
- locations
- timeline
- evidence
- motives
- injuries
- possessions
- statements
- discoveries

Do not contradict previously established facts.

============================================================
NO UNRESOLVED PLOT THREADS
============================================================

Do not introduce major:

- characters
- objects
- clues
- locations
- suspects
- relationships
- subplots

that have no meaningful purpose.

Every important element must either:

- contribute to solving the mystery
- contribute to character development
- serve as a meaningful red herring
- or contribute to the final emotional resolution

============================================================
STORY QUALITY
============================================================

The story should feel like a professionally constructed detective
or mystery audio drama.

Prioritize:

- logical causality
- suspense
- curiosity
- investigation
- character motivation
- believable dialogue opportunities
- clue placement
- deduction
- escalation
- satisfying revelation

Avoid:

- supernatural explanations unless explicitly required
- convenient coincidences
- deus ex machina
- random twists
- excessive exposition
- repetitive interrogations
- repetitive discovery scenes
- filler
- unrealistic detective behavior
- characters knowing information they could not know

============================================================
OUTPUT FORMAT
============================================================

Return ONLY valid JSON.

Do not use Markdown.

Do not use code fences.

Do not add explanations before the JSON.

Do not add explanations after the JSON.

Do not add comments inside the JSON.

The JSON must have exactly this structure:

{{
  "title": "Story title",
  "premise": "Complete story premise",
  "setting": "Detailed description of the story setting",

  "crime_type": "{crime_type}",
  "investigation_type": "Generated investigation type",
  "protagonist": "{protagonist}",
  "central_mystery": "Generated central mystery",
  "twist_type": "Generated fair-play twist",
  "ending_type": "{ending_type}",

  "characters": [
    "Character description"
  ],

  "case_file": {{
    "culprit": "Full name of the culprit; do not use an ambiguous description",
    "victim": "Full name of the victim or missing person",
    "motive": "Why the culprit committed the crime",
    "method": "Complete physically plausible method, including any staged evidence or escape route when relevant",
    "real_timeline": [
      "Exact time — event"
    ],
    "false_timeline": [
      "What investigators and audience initially infer happened; this must be plausible but objectively false"
    ],
    "initial_belief": "What police initially believe and why it appears true",
    "alibi": "The culprit's claimed alibi and how it is tested or broken",
    "clues": ["Fair-play clue and what it ultimately proves"],
    "red_herrings": ["Plausible false lead and how it is resolved"],
    "investigation_solution": "How the protagonist logically connects the clues and proves the truth",
    "twist": "The fair-play reinterpretation of earlier facts",
    "final_resolution": "Who did what, why, how, and the consequence",
    "immutable_facts": [
      "Facts that must never change in later beats, including phone/SIM ownership, exact route, weapon, times, and decisive evidence."
    ],
    "reveal_order": [
      {{
        "order": 1,
        "beat": 1,
        "event": "Short stable identifier for the discovery",
        "reveals": "The exact information learned in this moment",
        "audience_belief_after": "What the investigator and audience reasonably believe after this discovery"
      }}
    ]
  }},

  "beats": [
    {{
      "order": 1,
      "title": "Beat title",
      "summary": "Detailed summary of what happens in this beat",
      "purpose": "Why this beat exists and what it accomplishes",
      "target_duration_minutes": 6
    }}
  ],

  "target_duration_minutes": {target_duration_minutes}
}}

============================================================
FINAL VALIDATION
============================================================

Before returning the JSON, verify all of the following:

- The response is valid JSON.
- The top-level structure matches the requested structure.
- The title is not empty.
- The premise is not empty.
- The setting is not empty.
- crime_type is present.
- investigation_type is present.
- protagonist is present.
- central_mystery is present.
- twist_type is present.
- ending_type is present.
- At least one character exists.
- There are approximately 8-12 story beats.
- Beat order starts at 1.
- Beat order increases sequentially.
- Every beat has a title.
- Every beat has a concrete summary.
- Every beat has a purpose.
- Every beat has a positive target_duration_minutes.
- The total beat duration is exactly
  {target_duration_minutes} minutes.
- The crime is logically possible.
- case_file is complete and internally consistent with every beat.
- The method is physically possible. If a door is described as locked from
  inside, explicitly state the mechanism that lets the culprit leave afterward.
- real_timeline is the private, objective sequence of events. false_timeline
  records the plausible but mistaken sequence inferred at the beginning.
- reveal_order is the information sequence, not the event sequence: it must be
  sequential, point to valid beats, and introduce each major clue before its
  reinterpretation. Its audience belief must evolve as clues disprove the
  false timeline.
- Every exact time, device, route, weapon, and identity in a beat agrees with
  case_file. Revise the beats before returning JSON if any conflict exists.
- The culprit has a believable motive.
- The culprit has a believable opportunity.
- The culprit has a believable method.
- The protagonist actively investigates.
- The central mystery has a real underlying answer.
- Important clues are planted before the final revelation.
- At least one meaningful red herring exists when appropriate.
- The twist is foreshadowed.
- The final solution does not depend on completely new information.
- Earlier clues become meaningful after the revelation.
- The case_file method and twist faithfully realize the supplied core mechanism
  when one is supplied.
- The case_file clues and beats faithfully realize the supplied fair_play_focus
  when one is supplied, and reveal those clues before their reinterpretation.
- The false_assumption supplied (if any) is the belief the audience and
  investigators hold through the first half of the story; every false-timeline
  entry must be consistent with it.
- The misdirection_strategy supplied (if any) is actively implemented: the
  evidence that supports the false picture must appear early, feel natural,
  and only become reinterpretable at the reveal.
- The reveal_condition supplied (if any) is fully satisfied: the final
  resolution explains every element named in it without leaving gaps.
- If mechanism library references were supplied, the primary mechanism is the
  structural engine of the central deception, and each secondary mechanism
  contributes at least one distinct fair-play clue or misdirection layer.
- No major unresolved plot thread remains.
- The story can be understood primarily through audio narration.
- The ending matches the requested ending type.

Return ONLY the final JSON.
""".strip()
