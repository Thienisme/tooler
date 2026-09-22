from ai_mystery_story.domain.story.story_blueprint import StoryBlueprint


class StoryBlueprintValidator:

    def validate(
        self,
        blueprint: StoryBlueprint,
    ) -> None:

        self._validate_basic_fields(blueprint)
        self._validate_mystery_fields(blueprint)
        self._validate_characters(blueprint)
        self._validate_beats(blueprint)
        self._validate_duration(blueprint)
        self._validate_case_file(blueprint)

    def _validate_case_file(
        self,
        blueprint: StoryBlueprint,
    ) -> None:
        case_file = blueprint.case_file
        if case_file is None:
            raise ValueError("Story blueprint must include a case_file")

        if len(case_file.real_timeline) < 2:
            raise ValueError("case_file must contain a real timeline")

        if not case_file.false_timeline:
            raise ValueError("case_file must contain a false timeline")

        if not case_file.reveal_order:
            raise ValueError("case_file must contain a reveal order")

        expected_order = 1
        beat_count = len(blueprint.beats)
        for reveal in case_file.reveal_order:
            if reveal.order != expected_order:
                raise ValueError(
                    "reveal_order must use sequential order starting at 1"
                )
            if reveal.beat < 1 or reveal.beat > beat_count:
                raise ValueError("reveal_order references an invalid beat")
            expected_order += 1
        if len(case_file.immutable_facts) < 3:
            raise ValueError(
                "case_file must contain at least three immutable facts"
            )

        if not case_file.clues or not case_file.red_herrings:
            raise ValueError(
                "case_file must contain clues and at least one red herring"
            )

    def _validate_basic_fields(
        self,
        blueprint: StoryBlueprint,
    ) -> None:

        if not blueprint.title.strip():
            raise ValueError(
                "Story title cannot be empty"
            )

        if not blueprint.premise.strip():
            raise ValueError(
                "Story premise cannot be empty"
            )

        if not blueprint.setting.strip():
            raise ValueError(
                "Story setting cannot be empty"
            )

    def _validate_mystery_fields(
        self,
        blueprint: StoryBlueprint,
    ) -> None:

        fields = {
            "crime_type": blueprint.crime_type,
            "investigation_type": blueprint.investigation_type,
            "protagonist": blueprint.protagonist,
            "central_mystery": blueprint.central_mystery,
            "twist_type": blueprint.twist_type,
            "ending_type": blueprint.ending_type,
        }

        for field, value in fields.items():

            if not value or not value.strip():
                raise ValueError(
                    f"Story {field} cannot be empty"
                )

    def _validate_characters(
        self,
        blueprint: StoryBlueprint,
    ) -> None:

        if not blueprint.characters:
            raise ValueError(
                "Story must have at least one character"
            )

    def _validate_beats(
        self,
        blueprint: StoryBlueprint,
    ) -> None:

        if not blueprint.beats:
            raise ValueError(
                "Story must have at least one beat"
            )

        expected_order = 1

        for beat in blueprint.beats:

            if beat.order != expected_order:
                raise ValueError(
                    f"Invalid beat order: expected "
                    f"{expected_order}, got {beat.order}"
                )

            if not beat.title.strip():
                raise ValueError(
                    f"Beat {beat.order} has empty title"
                )

            if not beat.summary.strip():
                raise ValueError(
                    f"Beat {beat.order} has empty summary"
                )

            if not beat.purpose.strip():
                raise ValueError(
                    f"Beat {beat.order} has empty purpose"
                )

            if beat.target_duration_minutes <= 0:
                raise ValueError(
                    f"Beat {beat.order} must have "
                    f"positive duration"
                )

            expected_order += 1

    def _validate_duration(
        self,
        blueprint: StoryBlueprint,
    ) -> None:

        if blueprint.target_duration_minutes <= 0:
            raise ValueError(
                "Story duration must be greater than 0"
            )

        total_duration = sum(
            beat.target_duration_minutes
            for beat in blueprint.beats
        )

        if total_duration != blueprint.target_duration_minutes:
            raise ValueError(
                "Beat durations do not match story duration: "
                f"expected {blueprint.target_duration_minutes} "
                f"minutes, got {total_duration} minutes"
            )
