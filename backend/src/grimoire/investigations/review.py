"""Reviewing a finished investigation: is each finding's evidence still what the job read? (082)

A job explores without holding the library, and a person may read its report hours later. Evidence is
stale when a cited entity's head has moved to another revision, its head file was edited (another etag), or
it is gone. A stale finding is not wrong, but it was found in records that are no longer current, so it is
shown as stale and must not be acted on without a fresh run. Detection is by revision and etag only, never
by comparing names or text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .model import AcceptedFinding, Citation, InvestigationReport

#: Per cited entity: its current ``(revision, etag)``, or a reason it has none (``missing``, ``no_head``,
#: ``unreadable``, ``missing_source``, ``excluded_source``).
Current = tuple[str, str] | str


@dataclass(frozen=True, slots=True)
class EvidenceChange:
    """One citation whose record is no longer as read: ``head_moved``, ``edited`` or the reason it is gone."""

    citation: Citation
    reason: str
    revision: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewedFinding:
    finding: AcceptedFinding
    changes: tuple[EvidenceChange, ...]

    @property
    def current(self) -> bool:
        return not self.changes


@dataclass(frozen=True, slots=True)
class AuditReview:
    """The report with each finding marked current or stale. It writes nothing and decides nothing."""

    report: InvestigationReport
    findings: tuple[ReviewedFinding, ...]

    @property
    def stale(self) -> tuple[str, ...]:
        return tuple(item.finding.finding_id for item in self.findings if not item.current)

    def describe(self) -> tuple[str, ...]:
        lines = list(self.report.describe())
        for item in self.findings:
            for change in item.changes:
                lines.append(f"{item.finding.finding_id} is stale: {change.citation.kind}/"
                             f"{change.citation.entity_id} {change.reason}")
        return tuple(lines)


def review_findings(report: InvestigationReport, current: Mapping[tuple[str, str], Current]) -> AuditReview:
    """Mark each finding of ``report`` current or stale against ``current`` (every cited entity's head now)."""
    reviewed = []
    for item in report.findings:
        changes = []
        for citation in item.finding.citations:
            now = current.get(citation.record, "missing")
            if isinstance(now, str):
                changes.append(EvidenceChange(citation, now))
            elif now[0] != citation.revision:
                changes.append(EvidenceChange(citation, "head_moved", now[0]))
            elif now[1] != citation.etag:
                changes.append(EvidenceChange(citation, "edited", now[0]))
        reviewed.append(ReviewedFinding(item, tuple(changes)))
    return AuditReview(report, tuple(reviewed))


def cited_records(report: InvestigationReport) -> tuple[tuple[str, str], ...]:
    return tuple(sorted({citation.record for item in report.findings for citation in item.finding.citations}))


__all__ = ["AuditReview", "Current", "EvidenceChange", "ReviewedFinding", "cited_records", "review_findings"]
