from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autovid.presentation import cli


class RunCommandTests(unittest.TestCase):
    def test_run_defaults_to_all_stages_and_captions(self):
        args = cli.build_parser().parse_args(["run", "script.json", "--backend", "fake"])

        self.assertEqual(args.command, "run")
        self.assertEqual(args.backend, "fake")
        self.assertFalse(args.no_captions)
        self.assertFalse(args.burn_captions)

    def test_run_calls_stages_in_order_and_emits_final_path(self):
        with tempfile.TemporaryDirectory(prefix="autovid-run-") as directory:
            script_path = Path(directory) / "script.json"
            script_path.write_text("{}", encoding="utf-8")
            calls: list[str] = []

            def stage(name: str):
                def run(_args):
                    calls.append(name)
                    return 0

                return run

            handlers = {
                "cmd_validate": stage("validate"),
                "cmd_tts": stage("tts"),
                "cmd_images": stage("images"),
                "cmd_assembly": stage("assembly"),
                "cmd_mix": stage("mix"),
                "cmd_render": stage("render"),
                "cmd_captions": stage("captions"),
            }
            with contextlib.ExitStack() as stack:
                for name, handler in handlers.items():
                    stack.enter_context(mock.patch.object(cli, name, side_effect=handler))
                stack.enter_context(mock.patch.object(cli, "load_script", return_value=object()))
                stack.enter_context(
                    mock.patch.object(cli, "write_quality_report", return_value={"status": "pass"})
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = cli.main(["run", str(script_path), "--backend", "fake", "--quiet"])

            self.assertEqual(result, 0)
            self.assertEqual(
                calls,
                ["validate", "tts", "images", "assembly", "mix", "render", "captions"],
            )
            self.assertIn("final_video_subtitled.mp4", output.getvalue())

    def test_run_stops_after_a_failed_stage(self):
        with tempfile.TemporaryDirectory(prefix="autovid-run-") as directory:
            script_path = Path(directory) / "script.json"
            script_path.write_text("{}", encoding="utf-8")
            tts = mock.patch.object(cli, "cmd_tts")
            assembly = mock.patch.object(cli, "cmd_assembly")
            with mock.patch.object(cli, "cmd_validate", return_value=1), tts as tts_handler, assembly as assembly_handler:
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    result = cli.main(["run", str(script_path), "--quiet"])

            self.assertEqual(result, 1)
            tts_handler.assert_not_called()
            assembly_handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()