"""Structured input diagnostics shared by project preparation and API adapters."""

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class ProjectIssue:
    file: str
    code: str
    message: str
    row: int | None = None
    field: str | None = None
    line: int | None = None
    column: int | None = None
    severity: Literal["error", "warning"] = "error"
    details: dict | None = None


@dataclass(frozen=True)
class ProjectReport:
    """Stage feedback; only error-severity issues make the stage invalid."""
    stage: str
    issues: tuple[ProjectIssue, ...] = ()

    def to_dict(self):
        return {
            "valid": not any(issue.severity == "error" for issue in self.issues),
            "stage": self.stage,
            "issues": [asdict(issue) for issue in self.issues],
        }


class ProjectValidationError(ValueError):
    """Expected input failure with issues ready for JSON serialization."""

    def __init__(self, issues, *, stage="scenario_build"):
        self.stage = stage
        self.issues = tuple(issues)
        if not any(issue.severity == "error" for issue in self.issues):
            raise ValueError("ProjectValidationError requires an error; use ProjectReport for warnings")
        super().__init__("\n".join(
            f"{issue.file}{f', row {issue.row}' if issue.row is not None else ''}: {issue.message}"
            for issue in self.issues
        ))

    def to_dict(self):
        return ProjectReport(self.stage, self.issues).to_dict()


def input_issue(file, error, *, row=None):
    """Translate parser and input failures without interpreting message text."""
    code = "invalid_input"
    if isinstance(error, FileNotFoundError):
        code = "missing_file"
    elif isinstance(error, UnicodeError):
        code = "invalid_encoding"
    elif hasattr(error, "line") and hasattr(error, "col"):
        code = "invalid_toml"
    return ProjectIssue(
        file=file,
        code=getattr(error, "code", code),
        message=getattr(error, "detail", error.strerror if isinstance(error, OSError) and error.strerror else str(error)),
        row=row if row is not None else getattr(error, "row", None),
        field=getattr(error, "field", None),
        line=getattr(error, "line", None),
        column=getattr(error, "col", None),
    )
