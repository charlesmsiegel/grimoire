"""Decision capture (roadmap 01b, `routes/decision_capture.py`).

Every decide site files one prompt-log entry per decision scope, in the
prompt log's decision pool. What these pin, beyond "an entry appears": the
envelope's shape, one entry for many calls, a failed call kept as its kind
and status and never its text, the scope filing on a failed decision but
never on a cancel or an abandoned review, the strict scene-identity fence, a
capture that can never cost the decision, the retention pools, the size caps
and the diff route's refusal.

The speaker pick's own capture tests stay in `test_character_turns.py`, the
voice-drift phase's in `test_routes.py` and the duplicate check's in
`test_absorb_identity.py`, beside the fixtures each needs; the scene-break
check drives most of the rules here, being the one site a single route call
reaches.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import pytest

import grimoire.store as store
from grimoire import decisions, routes, wire
from grimoire.decisions import Answer, ItemResult
from grimoire.llm_errors import LLMError
from grimoire.routes import decision_capture
from grimoire.routes import scenes as scenes_routes

from .inference_fixtures import SPARE, decide_only
from .llm_fakes import FakeLLM, decision_reply

YES = decision_reply({"over": True}, rationales=["The ledger changed hands."])
NO = decision_reply({"over": False}, rationales=["They are still mid-argument."])
TITLE = "The Long Walk Back"
#: A native endpoint with no decisions for this model.
NO_ENDPOINT = LLMError("bad_response", "no decisions endpoint for this model", status=404)
#: A provider error whose text echoes a masked key: it must reach no capture.
LEAKY = LLMError("rate_limit", "slow down, key sk-...abcd", status=429)


def _scene(client, posts: int = 40, title: str = "Saltmarch") -> tuple[str, str]:
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test-capture"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": title}).json()["id"]
    for i in range(posts):
        store.scenes.append_message(cid, sid, "user" if i % 2 == 0 else "assistant",
                                    f"Post {i}.")
    return cid, sid


def _use(client, llm):
    client.app.dependency_overrides[routes.get_llm] = lambda: llm
    return llm


def _ask(client, cid, sid):
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert r.status_code == 200, r.text
    return r.json()


def _decisions(cid: str, sid: str) -> list[dict]:
    return [e for e in store.prompt_log.list_entries(cid, sid) if e.get("operation")]


def _entry(cid: str, sid: str) -> dict:
    (row,) = _decisions(cid, sid)
    entry = store.prompt_log.read_entry(cid, row["id"], scene=sid)
    assert entry is not None
    return entry


def _envelope(entry: dict) -> dict:
    last = entry["sections"][-1]
    assert last["id"] == decision_capture.OUTCOME_SECTION_ID and last["tokens"] == 0
    return json.loads(last["text"])


def _payload_bytes(cid: str) -> bytes:
    root = store.campaigns.campaign_root(cid) / "prompts"
    return b"".join(p.read_bytes() for p in sorted(root.glob("*.json")))


# ---- the site, and the entry's shape ----

def test_a_scene_break_check_files_one_decision_entry(client):
    cid, sid = _scene(client)
    llm = _use(client, FakeLLM([[YES], [TITLE]]))
    assert _ask(client, cid, sid)["verdict"] == "yes"
    (row,) = _decisions(cid, sid)
    assert (row["task"], row["operation"], row["scene"]) == ("scene-break", "decide", sid)
    entry = _entry(cid, sid)
    assert entry["operation"] == "decide"
    # One call: the speaker's single-call section ids.
    assert [s["id"] for s in entry["sections"]] == ["message_0", "message_1", "decision"]
    assert [s["text"] for s in entry["sections"][:-1]] == [
        m["content"] for m in llm.requests[0]["messages"]]
    envelope = _envelope(entry)
    (call,) = envelope["calls"]
    assert envelope["task"] == "scene-break"
    assert (envelope["notes"], envelope["elided_calls"], envelope["truncated"]) == (
        {}, 0, False)
    assert (call["part"], call["stage"], call["at"], call["mode"]) == (
        "", 0, [0], "structured")
    assert call["items"][0]["answers"]["over"]["answer"] is True
    assert call["items"][0]["rationale"] == "The ledger changed hands."
    # The title draft is a generation, and captures nothing here.
    assert len(store.prompt_log.list_entries(cid, sid)) == 1


def test_a_native_failure_and_the_fallback_that_answered_are_one_entry(client):
    """Test 2: the stage that failed and the stage that answered tell one
    story, in one entry, each call its own record."""
    cid, sid = _scene(client)
    decide_only(client, fallback=True, on=SPARE)
    _use(client, FakeLLM([[YES], [TITLE]], decisions=[NO_ENDPOINT]))
    assert _ask(client, cid, sid)["verdict"] == "yes"
    entry = _entry(cid, sid)
    failed, answered = _envelope(entry)["calls"]
    assert (failed["stage"], failed["mode"], failed["at"]) == (0, "native", [0])
    assert (failed["error_kind"], failed["error_status"]) == ("bad_response", 404)
    assert "error" not in failed and "items" not in failed
    assert (answered["stage"], answered["mode"]) == (1, "structured")
    assert (answered["provider"], answered["model"]) == SPARE
    # Two calls: each kept call's sections are numbered by call.
    ids = [s["id"] for s in entry["sections"]]
    assert ids[:1] == ["c0_message_0"] and "c1_message_0" in ids and ids[-1] == "decision"
    assert entry["sections"][0]["label"] == "call 1 · user"
    # The row names the call that answered; the native call sent no preset.
    assert entry["model"] == SPARE[1]


def test_capture_off_files_nothing_and_builds_no_native_body(client, monkeypatch):
    """Test 3: `prompt_log_depth: 0` hands `decide` no capture at all."""
    cid, sid = _scene(client)
    decide_only(client, fallback=False)
    store.config.write_config(prompt_log_depth="0")
    handed: list = []
    real = scenes_routes.operations.decide

    async def spy(*args, **kwargs):
        handed.append(kwargs.get("capture"))
        return await real(*args, **kwargs)

    monkeypatch.setattr(scenes_routes.operations, "decide", spy)
    monkeypatch.setattr(scenes_routes.operations, "_native_request",
                        lambda *a: pytest.fail("a native body was built"))
    _use(client, FakeLLM([[TITLE]], decisions=[ItemResult({"over": Answer(True)})]))
    assert _ask(client, cid, sid)["verdict"] == "yes"
    assert handed == [None]
    assert store.prompt_log.list_entries(cid, sid) == []


def test_a_raising_filer_costs_the_capture_alone(client, monkeypatch):
    """Tests 4 and 12: the decision stands, no error row and no ERROR line;
    one warning naming the task and the exception type, and nothing else."""
    _use(client, FakeLLM([[YES], [TITLE]]))
    cid, sid = _scene(client)

    def explode(*args, **kwargs):
        raise RuntimeError("Post 3. leaked into an exception")

    monkeypatch.setattr(store.prompt_log, "record", explode)
    assert _ask(client, cid, sid)["verdict"] == "yes"
    assert store.errors.summary(campaign=cid)["rows"] == []
    assert store.logs.read(level="error")["rows"] == []
    (line,) = store.logs.read(level="warning", contains="could not capture")["rows"]
    assert line["message"] == "could not capture a scene-break decision: RuntimeError"
    assert "Post 3" not in json.dumps(line)


def test_no_key_url_or_provider_text_reaches_a_payload(client):
    """Test 12: the key and the address never; a failed call's provider text
    never, only its kind and status."""
    cid, sid = _scene(client)
    decide_only(client, fallback=True, on=SPARE)
    _use(client, FakeLLM([[YES], [TITLE]], decisions=[LEAKY]))
    _ask(client, cid, sid)
    written = _payload_bytes(cid)
    assert written
    for secret in (b"sk-test-capture", b"https://", b"sk-...abcd", b"slow down"):
        assert secret not in written, secret
    failed = _envelope(_entry(cid, sid))["calls"][0]
    assert (failed["error_kind"], failed["error_status"]) == ("rate_limit", 429)


# ---- the fence ----

def test_a_scene_recreated_under_the_same_id_inherits_no_decision(client):
    cid, sid = _scene(client)
    llm = FakeLLM([[YES], [TITLE]])
    complete = llm.complete

    async def swap(messages, conn, usage=None, *, schema=None):
        if llm.calls == 0:
            store.scenes.delete_scene(cid, sid)
            again = store.scenes.create_scene(cid, "Saltmarch")
            assert again == sid
        return await complete(messages, conn, usage, schema=schema)

    llm.complete = swap  # type: ignore[method-assign]
    _use(client, llm)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/scene-break")
    assert store.prompt_log.list_entries(cid, sid) == []


def test_an_identity_less_scene_files_nothing_and_mints_no_identity(client):
    _use(client, FakeLLM([[YES], [TITLE]]))
    cid, sid = _scene(client)
    path = store.scenes.paths._scene_path(cid, sid)
    path.write_text("".join(line for line in path.read_text(encoding="utf-8")
                            .splitlines(True) if not line.startswith("identity:")),
                    encoding="utf-8")
    assert store.scenes.scene_identity_strict(cid, sid) is None
    _ask(client, cid, sid)
    assert _decisions(cid, sid) == []
    assert "identity:" not in path.read_text(encoding="utf-8")


def test_an_identity_read_that_raises_at_filing_files_nothing(client, monkeypatch):
    _use(client, FakeLLM([[YES], [TITLE]]))
    cid, sid = _scene(client)
    real = store.scenes.scene_identity_strict
    reads = [0]

    def flaky(c, s):
        reads[0] += 1
        if reads[0] > 1:
            raise OSError("the sync client has it")
        return real(c, s)

    monkeypatch.setattr(store.scenes, "scene_identity_strict", flaky)
    assert _ask(client, cid, sid)["verdict"] == "yes"
    assert reads[0] == 2 and _decisions(cid, sid) == []


def test_a_contended_campaign_lock_files_nothing_and_does_not_wait(client, monkeypatch):
    _use(client, FakeLLM([[YES], [TITLE]]))
    cid, sid = _scene(client)

    @contextlib.contextmanager
    def contended(_cid):
        yield False

    monkeypatch.setattr(store.locks, "campaign_lock_nowait", contended)
    before = store.revision.current(cid)
    assert _ask(client, cid, sid)["verdict"] == "yes"
    assert _decisions(cid, sid) == []
    # The verdict's own commit stamped; the skipped capture did not add one.
    assert store.revision.current(cid) != before


# ---- the scope's rules, driven directly ----

def _target() -> wire.Target:
    return wire.Target(kind="openrouter", provider_id="openrouter", model="vendor/active")


def _outcome(stage: int = 0, at: tuple[int, ...] = (0,), error: str = "") -> dict:
    record = decisions.outcome("structured", "openrouter", "vendor/active",
                               [ItemResult({"over": Answer(True)}, backend="structured")],
                               error=error)
    tail = {k: record.pop(k) for k in ("items", "error") if k in record}
    return {**record, "stage": stage, "at": list(at), **tail}


_MESSAGES = [{"role": "system", "content": "Decide."}, {"role": "user", "content": "Item 0"}]


def _scoped(cid, sid, body, **kwargs):
    """Run `body(scope)` inside a capture scope; whatever it raises comes out."""
    async def run():
        async with decision_capture.capturing(cid, sid, "scene-break", **kwargs) as scope:
            await body(scope)
    asyncio.run(run())


def test_a_failed_decision_is_filed_with_each_calls_kind_and_status(client):
    """Test 5: an `LLMError` no item answered files the entry, and the error
    still reaches the caller unchanged."""
    cid, sid = _scene(client, posts=0)
    error = LLMError("timeout", "Post 9 timed out at sk-...abcd", status=None)

    async def body(scope):
        await scope.hook().settled(_MESSAGES, _outcome(error="timeout: x"), _target(), error)
        raise error

    with pytest.raises(LLMError) as raised:
        _scoped(cid, sid, body)
    assert raised.value is error
    (call,) = _envelope(_entry(cid, sid))["calls"]
    assert (call["error_kind"], call["error_status"]) == ("timeout", None)
    assert b"sk-...abcd" not in _payload_bytes(cid)


def test_a_refused_request_is_filed_with_its_own_sentence(client):
    cid, sid = _scene(client, posts=0)

    async def body(scope):
        scope.hook()
        raise decisions.DecideRequestError("two refs read as one once normalised")

    with pytest.raises(decisions.DecideRequestError):
        _scoped(cid, sid, body)
    entry = _entry(cid, sid)
    assert [s["id"] for s in entry["sections"]] == ["decision"]
    envelope = _envelope(entry)
    assert envelope["calls"] == []
    assert envelope["error"] == "invalid_request: two refs read as one once normalised"


@pytest.mark.parametrize("raised", [asyncio.CancelledError, scenes_routes.Abandoned],
                         ids=["cancelled", "abandoned"])
def test_a_cancelled_or_abandoned_scope_files_nothing(client, raised):
    cid, sid = _scene(client, posts=0)

    async def body(scope):
        await scope.hook()(_MESSAGES, _outcome(), _target())
        raise raised()

    with pytest.raises(raised):
        _scoped(cid, sid, body)
    assert store.prompt_log.list_entries(cid, sid) == []


def test_a_closed_review_files_nothing_though_its_loop_swallowed_it(client):
    """Voice drift catches every exception per NPC, so its scope exits
    normally after a close: the review's own check is what stops the filing."""
    cid, sid = _scene(client, posts=0)

    async def gone():
        return True

    async def body(scope):
        await scope.hook("aese")(_MESSAGES, _outcome(), _target())

    _scoped(cid, sid, body, abandoned=gone)
    assert store.prompt_log.list_entries(cid, sid) == []


