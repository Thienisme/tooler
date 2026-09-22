"""
Aggregated quality report for a finished episode.

Every stage writes its own report next to its own artifacts, which is what a
stage needs to be debuggable on its own.  Nobody wants to read seven files to
answer "is this episode shippable?", so this module merges them into
`output/quality_report.json`: what ran, what failed, and every warning that
survived all the way to the deliverable.

It is derived data.  If a report is missing the stage simply did not run, and
that is stated rather than treated as an error — only `validate`, `tts`,
`images`, `assembly`, `mix` and `render` are on the critical path, and
captions are optional by design.
"""

from __future__ import annotations

from autovid.domain.script import Script
from autovid.paths import Paths, read_json, write_json

QUALITY_VERSION = 1

# Stage name -> its report file.  Order is pipeline order, so the merged
# issue list reads in the order the problems were produced.
STAGE_REPORTS: tuple[tuple[str, str], ...] = (
    ("validate", "validation_report.json"),
    ("tts", "tts_report.json"),
    ("images", "image_report.json"),
    ("assembly", "assembly_report.json"),
    ("mix", "mix_report.json"),
    ("render", "render_report.json"),
    ("captions", "captions_report.json"),
)

# Stages without which there is no video.  Captions are not on this list.
REQUIRED_STAGES = ("validate", "tts", "images", "assembly", "mix", "render")


def build_quality_report(script: Script, paths: Paths) -> dict:
    """Merge every stage report into one shippable-or-not summary."""
    stages: list[dict] = []
    issues: list[dict] = []
    reports: dict[str, dict] = {}
    missing: list[str] = []

    for stage, filename in STAGE_REPORTS:
        payload = read_json(paths.output_dir / filename)
        if not isinstance(payload, dict):
            missing.append(stage)
            continue

        reports[stage] = payload
        errors = payload.get("errors") or []
        warnings = payload.get("warnings") or []

        stages.append(
            {
                "stage": stage,
                "status": payload.get("status", "unknown"),
                "errors": len(errors),
                "warnings": len(warnings),
            }
        )
        # `strict` builds can flip a stage to fail without adding an issue,
        # so the failure itself is listed as well as its causes.
        if payload.get("status") not in (None, "pass") and not errors:
            issues.append(
                {
                    "stage": stage,
                    "code": f"{stage}_failed",
                    "severity": "error",
                    "message": f"stage '{stage}' reported status "
                    f"'{payload.get('status')}'",
                }
            )
        for issue in errors:
            issues.append({"stage": stage, **issue})
        for issue in warnings:
            issues.append({"stage": stage, **issue})

    errors_total = sum(1 for issue in issues if issue["severity"] == "error")
    warnings_total = len(issues) - errors_total
    stages_failed = [entry["stage"] for entry in stages if entry["status"] != "pass"]
    stages_not_run = [entry for entry in REQUIRED_STAGES if entry in missing]

    if errors_total or stages_failed or stages_not_run:
        status = "fail"
    else:
        status = "pass"

    return {
        "version": QUALITY_VERSION,
        "stage": "quality",
        "status": status,
        "title": script.video_metadata.title,
        "deliverable": _deliverable(reports.get("render")),
        "audio": _audio_summary(reports.get("mix")),
        "animation": _animation_summary(reports.get("assembly")),
        "stages": stages,
        "stages_not_run": stages_not_run,
        "totals": {
            "stages": len(stages),
            "stages_failed": len(stages_failed),
            "required_stages_not_run": len(stages_not_run),
            "errors": errors_total,
            "warnings": warnings_total,
            "scenes": script.scene_count,
        },
        "issues": issues,
        "state": _state_statuses(paths),
    }


def write_quality_report(script: Script, paths: Paths) -> dict:
    """Build the merged report and write it to `output/quality_report.json`."""
    report = build_quality_report(script, paths)
    write_json(paths.quality_report_path, report)
    return report


def _deliverable(render_report: dict | None) -> dict | None:
    if not isinstance(render_report, dict):
        return None
    deliverable = render_report.get("deliverable")
    return deliverable if isinstance(deliverable, dict) else None


def _animation_summary(assembly_report: dict | None) -> dict | None:
    """
    What the video does besides showing stills.

    Kept in the shipped summary because "the video has 40 character cues and
    which entrances they use" is the difference between the episode the
    script asked for and the episode that rendered.
    """
    if not isinstance(assembly_report, dict):
        return None
    totals = assembly_report.get("totals") or {}
    if not any(
        totals.get(key)
        for key in ("characters", "punch_ins", "transitions")
    ):
        return None
    return {
        "characters": totals.get("characters", 0),
        "character_layers": totals.get("character_layers", 0),
        "enters": totals.get("character_enters", {}),
        "exits": totals.get("character_exits", {}),
        "idles": totals.get("character_idles", {}),
        "character_sfx": totals.get("character_sfx", 0),
        "punch_ins": totals.get("punch_ins", 0),
        "transitions": totals.get("transitions", 0),
        "transition_types": totals.get("transition_types", []),
    }


def _audio_summary(mix_report: dict | None) -> dict | None:
    if not isinstance(mix_report, dict):
        return None
    totals = mix_report.get("totals") or {}
    return {
        "file": mix_report.get("output"),
        "duration_s": totals.get("duration_s"),
        "loudness": totals.get("loudness"),
        "target_lufs": totals.get("loudness_target_lufs"),
        "stems": totals.get("stems"),
        "sfx_placed": totals.get("sfx_placed"),
        "sfx_dropped": totals.get("sfx_dropped"),
    }


def _state_statuses(paths: Paths) -> dict:
    payload = read_json(paths.state_path)
    if not isinstance(payload, dict):
        return {}
    stages = payload.get("stages") or {}
    return {
        name: entry.get("status")
        for name, entry in stages.items()
        if isinstance(entry, dict)
    }
