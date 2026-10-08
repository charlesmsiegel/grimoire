"""Drive a detached review (absorb, audit, dossiers) to its answer.

The review family answers 202 and a run to poll (#396), which is what makes it
survive a locked phone -- and what makes every test that used to read the
review straight off the POST need somewhere to wait. That is all this is: the
polling loop a client does, plus the shaping that lets a test go on asserting
about a status code and a body.

Shared rather than written per suite, unlike the one-off helpers in
`test_runs_routes.py` and friends: four test modules end a scene, and four
copies of a poll loop is four places for a timeout to drift.

**A non-202 answer is returned untouched.** The pre-flight refusals -- 404 for
an unknown scene, 400 for an empty one, 409 for `already_absorbed` or a missing
key -- are still raised by the handler before a run is ever started, so a test
asserting on one of those is asserting on the real response.
"""

from __future__ import annotations

import json
import time

#: How long to wait for a run to reach a terminal state. Generous: these tests
#: fake the provider, so a run that is still going after this is wedged rather
#: than slow, and a test that hangs forever is worse than one that fails.
POLL_TIMEOUT = 30.0
POLL_INTERVAL = 0.01

# Identity-check fixture texts (continuity capstone slice C), one spelling for
# the store, route and routing suites. Whether a stored record is a candidate
# depends on these exact words, so each test that uses them first asserts the
# signal that is supposed to decide it.

#: A stored thread, as (id, title, beat). Seed it in a scene of its own, never
#: the one being absorbed, unless the test is about shared scenes.
LEDGER_THREAD = ("find-the-ledger", "Find the ledger",
                 "Winifred learned the harbour ledger exists.")

#: A proposed plot row rewording `LEDGER_THREAD`: clears `TOKEN_FLOOR` and
#: `CHAR_FLOOR` lexically, whatever the scene.
RECOVER_THE_LEDGER = {"title": "Recover the harbour ledger",
                      "beat": "Winifred went looking for the harbour ledger.",
                      "status": "open"}

#: A proposed plot row about something else: below both weak floors against
#: `LEDGER_THREAD`.
SALTMARCH_TITHE = {"title": "The Saltmarch tithe",
                   "beat": "The guild demanded Mara pay the Saltmarch tithe.",
                   "status": "open"}

#: An extraction reply proposing `RECOVER_THE_LEDGER` and nothing else, as the
#: string the model sends.
EXTRACTION_PROPOSING_RECOVER_THE_HARBOUR_LEDGER = json.dumps(
    {"one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],
     "plot_movements": [RECOVER_THE_LEDGER]})

#: What marks a request as the duplicate check's: the decide system phrase
#: every decision carries, and the heading only an identity item's context
#: holds (`continuity_identity/item.j2`), as `(system, user)` needles.
IDENTITY_MATCH = ("You answer closed questions about material you are given.",
                  "\nCandidates:\n")


def identity_requests(fake):
    """The requests `fake` received from the duplicate check: those whose
    system message and user message carry `IDENTITY_MATCH`'s two needles."""
    system, user = IDENTITY_MATCH
    return [r for r in fake.requests
            if any(m.get("role") == "system" and system in (m.get("content") or "")
                   for m in r["messages"])
            and any(m.get("role") == "user" and user in (m.get("content") or "")
                    for m in r["messages"])]


class Answer:
    """A response's shape, over a payload that came from a run.

    Only `status_code` and `json()`, because that is the whole of what the
    suites use -- a wider fake would invite a test to assert on a header that
    no longer exists.
    """

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def absorb(client, cid, sid, force=False):
    """End the scene and hand back the review, or the refusal.

    The review is read from `GET .../pending-review` rather than off the run,
    deliberately: that is where a real client reads it, so a test that passes
    here is exercising the durable path rather than a convenience copy.
    """
    url = f"/api/campaigns/{cid}/scenes/{sid}/absorb"
    resp = client.post(url + ("?force=true" if force else ""))
    return _settle(client, resp, cid, sid, lambda run: pending_review(client, cid, sid))


def audit(client, cid, sid):
    """Retry the audit phase and hand back `{mechanics, edits}`.

    From the RUN's result, not from the merged review: this is what the retry
    produced, and the stored review is what it produced folded into everything
    else -- two different questions, and the phase tests are asking the first.
    """
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/audit")
    return _settle(client, resp, cid, sid, _phase_result)


def dossiers(client, cid, sid):
    """Retry the dossier phase and hand back `{dossiers, edits}`."""
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/dossiers")
    return _settle(client, resp, cid, sid, _phase_result)


def pending_review(client, cid, sid):
    """The stored review, as an `Answer`.

    A review the watermark refuses is a 409 `review_stale` here rather than a
    200 with an empty body: every caller of this helper wants the review, and
    "the scene moved on" is a refusal to them however the route spells it.
    """
    resp = client.get(f"/api/campaigns/{cid}/scenes/{sid}/pending-review")
    if resp.status_code != 200:
        return resp
    body = resp.json()
    if body["review"] is None:
        return Answer(409, {"kind": "review_stale", **(body["stale"] or {})})
    return Answer(200, body["review"])


def cancel(client, cid, sid, generation):
    return client.delete(
        f"/api/campaigns/{cid}/scenes/{sid}/pending-review?generation={generation}")


def wait_for_run(client, cid, sid, run_id):
    """Poll one run to a terminal state and return its payload."""
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        run = client.get(
            f"/api/campaigns/{cid}/scenes/{sid}/runs/{run_id}").json()["run"]
        if run["state"] != "running":
            return run
        assert time.monotonic() < deadline, f"run {run_id} never reached a terminal state"
        time.sleep(POLL_INTERVAL)


def _settle(client, resp, cid, sid, collect):
    if resp.status_code != 202:
        return resp
    run = wait_for_run(client, cid, sid, resp.json()["run"]["id"])
    if run["state"] == "landed":
        return collect(run)
    # A failed or cancelled run carries the status the same refusal would have
    # had when these routes answered synchronously, so a test that asserted on
    # a 504 timeout or a 409 still asserts on one.
    error = run["error"] or {}
    return Answer(error.get("status", 500),
                  {"kind": error.get("kind"), "detail": error.get("detail")})


def _phase_result(run):
    return Answer(200, {k: v for k, v in (run["result"] or {}).items()
                        if k not in ("generation", "sid")})