def test_a_scope_that_decided_nothing_files_nothing(client):
    cid, sid = _scene(client, posts=0)

    async def body(scope):
        assert scope.hook() is not None

    _scoped(cid, sid, body)
    assert store.prompt_log.list_entries(cid, sid) == []


def test_a_site_fence_that_fails_files_nothing_and_stamps_nothing(client):
    """Test 10, both halves: a capture bumps the token once, after its write;
    a skipped one bumps nothing."""
    cid, sid = _scene(client, posts=0)

    async def body(scope):
        await scope.hook()(_MESSAGES, _outcome(), _target())

    before = store.revision.current(cid)
    _scoped(cid, sid, body, fence=lambda: False)
    assert store.prompt_log.list_entries(cid, sid) == []
    assert store.revision.current(cid) == before
    stamped: list = []
    real = store.revision.bump

    def bump(c):
        stamped.append(len(store.prompt_log.list_entries(cid, sid)))
        return real(c)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store.revision, "bump", bump)
        _scoped(cid, sid, body, fence=lambda: True)
    assert stamped == [1]         # once, and the entry was already written


def test_parts_and_notes_are_filed_with_their_calls(client):
    cid, sid = _scene(client, posts=0)

    async def body(scope):
        await scope.hook("aese")(_MESSAGES, _outcome(), _target())
        await scope.hook("mara")(_MESSAGES, _outcome(at=(1,)), _target())
        scope.note("mara", "draw", {"u": 0.25, "selected": "yes", "mass": 0.7})

    _scoped(cid, sid, body)
    envelope = _envelope(_entry(cid, sid))
    assert [c["part"] for c in envelope["calls"]] == ["aese", "mara"]
    assert envelope["notes"] == {"mara": {"draw": {"u": 0.25, "selected": "yes",
                                                   "mass": 0.7}}}


