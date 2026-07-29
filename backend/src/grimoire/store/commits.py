"""Idempotency ledger for the chronicle commit (#235).

`PUT /chronicle` is not idempotent and cannot cheaply be made so: timeline
events append, plot movements append a beat, weather spans append a record, and
`new_character`/`new_location`/`new_lore` create one. Six appends, each correct
on its own and each a duplicate when the same save runs twice.

Rather than make all six individually replay-safe, the commit carries an
idempotency key: `POST /absorb` mints one, the review sends it back, and a save
whose token is already spent returns the result the first one produced instead
of applying anything. That is what lets the review panel offer a retry after a
save whose response was lost -- the case where the write landed and the client
cannot tell.

Scope: this makes a *replay of the same review* safe. It does not make two
DIFFERENT reviews of one scene safe against each other (they carry different
tokens), and it does not repair a commit that failed partway -- both are the
wider contract in #271.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from . import atomic, campaigns
from .paths import now_iso

#: How long a completed entry stays retryable. Deliberately time, not count: a
#: review sits open on someone's screen for as long as they leave it there, and
#: evicting by count would drop their token the moment the campaign saw enough
#: other saves -- their retry would then replay every append. An UNFINISHED
#: reservation never expires at all; it is the entry whose loss lets a partly
#: landed commit run again.
RETAIN_DAYS = 30


def _path(cid: str):
    return campaigns.campaign_root(cid) / "commits.json"


def _read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}          # a garbled ledger forgets, it never blocks a save
    return data if isinstance(data, dict) else {}


def fingerprint(body: dict) -> str:
    """A stable digest of the save body.

    A token identifies the *attempt*; this identifies what the attempt was for.
    A review stays editable after a failed save, so a retry can carry the same
    token and different content -- returning the first result then reports
    success while silently discarding the edits made in between.
    """
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def lookup(cid: str, token: str) -> dict | None:
    """This token's ledger entry, or None when it is unseen.

    An entry is ``{"done": bool, "result": dict | None, "fingerprint": str,
    "sid": str, "at": iso}``. The ledger is campaign-scoped, so ``sid`` is what
    keeps one scene's spent token from answering for another's save. ``done``
    is False between ``reserve`` and ``record`` -- the commit began and its
    outcome is unknown, which is exactly the state a replay must not run again.

    An empty token is always unseen: a client that sends none opts out of the
    guard, and must not collide with every other tokenless save.
    """
    if not token:
        return None
    entry = _read(cid).get(token)
    if not isinstance(entry, dict) or "done" not in entry:
        return None
    return entry


def _prune(data: dict) -> dict:
    """Drop completed entries past RETAIN_DAYS. Reservations are kept."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=RETAIN_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {t: e for t, e in data.items()
            if not (isinstance(e, dict) and e.get("done")
                    and str(e.get("at", "")) < cutoff)}


def _put(cid: str, token: str, entry: dict) -> None:
    data = _prune(_read(cid))
    data[token] = {**entry, "at": now_iso()}
    atomic.write_text(_path(cid), json.dumps(data, indent=2) + "\n")


def reserve(cid: str, token: str, fp: str = "", sid: str = "") -> None:
    """Claim the token before the first non-idempotent write.

    Recording only *after* the effects leaves a window: a crash in between (or a
    failing ledger write) returns no response while the token still reads as
    unseen, so the retry re-runs every append. Reserving first makes that window
    durable -- the replay finds an unfinished entry and refuses instead.
    """
    if not token:
        return
    _put(cid, token, {"done": False, "result": None, "fingerprint": fp, "sid": sid})


def record(cid: str, token: str, result: dict, fp: str = "", sid: str = "") -> None:
    """Complete the reservation with what this token's save returned."""
    if not token:
        return
    _put(cid, token, {"done": True, "result": result, "fingerprint": fp, "sid": sid})
