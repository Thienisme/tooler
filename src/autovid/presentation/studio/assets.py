"""
The asset library: what the author can put on the stage.

A project keeps its pictures in `images/` and its sprites in
`assets/characters/`.  The studio indexes those folders rather than asking
for a file path, because a wrong path is the single most common reason a
hand-written script fails to validate, and a picker cannot produce one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# Folders the studio looks in, relative to the project root, in the order an
# author is most likely to have put the file there.
BACKGROUND_DIRS = ("images/backgrounds", "images", "assets/backgrounds")
CHARACTER_DIRS = ("assets/characters", "assets/sprites", "characters")
FRAME_DIRS = ("assets/frames",)

# The folder a narrator picture is simply *dropped into*.  Anything here
# shows up in the studio's storyteller list right away, with no JSON to
# write: the file name becomes the key `story_frame.image_file` carries.
# The project's own folder is read first, then the repository's, so one
# project can add a storyteller without touching the shared artwork.
NARRATOR_DIRS = ("assets/narrators",)

# The registry that names the storytellers.  Its keys -- `story_host`,
# `ke_su`, ... -- are what `story_frame.use` carries, and an entry brings
# its mouth flap and poses along, so it is still the way to describe a
# narrator that talks.  A bare picture in `assets/narrators/` needs none
# of that.
REGISTRY_RELATIVE = "assets/characters/characters.json"


@dataclass(frozen=True)
class Asset:
    """One file the author can place, with the path the script will carry."""

    path: Path
    relative: str

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def label(self) -> str:
        """A readable name: underscores become spaces, so it scans faster."""
        return self.path.stem.replace("_", " ")


@dataclass(frozen=True)
class NarratorAsset:
    """
    One registry entry: a character that can narrate the frame.

    The identity a script carries is the *key* (`story_frame.use`), not a
    path -- the artwork, its mouth flap and its poses all ride in from the
    registry entry, so pointing at the picture directly would drop them.
    """

    key: str
    image_file: str
    # The registry key this narrator came from, or None when it is just a
    # picture dropped in `assets/narrators/`.  The studio writes `use` for
    # the first and `image_file` for the second, and the schema accepts
    # either, so both kinds render.
    use: str | None = None

    @property
    def name(self) -> str:
        return self.key

    @property
    def label(self) -> str:
        """A readable name: underscores become spaces, so it scans faster."""
        return self.key.replace("_", " ")


class AssetLibrary:
    """
    The indexed assets of one project.

    `relative` is what goes into script.json.  Paths are stored the way the
    existing scripts already write them, so a project opened in the studio
    and saved back keeps pointing at the same files.
    """

    def __init__(self, workspace: Path, repo_root: Path) -> None:
        self.workspace = Path(workspace)
        self.repo_root = Path(repo_root)
        self._cache: dict[str, list[Asset]] | None = None
        self._registry: dict[str, dict] | None = None

    def invalidate(self) -> None:
        """Called after the author adds a picture to the folder."""
        self._cache = None
        self._registry = None

    def _scan(self, directories: tuple[str, ...]) -> list[Asset]:
        found: list[Asset] = []
        seen: set[Path] = set()
        for relative_dir in directories:
            directory = self.workspace / relative_dir
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                if not path.is_file() or path in seen:
                    continue
                seen.add(path)
                found.append(
                    Asset(path=path, relative=f"{relative_dir}/{path.name}")
                )
        return found

    @property
    def backgrounds(self) -> list[Asset]:
        return self._group("backgrounds")

    @property
    def characters(self) -> list[Asset]:
        return self._group("characters")

    @property
    def frames(self) -> list[Asset]:
        return self._group("frames")

    @property
    def narrator_images(self) -> list[Asset]:
        """Pictures dropped in the storyteller folders, unregistered."""
        return self._group("narrators")

    def registry(self) -> dict[str, dict]:
        """
        The character registry by key: the repo's, then the project's own.

        A project may ship its own `characters.json`; it wins for the keys
        it defines, so one project can replace a narrator without editing
        the shared file.  Keys starting with `_` are notes for humans, and
        a registry that is missing or malformed reads as empty rather than
        breaking the studio over a lookup.
        """
        if self._registry is None:
            merged: dict[str, dict] = {}
            for base in (self.repo_root, self.workspace):
                path = base / REGISTRY_RELATIVE
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(raw, dict):
                    continue
                for key, entry in raw.items():
                    if str(key).startswith("_") or not isinstance(entry, dict):
                        continue
                    merged[str(key)] = entry
            self._registry = merged
        return self._registry

    @property
    def narrators(self) -> list[NarratorAsset]:
        """
        Every storyteller the author can pick.

        The registry comes first -- its keys are what `story_frame.use`
        carries, and an entry brings its mouth flap and pose pool with it --
        then any bare picture dropped in `assets/narrators/`, named by its
        own file.  A picture the registry already names, or one whose name
        repeats a registry key, is skipped: the same storyteller should not
        appear twice in a list the author reads by name.
        """
        registry = self.registry()
        found: list[NarratorAsset] = []
        taken: set[str] = set()
        for key, entry in registry.items():
            image_file = entry.get("image_file")
            if not image_file:
                continue
            found.append(NarratorAsset(key=key, image_file=str(image_file), use=key))
            taken.add(str(image_file))
            taken.add(Path(str(image_file)).name)

        for asset in self._group("narrators"):
            if asset.name in registry or asset.relative in taken:
                continue
            if asset.path.name in taken:
                continue
            found.append(NarratorAsset(key=asset.name, image_file=asset.relative))
        return found

    def _scan_narrators(self) -> list[Asset]:
        """
        Storyteller pictures: the project's folder first, then the repo's.

        Both are written repo-or-workspace-style (`assets/narrators/x.png`),
        the shape the pipeline already resolves relative to the workspace
        and then the repository root -- so one path works whether the
        picture sits beside the project or in the shared artwork.
        """
        found = self._scan(NARRATOR_DIRS)
        names = {asset.path.name for asset in found}
        for relative_dir in NARRATOR_DIRS:
            directory = self.repo_root / relative_dir
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
                    continue
                if path.name in names:
                    continue
                names.add(path.name)
                found.append(
                    Asset(path=path, relative=f"{relative_dir}/{path.name}")
                )
        return found

    def _group(self, name: str) -> list[Asset]:
        if self._cache is None:
            self._cache = {
                "backgrounds": self._scan(BACKGROUND_DIRS),
                "characters": self._scan(CHARACTER_DIRS),
                "frames": self._scan(FRAME_DIRS),
                "narrators": self._scan_narrators(),
            }
        return self._cache[name]

    def resolve(self, relative: str) -> Path | None:
        """
        Turn a script's asset path back into a file.

        Scripts in this repo mix three conventions: repo-relative
        (`projects/.../images/...`), workspace-relative (`images/...`) and
        bare (`assets/characters/...`).  All three have to resolve, or the
        studio opens a project it cannot display.
        """
        if not relative:
            return None
        candidate = Path(relative)
        if candidate.is_file():
            return candidate
        for base in (self.workspace, self.repo_root):
            resolved = base / candidate
            if resolved.is_file():
                return resolved
        # Last resort: match on the file name anywhere in the project, so a
        # scene written with a different prefix still shows its picture.
        name = candidate.name
        for base in (self.workspace, self.repo_root):
            for found in base.rglob(name):
                if found.is_file() and found.suffix.lower() in IMAGE_SUFFIXES:
                    return found
        return None

    def relative_for(self, path: Path) -> str:
        """The path to store for a file the author just picked."""
        path = Path(path)
        for base in (self.workspace, self.repo_root):
            try:
                return str(path.relative_to(base))
            except ValueError:
                continue
        return str(path)

    def is_empty(self) -> bool:
        return not (self.backgrounds or self.characters or self.narrator_images)