# ---- size ----

def test_a_long_scope_keeps_four_calls_in_full(client):
    """Test 8: a fifth call's messages are dropped as it is appended, and the
    total is the kept calls' alone."""
    cid, sid = _scene(client, posts=0)
    held: list = []

    async def body(scope):
        for k in range(6):
            await scope.hook()(_MESSAGES, _outcome(at=(k,)), _target())
        held.extend(scope.calls)

    _scoped(cid, sid, body)
    assert [h.messages is None for h in held] == [False] * 4 + [True] * 2
    entry = _entry(cid, sid)
    envelope = _envelope(entry)
    assert envelope["elided_calls"] == 2 and len(envelope["calls"]) == 6
    assert [c["at"] for c in envelope["calls"]] == [[k] for k in range(6)]
    kept = [s for s in entry["sections"] if s["id"] != "decision"]
    assert {s["id"].split("_", 1)[0] for s in kept} == {"c0", "c1", "c2", "c3"}
    assert entry["total_tokens"] == sum(s["tokens"] for s in kept)


def test_an_outcome_past_the_cap_drops_whole_calls_from_the_end(client, monkeypatch):
    cid, sid = _scene(client, posts=0)
    monkeypatch.setattr(decision_capture, "MAX_OUTCOME_CHARS", 1200)

    async def body(scope):
        for k in range(6):
            await scope.hook()(_MESSAGES, _outcome(at=(k,)), _target())

    _scoped(cid, sid, body)
    text = _entry(cid, sid)["sections"][-1]["text"]
    envelope = json.loads(text)
    assert len(text) <= 1200 and envelope["truncated"] is True
    assert [c["at"] for c in envelope["calls"]] == [[k] for k in range(len(envelope["calls"]))]
    assert 0 < len(envelope["calls"]) < 6


