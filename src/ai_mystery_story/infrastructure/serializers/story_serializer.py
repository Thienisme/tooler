from pathlib import Path
from typing import Any
from uuid import UUID

from ai_mystery_story.domain.audio.audio_segment import AudioSegment
from ai_mystery_story.domain.audio.audio_track import AudioTrack
from ai_mystery_story.domain.narration.narration import Narration
from ai_mystery_story.domain.narration.narration_segment import (
    NarrationSegment,
)
from ai_mystery_story.domain.story.mystery_story import MysteryStory
from ai_mystery_story.domain.story.story_beat import StoryBeat
from ai_mystery_story.domain.story.story_blueprint import (
    StoryBlueprint,
    StoryCaseFile,
    StoryReveal,
)


class StorySerializer:

    @staticmethod
    def to_dict(
        story: MysteryStory,
    ) -> dict[str, Any]:

        blueprint = story.blueprint

        return {
            "id": str(story.id),

            "blueprint": {
                "title": blueprint.title,
                "premise": blueprint.premise,
                "setting": blueprint.setting,

                "crime_type": blueprint.crime_type,
                "investigation_type": blueprint.investigation_type,
                "protagonist": blueprint.protagonist,
                "central_mystery": blueprint.central_mystery,
                "twist_type": blueprint.twist_type,
                "ending_type": blueprint.ending_type,

                "characters": blueprint.characters,

                "beats": [
                    {
                        "order": beat.order,
                        "title": beat.title,
                        "summary": beat.summary,
                        "purpose": beat.purpose,
                        "target_duration_minutes": (
                            beat.target_duration_minutes
                        ),
                    }
                    for beat in blueprint.beats
                ],

                "target_duration_minutes": (
                    blueprint.target_duration_minutes
                ),

                "case_file": (
                    {
                        "culprit": blueprint.case_file.culprit,
                        "victim": blueprint.case_file.victim,
                        "motive": blueprint.case_file.motive,
                        "method": blueprint.case_file.method,
                        "real_timeline": blueprint.case_file.real_timeline,
                        "false_timeline": blueprint.case_file.false_timeline,
                        "initial_belief": blueprint.case_file.initial_belief,
                        "alibi": blueprint.case_file.alibi,
                        "clues": blueprint.case_file.clues,
                        "red_herrings": blueprint.case_file.red_herrings,
                        "investigation_solution": (
                            blueprint.case_file.investigation_solution
                        ),
                        "twist": blueprint.case_file.twist,
                        "final_resolution": (
                            blueprint.case_file.final_resolution
                        ),
                        "immutable_facts": (
                            blueprint.case_file.immutable_facts
                        ),
                        "reveal_order": [
                            {
                                "order": reveal.order,
                                "beat": reveal.beat,
                                "event": reveal.event,
                                "reveals": reveal.reveals,
                                "audience_belief_after": (
                                    reveal.audience_belief_after
                                ),
                            }
                            for reveal in blueprint.case_file.reveal_order
                        ],
                    }
                    if blueprint.case_file is not None
                    else None
                ),
            },

            "narration": (
                {
                    "segments": [
                        {
                            "order": segment.order,
                            "title": segment.title,
                            "text": segment.text,
                        }
                        for segment in story.narration.segments
                    ]
                }
                if story.narration is not None
                else None
            ),

            "audio": (
                {
                    "segments": [
                        {
                            "order": segment.order,
                            "title": segment.title,
                            "source_text": segment.source_text,
                            "file_path": str(segment.file_path),
                            "duration_seconds": (
                                segment.duration_seconds
                            ),
                        }
                        for segment in story.audio.segments
                    ],

                    "full_audio_path": str(
                        story.audio.full_audio_path
                    ),

                    "duration_seconds": (
                        story.audio.duration_seconds
                    ),
                }
                if story.audio is not None
                else None
            ),
        }

    @staticmethod
    def from_dict(
        data: dict[str, Any],
    ) -> MysteryStory:

        blueprint_data = data["blueprint"]

        beats = [
            StoryBeat(
                order=beat["order"],
                title=beat["title"],
                summary=beat["summary"],
                purpose=beat["purpose"],
                target_duration_minutes=(
                    beat["target_duration_minutes"]
                ),
            )
            for beat in blueprint_data.get("beats", [])
        ]

        blueprint = StoryBlueprint(
            title=blueprint_data["title"],
            premise=blueprint_data["premise"],
            setting=blueprint_data["setting"],

            crime_type=blueprint_data["crime_type"],
            investigation_type=(
                blueprint_data["investigation_type"]
            ),
            protagonist=blueprint_data["protagonist"],
            central_mystery=(
                blueprint_data["central_mystery"]
            ),
            twist_type=blueprint_data["twist_type"],
            ending_type=blueprint_data["ending_type"],

            characters=blueprint_data.get(
                "characters",
                [],
            ),

            beats=beats,

            target_duration_minutes=(
                blueprint_data.get(
                    "target_duration_minutes",
                    60,
                )
            ),

            case_file=StorySerializer._case_file_from_dict(
                blueprint_data.get("case_file")
            ),
        )

        narration_data = data.get("narration")

        narration = None

        if narration_data is not None:

            segments = [
                NarrationSegment(
                    order=segment["order"],
                    title=segment["title"],
                    text=segment["text"],
                )
                for segment in narration_data.get(
                    "segments",
                    [],
                )
            ]

            narration = Narration(
                segments=segments,
            )

        audio_data = data.get("audio")

        audio = None

        if audio_data is not None:

            audio_segments = [
                AudioSegment(
                    order=segment["order"],
                    title=segment["title"],
                    source_text=segment["source_text"],
                    file_path=Path(
                        segment["file_path"]
                    ),
                    duration_seconds=(
                        segment["duration_seconds"]
                    ),
                )
                for segment in audio_data.get(
                    "segments",
                    [],
                )
            ]

            full_audio_path = audio_data.get(
                "full_audio_path"
            )

            audio = AudioTrack(
                segments=audio_segments,

                full_audio_path=(
                    Path(full_audio_path)
                    if full_audio_path is not None
                    else None
                ),

                duration_seconds=audio_data.get(
                    "duration_seconds",
                    0.0,
                ),
            )

        return MysteryStory(
            id=UUID(data["id"]),
            blueprint=blueprint,
            narration=narration,
            audio=audio,
        )

    @staticmethod
    def _case_file_from_dict(
        data: Any,
    ) -> StoryCaseFile | None:
        if data is None:
            return None

        return StoryCaseFile(
            culprit=data["culprit"],
            victim=data["victim"],
            motive=data["motive"],
            method=data["method"],
            real_timeline=(
                data.get("real_timeline")
                or data.get("exact_timeline", [])
            ),
            false_timeline=data.get(
                "false_timeline", [data["initial_belief"]]
            ),
            initial_belief=data["initial_belief"],
            alibi=data["alibi"],
            clues=data["clues"],
            red_herrings=data["red_herrings"],
            investigation_solution=data["investigation_solution"],
            twist=data["twist"],
            final_resolution=data["final_resolution"],
            immutable_facts=data["immutable_facts"],
            reveal_order=[
                StoryReveal(
                    order=reveal["order"],
                    beat=reveal["beat"],
                    event=reveal["event"],
                    reveals=reveal["reveals"],
                    audience_belief_after=reveal[
                        "audience_belief_after"
                    ],
                )
                for reveal in data.get("reveal_order", [])
            ],
        )
