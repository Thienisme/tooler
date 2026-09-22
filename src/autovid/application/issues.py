"""
Shared issue reporting for pipeline stages.

Every stage collects its own errors and warnings but reports them the same
way, so the aggregated `quality_report.json` can merge them without
special cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Issue:
    """One error or warning raised by a stage."""

    code: str
    message: str
    severity: str = "error"
    scene_id: int | None = None
    unit_index: int | None = None

    def to_dict(self) -> dict:
        payload = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.scene_id is not None:
            payload["scene_id"] = self.scene_id
        if self.unit_index is not None:
            payload["unit_index"] = self.unit_index
        return payload

    def __str__(self) -> str:
        location = f" (scene {self.scene_id})" if self.scene_id is not None else ""
        return f"[{self.code}]{location} {self.message}"


@dataclass
class IssueCollector:
    """Accumulates issues and answers the pass/fail question."""

    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    def error(
        self,
        code: str,
        message: str,
        scene_id: int | None = None,
        unit_index: int | None = None,
    ) -> None:
        self.issues.append(
            Issue(code, message, "error", scene_id, unit_index)
        )

    def warn(
        self,
        code: str,
        message: str,
        scene_id: int | None = None,
        unit_index: int | None = None,
    ) -> None:
        self.issues.append(
            Issue(code, message, "warning", scene_id, unit_index)
        )

    def extend(self, issues: list[Issue]) -> None:
        self.issues.extend(issues)

    @property
    def status(self) -> str:
        return "fail" if self.errors else "pass"

    def codes(self) -> set[str]:
        return {issue.code for issue in self.issues}
