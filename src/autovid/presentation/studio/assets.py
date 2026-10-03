"""
The asset library: what the author can put on the stage.

A project keeps its pictures in `images/` and its sprites in
`assets/characters/`.  The studio indexes those folders rather than asking
for a file path, because a wrong path is the single most common reason a
hand-written script fails to validate, and a picker cannot produce one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# Folders the studio looks in, relative to the project root, in the order an
# author is most likely to have put the file there.
BACKGROUND_DIRS = ("images/backgrounds", "images", "assets/backgrounds")
CHARACTER_DIRS = ("assets/characters", "assets/sprites", "characters")
FRAME_DIRS = ("assets/frames",)


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

    def invalidate(self) -> None:
        """Called after the author adds a picture to the folder."""
        self._cache = None

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

    def _group(self, name: str) -> list[Asset]:
        if self._cache is None:
            self._cache = {
                "backgrounds": self._scan(BACKGROUND_DIRS),
                "characters": self._scan(CHARACTER_DIRS),
                "frames": self._scan(FRAME_DIRS),
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
        return not (self.backgrounds or self.characters)