def test_notes_count_against_the_cap(client, monkeypatch):
    cid, sid = _scene(client, posts=0)
    monkeypatch.setattr(decision_capture, "MAX_OUTCOME_CHARS", 1200)

    async def body(scope):
        await scope.hook()(_MESSAGES, _outcome(), _target())
        await scope.hook()(_MESSAGES, _outcome(at=(1,)), _target())
        scope.note("", "legal_set", "x" * 700)

    _scoped(cid, sid, body)
    envelope = _envelope(_entry(cid, sid))
    assert envelope["truncated"] is True and len(envelope["calls"]) < 2


# ---- retention pools ----

def _breakdown() -> dict:
    return {"sections": [], "total_tokens": 0, "dropped_tokens": 0, "budget_tokens": 0}


def test_decisions_and_turns_are_retained_apart(client):
    """Test 7 (the scene pools): each keeps `prompt_log_depth` entries, and
    neither evicts the other."""
    cid, sid = _scene(client, posts=0)
    store.config.write_config(prompt_log_depth="3")
    turns = [store.prompt_log.record(cid, sid, "chat", _breakdown()) for _ in range(2)]
    checks = [store.prompt_log.record(cid, sid, "scene-break", _breakdown(),
                                      operation="decide") for _ in range(4)]
    rows = store.prompt_log.list_entries(cid, sid)
    assert {r["id"] for r in rows} == set(turns) | set(checks[1:])
    turns += [store.prompt_log.record(cid, sid, "chat", _breakdown()) for _ in range(2)]
    rows = store.prompt_log.list_entries(cid, sid)
    assert {r["id"] for r in rows} == set(turns[1:]) | set(checks[1:])
    assert store.prompt_log.read_entry(cid, checks[0]) is None
    assert store.prompt_log.read_entry(cid, turns[0]) is None


