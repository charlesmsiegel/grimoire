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

import json

from . import atomic, campaigns

# Only a save the user could still be retrying is worth remembering; the ledger
# is a guard, not a history. Oldest tokens are evicted first.
KEEP = 20


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


def result_for(cid: str, token: str) -> dict | None:
    """The stored response for a spent token, or None when it is unseen.

    An empty token is always unseen: a client that sends none opts out of the
    guard, and must not collide with every other tokenless save.
    """
    if not token:
        return None
    entry = _read(cid).get(token)
    return entry if isinstance(entry, dict) else None


def record(cid: str, token: str, result: dict) -> None:
    """Remember what this token's save returned. No-op for an empty token."""
    if not token:
        return
    data = _read(cid)
    data.pop(token, None)                       # re-insert so it counts as newest
    data[token] = result
    for stale in list(data)[:-KEEP]:            # dicts keep insertion order
        del data[stale]
    atomic.write_text(_path(cid), json.dumps(data, indent=2) + "\n")
