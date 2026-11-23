"""082's read-only world audit on the Authoring page: ``authoring.audit`` and ``authoring.audit_review``.

``audit`` runs 082's deterministic ``ConsistencyAuditor`` over one world source through ``InvestigationService``, as
the library owner, within a budget the page chose inside this app's bounds (``BOUNDS``). 082 grants the job ``inspect``
alone over the source's own files, to a principal of its own, and revokes it however the job ends; nothing is written
to any source or campaign. (The library's security audit records that grant and every read, as 082 says.) The report
is an in-memory value in 082, so the app keeps it **in its memory**, under the job's id, and the page names it by
that id: a report is never sent back from the page, so a forged finding or citation cannot be reviewed as the
auditor's. A restart forgets every report. ``audit_review`` is 082's ``review``: it reads the head of every cited
record now and marks a finding stale when its evidence changed. It reads only.

Not built in 082 (named on the page): a model worker, repairs through a reviewed change set (milestone 2) and
resumable or queued jobs (milestone 3).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.core.identity import SourceRef
from grimoire.investigations import (
    Budget,
    InvestigationError,
    InvestigationReport,
    InvestigationSpec,
)
from grimoire.investigationstore import InvestigationService
from grimoire.textapi import ApiRefused

from .common import MAX_AUDITS, memory, refused, segment, source_of, whole, world_only

#: The budget a page may give one audit: (low, high, default) for each of 082's limits. 082 itself bounds every
#: job; these keep one run from the page short while the library waits on it.
BOUNDS = {"max_steps": (1, 2000, 400), "max_reads": (0, 1000, 200), "max_findings": (0, 200, 50),
          "max_seconds": (1, 60, 20)}
GOAL = "Check this world's records for dangling references, possible duplicates and missing links."
WRITTEN = ("Nothing was written: the audit only read this world's records, under a read-only grant that ended with "
           "it. The library's security log notes that grant and each read.")


def _citation(source: str, citation, read) -> dict:  # type: ignore[no-untyped-def]
    return {"kind": citation.kind, "entity_id": citation.entity_id, "revision": citation.revision,
            "etag": citation.etag, "field": citation.field, "quote": citation.quote,
            "ref": str(SourceRef(source, citation.kind, citation.entity_id, citation.revision)),
            "name": None if read is None else read.name}


def audit_view(report: InvestigationReport) -> dict:
    spec = report.spec
    source = spec.source
    budget = spec.budget
    return {
        "audit": spec.job_id, "source": source, "status": report.status, "reason": report.reason,
        "note": report.note, "written": False, "said": WRITTEN,
        "budget": {"max_steps": budget.max_steps, "max_reads": budget.max_reads, "max_findings": budget.max_findings,
                   "max_seconds": budget.max_seconds},
        "steps": len(report.steps), "reads": len(report.reads),
        "findings": [{"finding": item.finding_id, "category": item.finding.category, "summary": item.finding.summary,
                      "citations": [_citation(source, citation, report.reads.get(citation.record))
                                    for citation in item.finding.citations]} for item in report.findings],
        "rejected": [{"step": item.step, "reason": item.reason, "detail": item.detail,
                      "category": None if item.finding is None else item.finding.category,
                      "summary": None if item.finding is None else item.finding.summary} for item in report.rejected],
        "read": [{"kind": read.kind, "entity_id": read.entity_id, "revision": read.revision, "name": read.name,
                  "ref": str(SourceRef(source, read.kind, read.entity_id, read.revision))}
                 for _key, read in sorted(report.reads.items())],
        "unread": [{"kind": kind, "entity_id": entity_id, "ref": str(SourceRef(source, kind, entity_id))}
                   for kind, entity_id in report.unread],
    }


def _service(ctx: AdapterContext) -> InvestigationService:
    try:
        return InvestigationService(ctx.store, ctx.authority, owner=ctx.owner)
    except ValueError as error:
        raise ApiRefused("not_owner", str(error)) from None


def audit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Run one bounded, read-only audit of a world and keep its report in memory."""
    source = source_of(args)
    world_only(source, "A consistency audit")
    limits = {name: whole(args, name, low, high, default) for name, (low, high, default) in BOUNDS.items()}
    job_id = "audit-" + uuid.uuid4().hex[:16]
    try:
        spec = InvestigationSpec(job_id, source, GOAL, Budget(limits["max_steps"], limits["max_reads"],
                                                              limits["max_findings"], float(limits["max_seconds"])))
        report = _service(ctx).run(spec, clock=time.monotonic)
    except InvestigationError as error:
        raise refused(error, "investigation_refused") from None
    held = memory(ctx)
    held.keep(held.audits, job_id, report, MAX_AUDITS)
    return {"audit": audit_view(report)}


def report_of(ctx: AdapterContext, args: Mapping[str, object]) -> InvestigationReport:
    source = source_of(args)
    job = segment(args, "audit")
    report = memory(ctx).audits.get(job)  # type: ignore[arg-type]
    if type(report) is not InvestigationReport or report.spec.source != source:
        raise ApiRefused("unknown_audit", "no such audit in this app's memory: it was never run here, or the app "
                                          "restarted or forgot it since. Run the audit again")
    return report


def audit_review(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """082's review of a kept report: each finding current, or stale with what changed in its evidence. Reads only."""
    report = report_of(ctx, args)
    reviewed = _service(ctx).review(report)
    return {"audit": audit_view(report),
            "review": {"stale": list(reviewed.stale),
                       "findings": [{"finding": item.finding.finding_id, "current": item.current,
                                     "changes": [{"kind": change.citation.kind, "entity_id": change.citation.entity_id,
                                                  "field": change.citation.field, "reason": change.reason,
                                                  "revision": change.revision} for change in item.changes]}
                                    for item in reviewed.findings]}}


def audits_of(ctx: AdapterContext, source: str) -> list[dict]:
    """The audits this app holds for ``source``, newest first, in brief."""
    return [{"audit": report.spec.job_id, "status": report.status, "findings": len(report.findings)}  # type: ignore[attr-defined]
            for report in reversed(list(memory(ctx).audits.values())) if report.spec.source == source]  # type: ignore[attr-defined]


__all__ = ["BOUNDS", "audit", "audit_review", "audit_view", "audits_of"]
