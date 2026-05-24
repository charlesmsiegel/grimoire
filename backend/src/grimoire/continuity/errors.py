"""Continuity domain exceptions."""


class ContinuityError(Exception):
    """Base for continuity exceptions."""

    http_status = 500


class FactNotFoundError(ContinuityError, KeyError):
    http_status = 404


class CommitmentNotFoundError(ContinuityError, KeyError):
    http_status = 404


class ContradictionReportNotFoundError(ContinuityError, KeyError):
    http_status = 404


class ConfidenceFloorError(ContinuityError, ValueError):
    """Raised when a fact is rejected because its confidence is too low."""

    http_status = 400
