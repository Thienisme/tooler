"""
Command line interface for the autovid pipeline.

    python autovid.py validate <script.json> [--strict]

Stages run in the order defined by `autovid.paths.STAGE_ORDER`, each one
gated by the stage before it and each one resumable through
`output/state.json`.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from autovid.application.assembly import (
    DEFAULT_CRF,
    DEFAULT_PRESET,
    AssemblyStage,
)
from autovid.application.captions import (
    DEFAULT_MAX_CHARS_PER_LINE,
    CaptionsStage,
)
from autovid.application.images import ImagesStage
from autovid.application.mix import MixStage
from autovid.application.quality import write_quality_report
from autovid.application.render import DEFAULT_AUDIO_BITRATE, RenderStage
from autovid.application.tts import DEFAULT_MAX_RETRIES, TTSStage
from autovid.application.validate import ScriptValidator
from autovid.domain.script import Script, ScriptSchemaError, load_script
from autovid.infrastructure.tts.backend import FakeTTSBackend
from autovid.infrastructure.tts.vieneu_backend import VieNeuTTSBackend
from autovid.paths import Paths, StateStore, write_json

TTS_BACKENDS = ("vieneu", "fake")

ERROR_MARK = "[x]"
WARN_MARK = "[!]"
OK_MARK = "[ok]"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autovid",
        description="Automated explainer-video production pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "stages:\n"
            "  validate   check script.json and every asset before rendering\n"
            "  tts        per-sentence VieNeu voiceover + pacing + timeline\n"
            "  images     prepare frames and check image/overlay quality\n"
            "  assembly   render one clip per scene and join them (silent)\n"
            "  mix        mix voiceover, BGM and SFX into audio/mix.wav\n"
            "  render     mux picture and sound into output/final_video.mp4\n"
            "  captions   optional subtitles (script timings or faster-whisper)\n"

        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<stage>")

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "script",
            help="path to script.json (its folder becomes the workspace)",
        )
        sub.add_argument(
            "--workspace",
            type=Path,
            default=None,
            help="override the workspace folder (default: script.json's folder)",
        )
        sub.add_argument(
            "--out",
            type=Path,
            default=None,
            help="override the output folder (default: <workspace>/output)",
        )

    validate = subparsers.add_parser(
        "validate",
        help="check script.json, assets, ffmpeg and disk space",
        description=(
            "Stage 1. Validates the script schema, then checks every asset, "
            "font, SFX file, the ffmpeg toolchain and free disk space. "
            "Estimated runtime and disk usage are reported so surprises "
            "show up now rather than after an hour of rendering."
        ),
    )
    add_common(validate)
    validate.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors (use this for a release build)",
    )
    validate.add_argument(
        "--no-write",
        action="store_true",
        help="do not write reports or update state.json",
    )
    validate.add_argument(
        "--skip-engine-check",
        action="store_true",
        help="do not require the VieNeu engine to be installed (for CI and "
        "editor-only runs)",
    )
    validate.add_argument(
        "--json",
        action="store_true",
        help="print the validation report as JSON instead of a summary",
    )
    validate.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="only print errors and warnings",
    )

    tts = subparsers.add_parser(
        "tts",
        help="synthesize the voiceover, compute pauses, lay out the timeline",
        description=(
            "Stage 2. Synthesizes every sentence, measures its exact duration "
            "and natural silences, computes the pauses from those "
            "measurements, and writes timeline.json for the render stage. "
            "Sentence audio is cached, so re-running after a crash skips "
            "work that already succeeded."
        ),
    )
    add_common(tts)
    tts.add_argument(
        "--backend",
        choices=TTS_BACKENDS,
        default="vieneu",
        help="voice engine; 'fake' generates placeholder tones of realistic "
        "length, for testing the pipeline without the model",
    )
    tts.add_argument(
        "--voice",
        default=None,
        help="override tts_config.voice",
    )
    tts.add_argument(
        "--speed",
        type=float,
        default=None,
        help="override tts_config.speed (0.5-2.0)",
    )
    tts.add_argument(
        "--force",
        action="store_true",
        help="ignore the sentence cache and synthesize everything again",
    )
    tts.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"attempts per sentence (default {DEFAULT_MAX_RETRIES})",
    )
    tts.add_argument(
        "--normalize-each-scene",
        action="store_true",
        help="normalise scene stems individually as well as the master; "
        "off by default because it flattens intentional dynamics",
    )
    tts.add_argument(
        "--quiet",
        action="store_true",
        help="only print warnings, errors and the final summary",
    )

    images = subparsers.add_parser(
        "images",
        help="prepare scene frames and check image/overlay quality",
        description=(
            "Stage 3. Scales every frame once, at the size the render stage "
            "needs for Ken Burns headroom, then measures it: resolution "
            "adequacy, sharpness, blockiness and colour fidelity. Also "
            "resolves overlay fonts (with a system fallback) and reports the "
            "largest font size that actually fits the frame."
        ),
    )
    add_common(images)
    images.add_argument(
        "--fit",
        choices=("pad", "cover"),
        default="pad",
        help="'pad' keeps the whole image and letterboxes it (right for "
        "flat-colour artwork); 'cover' fills the frame and crops (right for "
        "photographs). Default: pad",
    )
    images.add_argument(
        "--pad-colour",
        default="#FFFFFF",
        help="colour used to fill the letterbox bars (default #FFFFFF, which "
        "is invisible on doodle artwork)",
    )
    images.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors (use this for a release build)",
    )
    images.add_argument(
        "--quiet",
        action="store_true",
        help="only print warnings, errors and the final summary",
    )

    assembly = subparsers.add_parser(
        "assembly",
        help="render one clip per scene and join them into a silent video",
        description=(
            "Stage 4. Renders every scene as its own clip -- Ken Burns "
            "zoom/pan plus text overlays -- and joins them into "
            "output/preview_video.mp4. Timing comes from timeline.json "
            "converted to whole frames, so the video is frame-exact against "
            "the voiceover. Clips are cached: re-running only re-renders what "
            "changed."
        ),
    )
    add_common(assembly)
    assembly.add_argument(
        "--preset",
        default=DEFAULT_PRESET,
        help=f"x264 preset for the scene clips (default {DEFAULT_PRESET})",
    )
    assembly.add_argument(
        "--crf",
        type=int,
        default=DEFAULT_CRF,
        help=f"x264 quality for the scene clips (default {DEFAULT_CRF}; lower "
        "is better and bigger)",
    )
    assembly.add_argument(
        "--force",
        action="store_true",
        help="re-render every scene even if its clip is already up to date",
    )
    assembly.add_argument(
        "--dry-run",
        action="store_true",
        help="report the frame plan, Ken Burns motion and overlay windows "
        "without encoding anything",
    )
    assembly.add_argument(
        "--no-characters",
        action="store_true",
        help="render the scenes without their character layers, to see the "
        "backgrounds and text on their own",
    )
    assembly.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors (use this for a release build)",
    )
    assembly.add_argument(
        "--quiet",
        action="store_true",
        help="only print warnings, errors and the final summary",
    )

    mix = subparsers.add_parser(
        "mix",
        help="mix the voiceover, background music and SFX",
        description=(
            "Stage 5. Loops and fades the background track to the video's "
            "length, places every SFX at its absolute timeline position, and "
            "sums the layers into audio/mix.wav. Each layer is also written "
            "as its own stem, so a wrong mix can be diagnosed by listening "
            "to one layer at a time. The mix is measured and only corrected "
            "when it has actually drifted from the loudness target."
        ),
    )
    add_common(mix)
    mix.add_argument(
        "--skip-music",
        action="store_true",
        help="mix the voice and SFX only (useful for checking SFX levels)",
    )
    mix.add_argument(
        "--quiet",
        action="store_true",
        help="only print warnings, errors and the final summary",
    )

    render = subparsers.add_parser(
        "render",
        help="mux the silent video and the mixed audio into the deliverable",
        description=(
            "Stage 6. Copies the assembled picture and encodes the mixed "
            "audio into output/final_video.mp4, then verifies what it wrote: "
            "stream count, codec, resolution, frame rate and length against "
            "the timeline. Also writes output/quality_report.json, which "
            "merges every stage report into one shippable-or-not summary."
        ),
    )
    add_common(render)
    render.add_argument(
        "--audio-bitrate",
        default=DEFAULT_AUDIO_BITRATE,
        help=f"AAC bitrate (default {DEFAULT_AUDIO_BITRATE})",
    )
    render.add_argument(
        "--voice-only",
        action="store_true",
        help="render the narration without music or SFX, ignoring "
        "audio/mix.wav",
    )
    render.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be muxed without encoding anything",
    )
    render.add_argument(
        "--quiet",
        action="store_true",
        help="only print warnings, errors and the final summary",
    )

    captions = subparsers.add_parser(
        "captions",
        help="write subtitles and attach them to the finished video",
        description=(
            "Stage 7. Builds output/captions.srt from the timings stage 2 "
            "measured -- the sentence durations and the pauses between them "
            "-- and attaches it to output/final_video.mp4, as a soft track "
            "by default or burned into the picture with --burn. Use --asr "
            "to transcribe the finished audio with faster-whisper instead."
        ),
    )
    add_common(captions)
    captions.add_argument(
        "--asr",
        action="store_true",
        help="time the cues by transcribing the audio with faster-whisper "
        "instead of using the pipeline's own measurements",
    )
    captions.add_argument(
        "--asr-model",
        default="small",
        help="faster-whisper model size when --asr is used (default small)",
    )
    captions.add_argument(
        "--burn",
        action="store_true",
        help="render the subtitles into the picture (re-encodes the video) "
        "instead of muxing a soft track",
    )
    captions.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS_PER_LINE,
        help=f"maximum characters per subtitle line "
        f"(default {DEFAULT_MAX_CHARS_PER_LINE})",
    )
    captions.add_argument(
        "--quiet",
        action="store_true",
        help="only print warnings, errors and the final summary",
    )

    return parser


def _print_header(script_path: Path, workspace: Path) -> None:
    print("\n=== autovid validate ===")
    print(f"script     : {script_path}")
    print(f"workspace  : {workspace}\n")


def _print_issues(report, quiet: bool) -> None:
    if report.errors:
        print(f"{ERROR_MARK} {len(report.errors)} error(s):")
        for issue in report.errors:
            location = f" (scene {issue.scene_id})" if issue.scene_id else ""
            print(f"    {ERROR_MARK} [{issue.code}]{location} {issue.message}")
        print()

    if report.warnings:
        print(f"{WARN_MARK} {len(report.warnings)} warning(s):")
        for issue in report.warnings:
            location = f" (scene {issue.scene_id})" if issue.scene_id else ""
            print(f"    {WARN_MARK} [{issue.code}]{location} {issue.message}")
        print()
    elif not report.errors and not quiet:
        print(f"{WARN_MARK} no warnings\n")


def _print_stats(stats: dict) -> None:
    print("estimate:")
    rows = (
        ("scenes", "scene_count"),
        ("text characters", "total_text_chars"),
        ("sentences", "total_sentences"),
        ("images found", "images_found"),
        ("images missing", "images_missing"),
        ("images to generate", "images_to_generate"),
        ("text overlays", "text_overlays"),
        ("sfx", "sfx_count"),
        ("characters", "characters"),
        ("punch-ins", "punch_ins"),
        ("narration", "estimated_narration_seconds"),
        ("runtime", "estimated_total_minutes"),
        ("disk needed", "estimated_disk_mb"),
    )
    units = {
        "estimated_narration_seconds": "s",
        "estimated_total_minutes": "min",
        "estimated_disk_mb": "MB",
        "estimated_total_seconds": "s",
    }
    for label, key in rows:
        if key not in stats:
            continue
        print(f"    {label:<20} {stats[key]}{units.get(key, '')}")
    print()


def cmd_validate(args: argparse.Namespace) -> int:
    script_path = Path(args.script).resolve()
    workspace = (
        Path(args.workspace).resolve()
        if args.workspace
        else script_path.parent
    )
    paths = Paths.from_workspace(workspace, args.out)

    if not script_path.exists():
        print(f"{ERROR_MARK} script not found: {script_path}", file=sys.stderr)
        return 2

    if not args.json:
        _print_header(script_path, paths.workspace)

    try:
        script = load_script(script_path)
    except ScriptSchemaError as error:
        if args.json:
            print(
                json.dumps(
                    {
                        "stage": "validate",
                        "status": "fail",
                        "schema_errors": error.errors,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(f"{ERROR_MARK} {error}\n")
        return 1

    validator = ScriptValidator(script, paths)
    report = validator.run(
        strict=args.strict, skip_engine_check=args.skip_engine_check
    )

    if args.json:
        print(
            json.dumps(
                report.to_dict(strict=args.strict), indent=2, ensure_ascii=False
            )
        )
    else:
        _print_issues(report, args.quiet)
        if not args.quiet:
            _print_stats(report.stats)
        if report.status == "pass":
            print(f"{OK_MARK} validation passed\n")
        else:
            print(f"{ERROR_MARK} validation failed\n")

    if not args.no_write:
        paths.create()
        write_json(
            paths.output_dir / "validation_report.json",
            report.to_dict(strict=args.strict),
        )
        write_json(
            paths.output_dir / "config_validated.json",
            dataclasses.asdict(script),
        )
        store = StateStore(paths.state_path)
        store.record(
            "validate",
            report.status,
            artifacts=[
                str(paths.output_dir / "validation_report.json"),
                str(paths.output_dir / "config_validated.json"),
            ],
            detail={
                "errors": len(report.errors),
                "warnings": len(report.warnings),
                "estimated_total_seconds": report.stats.get(
                    "estimated_total_seconds"
                ),
            },
        )
        if not args.json and not args.quiet:
            print(f"wrote {paths.output_dir / 'validation_report.json'}")
            print(f"wrote {paths.state_path}\n")

    return 0 if report.status == "pass" else 1


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def _build_backend(args: argparse.Namespace, script: Script):
    """Create the requested TTS backend, or return None with a message."""
    config = script.tts_config

    if args.backend == "fake":
        print(
            f"{WARN_MARK} using the fake backend: sentence lengths are "
            "realistic, the audio is a placeholder tone\n"
        )
        return FakeTTSBackend(speed=config.speed)

    try:
        return VieNeuTTSBackend(config.voice, speed=config.speed)
    except ImportError:
        print(
            f"{ERROR_MARK} the VieNeu TTS engine is not installed.\n"
            "    Install it with: pip install vieneu\n"
            f"    Or test the pipeline without it: --backend fake",
            file=sys.stderr,
        )
        return None


def cmd_tts(args: argparse.Namespace) -> int:
    script_path = Path(args.script).resolve()
    workspace = (
        Path(args.workspace).resolve() if args.workspace else script_path.parent
    )
    paths = Paths.from_workspace(workspace, args.out)

    if not script_path.exists():
        print(f"{ERROR_MARK} script not found: {script_path}", file=sys.stderr)
        return 2

    try:
        script = load_script(script_path)
    except ScriptSchemaError as error:
        print(f"{ERROR_MARK} {error}\n", file=sys.stderr)
        return 1

    overrides: dict = {}
    if args.voice:
        overrides["voice"] = args.voice
    if args.speed is not None:
        overrides["speed"] = args.speed
    if overrides:
        script = dataclasses.replace(
            script,
            tts_config=dataclasses.replace(script.tts_config, **overrides),
        )

    if not args.quiet:
        config = script.tts_config
        print("\n=== autovid tts ===")
        print(f"script     : {script_path}")
        print(f"workspace  : {paths.workspace}")
        print(
            f"voice      : {config.voice} @ {config.speed:.2f}x "
            f"({config.granularity} granularity)\n"
        )

    # Stage 1 is a gate: never synthesize into a workspace that cannot be
    # rendered later.  Warnings are printed but do not block.
    validation = ScriptValidator(script, paths).run(
        skip_engine_check=args.backend != "vieneu"
    )
    for issue in validation.warnings:
        print(f"{WARN_MARK} {issue}")
    if validation.errors:
        for issue in validation.errors:
            print(f"{ERROR_MARK} {issue}", file=sys.stderr)
        print(
            f"{ERROR_MARK} validation failed; run 'validate' for the full "
            "report",
            file=sys.stderr,
        )
        return 1

    backend = _build_backend(args, script)
    if backend is None:
        return 2

    progress = (lambda message: None) if args.quiet else _print_progress

    try:
        result = TTSStage(
            script,
            paths,
            backend,
            force=args.force,
            max_retries=args.max_retries,
            normalize_each_scene=args.normalize_each_scene,
            progress=progress,
        ).run()
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"\n{ERROR_MARK} voiceover failed: {error}", file=sys.stderr)
        return 1
    finally:
        backend.close()

    totals = result.report["totals"]
    loudness = totals["master_loudness"]

    print()
    for issue in result.issues:
        mark = ERROR_MARK if issue.severity == "error" else WARN_MARK
        print(f"{mark} {issue}")

    print(
        f"\nunits      : {totals['units']} "
        f"({totals['units_cached']} reused from cache, "
        f"{totals['units_synthesized']} synthesized, "
        f"{totals['retries']} retries)"
    )
    print(
        f"narration  : {format_duration(totals['scene_audio_s'])}  "
        f"pauses {format_duration(totals['scene_pauses_s'])}  "
        f"master {format_duration(totals['master_duration_s'])}"
    )
    print(
        f"loudness   : {loudness['integrated_lufs']:.1f} LUFS "
        f"(target {script.audio_config.master_volume:.1f}), "
        f"peak {loudness['true_peak_db']:.2f} dBFS"
    )
    print(f"timeline   : {paths.output_dir / 'timeline.json'}")

    if result.status == "pass":
        print(f"\n{OK_MARK} voiceover ready\n")
    else:
        print(f"\n{ERROR_MARK} voiceover finished with errors\n")

    store = StateStore(paths.state_path)
    store.record(
        "tts",
        result.status,
        artifacts=[
            str(paths.voiceover_path),
            str(paths.output_dir / "timeline.json"),
            str(paths.output_dir / "tts_report.json"),
            str(paths.output_dir / "pacing_report.json"),
        ],
        detail={
            "units": totals["units"],
            "units_cached": totals["units_cached"],
            "master_duration_s": totals["master_duration_s"],
            "integrated_lufs": loudness["integrated_lufs"],
        },
    )

    return 0 if result.status == "pass" else 1


def _print_progress(message: str) -> None:
    print(f"  {message}")


def _refresh_quality(script: Script, paths: Paths) -> None:
    """
    Rebuild the merged report once the resume manifest is up to date.

    The stage writes the merged report as soon as it can, but the manifest is
    recorded here, afterwards -- so without this second pass the shipped
    report would describe the pipeline as it stood one step ago.
    """
    if paths.quality_report_path.exists():
        write_quality_report(script, paths)


def cmd_images(args: argparse.Namespace) -> int:
    script_path = Path(args.script).resolve()
    workspace = (
        Path(args.workspace).resolve() if args.workspace else script_path.parent
    )
    paths = Paths.from_workspace(workspace, args.out)

    if not script_path.exists():
        print(f"{ERROR_MARK} script not found: {script_path}", file=sys.stderr)
        return 2

    try:
        script = load_script(script_path)
    except ScriptSchemaError as error:
        print(f"{ERROR_MARK} {error}\n", file=sys.stderr)
        return 1

    if not args.quiet:
        metadata = script.video_metadata
        print("\n=== autovid images ===")
        print(f"script     : {script_path}")
        print(f"workspace  : {paths.workspace}")
        print(f"frame      : {metadata.resolution} (fit={args.fit})\n")

    validation = ScriptValidator(script, paths).run(skip_engine_check=True)
    if validation.errors:
        for issue in validation.errors:
            print(f"{ERROR_MARK} {issue}", file=sys.stderr)
        return 1

    progress = (lambda message: None) if args.quiet else _print_progress

    try:
        result = ImagesStage(
            script,
            paths,
            fit=args.fit,
            pad_colour=args.pad_colour,
            progress=progress,
        ).run()
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"\n{ERROR_MARK} image preparation failed: {error}", file=sys.stderr)
        return 1

    warnings = [issue for issue in result.issues if issue.severity == "warning"]
    if args.strict and warnings and result.status == "pass":
        # --strict is for release builds: nothing questionable ships.
        result = dataclasses.replace(result, status="fail")

    totals = result.report["totals"]

    print()
    for issue in result.issues:
        mark = ERROR_MARK if issue.severity == "error" else WARN_MARK
        print(f"{mark} {issue}")
    if args.strict and warnings and result.status == "fail":
        print(
            f"{ERROR_MARK} --strict is on and {len(warnings)} warning(s) must "
            "be resolved first"
        )

    print(
        f"\nframes     : {totals['prepared']}/{totals['scenes']} prepared "
        f"at {totals['prepared_size']} "
        f"({totals['prepared_disk_mb']} MB)"
    )
    print(
        f"sharpness  : {totals['sharpness_evaluated']} evaluated, "
        f"avg {totals['sharpness_avg']}, min {totals['sharpness_min']}"
    )
    print(
        f"overlays   : {totals['overlays']} "
        f"({totals['fallback_fonts']} using a fallback font)"
    )
    print(f"report     : {paths.output_dir / 'image_report.json'}")

    if result.status == "pass":
        print(f"\n{OK_MARK} frames ready\n")
    else:
        print(f"\n{ERROR_MARK} frame preparation finished with errors\n")

    store = StateStore(paths.state_path)
    store.record(
        "images",
        result.status,
        artifacts=[
            str(paths.prepared_images_dir),
            str(paths.output_dir / "image_report.json"),
        ],
        detail={
            "prepared": totals["prepared"],
            "prepared_size": totals["prepared_size"],
            "warnings": totals["warnings"],
        },
    )

    return 0 if result.status == "pass" else 1


def cmd_assembly(args: argparse.Namespace) -> int:
    script_path = Path(args.script).resolve()
    workspace = (
        Path(args.workspace).resolve() if args.workspace else script_path.parent
    )
    paths = Paths.from_workspace(workspace, args.out)

    if not script_path.exists():
        print(f"{ERROR_MARK} script not found: {script_path}", file=sys.stderr)
        return 2

    try:
        script = load_script(script_path)
    except ScriptSchemaError as error:
        print(f"{ERROR_MARK} {error}\n", file=sys.stderr)
        return 1

    metadata = script.video_metadata
    if not args.quiet:
        print("\n=== autovid assembly ===")
        print(f"script     : {script_path}")
        print(f"workspace  : {paths.workspace}")
        print(
            f"frame      : {metadata.resolution} @ {metadata.fps}fps "
            f"(crf {args.crf}, preset {args.preset})\n"
        )

    # Stage 1 gates this stage for the same reason it gates tts: rendering
    # against a broken script wastes the slowest step in the pipeline.
    validation = ScriptValidator(script, paths).run(skip_engine_check=True)
    if validation.errors and not args.dry_run:
        for issue in validation.errors:
            print(f"{ERROR_MARK} {issue}", file=sys.stderr)
        print(f"{ERROR_MARK} validation failed; run 'validate'\n", file=sys.stderr)
        return 1

    progress = (lambda message: None) if args.quiet else _print_progress

    try:
        result = AssemblyStage(
            script,
            paths,
            preset=args.preset,
            crf=args.crf,
            force=args.force,
            dry_run=args.dry_run,
            skip_characters=args.no_characters,
            progress=progress,
        ).run()
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"\n{ERROR_MARK} assembly failed: {error}", file=sys.stderr)
        return 1

    warnings = [issue for issue in result.issues if issue.severity == "warning"]
    status = result.status
    if args.strict and warnings and status == "pass":
        status = "fail"

    print()
    for issue in result.issues:
        mark = ERROR_MARK if issue.severity == "error" else WARN_MARK
        print(f"{mark} {issue}")
    if args.strict and warnings and status == "fail":
        print(
            f"{ERROR_MARK} --strict is on and {len(warnings)} warning(s) must "
            "be resolved first"
        )

    totals = result.report.get("totals") or {}
    plan = result.plan
    if plan is not None:
        print(
            f"\nplan       : {len(plan.scenes)} scenes, "
            f"{plan.total_frames} frames, "
            f"{format_duration(plan.total_duration_s)}"
        )
    if totals:
        print(
            f"clips      : {totals.get('clips_rendered', 0)} rendered, "
            f"{totals.get('clips_cached', 0)} reused from cache"
        )
        print(
            f"transitions: {totals.get('transitions', 0)}"
            + (
                f" ({', '.join(totals.get('transition_types') or [])})"
                if totals.get("transition_types")
                else ""
            )
        )
        print(
            f"overlays   : {totals.get('overlays', 0)} "
            f"({totals.get('overlay_spans', 0)} layers, "
            f"{totals.get('dropped_overlays', 0)} dropped)"
        )
        print(
            f"motion     : "
            + (
                ", ".join(
                    f"{kind} x{count}"
                    for kind, count in sorted(
                        (totals.get("motion_types") or {}).items()
                    )
                )
                or "none"
            )
        )
        if totals.get("characters"):
            print(
                f"characters : {totals['characters']} cue(s), "
                f"{totals.get('character_layers', 0)} layer(s), "
                f"{totals.get('character_sfx', 0)} with sound"
            )
            parts = []
            for label, key in (
                ("in", "character_enters"),
                ("out", "character_exits"),
                ("idle", "character_idles"),
            ):
                counts = totals.get(key) or {}
                if counts:
                    parts.append(
                        label
                        + " "
                        + ", ".join(
                            f"{name} x{count}"
                            for name, count in sorted(counts.items())
                        )
                    )
            if parts:
                print("             " + " | ".join(parts))
        if totals.get("punch_ins"):
            print(f"punch-ins  : {totals['punch_ins']}")
        if totals.get("render_seconds"):
            print(
                f"render     : {format_duration(totals['render_seconds'])} "
                f"for {totals.get('frames_rendered', 0)} frames"
            )

    preview = result.report.get("preview")
    if preview:
        print(
            f"preview    : {preview['file']} "
            f"({format_duration(preview['duration_s'])}, "
            f"{preview['size_mb']} MB, silent)"
        )
    elif args.dry_run:
        print(f"\n{WARN_MARK} dry run: nothing was encoded")

    print(f"report     : {paths.output_dir / 'assembly_report.json'}")

    if status == "pass":
        print(f"\n{OK_MARK} video assembled\n")
    else:
        print(f"\n{ERROR_MARK} assembly finished with errors\n")

    if args.dry_run:
        return 0 if status == "pass" else 1

    artifacts = [str(paths.output_dir / "assembly_report.json")]
    if preview:
        artifacts.append(preview["file"])

    StateStore(paths.state_path).record(
        "assembly",
        status,
        artifacts=artifacts,
        detail={
            "scenes": totals.get("scenes", 0),
            # The video's length in frames, not how many were re-encoded on
            # this run: a fully cached re-run still produced the same video.
            "frames": totals.get("frames_total", 0),
            "frames_rendered": totals.get("frames_rendered", 0),
            "duration_s": preview["duration_s"] if preview else None,
            "warnings": len(warnings),
        },
    )

    return 0 if status == "pass" else 1


def cmd_mix(args: argparse.Namespace) -> int:
    script_path = Path(args.script).resolve()
    workspace = (
        Path(args.workspace).resolve() if args.workspace else script_path.parent
    )
    paths = Paths.from_workspace(workspace, args.out)

    if not script_path.exists():
        print(f"{ERROR_MARK} script not found: {script_path}", file=sys.stderr)
        return 2

    try:
        script = load_script(script_path)
    except ScriptSchemaError as error:
        print(f"{ERROR_MARK} {error}\n", file=sys.stderr)
        return 1

    config = script.audio_config
    if not args.quiet:
        print("\n=== autovid mix ===")
        print(f"script     : {script_path}")
        print(f"workspace  : {paths.workspace}")
        print(
            f"music      : {config.background_music or 'none'} "
            f"(at {config.background_volume:.2f}, "
            f"skip_music={args.skip_music})"
        )
        print(f"sfx        : {sum(len(scene.sfx) for scene in script.scenes)}\n")

    validation = ScriptValidator(script, paths).run(skip_engine_check=True)
    if validation.errors:
        for issue in validation.errors:
            print(f"{ERROR_MARK} {issue}", file=sys.stderr)
        print(f"{ERROR_MARK} validation failed; run 'validate'\n", file=sys.stderr)
        return 1

    progress = (lambda message: None) if args.quiet else _print_progress

    try:
        result = MixStage(
            script,
            paths,
            skip_music=args.skip_music,
            progress=progress,
        ).run()
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"\n{ERROR_MARK} mixing failed: {error}", file=sys.stderr)
        return 1

    print()
    for issue in result.issues:
        mark = ERROR_MARK if issue.severity == "error" else WARN_MARK
        print(f"{mark} {issue}")

    totals = result.report.get("totals") or {}
    if result.mix is not None:
        print(
            f"\nstems      : {totals.get('stems', 0)} "
            f"(music {'yes' if totals.get('music_used') else 'no'}, "
            f"sfx {'yes' if totals.get('sfx_used') else 'no'})"
        )
        print(
            f"sfx        : {totals.get('sfx_placed', 0)} placed, "
            f"{totals.get('sfx_dropped', 0)} dropped"
        )
        loudness = totals.get("loudness") or {}
        if loudness:
            print(
                f"loudness   : {loudness.get('integrated_lufs', 0):.1f} LUFS "
                f"(target {totals.get('loudness_target_lufs', 0):.1f}), "
                f"peak {loudness.get('true_peak_db', 0):+.2f} dBFS"
            )
        print(
            f"duration   : {format_duration(totals.get('duration_s', 0))} "
            f"({totals.get('target_source', 'timeline')})"
        )
        print(f"mix        : {result.mix}")
    print(f"report     : {paths.output_dir / 'mix_report.json'}")

    if result.status == "pass":
        print(f"\n{OK_MARK} audio mixed\n")
    else:
        print(f"\n{ERROR_MARK} mix finished with errors\n")

    StateStore(paths.state_path).record(
        "mix",
        result.status,
        artifacts=[
            str(paths.output_dir / "mix_report.json"),
            *([str(result.mix)] if result.mix is not None else []),
        ],
        detail={
            "duration_s": totals.get("duration_s"),
            "stems": totals.get("stems", 0),
            "sfx_placed": totals.get("sfx_placed", 0),
            "warnings": len(result.report.get("warnings") or []),
        },
    )

    return 0 if result.status == "pass" else 1


def cmd_render(args: argparse.Namespace) -> int:
    script_path = Path(args.script).resolve()
    workspace = (
        Path(args.workspace).resolve() if args.workspace else script_path.parent
    )
    paths = Paths.from_workspace(workspace, args.out)

    if not script_path.exists():
        print(f"{ERROR_MARK} script not found: {script_path}", file=sys.stderr)
        return 2

    try:
        script = load_script(script_path)
    except ScriptSchemaError as error:
        print(f"{ERROR_MARK} {error}\n", file=sys.stderr)
        return 1

    if not args.quiet:
        print("\n=== autovid render ===")
        print(f"script     : {script_path}")
        print(f"workspace  : {paths.workspace}")
        print(
            f"video      : {paths.preview_path.name} "
            f"({script.video_metadata.resolution} @ "
            f"{script.video_metadata.fps}fps)"
        )
        if args.voice_only:
            audio_label = paths.voiceover_path.name + " (voice only)"
        elif paths.mix_path.exists():
            audio_label = paths.mix_path.name
        else:
            # The stage falls back to the narration when the mix is missing;
            # saying which one it would actually use beats implying the other.
            audio_label = (
                f"{paths.voiceover_path.name} (no {paths.mix_path.name} yet)"
            )
        print(f"audio      : {audio_label} -> aac {args.audio_bitrate}\n")

    validation = ScriptValidator(script, paths).run(skip_engine_check=True)
    if validation.errors:
        for issue in validation.errors:
            print(f"{ERROR_MARK} {issue}", file=sys.stderr)
        print(f"{ERROR_MARK} validation failed; run 'validate'\n", file=sys.stderr)
        return 1

    progress = (lambda message: None) if args.quiet else _print_progress

    try:
        result = RenderStage(
            script,
            paths,
            audio_bitrate=args.audio_bitrate,
            use_voiceover=args.voice_only,
            dry_run=args.dry_run,
            progress=progress,
        ).run()
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"\n{ERROR_MARK} render failed: {error}", file=sys.stderr)
        return 1

    print()
    for issue in result.issues:
        mark = ERROR_MARK if issue.severity == "error" else WARN_MARK
        print(f"{mark} {issue}")

    deliverable = result.report.get("deliverable")
    if deliverable:
        print(
            f"\ndeliverable: {deliverable['file']} "
            f"({format_duration(deliverable['duration_s'])}, "
            f"{deliverable['resolution']}, {deliverable['size_mb']} MB, "
            f"audio {'yes' if deliverable['has_audio'] else 'NO'})"
        )

    quality = result.quality or {}
    if quality:
        totals = quality.get("totals") or {}
        print(
            f"quality    : {quality.get('status')} "
            f"({totals.get('errors', 0)} error(s), "
            f"{totals.get('warnings', 0)} warning(s) across "
            f"{totals.get('stages', 0)} stage(s))"
        )

    if args.dry_run:
        # Nothing was encoded, so there is no report and no deliverable; the
        # run is a plan, and saying "rendered" would be a lie.
        if result.status == "pass":
            print(f"\n{OK_MARK} dry run passed: nothing was encoded\n")
        else:
            print(f"\n{ERROR_MARK} dry run found errors\n")
        return 0 if result.status == "pass" else 1

    print(f"report     : {paths.output_dir / 'render_report.json'}")

    if result.status == "pass":
        print(f"\n{OK_MARK} final video rendered\n")
    else:
        print(f"\n{ERROR_MARK} render finished with errors\n")

    artifacts = [str(paths.output_dir / "render_report.json")]
    if result.output is not None:
        artifacts.append(str(result.output))
    if paths.quality_report_path.exists():
        artifacts.append(str(paths.quality_report_path))

    totals = result.report.get("totals") or {}
    StateStore(paths.state_path).record(
        "render",
        result.status,
        artifacts=artifacts,
        detail={
            "duration_s": totals.get("duration_s"),
            "has_audio": totals.get("has_audio", False),
            "quality_status": quality.get("status"),
            "warnings": len(result.report.get("warnings") or []),
        },
    )
    _refresh_quality(script, paths)

    return 0 if result.status == "pass" else 1


def cmd_captions(args: argparse.Namespace) -> int:
    script_path = Path(args.script).resolve()
    workspace = (
        Path(args.workspace).resolve() if args.workspace else script_path.parent
    )
    paths = Paths.from_workspace(workspace, args.out)

    if not script_path.exists():
        print(f"{ERROR_MARK} script not found: {script_path}", file=sys.stderr)
        return 2

    try:
        script = load_script(script_path)
    except ScriptSchemaError as error:
        print(f"{ERROR_MARK} {error}\n", file=sys.stderr)
        return 1

    if not args.quiet:
        print("\n=== autovid captions ===")
        print(f"script     : {script_path}")
        print(f"workspace  : {paths.workspace}")
        print(
            f"source     : {'faster-whisper (' + args.asr_model + ')' if args.asr else 'measured timings'}"
        )
        print(f"mode       : {'burned in' if args.burn else 'soft subtitles'}\n")

    # No asset gate here, on purpose: captions work on the finished video,
    # so a missing SFX or font must not stop them from being produced.
    progress = (lambda message: None) if args.quiet else _print_progress

    try:
        result = CaptionsStage(
            script,
            paths,
            asr=args.asr,
            burn=args.burn,
            max_chars_per_line=args.max_chars,
            asr_model=args.asr_model,
            progress=progress,
        ).run()
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"\n{ERROR_MARK} captions failed: {error}", file=sys.stderr)
        return 1

    print()
    for issue in result.issues:
        mark = ERROR_MARK if issue.severity == "error" else WARN_MARK
        print(f"{mark} {issue}")

    totals = result.report.get("totals") or {}
    if result.cues:
        print(
            f"\ncues       : {totals.get('cues', 0)} "
            f"covering {totals.get('coverage_pct', 0)}% of the video "
            f"(longest {format_duration(totals.get('cue_longest_s', 0))})"
        )
        print(f"subtitles  : {result.subtitles}")
        print(f"video      : {result.video}")
    print(f"report     : {paths.output_dir / 'captions_report.json'}")

    if result.status == "pass":
        print(f"\n{OK_MARK} captions attached\n")
    else:
        print(f"\n{ERROR_MARK} captions finished with errors\n")

    if result.status != "pass":
        StateStore(paths.state_path).record(
            "captions",
            "fail",
            artifacts=[str(paths.output_dir / "captions_report.json")],
            detail={"errors": len(result.issues)},
        )
        _refresh_quality(script, paths)
        return 1

    StateStore(paths.state_path).record(
        "captions",
        "pass",
        artifacts=[
            str(paths.output_dir / "captions_report.json"),
            str(result.subtitles),
            str(result.video),
        ],
        detail={
            "cues": totals.get("cues", 0),
            "mode": "burn" if args.burn else "soft",
            "source": "asr" if args.asr else "timeline",
        },
    )
    _refresh_quality(script, paths)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 2

    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "tts":
        return cmd_tts(args)
    if args.command == "images":
        return cmd_images(args)
    if args.command == "assembly":
        return cmd_assembly(args)
    if args.command == "mix":
        return cmd_mix(args)
    if args.command == "render":
        return cmd_render(args)
    if args.command == "captions":
        return cmd_captions(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
