"""
Pure-Python Motion-JPEG muxer.

`bin/ffmpeg` in this repository is a Linux ELF binary, and the macOS build
of the pipeline has no ffmpeg until Homebrew installs one.  A preview
should not be blocked on that: Motion JPEG is a real video codec, so a
plain MJPEG movie can be assembled from JPEG frames with nothing but the
standard library.  QuickTime, VLC and IINA all play it; browsers mostly do
not, which is fine for "look at the staging before I render the real
thing".

The container is a minimal ISO base media file:

    ftyp
    mdat                       every JPEG frame, back to back
    moov
      mvhd
      trak
        tkhd
        mdia
          mdhd
          hdlr
          minf
            vmhd
            dinf > dref > url
            stbl
              stsd > jpeg     the one sample entry
              stts             one sample per frame: constant frame rate
              stss             every frame is a sync sample
              stsc             one chunk
              stsz             per-sample sizes
              stco             the chunk's offset

Everything is constant frame rate with every frame a keyframe, which is
what a preview wants anyway: random access and a trivial timeline.
"""

from __future__ import annotations

import struct
from pathlib import Path

MATRIX = [
    0x00010000, 0, 0,
    0, 0x00010000, 0,
    0, 0, 0x40000000,
]

# A fixed 24 fps preview timescale: MJPEG frame rate is only a playback
# hint, and the preview builder renders at whatever it renders at.
TIMESCALE = 24


def _box(kind: bytes, *payload: bytes) -> bytes:
    body = b"".join(payload)
    return struct.pack(">I", len(body) + 8) + kind + body


def _full(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _box(kind, struct.pack(">B3s", version, flags.to_bytes(3, "big")), payload)


def _matrix() -> bytes:
    return struct.pack(">9i", *MATRIX)


def _compressor_name(text: str = "Motion JPEG") -> bytes:
    """
    The 32-byte Pascal string a VisualSampleEntry carries.

    QuickTime reads it to pick a decoder; an all-zero name is a valid
    Pascal string of length zero, but QuickTime rejects the whole file as
    "not compatible" rather than falling back, so it has to be filled in.
    """
    raw = text.encode("ascii", "replace")[:31]
    return bytes([len(raw)]) + raw + b"\x00" * (31 - len(raw))


def write_mjpeg_movie(
    path: Path,
    frames: list[bytes],
    *,
    width: int,
    height: int,
    fps: int = TIMESCALE,
) -> Path:
    """
    Write `frames` (raw JPEG payloads) as an MJPEG movie at `path`.

    One sample per frame, all sync samples, so the timeline is a single
    entry in stts and a size table in stsz.
    """
    if not frames:
        raise ValueError("a movie needs at least one frame")

    count = len(frames)
    # ftyp + mdat header + payload
    payload = b"".join(frames)
    mdat_offset = 0  # patched after we know the header sizes
    ftyp = _box(b"ftyp", b"qt  ", struct.pack(">I", 512), b"qt  ")

    # Sample sizes, chunk offsets.
    sizes = [len(frame) for frame in frames]
    # mdat data starts right after ftyp + the 8-byte mdat header.
    data_start = len(ftyp) + 8

    stsd = _box(
        b"stsd",
        struct.pack(">I", 0),  # version + flags
        struct.pack(">I", 1),  # one sample entry
        _box(
            b"jpeg",
            b"\x00" * 6,  # reserved
            struct.pack(">H", 1),  # data reference index
            struct.pack(">HH", 0, 0),  # pre_defined, reserved
            b"\x00" * 12,  # pre_defined[3]
            struct.pack(">HH", width, height),
            struct.pack(">II", 0x00480000, 0x00480000),  # 72 dpi
            struct.pack(">I", 0),  # reserved
            struct.pack(">H", 1),  # frame count per sample
            _compressor_name(),
            struct.pack(">H", 24),  # depth
            struct.pack(">h", -1),  # pre_defined
        ),
    )
    stts = _box(
        b"stts",
        struct.pack(">I", 0),
        struct.pack(">I", 1),
        struct.pack(">II", count, 1),  # count samples, delta 1 each
    )
    stss = _box(
        b"stss",
        struct.pack(">I", 0),
        struct.pack(">I", count),
        b"".join(struct.pack(">I", index + 1) for index in range(count)),
    )
    stsc = _box(
        b"stsc",
        struct.pack(">I", 0),
        struct.pack(">I", 1),
        struct.pack(">III", 1, count, 1),  # first chunk, samples per chunk, desc idx
    )
    stsz = _box(
        b"stsz",
        struct.pack(">I", 0),
        struct.pack(">I", 0),  # 0 = per-sample table follows
        struct.pack(">I", count),
        b"".join(struct.pack(">I", size) for size in sizes),
    )
    stco = _box(
        b"stco",
        struct.pack(">I", 0),
        struct.pack(">I", 1),
        struct.pack(">I", data_start),
    )
    stbl = _box(b"stbl", stsd, stts, stss, stsc, stsz, stco)

    dinf = _box(b"dinf", _box(b"dref", struct.pack(">II", 0, 1), _full(b"url ", 0, 1, b"")))
    vmhd = _full(b"vmhd", 0, 1, struct.pack(">HHHH", 0, 0, 0, 0))
    minf = _box(b"minf", vmhd, dinf, stbl)

    duration = count  # in movie timescale units
    hdlr = _box(
        b"hdlr",
        struct.pack(">II", 0, 0),
        b"vide",  # handler type
        b"\x00" * 12,  # reserved
        b"VideoHandler\x00",
    )
    # mdhd v0 ends with a 2-byte pre_defined after the language code; leaving
    # it out makes every following box read at the wrong offset, which is how
    # QuickTime ends up declaring the whole file incompatible.
    mdhd = _full(
        b"mdhd",
        0,
        0,
        struct.pack(">IIII", 0, 0, fps, duration)
        + struct.pack(">H", 0x55C4)  # language: "und"
        + struct.pack(">H", 0),  # pre_defined
    )
    mdia = _box(b"mdia", mdhd, hdlr, minf)

    width_fixed = width << 16
    height_fixed = height << 16
    tkhd = _full(
        b"tkhd",
        0,
        3,  # enabled | in movie
        struct.pack(">IIIII", 0, 0, 1, 0, duration)
        + b"\x00" * 8  # reserved
        + struct.pack(">hh", 0, 0)  # layer, alternate group
        + struct.pack(">hh", 0, 0)  # volume, reserved
        + _matrix()
        + struct.pack(">II", width_fixed, height_fixed),
    )
    trak = _box(b"trak", tkhd, mdia)

    mvhd = _full(
        b"mvhd",
        0,
        0,
        struct.pack(">IIII", 0, 0, fps, duration)
        + struct.pack(">I", 0x00010000)  # rate
        + struct.pack(">H", 0x0100)  # volume
        + b"\x00" * 10  # reserved
        + _matrix()
        + b"\x00" * 24  # pre_defined
        + struct.pack(">I", 2),  # next track id
    )
    moov = _box(b"moov", mvhd, trak)

    with path.open("wb") as handle:
        handle.write(ftyp)
        handle.write(struct.pack(">I", len(payload) + 8) + b"mdat")
        handle.write(payload)
        handle.write(moov)

    return path