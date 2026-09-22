"""
Filesystem layout for an autovid workspace, plus the resume manifest.

A "workspace" is the folder that holds `script.json`.  Everything the
pipeline produces lives inside it:

    <workspace>/
    ├── script.json
    ├── audio/                 # per-scene voiceover + voiceover_full.wav
    │   ├── voiceover_full.wav # stage 2: normalised narration master
    │   ├── stem_music.wav     # stage 5: BGM bed, looped and faded
    │   ├── stem_sfx.wav       # stage 5: every SFX placed on the timeline
    │   └── mix.wav            # stage 5: voice + music + SFX mixed down
    ├── cache/                 # resumable scratch data (segments, chunks)
    └── output/
        ├── prepared_images/
        ├── preview_video.mp4  # stage 4: silent, frame-exact against audio
        ├── final_video.mp4    # stage 6: the deliverable (video + audio)
        ├── final_video_subtitled.mp4   # stage 7, when captions were asked for
        ├── captions.srt       # stage 7: subtitle file (SRT)
        ├── quality_report.json
        ├── state.json         # resume manifest, written after every stage
        └── logs/autovid.log

Asset paths inside script.json (`assets/audio/bg.mp3`, `assets/fonts/x.ttf`,
`assets/sfx/whoosh.mp3`) are resolved relative to the workspace first, then
relative to the repository root, so shared assets can live in one place.
"""

from __future__ import annotations

import datetime as _datetime
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATE_VERSION = 1

# Stages in execution order.  Used for "resume from where we stopped".
STAGE_ORDER: tuple[str, ...] = (
    "validate",
    "tts",
    "images",
    "assembly",
    "mix",
    "render",
    "captions",
)


def _now() -> str:
    return _datetime.datetime.now().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Paths:
    """Resolved directory layout for one workspace."""

    workspace: Path
    audio_dir: Path
    cache_dir: Path
    output_dir: Path
    prepared_images_dir: Path
    segments_dir: Path
    logs_dir: Path

    @classmethod
    def from_workspace(
        cls, workspace: Path, output_dir: Path | None = None
    ) -> "Paths":
        workspace = workspace.resolve()
        output = (
            output_dir.resolve() if output_dir is not None else workspace / "output"
        )
        cache = workspace / "cache"
        return cls(
            workspace=workspace,
            audio_dir=workspace / "audio",
            cache_dir=cache,
            output_dir=output,
            prepared_images_dir=output / "prepared_images",
            segments_dir=cache / "segments",
            logs_dir=output / "logs",
        )

    @property
    def script_path(self) -> Path:
        return self.workspace / "script.json"

    @property
    def state_path(self) -> Path:
        return self.output_dir / "state.json"

    @property
    def voiceover_path(self) -> Path:
        return self.audio_dir / "voiceover_full.wav"

    @property
    def mix_path(self) -> Path:
        """Stage 5's deliverable: narration + BGM + SFX, one audio file."""
        return self.audio_dir / "mix.wav"

    @property
    def mix_cache_dir(self) -> Path:
        """Canonical-format copies of BGM/SFX, so the mix graph is uniform."""
        return self.cache_dir / "mix"

    @property
    def preview_path(self) -> Path:
        """Stage 4's silent video, which stage 6 muxes audio into."""
        return self.output_dir / "preview_video.mp4"

    @property
    def final_video_path(self) -> Path:
        return self.output_dir / "final_video.mp4"

    @property
    def subtitles_path(self) -> Path:
        return self.output_dir / "captions.srt"

    @property
    def subtitled_video_path(self) -> Path:
        return self.output_dir / "final_video_subtitled.mp4"

    @property
    def quality_report_path(self) -> Path:
        return self.output_dir / "quality_report.json"

    def scene_audio_path(self, scene_id: int) -> Path:
        return self.audio_dir / f"scene_{scene_id:03d}.wav"

    def create(self) -> None:
        """Create every directory this workspace needs."""
        for directory in (
            self.audio_dir,
            self.cache_dir,
            self.mix_cache_dir,
            self.output_dir,
            self.prepared_images_dir,
            self.segments_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def resolve_asset(reference: str, workspace: Path) -> Path | None:
    """
    Resolve an asset reference from script.json.

    Absolute paths are used as-is.  Relative paths are tried against the
    workspace first, then against the repository root.  Returns None when
    the file cannot be found so callers can report a precise error.
    """
    candidate = Path(reference).expanduser()
    if candidate.is_absolute():
        return candidate if candidate.exists() else None

    for base in (workspace.resolve(), PROJECT_ROOT):
        resolved = (base / candidate).resolve()
        if resolved.exists():
            return resolved
    return None


def write_json(path: Path, payload: Any) -> None:
    """Write a report/manifest as pretty UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    """Read a JSON file, returning None when it does not exist."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


@dataclass
class StateStore:
    """
    Resume manifest stored at `output/state.json`.

    Each stage records its status and the artifacts it produced, which is
    what makes "re-run the pipeline, skip finished work" possible.
    """

    path: Path
    data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.data:
            loaded = read_json(self.path)
            if isinstance(loaded, dict) and "stages" in loaded:
                self.data = loaded
            else:
                self.data = {"version": STATE_VERSION, "stages": {}}

    @property
    def stages(self) -> dict:
        return self.data.setdefault("stages", {})

    def record(
        self,
        stage: str,
        status: str,
        *,
        artifacts: list[str] | None = None,
        detail: dict | None = None,
        started_at: str | None = None,
    ) -> None:
        record = self.stages.get(stage, {})
        record.update(
            {
                "status": status,
                "finished_at": _now(),
                "artifacts": artifacts or [],
                "detail": detail or {},
            }
        )
        if started_at is not None:
            record["started_at"] = started_at
        self.stages[stage] = record
        self.save()

    def get(self, stage: str) -> dict | None:
        record = self.stages.get(stage)
        return record if isinstance(record, dict) else None

    def is_success(self, stage: str) -> bool:
        record = self.get(stage)
        return bool(record and record.get("status") == "pass")

    def save(self) -> None:
        self.data["version"] = STATE_VERSION
        self.data["updated_at"] = _now()
        write_json(self.path, self.data)
