"""Run and review bounded read-only investigations of one library's sources (082).

``InvestigationService`` is trusted application code acting for the library's owner and is never handed to
a worker. For each run it:

1. issues one grant, from the owner, of ``inspect`` only over the job's source (``sources/<id>/_source``) to
   a principal of the job's own (``investigator:<job_id>``);
2. mints an **untrusted** handle for that principal, which only the toolbox holds; the worker sees only
   observations, never the handle, the toolbox or the store;
3. runs the job (``grimoire.investigations.run_job``), and
4. revokes the grant (unless the owner already did), however the job ended, so no handle of the job can read anything afterwards.

Both the grant's lifecycle and every read decision are in the library's ``security`` audit when the
authority is the durable one. Nothing else is written: a cancelled, failed or exhausted job leaves every
source and campaign file exactly as it was. ``review`` re-reads the cited entities' heads and marks a finding
stale when its evidence changed since the run.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from grimoire.changesets import ChangeSetError
from grimoire.changesetstore import ChangeSetLibrary
from grimoire.investigations import (
    AuditReview,
    ConsistencyAuditor,
    InvestigationError,
    InvestigationReport,
    InvestigationSpec,
    Worker,
    cited_records,
    review_findings,
    run_job,
)
from grimoire.investigations.job import Cancel
from grimoire.investigations.review import Current
from grimoire.security import Grant, Permission, ResourcePath, is_human
from grimoire.store import LibraryStore, StoreError

from .toolbox import WorldToolbox, source_address


def _new_id() -> str:
    return f"investigation-{uuid.uuid4().hex}"


class InvestigationService:
    """Run investigations of ``store``'s sources on ``authority`` (a ``DurableAuthority`` or kernel
    ``Authorizer``) for ``owner``, the human who grants each job its read-only scope."""

    def __init__(self, store: LibraryStore, authority: object, *, owner: str,
                 new_id: Callable[[], str] = _new_id) -> None:
        if type(owner) is not str or not is_human(owner):
            raise ValueError("an investigation's scope is granted by a human owner")
        if not callable(new_id):
            raise TypeError("new_id must be callable")
        self.store = store
        self.authority = authority
        self.owner = owner
        self._new_id = new_id

    @staticmethod
    def principal(spec: InvestigationSpec) -> str:
        return f"investigator:{spec.job_id}"

    def run(self, spec: InvestigationSpec, worker: Worker | None = None, *, cancel: Cancel | None = None,
            clock: Callable[[], float] = time.monotonic) -> InvestigationReport:
        """Run one job; ``worker`` defaults to the built-in ``ConsistencyAuditor`` of the spec's source.

        Refuses (``InvestigationError``) a source the library does not admit (``missing_source``,
        ``excluded_source``) before granting anything."""
        if type(spec) is not InvestigationSpec:
            raise TypeError("run takes an InvestigationSpec")
        found = self.store.check_source(spec.source)
        if isinstance(found, str):
            raise InvestigationError(found, spec.source)
        principal = self.principal(spec)
        grant = Grant(self._new_id(), principal, frozenset({Permission.INSPECT}),
                      ResourcePath.parse(source_address(spec.source)), self.owner)
        self.authority.add_grant(grant)  # type: ignore[attr-defined]
        try:
            handle = self.authority.handle_for(principal, trusted=False)  # type: ignore[attr-defined]
            tools = WorldToolbox(self.store, handle, spec.source)
            return run_job(spec, worker if worker is not None else ConsistencyAuditor(spec.source), tools,
                           clock=clock, cancel=cancel)
        finally:
            # The owner may already have revoked it mid-run; that read was refused, and the report still stands.
            if any(active.grant_id == grant.grant_id
                   for active in self.authority.active_grants()):  # type: ignore[attr-defined]
                self.authority.revoke_grant(grant.grant_id)  # type: ignore[attr-defined]

    def review(self, report: InvestigationReport) -> AuditReview:
        """Each finding marked current, or stale with what changed in its evidence. Reads only."""
        if type(report) is not InvestigationReport:
            raise TypeError("review takes an InvestigationReport")
        heads = ChangeSetLibrary(self.store, None)
        current: dict[tuple[str, str], Current] = {}
        with self.store.lock:
            for kind, entity_id in cited_records(report):
                try:
                    revision, _envelope, etag = heads.head(report.spec.source, kind, entity_id)
                except ChangeSetError as error:
                    current[(kind, entity_id)] = error.reason
                except (StoreError, OSError, ValueError):
                    current[(kind, entity_id)] = "unreadable"
                else:
                    current[(kind, entity_id)] = (revision, etag)
        return review_findings(report, current)


__all__ = ["InvestigationService"]
