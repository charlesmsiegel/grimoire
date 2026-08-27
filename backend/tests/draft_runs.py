"""Drive a detached `draft` route to its answer.

The twelve computing draft routes answer 202 and a run to poll (#398) -- a
tagline, a voice anchor, an image description, scene suggestions, a scene
intent, a scenario proposal, a model-catalog refresh. That is what makes them
survive a locked phone, and what makes every test that used to read the payload
straight off the POST need somewhere to wait.

`review_runs.py` is the same thing one class over, and this is deliberately not
merged into it: a review is scene-scoped and its result is durable, so its
helper reads `GET .../pending-review`; a draft belongs to a campaign, a world
or the app, and its result is held on the run and reaped. Sharing the loop
would mean a parameter deciding which of those two it was on every call.

**A non-202 answer is returned untouched.** Every pre-flight refusal -- 404 for
an unknown character, 409 for a connection that cannot read pictures, 400 for
an empty scene description -- is still raised while the request is there to
receive it, so a test asserting on one of those is asserting on the real
response.
"""

from __future__ import annotations

import time

#: How long to wait for a run to reach a terminal state, and how often to look.
#: Generous: these tests fake the provider, so a run still going after this is
#: wedged rather than slow, and a test that hangs forever is worse than one
#: that fails.
POLL_TIMEOUT = 30.0
POLL_INTERVAL = 0.01


class Answer:
    """A response's shape, over a payload that came from a run.

    Only `status_code` and `json()`, which is the whole of what the suites use
    -- a wider fake would invite a test to assert on a header the run never
    had.
    """

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def post(client, url, **kwargs):
    """POST a draft route and hand back what it eventually produced."""
    return settle(client, client.post(url, **kwargs))


def settle(client, resp):
    """Wait out the run a 202 named, and shape its outcome as the old response.

    The subject is read off the REQUEST's path rather than passed in, because
    it is already decided by the URL the test wrote -- a campaign draft under
    `/api/campaigns/{cid}/`, a world draft under `/api/worlds/{wid}/`, and the
    model refresh under neither. Asking each call site to repeat it is one more
    thing for sixty of them to get subtly wrong.
    """
    if resp.status_code != 202:
        return resp
    base = runs_base(resp.request.url.path)
    run = wait_for_run(client, base, resp.json()["run"]["id"])
    if run["state"] == "landed":
        return Answer(200, run["result"])
    # A failed run carries the status the same refusal would have had when
    # these routes answered synchronously, so a test that asserted on a 504
    # timeout or a 409 `missing_key` still asserts on one.
    error = run["error"] or {}
    # Everything but the status, which becomes the status. `retry_after` is the
    # field that made this a comprehension rather than two named keys: a rate
    # limit's window rides in the body now, because the 202 that would have
    # carried the header went out before the provider was called.
    return Answer(error.get("status", 500),
                  {k: v for k, v in error.items() if k != "status"})


def runs_base(path: str) -> str:
    """Where the runs of the subject this path belongs to are addressed."""
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[:2] == ["api", "campaigns"]:
        return f"/api/campaigns/{parts[2]}/runs"
    if len(parts) >= 3 and parts[:2] == ["api", "worlds"]:
        return f"/api/worlds/{parts[2]}/runs"
    return "/api/runs"


def wait_for_run(client, base: str, run_id: str) -> dict:
    """Poll one run to a terminal state and return its payload."""
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        run = client.get(f"{base}/{run_id}").json()["run"]
        if run["state"] != "running":
            return run
        assert time.monotonic() < deadline, f"run {run_id} never reached a terminal state"
        time.sleep(POLL_INTERVAL)