def test_a_non_string_operation_is_not_listed(client):
    cid, sid = _scene(client, posts=0)
    eid = store.prompt_log.record(cid, sid, "chat", _breakdown())
    path = store.campaigns.campaign_root(cid) / "prompts" / "index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    index["entries"][0]["operation"] = {"oops": 1}
    path.write_text(json.dumps(index), encoding="utf-8")
    assert store.prompt_log.list_entries(cid, sid) == []
    assert eid is not None


# ---- the diff route ----

def test_a_decision_entry_is_not_comparable_with_the_live_prompt(client):
    _use(client, FakeLLM([[NO]]))
    cid, sid = _scene(client)
    _ask(client, cid, sid)
    (row,) = _decisions(cid, sid)
    base = f"/api/campaigns/{cid}/scenes/{sid}/prompts/{row['id']}/diff"
    r = client.get(base)
    assert r.status_code == 409 and r.json()["kind"] == "not_comparable"
    # Against another frozen entry it is a comparison like any other.
    assert client.get(base, params={"against": row["id"]}).status_code == 200


def test_a_legacy_speaker_entry_is_not_comparable_either(client):
    cid, sid = _scene(client, posts=0)
    eid = store.prompt_log.record(cid, sid, "response-selector", _breakdown())
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts/{eid}/diff")
    assert r.status_code == 409 and r.json()["kind"] == "not_comparable"


# ---- campaign level: the reconciliation sweep (01b-S3) ----

