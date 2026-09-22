import json
from pathlib import Path
from uuid import UUID

from ai_mystery_story.domain.story.mystery_story import MysteryStory
from ai_mystery_story.domain.story.story_repository import StoryRepository
from ai_mystery_story.infrastructure.serializers.story_serializer import (
    StorySerializer,
)


class JsonStoryRepository(StoryRepository):

    def __init__(self, projects_dir: Path):
        self.projects_dir = projects_dir

    def save(
        self,
        story: MysteryStory,
        topic_id: str | None = None,
    ) -> None:
        if topic_id is not None:
            story_dir = self.projects_dir / topic_id
            story_dir.mkdir(parents=True, exist_ok=True)
        else:
            story_dir = self._find_project_dir(story_id=story.id)

            # Keep older callers, which only know a story ID, working.
            if story_dir is None:
                story_dir = self.projects_dir / str(story.id)
                story_dir.mkdir(parents=True, exist_ok=True)

        story_file = story_dir / "story.json"

        data = StorySerializer.to_dict(story)

        # Preserve extra top-level keys that live in the JSON file but are
        # not part of the domain model (e.g. "narration.shorts" added by
        # external review/edit workflows).  We do a shallow merge: keys
        # unknown to the serializer are kept as-is; known keys are always
        # taken from the freshly-serialised data so domain state wins.
        if story_file.exists():
            try:
                existing = json.loads(story_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}

            # Preserve extra top-level keys
            for key, value in existing.items():
                if key not in data:
                    data[key] = value

            # Preserve extra keys inside "narration" (e.g. "shorts")
            if "narration" in existing and isinstance(existing["narration"], dict) \
                    and "narration" in data and isinstance(data["narration"], dict):
                for key, value in existing["narration"].items():
                    if key not in data["narration"]:
                        data["narration"][key] = value

        story_file.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def get(
        self,
        story_id: UUID,
    ) -> MysteryStory | None:

        story_file = self._find_story_file(
            story_id=story_id,
        )

        if story_file is None:
            return None

        data = json.loads(
            story_file.read_text(
                encoding="utf-8",
            )
        )

        return StorySerializer.from_dict(data)

    def _find_project_dir(
        self,
        story_id: UUID,
    ) -> Path | None:

        if not self.projects_dir.exists():
            return None

        for project_dir in self.projects_dir.iterdir():

            if not project_dir.is_dir():
                continue

            story_file = project_dir / "story.json"

            if not story_file.exists():
                continue

            try:
                data = json.loads(story_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue

            if data.get("id") == str(story_id):
                return project_dir

        return None

    def _find_story_file(
        self,
        story_id: UUID,
    ) -> Path | None:

        project_dir = self._find_project_dir(
            story_id=story_id,
        )

        if project_dir is None:
            return None

        return project_dir / "story.json"

    def get_by_topic_id(
        self,
        topic_id: str,
    ) -> MysteryStory | None:
        story_file = self.projects_dir / topic_id / "story.json"

        if not story_file.exists():
            return None

        data = json.loads(story_file.read_text(encoding="utf-8"))
        return StorySerializer.from_dict(data)
