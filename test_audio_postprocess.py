import subprocess
from pathlib import Path


INPUT_FILE = Path(
    "projects/mystery-000/audio/segments/segment_01.mp3"
)

OUTPUT_DIR = INPUT_FILE.parent / "postprocessed_test_v2"


PRESETS = {
    # A - Bản gốc, chỉ convert lại để tiện A/B
    "original": (
        "loudnorm=I=-16:TP=-1.5:LRA=7"
    ),

    # B - Mystery
    # Trầm hơn, tối hơn nhưng vẫn tự nhiên.
    "mystery": (
        "highpass=f=65,"
        "equalizer=f=100:t=q:w=0.9:g=3.5,"
        "equalizer=f=220:t=q:w=1.0:g=1.5,"
        "equalizer=f=1200:t=q:w=1.0:g=-1.0,"
        "equalizer=f=3000:t=q:w=0.9:g=-2.5,"
        "equalizer=f=7000:t=q:w=0.8:g=-1.5,"
        "acompressor="
        "threshold=-22dB:"
        "ratio=2.5:"
        "attack=15:"
        "release=180:"
        "makeup=1,"
        "loudnorm=I=-16:TP=-1.5:LRA=7"
    ),

    # C - Horror
    # Tối hơn và có cảm giác "dark narrator".
    "horror": (
        "highpass=f=60,"
        "equalizer=f=90:t=q:w=0.9:g=5,"
        "equalizer=f=180:t=q:w=1.0:g=3,"
        "equalizer=f=450:t=q:w=1.0:g=-1.5,"
        "equalizer=f=1800:t=q:w=1.0:g=-2,"
        "equalizer=f=3200:t=q:w=0.9:g=-4,"
        "equalizer=f=7000:t=q:w=0.8:g=-3,"
        "acompressor="
        "threshold=-24dB:"
        "ratio=3.2:"
        "attack=12:"
        "release=200:"
        "makeup=1,"
        "aecho=0.8:0.10:45:0.07,"
        "loudnorm=I=-16:TP=-1.5:LRA=7"
    ),

    # D - Deep Horror
    # Mạnh nhất. Có pitch shift nhẹ để kiểm tra cảm giác giọng trầm.
    "deep_horror": (
        "highpass=f=55,"
        "asetrate=24000*0.97,"
        "aresample=24000,"
        "atempo=1.0309,"
        "equalizer=f=85:t=q:w=0.9:g=6,"
        "equalizer=f=170:t=q:w=1.0:g=3.5,"
        "equalizer=f=400:t=q:w=1.0:g=-2,"
        "equalizer=f=1600:t=q:w=1.0:g=-2.5,"
        "equalizer=f=3000:t=q:w=0.9:g=-5,"
        "equalizer=f=7000:t=q:w=0.8:g=-4,"
        "acompressor="
        "threshold=-25dB:"
        "ratio=3.5:"
        "attack=10:"
        "release=220:"
        "makeup=1,"
        "aecho=0.8:0.14:55:0.09,"
        "loudnorm=I=-16:TP=-1.5:LRA=7"
    ),
}


def process_audio(style: str, audio_filter: str) -> None:
    output_file = (
        OUTPUT_DIR / f"segment_01_{style}_v2.mp3"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(INPUT_FILE),
        "-af",
        audio_filter,
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(output_file),
    ]

    print()
    print("=" * 50)
    print(f"Processing: {style.upper()}")
    print(f"Input:      {INPUT_FILE}")
    print(f"Output:     {output_file}")
    print("=" * 50)

    subprocess.run(command, check=True)

    print(f"Done: {output_file}")


def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Input file not found: {INPUT_FILE}"
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    for style, audio_filter in PRESETS.items():
        process_audio(
            style,
            audio_filter,
        )

    print()
    print("=" * 50)
    print("Audio post-processing V2 completed.")
    print("=" * 50)
    print()
    print(f"Output directory:")
    print(f"  {OUTPUT_DIR}")
    print()
    print("Generated files:")

    for style in PRESETS:
        print(
            f"  segment_01_{style}_v2.mp3"
        )


if __name__ == "__main__":
    main()