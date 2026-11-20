"""Bounded, read-only investigation jobs (082): pure values, evidence checks, the job loop and the built-in auditor.

The store-backed toolbox and the service that grants a job its read-only authority are the adapter
``grimoire.investigationstore``. See docs/INVESTIGATIONS.md.
"""

from .auditor import ConsistencyAuditor, name_key
from .evidence import check_citation, check_finding, resolve
from .job import Cancel, Tools, Worker, run_job
from .model import (
    ACTIONS,
    TOOLS,
    AcceptedFinding,
    Action,
    Budget,
    Citation,
    Finding,
    Finish,
    InvestigationError,
    InvestigationReport,
    InvestigationSpec,
    Listed,
    ListRecords,
    Observation,
    ReadRecord,
    RecordRead,
    RejectedFinding,
    Remaining,
    Report,
    Step,
    ToolResult,
)
from .review import (
    AuditReview,
    EvidenceChange,
    ReviewedFinding,
    cited_records,
    review_findings,
)

__all__ = [
    "ACTIONS", "TOOLS", "AcceptedFinding", "Action", "AuditReview", "Budget", "Cancel", "Citation",
    "ConsistencyAuditor", "EvidenceChange", "Finding", "Finish", "InvestigationError", "InvestigationReport",
    "InvestigationSpec", "ListRecords", "Listed", "Observation", "ReadRecord", "RecordRead", "RejectedFinding",
    "Remaining", "Report", "ReviewedFinding", "Step", "ToolResult", "Tools", "Worker", "check_citation",
    "check_finding", "cited_records", "name_key", "resolve", "review_findings", "run_job",
]