from .llm_fakes import from_entries  # noqa: E402
from .test_continuity_reconcile_routes import (  # noqa: E402 - the sweep's own fixtures
    _campaign,
    _duplicate,
    _held,
    _install,
    _live,
    _refresh,
    _reply,
    _settled,
    _threads,
)
from .test_continuity_reconcile_routes import _entry as _sweep_entry  # noqa: E402
from .test_continuity_reconcile_routes import _key as _sweep_key  # noqa: E402


def _swept(client) -> tuple[str, str]:
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _sweep_key(client)
    _install(client, from_entries([_sweep_entry(_reply(_duplicate()))]))
    return cid, sid


def test_a_sweep_files_one_campaign_level_entry(client):
    cid, sid = _swept(client)
    run = _settled(client, cid, _refresh(client, cid))
    assert run["state"] == "landed" and run["result"]["llm"] == "ok", run
    (row,) = client.get(f"/api/campaigns/{cid}/prompts").json()["entries"]
    assert (row["task"], row["operation"], row["scene"]) == (
        "continuity-reconcile", "decide", store.prompt_log.NO_SCENE)
    entry = client.get(f"/api/campaigns/{cid}/prompts/{row['id']}").json()
    (call,) = _envelope(entry)["calls"]
    assert (call["stage"], call["at"]) == (0, [0])
    assert call["items"][0]["rationale"]
    # No scene's list holds it, and no scene's route can read it.
    assert store.prompt_log.list_entries(cid, sid) == []
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts/{row['id']}")
    assert r.status_code == 404


def test_the_campaign_routes_list_only_campaign_level_entries(client):
    cid, sid = _scene(client, posts=0)
    turn = store.prompt_log.record(cid, sid, "chat", _breakdown())
    check = store.prompt_log.record(cid, sid, "scene-break", _breakdown(), operation="decide")
    sweep = store.prompt_log.record(cid, "", "continuity-reconcile", _breakdown(),
                                    operation="decide")
    rows = client.get(f"/api/campaigns/{cid}/prompts").json()["entries"]
    assert [r["id"] for r in rows] == [sweep]
    for eid in (turn, check):
        assert client.get(f"/api/campaigns/{cid}/prompts/{eid}").status_code == 404
    assert client.get("/api/campaigns/nope/prompts").status_code == 404


def test_a_forgotten_sweep_files_nothing(client):
    """Test 6: the sweep fences itself on its run -- a run forgotten by a
    campaign delete must not file into a same-named replacement."""
    _wid, cid, sid = _campaign(client)
    _threads(cid, sid)
    _sweep_key(client)
    held = _install(client, _held(_reply(_duplicate())))
    assert _refresh(client, cid).status_code == 202
    held.await_held()
    run = _live(client, cid)
    run.forgotten = True
    held.release()
    # Forgotten, the run answers no route: wait on it directly.
    deadline = time.monotonic() + 10
    while run.state == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert run.state != "running"
    assert store.prompt_log.list_entries(cid, store.prompt_log.NO_SCENE) == []


def test_campaign_decisions_are_a_pool_of_their_own(client):
    """Test 7 (the campaign pool): sweeps evict only sweeps, and neither turns
    nor scene checks evict a sweep."""
    cid, sid = _scene(client, posts=0)
    store.config.write_config(prompt_log_depth="3")

    def sweeps(n):
        return [store.prompt_log.record(cid, "", "continuity-reconcile", _breakdown(),
                                        operation="decide") for _ in range(n)]

    def checks(n):
        return [store.prompt_log.record(cid, sid, "scene-break", _breakdown(),
                                        operation="decide") for _ in range(n)]

    turn = store.prompt_log.record(cid, sid, "chat", _breakdown())
    check = checks(1)
    swept = sweeps(4)
    assert {r["id"] for r in store.prompt_log.list_entries(cid, "")} == set(swept[1:])
    assert {r["id"] for r in store.prompt_log.list_entries(cid, sid)} == {turn, *check}
    check += checks(3)
    assert {r["id"] for r in store.prompt_log.list_entries(cid, "")} == set(swept[1:])
    assert {r["id"] for r in store.prompt_log.list_entries(cid, sid)} == {turn, *check[1:]}
