"""What each LLM connection's provider last actually did (#146).

Before this, the only claim the app made about a provider was "a key string is
present in the store" — which the topbar drew as a green dot and the
Configuration page repeated in words. A revoked key, an expired Claude login
and a local endpoint that is not running all looked exactly like a working
setup right up until a scene failed.

Two things write here, and they answer the same question from different sides:

* the **check** route, when a reader clicks Test connection — the on-demand
  half, which asks the provider a question that costs nothing to answer;
* the **facade's observer**, as every real generation settles — the passive
  half, which is what keeps the answer current between clicks.

There is deliberately no third writer on a timer. #146 sketched one and
recommended against it, and the passive half is why: a poller re-asks a
question that the traffic already answers, and it answers it *less* well —
a scene turn is the exact call whose failure the reader cares about, while a
poll can only report on a cheaper request that may succeed where the real one
would not. What a poll adds over this is freshness while nobody is playing,
which is precisely when it does not matter.

## Why this is in memory, and per app

It is a statement about *now*, held for as long as "now" lasts. Persisting it
would mean a status file that outlives the condition it describes — reopening
the app the morning after a rate limit to a red dot and no way to clear it but
generating — and a write on the failure path, where the store is the last thing
worth touching.

Per app rather than per module, following `app.state`: a `TestClient` builds an
app per test, and module state would leak one test's recorded failures into the
next. The cost is that the status resets on restart, to `unknown`, which is
true: nothing has been observed yet.
"""

from __future__ import annotations

from .store import paths

#: No observation yet — the app has just started, or nothing has used this
#: connection. Distinct from `error` on purpose: "not known to be broken" and
#: "known to be broken" must not draw the same dot.
UNKNOWN = "unknown"
OK = "ok"
ERROR = "error"


class ProviderHealth:
    """Per-connection health, keyed by connection id.

    Only the latest outcome per connection is kept. A history would be a
    different feature (#154's error counts and latencies) with a different
    shape; what a status dot needs is the last thing that happened.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, dict] = {}

    def record(self, conn: dict, error=None) -> dict:
        """File one outcome for `conn` and return the status it produces.

        A connection with no id is dropped rather than filed under `""`: the
        facade is handed connection *dicts*, and a caller that builds one by
        hand (tests, and `_fallback_connection`'s dead ends) would otherwise
        collide with every other anonymous connection in one shared slot.
        """
        cid = conn.get("id") or ""
        if not cid:
            return self.status("")
        status = {"state": OK if error is None else ERROR,
                  "kind": "" if error is None else getattr(error, "kind", "bad_response"),
                  "detail": "" if error is None else getattr(error, "detail", str(error)),
                  "at": paths.now_iso()}
        self._by_id[cid] = status
        return dict(status)

    def status(self, cid: str) -> dict:
        """What is known about `cid`. Never raises and never returns None: an
        unknown connection has a status, and it is `unknown`."""
        known = self._by_id.get(cid)
        if known is None:
            return {"state": UNKNOWN, "kind": "", "detail": "", "at": ""}
        return dict(known)

    def forget(self, cid: str) -> None:
        """Drop what is known about `cid`.

        Called when a connection is deleted. Ids are slugs and are reusable —
        deleting `endpoint` and creating another connection named "Endpoint"
        lands on the same id — so leaving the old verdict in place would greet
        a brand-new connection with the previous one's failure.
        """
        self._by_id.pop(cid, None)
