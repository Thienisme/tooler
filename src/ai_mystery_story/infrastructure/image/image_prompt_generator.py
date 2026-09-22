"""
Image Prompt Generator - Generate prompts for each story beat + thumbnail.

Uses beat duration to sync images with audio narration.
"""

import os


class ImagePromptGenerator:
    """
    Generate image prompts from narration text.
    
    Supports two modes:
    1. Beat-based: Each beat → 1 image (synced with audio)
    2. Fixed parts: Split text into N equal parts (legacy)
    """

    def __init__(self, num_parts: int = 10, use_gemini: bool = True):
        self.num_parts = num_parts
        self.use_gemini = use_gemini and os.getenv("GEMINI_API_KEY")
        
        if self.use_gemini:
            from .gemini_image_prompt_analyzer import GeminiImagePromptAnalyzer
            self.analyzer = GeminiImagePromptAnalyzer()
        else:
            self.analyzer = None

    def generate_prompts_from_beats(
        self,
        beats: list[dict],
        story_title: str = "",
        story_premise: str = "",
    ) -> list[dict]:
        """
        Generate image prompts for each story beat.
        
        Each beat → 1 image with duration matching the beat.
        
        Args:
            beats: List of beat dicts with keys:
                - order: int
                - title: str
                - summary: str
                - text: str (narration text for this beat)
                - duration_minutes: int or float
            story_title: Story title for context
            story_premise: Story premise for context
            
        Returns:
            List of {
                "prompt": str,
                "filename": str,
                "seed": int,
                "text_preview": str,
                "description": str,
                "duration_seconds": float,
                "beat_order": int,
                "beat_title": str,
            }
        """
        prompts = []
        
        for beat in beats:
            beat_order = beat.get("order", 0)
            beat_title = beat.get("title", f"Beat {beat_order}")
            beat_text = beat.get("text", beat.get("summary", ""))
            duration_minutes = beat.get("duration_minutes", 5)
            duration_seconds = duration_minutes * 60
            
            # Generate prompt using Gemini or fallback
            if self.use_gemini and self.analyzer:
                try:
                    result = self.analyzer.analyze_part(
                        part_text=beat_text,
                        story_title=story_title,
                        story_premise=story_premise,
                        part_number=beat_order,
                        total_parts=len(beats),
                    )
                    visual_prompt = result["prompt"]
                    description = result["description"]
                except Exception as e:
                    print(f"Gemini analysis failed for beat {beat_order}: {e}")
                    visual_prompt = self._extract_visual_prompt_fallback(
                        beat_text, story_title
                    )
                    description = f"Beat {beat_order}: {beat_title}"
            else:
                visual_prompt = self._extract_visual_prompt_fallback(
                    beat_text, story_title
                )
                description = f"Beat {beat_order}: {beat_title}"
            
            prompts.append({
                "prompt": visual_prompt,
                "filename": f"beat_{beat_order:02d}.jpg",
                "seed": beat_order * 42 + 7,
                "text_preview": beat_text[:100] + "..." if len(beat_text) > 100 else beat_text,
                "description": description,
                "duration_seconds": duration_seconds,
                "beat_order": beat_order,
                "beat_title": beat_title,
            })
        
        return prompts

    def generate_prompts(
        self,
        narration_text: str,
        story_title: str = "",
        story_premise: str = "",
    ) -> list[dict]:
        """
        Generate image prompts for each part of the narration (legacy mode).
        
        Args:
            narration_text: Full narration text
            story_title: Story title for context
            story_premise: Story premise for context

        Returns:
            List of {"prompt": str, "filename": str, "seed": int, "text_preview": str, "description": str}
        """
        # Split text into parts
        parts = self._split_text(narration_text, self.num_parts)
        
        prompts = []
        
        for i, part_text in enumerate(parts, 1):
            if self.use_gemini and self.analyzer:
                # Use Gemini to analyze and generate prompt
                try:
                    result = self.analyzer.analyze_part(
                        part_text=part_text,
                        story_title=story_title,
                        story_premise=story_premise,
                        part_number=i,
                        total_parts=len(parts),
                    )
                    visual_prompt = result["prompt"]
                    description = result["description"]
                except Exception as e:
                    print(f"Gemini analysis failed for part {i}: {e}")
                    # Fallback to simple extraction
                    visual_prompt = self._extract_visual_prompt_fallback(
                        part_text, story_title
                    )
                    description = f"Phần {i} của câu chuyện"
            else:
                # Fallback without Gemini
                visual_prompt = self._extract_visual_prompt_fallback(
                    part_text, story_title
                )
                description = f"Phần {i} của câu chuyện"
            
            prompts.append({
                "prompt": visual_prompt,
                "filename": f"part_{i:02d}.jpg",
                "seed": i * 42 + 7,
                "text_preview": part_text[:100] + "..." if len(part_text) > 100 else part_text,
                "description": description,
            })

        return prompts

    def generate_thumbnail_prompt(
        self,
        story_title: str,
        story_premise: str,
        narration_text: str = "",
    ) -> dict:
        """
        Generate a thumbnail prompt based on story title and premise.

        Returns:
            {"prompt": str, "filename": str, "seed": int, "description": str}
        """
        if self.use_gemini and self.analyzer:
            try:
                result = self.analyzer.analyze_thumbnail(
                    story_title=story_title,
                    story_premise=story_premise,
                    narration_text=narration_text,
                )
                return {
                    "prompt": result["prompt"],
                    "filename": "thumbnail.jpg",
                    "seed": 999,
                    "description": result["description"],
                }
            except Exception as e:
                print(f"Gemini thumbnail analysis failed: {e}")
        
        # Fallback - MAXIMUM HORROR
        prompt = (
            f"Extremely terrifying movie poster thumbnail for Vietnamese horror story \"{story_title}\". "
            f"Premise: {story_premise}. "
            f"A nightmarish scene: a dark corridor stretching into impossible depth, "
            f"a lone figure at the far end facing away, their shadow reaching toward the viewer, "
            f"blood-red cracks in the walls forming faces, volumetric fog seeping from below, "
            f"a single flickering lightbulb casting harsh chiaroscuro shadows, "
            f"found footage horror aesthetic, 1980s film grain, cold blue-red color palette, "
            f"hyperrealistic, cinematic horror movie poster, deeply unsettling atmosphere, "
            f"something WRONG in the darkness that the viewer can almost see. "
            f"eerie, haunting, unsettling, sinister, creepy, dark surrealism, analog horror"
        )
        
        return {
            "prompt": prompt,
            "filename": "thumbnail.jpg",
            "seed": 999,
            "description": f"Thumbnail cho câu chuyện {story_title}",
        }

    def _split_text(self, text: str, num_parts: int) -> list[str]:
        """Split text into roughly equal parts at sentence boundaries."""
        # Split by paragraphs first
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        if len(paragraphs) <= num_parts:
            # Fewer paragraphs than parts - split paragraphs
            return self._split_by_chars(text, num_parts)

        # Group paragraphs into parts
        parts = []
        current = ""
        target_len = len(text) // num_parts

        for para in paragraphs:
            candidate = f"{current}\n\n{para}" if current else para
            if len(candidate) <= target_len * 1.5 or not current:
                current = candidate
            else:
                if current:
                    parts.append(current)
                current = para

        if current:
            parts.append(current)

        # Merge or split to get exactly num_parts
        while len(parts) < num_parts:
            # Find longest part and split it
            longest_idx = max(range(len(parts)), key=lambda i: len(parts[i]))
            longest = parts.pop(longest_idx)
            mid = len(longest) // 2
            # Find sentence boundary near midpoint
            split_at = longest.rfind(".", 0, mid + 100)
            if split_at < mid - 100:
                split_at = mid
            else:
                split_at += 1
            parts.insert(longest_idx, longest[:split_at].strip())
            parts.insert(longest_idx + 1, longest[split_at:].strip())

        return parts[:num_parts]

    def _split_by_chars(self, text: str, num_parts: int) -> list[str]:
        """Split text by character count at sentence boundaries."""
        part_len = len(text) // num_parts
        parts = []
        current = ""

        sentences = text.replace("\n\n", " ").split(". ")
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            candidate = f"{current}. {sentence}" if current else sentence
            if len(candidate) <= part_len * 1.3 or not current:
                current = candidate
            else:
                if current:
                    parts.append(current + ".")
                current = sentence

        if current:
            parts.append(current + "." if not current.endswith(".") else current)

        # Ensure we have exactly num_parts
        while len(parts) < num_parts:
            parts.append("")

        return parts[:num_parts]

    # Horror style modifiers - always appended to fallback prompts
    HORROR_STYLE = (
        "eerie, haunting, unsettling, sinister, creepy, "
        "volumetric fog, dimly lit, chiaroscuro lighting, "
        "flickering light, analog horror, found footage style, "
        "1980s horror film grain, dark surrealism, "
        "cold color grading, deep shadows, unsettling atmosphere"
    )

    def _extract_visual_prompt_fallback(
        self,
        text: str,
        title: str,
    ) -> str:
        """
        Fallback: Extract visual elements from text using keyword matching.
        Used when Gemini is unavailable.
        
        DIVERSITY RULE: Analyze text content to create specific prompts,
        with MAXIMUM horror atmosphere.
        """
        text_lower = text.lower()
        
        # 1. Detect main subject (person, object, or scene)
        subject = self._detect_main_subject(text_lower)
        
        # 2. Detect specific location
        location = self._detect_location(text_lower)
        
        # 3. Detect action or event
        action = self._detect_action(text_lower)
        
        # 4. Detect lighting/time
        lighting = self._detect_lighting(text_lower)
        
        # 5. Detect horror elements
        horror = self._detect_horror_elements(text_lower)
        
        # 6. Build specific prompt with horror
        prompt_parts = []
        
        if subject:
            prompt_parts.append(subject)
        if location:
            prompt_parts.append(location)
        if action:
            prompt_parts.append(action)
        if lighting:
            prompt_parts.append(lighting)
        if horror:
            prompt_parts.append(horror)
        
        if prompt_parts:
            prompt = f"A terrifying scene showing {', '.join(prompt_parts)}. {self.HORROR_STYLE}"
        else:
            prompt = f"A deeply unsettling scene from the horror story {title}. A dark corridor stretches into absolute darkness, a single flickering light reveals impossible shadows, something moves in the periphery. {self.HORROR_STYLE}"
        
        return prompt

    def _detect_main_subject(self, text: str) -> str:
        """Detect the main subject in the text - with horror twist."""
        # People - but make them unsettling
        if any(word in text for word in ['minh', 'anh', 'chị', 'ông', 'bà', 'người', 'hắn', 'kẻ']):
            if any(word in text for word in ['đứng', 'ngồi', 'đi', 'chạy', 'bước']):
                return "a lone figure standing motionless, their face obscured by deep shadow, an unnatural stillness about them"
        
        # Police/emergency - make it found footage style
        if any(word in text for word in ['cảnh sát', 'công an', 'đội tuần tra', 'ambulance']):
            return "uniformed officers approaching a dark doorway, flashlight beams cutting through thick fog, their shadows stretching impossibly long"
        
        # Objects - make them sinister
        if 'điện thoại' in text:
            return "a cracked smartphone screen glowing with eerie blue light, displaying an incoming call from an unknown number, a trembling hand reaching for it"
        if 'chìa khóa' in text:
            return "a rusty key lying on a blood-stained floor, its shadow twisting into an impossible shape"
        if 'dao' in text or 'cây' in text:
            return "a sharp blade catching dim light, dark liquid dripping from its edge"
        if 'cửa' in text:
            return "a half-open door revealing absolute darkness beyond, something barely visible in the void"
        if 'gương' in text:
            return "a cracked mirror reflecting a room that isn't there, the reflection shows a dark corridor with a silhouette at the end"
        
        return ""

    def _detect_location(self, text: str) -> str:
        """Detect the specific location - with horror atmosphere."""
        locations = {
            'ngõ': "in a profoundly narrow Vietnamese alley stretching into absolute darkness, walls closing in, a single dying lightbulb flickering overhead",
            'chung cư': "inside a decaying Soviet-era apartment building, peeling wallpaper, water stains on ceiling, the elevator hasn't worked in years",
            'căn hộ': "inside a cramped apartment room, furniture covered in white sheets like ghosts, dust particles floating in a single beam of light",
            'phòng': "in a dark room, the only light source a cracked phone screen casting sickly blue shadows on the walls",
            'hành lang': "in a long dimly lit corridor, fluorescent tubes flickering erratically, doors on both sides slightly ajar revealing darkness",
            'cầu thang': "on a concrete spiral staircase descending into impossibly deep darkness, each step echoing wrong",
            'đường': "on an empty street at 3AM, streetlights casting pools of sickly yellow light between stretches of absolute blackness",
            'nhà': "inside an old Vietnamese house, wooden floorboards creaking under invisible weight, family photos on walls with faces scratched out",
            'sân': "in a desolate courtyard, overgrown weeds pushing through cracked concrete, a children's swing moving on its own",
            'vườn': "in an overgrown garden at night, dead trees forming claw-like shapes against the moonlit sky",
            'wc': "in a bathroom, water dripping from the faucet in perfect rhythm, the mirror fogging up despite no one being there",
            'giường': "in bed, the sheets pulled tight, a cold spot beside the sleeper where no one should be",
        }
        
        for keyword, location in locations.items():
            if keyword in text:
                return location
        
        return ""

    def _detect_action(self, text: str) -> str:
        """Detect the action or event happening - with horror twist."""
        actions = {
            'gọi': "pressing a phone to their ear, eyes wide with growing terror as they hear something impossible on the other end",
            'nghe': "frozen in place, listening to a sound that shouldn't exist, every muscle tensed",
            'thấy': "discovering something deeply wrong, their face illuminated by a flashlight revealing a horrifying sight",
            'chạy': "running through darkness, footsteps echoing wrong behind them, getting closer",
            'trốn': "pressing themselves against a wall in absolute darkness, holding their breath, a shadow passing in front of the doorway",
            'tìm': "searching with a flickering flashlight, the beam catching something that moves when it shouldn't",
            'đứng': "standing perfectly still in the wrongness, unable to move, watching something approach",
            'ngồi': "sitting bolt upright in bed, eyes wide open in the dark, knowing they are not alone",
            'nằm': "lying frozen under sheets, feeling the mattress compress beside them as something sits down",
            'mở': "slowly opening a door to reveal something that defies explanation",
            'đóng': "slamming a door shut, pressing their back against it, something scratching from the other side",
            'rơi': "dropping something that shatters in the silence, the sound impossibly loud",
            'chảy': "dark liquid seeping from cracks in the wall, pooling on the floor",
        }
        
        for keyword, action in actions.items():
            if keyword in text:
                return action
        
        return ""

    def _detect_lighting(self, text: str) -> str:
        """Detect lighting conditions - horror themed."""
        if any(word in text for word in ['đêm', 'tối', 'bóng tối']):
            return "in oppressive darkness, only a single harsh light source creating deep chiaroscuro shadows, volumetric fog catching the light"
        if any(word in text for word in ['sáng', 'đèn', 'ánh đèn']):
            return "under a dying fluorescent light that flickers erratically, casting strobe-like shadows that seem to move independently"
        if any(word in text for word in ['bình minh', 'mặt trời', 'nắng']):
            return "in cold grey daylight that makes everything look wrong, colors drained, shadows too sharp"
        if any(word in text for word in ['sáng', 'đèn', 'ánh đèn', 'đom đóm']):
            return "under a single bare lightbulb swinging slightly, creating moving shadows that seem to reach toward the viewer"
        
        return ""

    def _detect_horror_elements(self, text: str) -> str:
        """Detect horror-specific elements in the text."""
        horror_elements = {
            'ma': "a ghostly translucent figure materializing in the corner",
            'hồn': "an ethereal presence leaving frost patterns on the glass",
            'tiếng': "an unexplained sound emanating from the walls themselves",
            'gõ': "rhythmic knocking from inside a sealed room",
            'khóc': "the sound of weeping with no visible source",
            'cười': "hysterical laughter echoing from an empty room",
            'máu': "dark crimson stains spreading across the ceiling",
            'chết': "a body positioned in an impossible pose",
            'mất tích': "an empty space where someone should be, their belongings undisturbed",
            'ám': "an oppressive presence filling the room like physical weight",
            'rùng rợn': "every surface covered in scratches that form desperate messages",
            'sợ': "pure primal terror frozen on a face illuminated by a dying light",
            'giấc mơ': "a nightmare made real, the boundary between sleep and waking dissolved",
            'giật mình': "jolting awake to find a face inches from theirs in the dark",
            'bóng': "a shadow that moves independently of its source",
            'tiếng bước chân': "footsteps approaching from the dark corridor, getting closer, never arriving",
            'điện thoại': "a phone ringing in the empty room, the caller ID shows the number of the dead",
            'cửa': "a door that was closed now standing open, leading to a room that shouldn't exist",
            'gương': "a mirror reflecting a room with one extra person who isn't there",
            'đồng hồ': "a clock stopped at the exact moment of death",
        }
        
        found = []
        for keyword, element in horror_elements.items():
            if keyword in text:
                found.append(element)
        
        return found[0] if found else ""
