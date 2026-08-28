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
module takes no lock (`store/locks.py` classifies it).

**A damaged file refuses everything, and is repaired by the next read; an absent
one only means "never stamped".** The two are told apart because they mean
different things. A file holding something `bump` would not have written says
nothing about what has happened to the campaign, so `require` refuses against it
whatever the caller holds -- `INITIAL` included, which is the one value a caller
can hold without anything having been stamped. Refusing forever is not the same
promise, though, and would be a worse one: `current` therefore mints a
replacement, so damage costs every earlier holder its next operation and then
the campaign works again. A file that is simply not there is indistinguishable
from a campaign nothing has ever written, and reads as `INITIAL` so that such a
campaign can be priced at all. The residual is exactly that: a token file
DELETED after writes lets a caller still holding `INITIAL` through, and a caller
can only be holding it from having read the campaign before anything stamped
it.

**What moves it, and what does not.** The default is the activity middleware in
`main.py`, which fires for every campaign-scoped mutating request that answered
2xx, streams included -- one place, so a route written tomorrow is covered
without anybody remembering this file exists.

What stamps for itself on top of that has one reason in several shapes: the
response line is not the moment.

`clock.advance` bumps inside the lock hold that covers its commit, because a
check the token has not moved under does not exclude a second caller holding the
same one.

Everything a DETACHED run writes bumps where it writes, because the run outlives
the response: `routes.scenes._under_review_lock` for a review's terminal write
(whose route answered 202 minutes earlier and correctly declares itself
`@computes_only`), `routes.scenes._rolling_commit` and `_break_commit` for the
follow-ups a landed turn schedules, and `routes.streaming._turn_settled` at each
of a turn's terminal points. That last one is deliberately not "a post landed":
a roll fence that closes with no narration writes a proposal record and nothing
else, and a failed turn's rollback takes a post back OFF.

A multi-campaign write reached from a WORLD route stamps every campaign it
wrote, for the same reason and one step further out: nothing under
`/api/campaigns/...` runs at all. `sync.demote`, `store.reclassify` and the
world-module rebind each do it inside the `hold_all` that covers their writes.

What all of that leaves out is worth saying plainly. A store written by
something other than this app (a hand edit, a sync client landing a file, a
second grimoire process older than this module) moves nothing here. Neither
does an edit to a campaign's WORLD, unless it writes into the campaign: the
token records writes to the campaign, and a campaign renders inherited content
it does not hold a copy of, so a world edit can change what a digest reads
without moving a thing. Stamping every campaign of a world on every world edit
is a fan-out this value is not worth. So a token that has not changed is evidence and not proof, which
is the honest shape for a guard whose failure mode is a re-priced retry rather
than a lost write.

The route layer, not this module, decides what a mismatch costs: `/advance`
turns `RevisionMismatchError` into a 409 the client re-prices and re-asks against.
"""

from __future__ import annotations

import logging
import re
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

#: What `bump` writes: `uuid4().hex`, and nothing else ever. Checked on the way
#: back in, so a file holding anything this module would not have written is
#: known to be damaged rather than merely unfamiliar -- which is what lets
#: `require` refuse against it. A length bound alone was not enough: it accepted
#: a truncated or hand-edited value verbatim, including the literal `0`, which
#: is the one string a caller can legitimately still be holding (Codex review).
_MINTED = re.compile(r"\A[0-9a-f]{32}\Z")


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


def _stored(cid: str) -> str | None:
    """The file's contents when it holds a token this module minted, `""` when it
    holds something else, and None when there is nothing there.

    Three answers rather than two, because `current` and `require` want different
    things from the middle one -- see both.

    Never raises over the file. An id that cannot name a campaign at all still
    raises `CampaignNotFound` out of `campaign_root`, which is where that check
    belongs; unreadable, non-UTF-8 from a half-landed sync, and holding something
    this module would not have written are all "damaged".
    """
    try:
        raw = _path(cid).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 -- see docstring: a damaged file is not a crash
        return ""
    return raw if _MINTED.match(raw) else ""


def current(cid: str) -> str:
    """This campaign's token, or `INITIAL`.

    The REPORTING path: what a caller is handed to price against.

    **A damaged file is REPAIRED here, and that is what keeps fail-closed from
    meaning stuck.** `require` refuses against damage whatever the caller holds,
    which is right -- but with `current` reporting a value that check would
    always reject, the client's whole recovery loop is a treadmill: price, get
    `campaign_moved`, price again, get the same value, forever, until some
    unrelated write happens to replace the file (Codex review). Minting one here
    ends it in one round: every earlier holder is refused, which is the honest
    answer for a token nobody can vouch for, and the caller in front of us gets
    a value that will work.

    An ABSENT file is not damage and is not repaired: it cannot be told from a
    campaign nothing has ever written, and `INITIAL` is what makes such a
    campaign priceable at all.
    """
    stored = _stored(cid)
    if stored == "":
        return _repair(cid)
    return stored or INITIAL


def _repair(cid: str) -> str:
    """Replace a file this module did not write, and return what replaced it.

    Best-effort: a store that will not take this write leaves the damage in
    place, and `INITIAL` then reports what `require` will go on refusing. That is
    the same fail-closed answer as before, for a disk that has bigger problems
    than a token.
    """
    token = uuid.uuid4().hex
    try:
        atomic.write_text(_path(cid), token + "\n")
    except Exception:   # see docstring: nothing here may propagate
        log.warning("revision: could not replace the damaged token in %s", cid,
                    exc_info=True)
        return INITIAL
    return token


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
    except FileNotFoundError:
        # The campaign directory is not there. `DELETE /campaigns/{cid}` reaches
        # here through the activity middleware, having just removed the whole
        # tree, and that is the ordinary path rather than a fault -- a traceback
        # for it would put a storage-failure warning in the log after every
        # deletion, in a file a user may hand to somebody else (Codex review).
        # There is nothing to stamp and nothing to say.
        return INITIAL
    except Exception:   # see docstring: nothing here may propagate
        log.warning("revision: could not stamp %s", cid, exc_info=True)
        # `_stored`, not `current`: a store that just refused this write is not
        # one to attempt a repair against on the way out of the failure.
        return _stored(cid) or INITIAL
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
    stored = _stored(cid)
    if stored == "":
        # Present, and not a value this module wrote. The CHECKING path parts
        # company with the reporting one here: `current` degrades this to
        # `INITIAL` so a caller is handed something usable, and doing the same
        # here would let a caller still holding `INITIAL` -- the one token it can
        # hold without anything having been stamped -- match a campaign that has
        # been written and then had its token damaged away. A file we did not
        # write says nothing about what happened to the campaign, so nothing may
        # be certified against it.
        raise RevisionMismatchError(cid, expected, INITIAL)
    now = stored or INITIAL
    if expected != now:
        raise RevisionMismatchError(cid, expected, now)
