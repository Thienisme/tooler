"""
Gemini Image Prompt Analyzer - Use Gemini to analyze narration text
and generate detailed HORROR image prompts for Pollinations.ai.
"""

import os
import time
from google import genai
from google.genai import errors


# Horror style modifiers - always appended to every prompt
HORROR_STYLE = (
    "eerie, haunting, unsettling, sinister, creepy, "
    "volumetric fog, dimly lit, chiaroscuro lighting, "
    "flickering light, analog horror, found footage style, "
    "1980s horror film grain, dark surrealism, "
    "cold color grading, deep shadows, unsettling atmosphere"
)

THUMBNAIL_HORROR_STYLE = (
    "extremely terrifying, blood-soaked, nightmare fuel, "
    "demonic presence, paranormal horror, visceral dread, "
    "body horror elements, tattered fabric, blood splatter, "
    "abandoned decaying environment, oppressive darkness, "
    "sinister shadows forming human shapes, "
    "distorted reflections, cracked walls oozing dark liquid, "
    "hyperrealistic, cinematic horror movie poster, "
    "extreme chiaroscuro, cold blue-red color palette, "
    "Japanese horror meets Vietnamese urban legend aesthetic"
)


class GeminiImagePromptAnalyzer:
    """
    Use Gemini AI to analyze narration text and generate
    HORROR-themed image prompts for Pollinations.ai.
    """

    def __init__(self, model: str = "gemini-2.5-flash"):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_retries = 3

    def analyze_part(
        self,
        part_text: str,
        story_title: str,
        story_premise: str,
        part_number: int,
        total_parts: int,
    ) -> dict:
        """
        Analyze a part of narration and generate a HORROR image prompt.
        """
        prompt = self._build_analysis_prompt(
            part_text, story_title, story_premise, part_number, total_parts
        )

        response_text = self._call_gemini(prompt)
        result = self._parse_response(response_text)

        # Ensure horror style is always appended
        if result["prompt"] and HORROR_STYLE not in result["prompt"]:
            result["prompt"] = result["prompt"].rstrip(". ") + f". {HORROR_STYLE}"

        return result

    def analyze_thumbnail(
        self,
        story_title: str,
        story_premise: str,
        narration_text: str,
    ) -> dict:
        """
        Analyze story and generate a MAXIMUM HORROR thumbnail prompt.
        """
        context = narration_text[:2000] if len(narration_text) > 2000 else narration_text

        prompt = f"""You are a MASTER of horror movie poster design. You create images that make people SCREAM.

YOUR TASK: Create the most TERRIFYING thumbnail for this Vietnamese horror mystery story.

STORY TITLE: {story_title}
STORY PREMISE: {story_premise}

STORY OPENING (use for visual context):
{context}

REQUIREMENTS:
1. The thumbnail MUST be absolutely TERRIFYING and NIGHTMARISH
2. Include elements of BLOOD, DARKNESS, PARANORMAL PRESENCE
3. Show something WRONG - distorted faces, impossible shadows, unnatural poses
4. The atmosphere should feel like a NIGHTMARE you can't wake up from
5. Use Vietnamese urban horror elements (old apartments, narrow alleys, flickering lights)
6. Make it look like a still from a FOUND FOOTAGE horror film
7. Include unsettling details: scratches on walls, dripping liquid, distorted reflections

VISUAL ELEMENTS TO INCLUDE (pick 3-4):
- Blood splatter or dripping red liquid
- A sinister shadow or silhouette that shouldn't be there
- Cracked, decaying walls with dark stains
- A single flickering light source creating harsh shadows
- Distorted human figures (too many limbs, wrong proportions)
- An object that is WRONG (clock with wrong time, mirror showing different room)
- Fog or mist seeping through cracks
- A dark doorway leading to absolute darkness

COMPOSITION:
- Center the most TERRIFYING element
- Use extreme close-up or dramatic wide angle
- Cold blue tones mixed with blood red accents
- Heavy film grain and vignette

Return ONLY a JSON object:
{{
    "prompt": "Extremely detailed English horror image prompt (150-200 words) - MUST be terrifying",
    "description": "Vietnamese description of the horrifying scene (2-3 sentences)"
}}"""

        response_text = self._call_gemini(prompt)
        result = self._parse_response(response_text)

        # Ensure MAXIMUM horror style for thumbnail
        if result["prompt"] and THUMBNAIL_HORROR_STYLE not in result["prompt"]:
            result["prompt"] = result["prompt"].rstrip(". ") + f". {THUMBNAIL_HORROR_STYLE}"

        return result

    def _build_analysis_prompt(
        self,
        part_text: str,
        story_title: str,
        story_premise: str,
        part_number: int,
        total_parts: int,
    ) -> str:
        """Build the HORROR prompt for Gemini to analyze a story part."""

        return f"""You are a HORROR VISUAL DIRECTOR for a Vietnamese mystery horror story.

CRITICAL RULE: Each part MUST have a COMPLETELY DIFFERENT horrifying visual scene.
- Part {part_number} of {total_parts}
- DO NOT repeat scenes from other parts
- Each part shows a UNIQUE MOMENT OF TERROR

YOUR TASK: Analyze this narration text and create a TERRIFYING image prompt.

STORY CONTEXT:
- Title: {story_title}
- Premise: {story_premise}

NARRATION TEXT FOR THIS PART:
{part_text}

ANALYSIS STEPS:
1. Read the narration text carefully
2. Find the MOST DISTURBING or UNSETTLING moment in this part
3. Identify WHAT MAKES IT SCARY (not just the location)
4. Think about WHAT THE READER WOULD FEAR seeing
5. Create a prompt that captures this SPECIFIC HORROR

HORROR VISUAL RULES:
- Every image must feel WRONG or UNSETTLING
- Use lighting to CREATE FEAR (harsh shadows, single light source)
- Show the ABSENCE of something (empty chair, missing person, blank eyes)
- Include subtle wrongness (slightly off proportions, impossible angles)
- Make the viewer feel WATCHED or FOLLOWED
- The environment itself should feel HOSTILE

HORROR ELEMENTS TO USE (pick 2-3 per image):
- Volumetric fog / mist / haze
- Chiaroscuro lighting (extreme light/dark contrast)
- Flickering or dying light sources
- Blood, dark stains, or unexplained liquid
- Cracked, decaying, or rotting surfaces
- Shadows that don't match objects
- Reflections that show something different
- Doors/windows to absolute darkness
- Distorted or impossible geometry
- A single unsettling detail in an otherwise normal scene

PROMPT REQUIREMENTS:
- MUST be in English
- 120-180 words
- Focus on WHAT IS SEEN that is TERRIFYING
- Include SPECIFIC scary details from the narration
- Make it feel like a still from a HORROR FILM
- The viewer should feel UNEASY looking at it

GOOD EXAMPLES:
✓ "A narrow Vietnamese alley at night, a single flickering fluorescent tube casts sickly yellow light, at the far end a FIGURE stands motionless facing the wall, its shadow stretches impossibly long toward the camera, volumetric fog swirls at ankle height, analog horror aesthetic, film grain"
✓ "Inside a decaying apartment bathroom, a cracked mirror reflects a room that ISN'T there, the reflection shows a dark corridor with a silhouette at the end, water drips from the ceiling creating dark pools on cracked tiles, chiaroscuro lighting, unsettling atmosphere"
✓ "Close-up of a smartphone screen showing 23:47, the screen reflects a FACE that isn't the person holding it, the reflected face has hollow eyes and an open mouth, cold blue light illuminates trembling fingers, found footage style, horror film grain"

BAD EXAMPLES (too generic, not scary):
✗ "Dark hallway with eerie atmosphere"
✗ "Creepy building at night"
✗ "Mysterious scene with shadows"

Return ONLY a JSON object:
{{
    "prompt": "Detailed English horror image prompt (120-180 words) - MUST be terrifying and unique",
    "description": "Vietnamese description of the horrifying scene (1-2 sentences)"
}}"""

    def _call_gemini(self, prompt: str) -> str:
        """Call Gemini API with retry logic."""
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                print(f"Gemini horror prompt (attempt {attempt}/{self.max_retries})...")

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                )

                if not response.text:
                    raise RuntimeError("Gemini returned empty response")

                return response.text

            except errors.ServerError as exc:
                last_error = exc
                print(f"Gemini server error: {exc}")

                if attempt >= self.max_retries:
                    break

                delay = min(2 ** (attempt - 1) * 2, 30)
                print(f"Retrying in {delay}s...")
                time.sleep(delay)

            except Exception as exc:
                last_error = exc
                print(f"Gemini error: {exc}")
                break

        raise RuntimeError(
            f"Gemini analysis failed after {self.max_retries} attempts: {last_error}"
        )

    def _parse_response(self, response_text: str) -> dict:
        """Parse Gemini response JSON."""
        import json
        import re

        text = response_text.strip()
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

        try:
            data = json.loads(text)
            return {
                "prompt": data.get("prompt", ""),
                "description": data.get("description", ""),
            }
        except json.JSONDecodeError:
            json_match = re.search(r'\{[^{}]*"prompt"[^{}]*\}', text, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    return {
                        "prompt": data.get("prompt", ""),
                        "description": data.get("description", ""),
                    }
                except json.JSONDecodeError:
                    pass

            return {
                "prompt": text[:500],
                "description": "Ảnh rùng rợn cho đoạn truyện",
            }
