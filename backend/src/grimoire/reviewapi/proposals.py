"""Exact-bytes proposals (110): what a human approves in the review UI, and the digest that binds it.

A proposal is one planned write of a domain service (037's card import, 036's history import, 026's V1
import, 042's rebase or 043's change set) held in memory with the exact bytes it previewed. The UI shows
every file; approving names the proposal **and** its digest, and the digest is recomputed from the plan's
own preview before the domain service commits. So changed staged bytes never commit under an approval of
other bytes, and a library that moved since is refused by the domain service's own compare-and-swap.

Proposals are not a store: they live only in this process, a bounded few at a time, and a restart or an
eviction means previewing again. Nothing here writes.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

#: the digest format's own name, hashed in, so a digest of another format never matches
DIGEST_FORMAT = "grimoire.review-preview/1"
#: proposals held at once; the oldest is dropped (previewed again) past this
MAX_PROPOSALS = 8
#: a file's text is shown when it is UTF-8 and no larger than this; otherwise only its size and hash
MAX_SHOWN_TEXT = 256 * 1024
#: a diff is shown when both sides are text and the diff is no larger than this
MAX_SHOWN_DIFF = 256 * 1024


def preview_digest(kind: str, preview: Mapping[str, bytes]) -> str:
    """``sha256:`` of the proposal's kind and every ``(path, sha256 of its bytes)``, in path order."""
    rows = [[path, hashlib.sha256(preview[path]).hexdigest()] for path in sorted(preview)]
    document = json.dumps([DIGEST_FORMAT, kind, rows], ensure_ascii=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(document.encode("ascii")).hexdigest()


@dataclass
class Proposal:
    """One planned write. ``commit`` writes it (the domain service's own call); ``reject`` ends it without
    writing (``None``: nothing to withdraw). ``view`` is the domain report shown with the files."""

    proposal_id: str
    kind: str
    preview: Callable[[], dict[str, bytes]]
    commit: Callable[[], dict]
    view: dict
    digest: str
    files: list[dict]
    reject: Callable[[], None] | None = None
    state: str = "open"  # open | applied | rejected | failed
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def describe(self) -> dict:
        return {"proposal": self.proposal_id, "kind": self.kind, "state": self.state, "digest": self.digest,
                "writes": len(self.files), "files": self.files, "report": self.view}


class Proposals:
    """The bounded in-memory holder of open proposals."""

    def __init__(self, *, limit: int = MAX_PROPOSALS, new_id: Callable[[], str] | None = None) -> None:
        self._limit = limit
        self._new_id = new_id or (lambda: "p-" + uuid.uuid4().hex[:16])
        self._held: OrderedDict[str, Proposal] = OrderedDict()

    def add(self, kind: str, *, preview: Callable[[], dict[str, bytes]], commit: Callable[[], dict], view: dict,
            files: list[dict], reject: Callable[[], None] | None = None) -> Proposal:
        proposal_id = self._new_id()
        while proposal_id in self._held:
            proposal_id = self._new_id()
        proposal = Proposal(proposal_id, kind, preview, commit, view, preview_digest(kind, preview()), files, reject)
        self._held[proposal_id] = proposal
        while len(self._held) > self._limit:
            _, dropped = self._held.popitem(last=False)
            if dropped.reject is not None:
                try:
                    dropped.reject()
                except Exception:  # noqa: BLE001 - an evicted review's withdrawal is best effort
                    pass
        return proposal

    def get(self, proposal_id: str) -> Proposal | None:
        return self._held.get(proposal_id)

    def drop(self, proposal_id: str) -> None:
        self._held.pop(proposal_id, None)

    def __len__(self) -> int:
        return len(self._held)


__all__ = ["DIGEST_FORMAT", "MAX_PROPOSALS", "MAX_SHOWN_DIFF", "MAX_SHOWN_TEXT", "Proposal", "Proposals",
           "preview_digest"]
