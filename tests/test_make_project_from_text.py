"""
`tools/make_project_from_text.py`, exercised without running the pipeline.

The tool is the bridge from "a writer's text" to a `script.json` stage 1
accepts, so what matters here is that the script it builds is *valid* -- the
scene breaks in particular, which are 0-based indices the validator range-
checks, and which were written one too high.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
SRC_DIR = REPO_ROOT / "src"
for path in (str(TOOLS_DIR), str(SRC_DIR)):
    # Unconditional front inserts: the editable install already has src at
    # the end of sys.path, so a guarded insert would leave the repo-root
    # autovid.py shadowing the package under unittest (cwd = project root).
    sys.path.insert(0, path)


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "make_project_from_text", TOOLS_DIR / "make_project_from_text.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_project = load_tool()


class _Workspace:
    """A throwaway workspace with one real image the validator can find."""

    def __enter__(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_from_text_")
        self.root = Path(self._tmp.name)
        (self.root / "images").mkdir(parents=True, exist_ok=True)
        from PIL import Image

        for index in range(1, 4):
            Image.new("RGB", (320, 180), (40 + index * 30, 60, 90)).save(
                self.root / "images" / f"scene_{index:03d}.png"
            )
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False

    @property
    def images(self) -> list[Path]:
        return sorted((self.root / "images").glob("*.png"))


def _build(scenes: list[str], overlays: dict[int, str], images: list[Path]) -> dict:
    """A script the way main() writes one."""
    return make_project.build_script(
        scenes,
        overlays,
        images,
        title="Bai kiem tra",
        author="Buffy",
        voice="Anh Khôi",
        speed=1.0,
        music=None,
        music_volume=-22.0,
    )


class SectionBreakTest(unittest.TestCase):
    """
    `pacing.section_breaks` holds 0-based scene indices.

    `pacing.py` looks a break up with `index in section_breaks`, where index 0
    is the first scene, and the validator range-checks the values.  Writing
    `index + 1` pushed the last heading one past the end, so every text with a
    heading produced a script that failed validation -- the writer's chapter
    marks became the one thing that could not be saved.
    """

    def test_a_heading_on_the_first_scene_is_not_a_break(self) -> None:
        with _Workspace() as work:
            script = _build(["Cau mot."], {0: "Mo dau"}, work.images)
        self.assertEqual(script["pacing"]["section_breaks"], [])

    def test_a_heading_mid_script_is_a_break_on_that_scene(self) -> None:
        with _Workspace() as work:
            script = _build(["A.", "B.", "C."], {2: "Hoi hai"}, work.images)
        self.assertEqual(script["pacing"]["section_breaks"], [2])

    def test_every_break_points_at_a_scene_that_exists(self) -> None:
        scenes = ["A.", "B.", "C.", "D."]
        with _Workspace() as work:
            script = _build(scenes, {0: "Mo dau", 2: "Hoi hai"}, work.images)
        count = len(script["scenes"])
        for value in script["pacing"]["section_breaks"]:
            self.assertLess(
                value, count,
                f"section break {value} points past the last scene ({count})",
            )

    def test_breaks_are_sorted_and_unique(self) -> None:
        with _Workspace() as work:
            script = _build(["A.", "B.", "C.", "D."], {3: "Sau", 1: "Giua"}, work.images)
        self.assertEqual(script["pacing"]["section_breaks"], [1, 3])

    def test_the_script_it_writes_passes_the_schema(self) -> None:
        from autovid.domain.script import ScriptSchemaError, parse_script

        with _Workspace() as work:
            script = _build(["A.", "B.", "C."], {0: "Mo dau", 2: "Hoi hai"}, work.images)
        try:
            parse_script(script)
        except ScriptSchemaError as error:
            self.fail(f"the tool wrote a schema-invalid script: {error}")

    def test_the_script_it_writes_has_no_validation_errors(self) -> None:
        """
        The real validator, not a hand-written mirror of it.

        The tool's whole promise is that a content problem is reported now
        instead of part-way through a render, so an unbuildable script is the
        one failure that must not come out of here.
        """
        from autovid.application.validate import ScriptValidator
        from autovid.domain.script import parse_script
        from autovid.paths import Paths

        with _Workspace() as work:
            script = _build(
                [
                    "Khu chung cư Hoa Mai nằm ngay trung tâm thành phố.",
                    "Hồi thứ hai, người bảo vệ kể lại chuyện gì đó lạ lùng.",
                    "Không ai biết người đàn ông ấy từ đâu đến vậy.",
                ],
                {0: "Mo dau", 2: "Hoi hai"},
                work.images,
            )
            parsed = parse_script(script)
            report = ScriptValidator(parsed, Paths.from_workspace(work.root)).run()
        # The engine is not installed on every machine; that check is the
        # pipeline's business, not this tool's.  Everything else must pass.
        codes = {issue.code for issue in report.errors if issue.code != "tts_engine_missing"}
        self.assertEqual(codes, set(), f"the generated script fails: {sorted(codes)}")


class ParseTextTest(unittest.TestCase):
    """The writer's own formatting is what decides the scene boundaries."""

    def test_a_blank_line_starts_a_new_scene(self) -> None:
        blocks = make_project.parse_text("Mot.\n\nHai.\n")
        self.assertEqual([kind for kind, _ in blocks], ["body", "body"])

    def test_bracketed_headings_are_headings(self) -> None:
        blocks = make_project.parse_text("[[ Chuong 1 ]]\n\nMot.\n")
        self.assertEqual(blocks[0], ("heading", "Chuong 1"))
        self.assertEqual(blocks[1][0], "body")

    def test_a_long_paragraph_is_split_into_several_scenes(self) -> None:
        long_text = " ".join(f"Cau so {i}." for i in range(40))
        scenes = make_project.split_body(long_text, scene_seconds=4.0)
        self.assertGreater(len(scenes), 1)


if __name__ == "__main__":
    unittest.main()