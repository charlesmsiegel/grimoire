"""Trusted director-only causal review. No provider, effect execution or repair."""
from dataclasses import dataclass, replace

from grimoire.continuity import Dependency, ImpactReport, Pin, Rehearsal, RetconRequest, preview_impact
from grimoire.core.authority import AuthorityError
from grimoire.core.identity import ContinuityRef, validate_segment
from grimoire.graph.index import require_director
from grimoire.store import ScopeRecord

from .snapshot import DEPENDENCY_SCHEMA, native_snapshot


@dataclass(frozen=True)
class Correction:
    source: Pin
    dependent: Pin
    reason: str
    certainty: str
    chain: tuple[Pin, ...]


@dataclass(frozen=True)
class RehearsalPreview:
    rehearsal: Rehearsal
    report: ImpactReport


class ContinuityLibrary:
    def __init__(self, store, handle, *, issued=None, owners=None):
        # Unbound stores have no intrinsic issuer. The trusted application must supply
        # its verified issuer and owner set; a handle cannot vouch for itself.
        self.store, self.handle = store, handle
        self._issued = issued
        self._owners = frozenset(owners or ())
        self._require_owner()

    def _require_owner(self):
        authority = self.store.authority
        issued = getattr(authority, 'issued', None) if authority is not None else self._issued
        owners = getattr(authority, 'owners', ()) if authority is not None else self._owners
        if (not callable(issued) or not issued(self.handle)
                or getattr(self.handle, 'trusted', False) is not True
                or getattr(self.handle, 'principal', None) not in owners):
            raise AuthorityError('continuity review requires this library owner and verified issuer')

    def snapshot(self, campaign, *, requested_by='director'):
        require_director(requested_by)
        self._require_owner()
        validate_segment(campaign, 'campaign_id')
        with self.store.lock:
            if self.store.campaign_presence(campaign) != 'present':
                raise ValueError('missing campaign')
            return native_snapshot(self.store, campaign)

    def preview(self, campaign, roots, action, *, requested_by='director'):
        require_director(requested_by)
        request = RetconRequest(action, tuple(roots))
        if request.roots[0].campaign_id != campaign: raise ValueError('cross-campaign roots')
        snap = self.snapshot(campaign, requested_by=requested_by)
        report = preview_impact(snap.pins.values(), snap.dependencies, request.roots)
        return replace(report, problems=tuple(sorted(set(report.problems + snap.problems))))

    def rehearse(self, campaign, roots, action, alternatives, *, requested_by='director'):
        require_director(requested_by)
        request = RetconRequest(action, tuple(roots))
        rehearsal = Rehearsal(request, tuple(alternatives))
        return RehearsalPreview(rehearsal, self.preview(campaign, request.roots, action, requested_by=requested_by))

    def corrections(self, campaign, roots, action='edit', *, requested_by='director'):
        """053 input references only. Incomplete reports refuse correction export."""
        report = self.preview(campaign, roots, action, requested_by=requested_by)
        if not report.complete: raise ValueError('incomplete continuity report: ' + '; '.join(report.problems))
        return tuple(Correction(item.chain[-2], item.target, item.reason, item.certainty, item.chain)
                     for item in report.impacts)

    def declare_dependency(self, record_id, source, dependent, reason, *, requested_by='director'):
        require_director(requested_by)
        edge = Dependency(source, dependent, reason, 'known')
        if source.campaign_id != dependent.campaign_id: raise ValueError('cross-campaign declaration')
        with self.store.lock:
            snap = self.snapshot(source.campaign_id, requested_by=requested_by)
            if snap.problems: raise ValueError('incomplete native snapshot: ' + '; '.join(snap.problems))
            if snap.pins.get(source.path) != source or snap.pins.get(dependent.path) != dependent:
                raise ValueError('missing or stale declaration endpoint')
            trial = preview_impact(snap.pins.values(), snap.dependencies + (edge,), (source,))
            if not trial.complete: raise ValueError('; '.join(trial.problems))
            ref = ContinuityRef(source.campaign_id, record_id)
            payload = {'source': {'path': source.path, 'revision': source.revision},
                       'dependent': {'path': dependent.path, 'revision': dependent.revision},
                       'reason': reason, 'certainty': 'known'}
            txn = self.store.transaction()
            txn.put(ScopeRecord(ref, DEPENDENCY_SCHEMA, payload, owner=self.handle.principal), None)
            txn.commit(self.handle)
            return ref
