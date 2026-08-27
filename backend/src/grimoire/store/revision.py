"""A campaign's write token: one value that changes whenever the campaign does
(#409).

Stored at ``<campaign>/revision.txt``, holding one opaque token and nothing
else. It is not a version number, a count of writes, or an ordering: two
tokens are only ever compared for equality, and the one question it answers is

    is this campaign still in the state the value I am holding was read from?

That question has no answer today, which is what #409 is about.
``POST /campaigns/{cid}/advance`` resolves its starting moment from the live
clock at the moment it runs, so a caller that priced a move against one state
and confirmed against another gets a different move than the one it showed. A
guard needs something to compare, and nothing the store exposes moves on *any*
campaign mutation: the clock moves only when time does, and a scene edit, an
absorb or a lore write leaves it reading exactly the same.

**Opaque and unique, rather than a counter.** A counter is a
read-modify-write, and two of those racing lose one of the two increments --
which here means a mutation whose token never moved, so a stale expectation
passes the very check it was written for. A fresh unique value is a blind
write with nothing to lose: two concurrent bumps leave one of two tokens on
disk and *either* is a correct answer, because the only property required is
"different from what any earlier reader is holding". That is also why this
module takes no lock (`store/locks.py` classifies it), and why the file is
disposable -- an unreadable or absent one reads as `INITIAL`, which no minted
token can equal, so a damaged file refuses stale expectations rather than
waving them through.

**What moves it, and what does not.** The bump is at the two places that know a
campaign was written: the activity middleware in `main.py`, which fires for
every campaign-scoped mutating request that answered 2xx, and
`routes.streaming._persist_reply`, which is where a *detached* turn lands its
posts -- the middleware deliberately skips streams, since a stream's status is
sent before its outcome is known. What that leaves out is worth saying plainly:
a store written by something other than this app (a hand edit, a sync client
landing a file, a second grimoire process older than this module) moves
nothing here. So a token that has not changed is evidence and not proof, which
is the honest shape for a guard whose failure mode is a re-priced retry rather
than a lost write.

The route layer, not this module, decides what a mismatch costs: `/advance`
turns `RevisionMismatchError` into a 409 the client re-prices and re-asks against.
"""

from __future__ import annotations

import logging
import uuid

from . import atomic
from .campaigns import paths as campaigns_paths

log = logging.getLogger(__name__)

#: What a campaign that has never been stamped reads as, and what an unreadable
#: file degrades to. A minted token is 32 hex characters, so nothing this module
#: writes can ever collide with it -- a client holding `INITIAL` is therefore
#: refused the moment anything at all has been recorded, which is the direction
#: to be wrong in.
INITIAL = "0"

#: A token is written by this module and only ever compared for equality, so it
#: needs no structure. The length check on the way back in is a shape check on a
#: hand-editable file, not a validation of anything: past this, the file is not
#: something we wrote and `INITIAL` is the honest reading of it.
_MAX_LEN = 64


class RevisionMismatchError(Exception):
    """The campaign has been written since the caller read its revision.

    Carries both values because the refusal is a message to a client that has
    to decide what to do next: `expected` is what it priced against, `current`
    what it would price against now.
    """

    def __init__(self, cid: str, expected: str, current: str) -> None:
        super().__init__(f"{cid} has changed since revision {expected!r}")
        self.cid, self.expected, self.current = cid, expected, current


#: The token's filename, named here so the one other module that has to reach
#: it -- `store/fork.py`, which drops the copy a `copytree` carried over -- does
#: not spell it a second time.
FILENAME = "revision.txt"


def _path(cid: str):
    return campaigns_paths.campaign_root(cid) / FILENAME


def current(cid: str) -> str:
    """This campaign's token, or `INITIAL`.

    Never raises over the file. An id that cannot name a campaign at all still
    raises `CampaignNotFound` out of `campaign_root`, which is where that check
    belongs; everything else -- absent, unreadable, non-UTF-8 from a half-landed
    sync, or holding something this module would not have written -- reads as
    `INITIAL`. That is deliberately the *strictest* degradation available: every
    stale-check against a damaged file refuses, and a refusal costs a re-price.
    """
    try:
        token = _path(cid).read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001 -- see docstring: a damaged file is not a crash
        return INITIAL
    return token if token and len(token) <= _MAX_LEN else INITIAL


def bump(cid: str) -> str:
    """Record that this campaign has been written, and return the new token.

    Never raises, for the reason `campaigns.read.touch_quietly` never does: every
    caller reaches this *after* the mutation it records has committed, so raising
    would turn work the user already has into a reported failure. A bump that
    does not land costs a stale expectation its refusal -- the guard fails open
    for one caller -- which is the cheaper of the two losses and the same one a
    store written by another program already carries.

    The failure is logged rather than passed over: a revision file that cannot be
    written is a disk the next real write is about to fail on too.
    """
    token = uuid.uuid4().hex
    try:
        atomic.write_text(_path(cid), token + "\n")
    except Exception:   # see docstring: nothing here may propagate
        log.warning("revision: could not stamp %s", cid, exc_info=True)
        return current(cid)
    return token


def require(cid: str, expected: str) -> None:
    """Raise `RevisionMismatchError` unless `expected` is still this campaign's token.

    An empty `expected` is "no expectation" and always passes: the token is
    optional on every endpoint that takes one, so a client that predates it --
    or `curl` -- keeps working and simply gets what it has today. Spelled here
    rather than at each call site so the two endpoints cannot disagree about
    what an absent token means.
    """
    if not expected:
        return
    now = current(cid)
    if expected != now:
        raise RevisionMismatchError(cid, expected, now)
