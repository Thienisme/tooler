"""The bundled ffmpeg binaries only run where their executable format matches."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autovid.infrastructure.ffmpeg import _is_native  # noqa: E402


ELF = b"\x7fELF\x02\x00\x01\x00" + b"\x00" * 56
MACHO64 = b"\xcf\xfa\xed\xfe" + b"\x07\x00\x00\x01" + b"\x00" * 48
MACHO32 = b"\xce\xfa\xed\xfe" + b"\x07\x00\x00\x01" + b"\x00" * 44
PE = b"MZ\x90\x00" + b"\x00" * 60


class NativeBinaryTest(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="autovid_ffmpeg_")
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, payload: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(payload)
        return path

    def test_the_repository_binaries_are_linux_elf(self) -> None:
        """The real bundled binaries must not be handed to a macOS pipeline."""
        from autovid.infrastructure.ffmpeg import BIN_DIR

        for name in ("ffmpeg", "ffprobe"):
            bundled = BIN_DIR / name
            if bundled.exists():
                with self.subTest(binary=name):
                    self.assertFalse(_is_native(bundled))

    def test_elf_is_only_native_on_linux(self) -> None:
        self.assertEqual(_is_native(self.write("elf", ELF)), sys.platform == "linux")

    def test_macho_is_only_native_on_darwin(self) -> None:
        for payload in (MACHO64, MACHO32):
            path = self.write("macho", payload)
            with self.subTest(magic=payload[:4]):
                self.assertEqual(
                    _is_native(path), sys.platform == "darwin"
                )

    def test_pe_is_only_native_on_windows(self) -> None:
        self.assertEqual(_is_native(self.write("pe", PE)), sys.platform == "win32")

    def test_a_shell_script_is_accepted(self) -> None:
        script = b"#!/bin/sh\necho ffmpeg\n"
        self.assertTrue(_is_native(self.write("ffmpeg", script)))


if __name__ == "__main__":
    unittest.main()