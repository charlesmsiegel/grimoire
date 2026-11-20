"""Explicit, director-only preparation with no canonical writer or inference client."""
from collections.abc import Callable
from threading import Lock

from grimoire.core.identity import validate_segment
from .model import Draft, PreparedScene, Proposal, Review, Snapshot, text


class PreparationSession:
    """Trusted adapter seam. Reader must supply current director-authorized evidence.

    Proposer is a local deterministic/scripted function. Network/model integration
    requires a separately authorized metered adapter, outside this first slice.
    Callbacks must not reenter this session. All state is disposable and in memory.
    """
    def __init__(self, campaign_id: str, reader: Callable[[], Snapshot],
                 proposer: Callable[[Snapshot, str], Draft]):
        validate_segment(campaign_id, 'campaign_id')
        self._campaign = campaign_id
        self._reader = reader
        self._proposer = proposer
        self._lock = Lock()
        self._proposals: dict[str, Proposal] = {}
        self._rejected: set[str] = set()
        self._accepted: dict[str, PreparedScene] = {}

    def _current(self) -> Snapshot:
        snapshot = self._reader()
        if type(snapshot) is not Snapshot or snapshot.campaign_id != self._campaign:
            raise ValueError('wrong_campaign')
        return snapshot

    def prepare(self, request_id: str, brief: str) -> Proposal:
        validate_segment(request_id, 'request_id')
        text(brief)
        with self._lock:
            if request_id in self._rejected:
                raise ValueError('rejected')
            current = self._current()
            if request_id in self._proposals:
                previous = self._proposals[request_id]
                if previous.brief != brief or previous.snapshot != current:
                    raise ValueError('request_conflict')
                return previous
            draft = self._proposer(current, brief)
            if type(draft) is not Draft:
                raise ValueError('invalid_draft')
            draft.check(current)
            proposal = Proposal(request_id, brief, current, draft)
            self._proposals[request_id] = proposal
            return proposal

    def _registered(self, proposal: Proposal) -> None:
        if type(proposal) is not Proposal or self._proposals.get(proposal.request_id) != proposal:
            raise ValueError('unknown_proposal')
        if proposal.request_id in self._rejected:
            raise ValueError('rejected')

    def reject(self, proposal: Proposal) -> None:
        with self._lock:
            if type(proposal) is Proposal and self._proposals.get(proposal.request_id) == proposal:
                if proposal.request_id in self._rejected:
                    return
            self._registered(proposal)
            self._rejected.add(proposal.request_id)
            self._accepted.pop(proposal.request_id, None)

    def accept(self, review: Review) -> PreparedScene:
        with self._lock:
            if type(review) is not Review:
                raise ValueError('invalid_review')
            self._registered(review.proposal)
            if any(v is not True for v in (review.credible_stakes, review.open_agency, review.grounded)):
                raise ValueError('review_required')
            if self._current() != review.proposal.snapshot:
                raise ValueError('stale')
            # Freshness precedes even a replay: old approval never claims current validity.
            key = review.proposal.request_id
            if key not in self._accepted:
                self._accepted[key] = PreparedScene(review)
            return self._accepted[key]
