import json
from typing import Any

from ai_mystery_story.domain.story.story_beat import StoryBeat
from ai_mystery_story.domain.story.story_blueprint import (
    StoryBlueprint,
    StoryCaseFile,
    StoryReveal,
)


class StoryBlueprintParser:

    def parse(self, content: str) -> StoryBlueprint:
        data = self._parse_json(content)

        return StoryBlueprint(
            title=self._required_string(data, "title"),
            premise=self._required_string(data, "premise"),
            setting=self._required_string(data, "setting"),

            crime_type=self._required_string(
                data,
                "crime_type",
            ),
            investigation_type=self._required_string(
                data,
                "investigation_type",
            ),
            protagonist=self._required_string(
                data,
                "protagonist",
            ),
            central_mystery=self._required_string(
                data,
                "central_mystery",
            ),
            twist_type=self._required_string(
                data,
                "twist_type",
            ),
            ending_type=self._required_string(
                data,
                "ending_type",
            ),

            characters=self._parse_characters(data),
            beats=self._parse_beats(data),
            target_duration_minutes=self._parse_duration(data),
            case_file=self._parse_case_file(data),
        )

    def _parse_case_file(
        self,
        data: dict[str, Any],
    ) -> StoryCaseFile:
        case_file = data.get("case_file")

        if not isinstance(case_file, dict):
            raise ValueError("Missing or invalid case_file")

        return StoryCaseFile(
            culprit=self._required_string(case_file, "culprit"),
            victim=self._required_string(case_file, "victim"),
            motive=self._required_string(case_file, "motive"),
            method=self._required_string(case_file, "method"),
            real_timeline=self._string_list(
                case_file, "real_timeline"
            ),
            false_timeline=self._string_list(
                case_file, "false_timeline"
            ),
            initial_belief=self._required_string(case_file, "initial_belief"),
            alibi=self._required_string(case_file, "alibi"),
            clues=self._string_list(case_file, "clues"),
            red_herrings=self._string_list(case_file, "red_herrings"),
            investigation_solution=self._required_string(
                case_file, "investigation_solution"
            ),
            twist=self._required_string(case_file, "twist"),
            final_resolution=self._required_string(
                case_file, "final_resolution"
            ),
            immutable_facts=self._string_list(
                case_file, "immutable_facts"
            ),
            reveal_order=self._parse_reveal_order(case_file),
        )

    def _parse_reveal_order(
        self,
        case_file: dict[str, Any],
    ) -> list[StoryReveal]:
        reveals = case_file.get("reveal_order")

        if not isinstance(reveals, list) or not reveals:
            raise ValueError("Missing or invalid list: reveal_order")

        result: list[StoryReveal] = []

        for reveal in reveals:
            if not isinstance(reveal, dict):
                raise ValueError("Every reveal_order item must be an object")

            result.append(
                StoryReveal(
                    order=self._required_int(reveal, "order"),
                    beat=self._required_int(reveal, "beat"),
                    event=self._required_string(reveal, "event"),
                    reveals=self._required_string(reveal, "reveals"),
                    audience_belief_after=self._required_string(
                        reveal, "audience_belief_after"
                    ),
                )
            )

        return result

    def _string_list(
        self,
        data: dict[str, Any],
        field: str,
    ) -> list[str]:
        value = data.get(field)
        if not isinstance(value, list) or not value:
            raise ValueError(f"Missing or invalid list: {field}")
        if not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f"Every {field} item must be a non-empty string")
        return [item.strip() for item in value]

    def _parse_json(self, content: str) -> dict[str, Any]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "AI response is not valid JSON"
            ) from exc

        if not isinstance(data, dict):
            raise ValueError(
                "AI response must be a JSON object"
            )

        return data

    def _required_string(
        self,
        data: dict[str, Any],
        field: str,
    ) -> str:
        value = data.get(field)

        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Missing or invalid field: {field}"
            )

        return value.strip()

    def _parse_characters(
        self,
        data: dict[str, Any],
    ) -> list[str]:

        characters = data.get("characters", [])

        if not isinstance(characters, list):
            raise ValueError(
                "characters must be a list"
            )

        if not all(
            isinstance(character, str)
            for character in characters
        ):
            raise ValueError(
                "Every character must be a string"
            )

        return characters

    def _parse_beats(
        self,
        data: dict[str, Any],
    ) -> list[StoryBeat]:

        beats = data.get("beats", [])

        if not isinstance(beats, list):
            raise ValueError(
                "beats must be a list"
            )

        result: list[StoryBeat] = []

        for beat in beats:

            if not isinstance(beat, dict):
                raise ValueError(
                    "Every beat must be an object"
                )

            result.append(
                StoryBeat(
                    order=self._required_int(
                        beat,
                        "order",
                    ),
                    title=self._required_string(
                        beat,
                        "title",
                    ),
                    summary=self._required_string(
                        beat,
                        "summary",
                    ),
                    purpose=self._required_string(
                        beat,
                        "purpose",
                    ),
                    target_duration_minutes=self._required_int(
                        beat,
                        "target_duration_minutes",
                    ),
                )
            )

        return result

    def _parse_duration(
        self,
        data: dict[str, Any],
    ) -> int:

        duration = data.get(
            "target_duration_minutes",
            60,
        )

        if not isinstance(duration, int):
            raise ValueError(
                "target_duration_minutes must be an integer"
            )

        if duration <= 0:
            raise ValueError(
                "target_duration_minutes must be greater than 0"
            )

        return duration

    def _required_int(
        self,
        data: dict[str, Any],
        field: str,
    ) -> int:

        value = data.get(field)

        if not isinstance(value, int):
            raise ValueError(
                f"Missing or invalid integer field: {field}"
            )

        return value
