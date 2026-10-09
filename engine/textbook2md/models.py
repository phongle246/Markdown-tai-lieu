from __future__ import annotations

from dataclasses import asdict, dataclass, field

SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# chapter statuses (LXIX)
NOT_STARTED, PROCESSING, COMPLETED = "NOT_STARTED", "PROCESSING", "COMPLETED"
COMPLETED_WITH_WARNINGS, REVIEW_REQUIRED, FAILED = "COMPLETED_WITH_WARNINGS", "REVIEW_REQUIRED", "FAILED"


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    page: int | None = None
    block_id: str | None = None
    category: str = "structure"

    def to_dict(self):
        return asdict(self)


def status_from_issues(issues: list[Issue], failed: bool = False) -> str:
    if failed:
        return FAILED
    sevs = {i.severity for i in issues}
    if sevs & {"CRITICAL", "HIGH"}:
        return REVIEW_REQUIRED
    if sevs & {"MEDIUM", "LOW"}:
        return COMPLETED_WITH_WARNINGS
    return COMPLETED


def report_status_label(status: str) -> str:
    return {COMPLETED: "PASS", COMPLETED_WITH_WARNINGS: "PASS WITH WARNINGS",
            REVIEW_REQUIRED: "REVIEW REQUIRED", FAILED: "FAILED"}.get(status, status)
