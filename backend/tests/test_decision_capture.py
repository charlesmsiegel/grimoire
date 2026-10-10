"""Decision capture at every decide site (roadmap 01b).

Each decide site opens one capture scope (`routes.decision_capture`): the
per-call hook holds each settled call in memory, and the scope files ONE
prompt-log entry once `decide` has returned or failed -- the requests as
sent, every call's record with its stage and batch indices, never a
provider's own words -- into a retention pool of its own, fenced on the
scene's strict identity.

The helper is driven directly over a real `inference.decide` (the decide
tests' format-2 store and fakes); the sites through their real routes, with
the fixtures their own suites use.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

import grimoire.store as store
from grimoire import decisions, inference, llm, routes
from grimoire.decisions import Item
from grimoire.llm_errors import LLMError
from grimoire.routes import character_turns, decision_capture
from grimoire.routes import scenes as scenes_routes

from . import inference_baseline as base
from . import review_runs
from .llm_fakes import FakeLLM, decision_reply
from .test_absorb_identity import _llm, _one_per_chunk, _seed_ledger, _two_rows
from .test_absorb_identity import _row as _identity_row
from .test_character_turns import _chat, seed
from .test_inference_decide import _item, _resolved, _store
from .test_inference_decide_native import _Endpoint, _native_resolution, _yes
from .test_routes import _DOSSIER, _EXTRACTION, _absorb_script, _verdict, _voice_scene
from .test_scene_break_routes import YES, _judge, _key, _mid_flight, _post, _use
from .test_scene_break_routes import _scene as _break_scene

DECIDE = "decide"


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.0)


@pytest.fixture
def fresh(tmp_path):
    """The decide tests' format-2 store (the Decision role on `openrouter`,
    `spare` behind it), and a campaign with one scene to capture into."""
    with base.client_at(tmp_path) as c:
        yield c


@pytest.fixture
def absorbed(client):
    """(cid, s0, sid): `test_absorb_identity`'s scene -- a key, a world with
    Mara, an earlier scene `s0` to seed records in, and "Saltmarch" with two
    posts to absorb."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-active"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara", "version_name": "main"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    s0 = store.scenes.create_scene(cid, "Saltmarch docks")
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "Something happened at the docks.")
    store.scenes.append_message(cid, sid, "assistant", "The keeper said nothing.")
    return cid, s0, sid


def _campaign(client, *, fallback: bool = True) -> tuple[str, str]:
    _store(client, fallback=fallback)
    cid = store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))
    return cid, store.scenes.create_scene(cid, "Mara")


def _run(cid, sid, body, *, task="scene-break", **scope):
    async def go():
        async with decision_capture.capturing(cid, sid, task, **scope) as s:
            return await body(s)
    return asyncio.run(go())


def _decide_in(cid, sid, fake, items, *, resolved=None, part="", **scope):
    async def body(s):
        return await inference.decide(
            "scene-break", items, client=fake, resolved=resolved or _resolved(),
            campaign=cid, scene=sid, capture=s.hook(part))
    return _run(cid, sid, body, **scope)


def _entries(cid, sid, task=None):
    return [e for e in store.prompt_log.list_entries(cid, sid)
            if task is None or e["task"] == task]


def _only(cid, sid, task=None) -> tuple[dict, dict]:
    (row,) = _entries(cid, sid, task)
    payload = store.prompt_log.read_entry(cid, row["id"], scene=sid)
    assert payload is not None
    return row, payload


def _envelope(payload: dict) -> dict:
    last = payload["sections"][-1]
    assert (last["id"], last["tokens"]) == (decision_capture.OUTCOME_SECTION_ID, 0)
    return json.loads(last["text"])


def _payload_bytes(cid: str, eid: str) -> bytes:
    return (store.campaigns.campaign_root(cid) / "prompts" / f"{eid}.json").read_bytes()


def _secrets() -> list[str]:
    """Every key and base URL a connection holds."""
    found = []
    for conn in store.llm_connections.list_connections():
        raw = store.llm_connections.read_connection_raw(conn["id"])
        found += [raw.get(k) for k in ("api_key", "base_url") if raw.get(k)]
    assert found
    return found


def _warnings(contains: str = "") -> list[dict]:
    return [r for r in store.logs.scan(level="warning", contains=contains)
            if r.get("level") == "warning"]


def _errors() -> list[dict]:
    return list(store.logs.scan(level="error"))


# ---- §6 test 1: one entry per scope, at each site ----


def test_a_scope_files_one_entry_with_the_outcome_last(fresh):
    cid, sid = _campaign(fresh)
    fake = FakeLLM([[decision_reply({"over": True})]])
    _decide_in(cid, sid, fake, [_item()])
    row, payload = _only(cid, sid)
    assert (row["task"], row["operation"], payload["operation"]) == (
        "scene-break", DECIDE, DECIDE)
    assert [s["id"] for s in payload["sections"]] == ["message_0", "message_1", "decision"]
    body = _envelope(payload)
    assert body["task"] == "scene-break"
    (call,) = body["calls"]
    assert (call["part"], call["stage"], call["at"], call["mode"]) == (
        "", 0, [0], "structured")
    assert payload["total_tokens"] == sum(s["tokens"] for s in payload["sections"]) > 0
    assert row["model"] == "vendor/active"


def test_a_two_chunk_check_is_one_entry_with_both_calls(fresh):
    cid, sid = _campaign(fresh)
    items = [_item(f"Mara counts to {n}.") for n in range(9)]
    fake = FakeLLM([[decision_reply(*[{"over": True}] * 8)],
                    [decision_reply({"over": False})]])
    _decide_in(cid, sid, fake, items)
    _row, payload = _only(cid, sid)
    ids = [s["id"] for s in payload["sections"]]
    assert ids == ["c0_message_0", "c0_message_1", "c1_message_0", "c1_message_1",
                   "decision"]
    assert payload["sections"][2]["label"] == "call 2 · system"
    calls = _envelope(payload)["calls"]
    assert [c["at"] for c in calls] == [list(range(8)), [8]]


def test_the_speaker_pick_files_one_decision_entry(client):
    cid, sid = seed(client)
    fake = FakeLLM([[decision_reply({"next": "characters:winifred"})],
                    ['Winifred answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    _chat(client, cid, sid, "Winifred, the lamps.")
    row, payload = _only(cid, sid, "response-selector")
    assert row["operation"] == DECIDE
    assert [c["mode"] for c in _envelope(payload)["calls"]] == ["structured"]


def test_the_scene_break_check_files_one_decision_entry(client):
    cid, sid = _break_scene(client, posts=4)
    _key(client)
    _use(client, _judge(YES))
    assert _post(client, cid, sid, force="true")["asked"] is True
    # The verdict only: the title drafted after it is a generation, uncaptured.
    row, payload = _only(cid, sid, "scene-break")
    assert row["operation"] == DECIDE
    body = _envelope(payload)
    (call,) = body["calls"]
    assert call["items"][0]["answers"]["over"]["answer"] is True
    assert [e["task"] for e in _entries(cid, sid)] == ["scene-break"]


def test_a_voice_drift_phase_is_one_entry_parted_by_npc(client):
    cid, sid = _voice_scene(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: _absorb_script(
        _EXTRACTION, _DOSSIER, _verdict("drift", "She hedged."))
    body = review_runs.absorb(client, cid, sid).json()
    assert body["voice"]["checked"] == ["aese"]
    row, payload = _only(cid, sid, "voice-drift")
    assert row["operation"] == DECIDE
    (call,) = _envelope(payload)["calls"]
    assert call["part"] == "aese"


def test_a_two_chunk_identity_check_is_one_entry(client, absorbed, monkeypatch):
    cid, s0, sid = absorbed
    _seed_ledger(cid, s0)
    _one_per_chunk(monkeypatch)
    _llm(client, _two_rows(), decision_reply(_identity_row("new")))
    body = review_runs.absorb(client, cid, sid)
    assert body.status_code == 200, body.text
    row, payload = _only(cid, sid, "continuity-identity")
    assert row["operation"] == DECIDE
    assert [s["id"] for s in payload["sections"]][-1] == "decision"
    assert {s["id"].split("_", 1)[0] for s in payload["sections"][:-1]} == {"c0", "c1"}
    assert [c["at"] for c in _envelope(payload)["calls"]] == [[0], [1]]


# ---- §6 test 2: a fallback stage after a native failure ----


def test_a_native_failure_and_its_fallback_answer_are_one_entry(fresh):
    resolved = _native_resolution(fresh, fallback=True)
    cid = store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))
    sid = store.scenes.create_scene(cid, "Mara")
    item = _item("Winifred turns page 0.")
    fake = _Endpoint([[decision_reply({"over": False})]],
                     {item.context: LLMError("network", "connection reset")})
    _decide_in(cid, sid, fake, [item], resolved=resolved)
    _row, payload = _only(cid, sid)
    failed, answered = _envelope(payload)["calls"]
    assert (failed["stage"], failed["mode"], failed["error_kind"]) == (0, "native", "network")
    assert "error" not in failed
    assert (answered["stage"], answered["mode"], answered["at"]) == (1, "structured", [0])
    assert "error_kind" not in answered
    for secret in _secrets():
        assert secret.encode() not in _payload_bytes(cid, _row["id"])


# ---- §6 test 3: capture off ----


def test_capture_off_builds_nothing_and_files_nothing(fresh, monkeypatch):
    resolved = _native_resolution(fresh, fallback=False)
    cid = store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))
    sid = store.scenes.create_scene(cid, "Mara")
    store.config.write_config(prompt_log_depth="0")

    def built(*_a, **_k):
        raise AssertionError("a native body was built with capture off")

    monkeypatch.setattr(inference, "_native_request", built)
    hooks = []

    async def body(s):
        hooks.append(s.hook())
        return await inference.decide("scene-break", [_item()], client=FakeLLM(
            [["unused"]], decisions=[_yes()]), resolved=resolved, capture=s.hook())

    _run(cid, sid, body)
    assert hooks == [None]
    assert _entries(cid, sid) == []


# ---- §6 test 4 and 12: a filer that raises costs only the capture ----


def test_a_raising_filer_costs_the_capture_and_nothing_else(fresh, monkeypatch):
    cid, sid = _campaign(fresh)

    def broken(*_a, **_k):
        raise RuntimeError("Seraphine's prompt text must not reach the log")

    monkeypatch.setattr(store.prompt_log, "record", broken)
    fake = FakeLLM([[decision_reply({"over": True})]])
    got = _decide_in(cid, sid, fake, [_item()])
    assert got.items[0].answers["over"].answer is True
    assert store.errors.summary(campaign=cid)["rows"] == []
    assert _errors() == []
    (line,) = _warnings("scene-break")
    text = json.dumps(line)
    assert "RuntimeError" in text and "Seraphine" not in text


# ---- §6 test 5: what crosses the scope ----


def _target():
    return _resolved().attempts[0].target


def _held(s, part=""):
    """One settled call handed to the hook by hand."""
    return s.hook(part)([{"role": "user", "content": "Mara waits."}],
                        {"mode": "structured", "provider": "openrouter",
                         "model": "vendor/active", "items": []}, _target())


def test_a_cancelled_scope_files_nothing(fresh):
    cid, sid = _campaign(fresh)

    async def body(s):
        await _held(s)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        _run(cid, sid, body)
    assert _entries(cid, sid) == []


def test_an_abandoned_scope_files_nothing(fresh):
    cid, sid = _campaign(fresh)

    async def body(s):
        await _held(s)
        raise scenes_routes.Abandoned

    with pytest.raises(scenes_routes.Abandoned):
        _run(cid, sid, body)
    assert _entries(cid, sid) == []


def test_a_scope_whose_review_was_closed_files_nothing(fresh):
    """A loop that swallowed `Abandoned` exits normally: the review's own
    check is what keeps the scope from filing."""
    cid, sid = _campaign(fresh)

    async def closed():
        return True

    async def body(s):
        await _held(s, "mara")

    _run(cid, sid, body, task="voice-drift", abandoned=closed)
    assert _entries(cid, sid) == []


def test_a_voice_phase_that_swallowed_abandoned_files_nothing(client, monkeypatch):
    cid, sid = _voice_scene(client)
    resolved = routes.common.require_inference("voice-drift", cid, operation="decide")

    async def decide(task, items, *, capture=None, **_kw):
        await capture([{"role": "user", "content": "Aese speaks."}],
                      {"mode": "structured", "provider": "openrouter", "model": "m",
                       "items": []}, resolved.chain.primary)
        raise scenes_routes.Abandoned

    async def closed():
        return True

    monkeypatch.setattr(scenes_routes.operations, "decide", decide)
    _edits, block = asyncio.run(scenes_routes._stage_voice_drift(
        cid, sid, "Aese: Hello.", object(), resolved, scenes_routes._Budget(0),
        abandoned=closed))
    assert block["failed"] and block["failed"][0]["id"] == "aese"
    assert _entries(cid, sid, "voice-drift") == []


def test_a_decision_nobody_answered_is_filed_and_still_raised(fresh):
    cid, sid = _campaign(fresh, fallback=False)
    fake = FakeLLM([[""]], error=LLMError("rate_limit", "slow down", status=429))
    with pytest.raises(LLMError) as exc:
        _decide_in(cid, sid, fake, [_item()])
    assert exc.value.kind == "rate_limit"
    _row, payload = _only(cid, sid)
    (call,) = _envelope(payload)["calls"]
    assert (call["error_kind"], call["error_status"]) == ("rate_limit", 429)
    assert "error" not in call


def test_a_refused_request_files_its_own_sentence_and_no_call(fresh):
    cid, sid = _campaign(fresh)
    with pytest.raises(decisions.DecideRequestError):
        _decide_in(cid, sid, FakeLLM([["unused"]]), [Item("Mara waits.", ())])
    _row, payload = _only(cid, sid)
    assert [s["id"] for s in payload["sections"]] == ["decision"]
    body = _envelope(payload)
    assert body["calls"] == []
    assert body["error"] == "invalid_request: item 0 asks no questions"


def test_a_scope_that_asked_nothing_files_nothing(fresh):
    cid, sid = _campaign(fresh)

    async def body(_s):
        return None

    _run(cid, sid, body)
    assert _entries(cid, sid) == []


# ---- §6 test 6: the fence ----


def test_a_scene_recreated_under_its_id_mid_check_gets_no_entry(client):
    cid, sid = _break_scene(client, posts=4)
    _key(client)
    title = store.scenes.read_scene_meta(cid, sid)["title"]

    def recreate():
        store.scenes.delete_scene(cid, sid)
        assert store.scenes.create_scene(cid, title) == sid

    _use(client, _mid_flight(_judge(YES), 1, recreate))
    _post(client, cid, sid, force="true")
    assert _entries(cid, sid, "scene-break") == []


def test_an_identity_less_scene_captures_nothing_and_is_not_written(fresh):
    cid, sid = _campaign(fresh)
    path = store.scenes.paths._scene_path(cid, sid)
    text = path.read_text(encoding="utf-8")
    stripped = "".join(line for line in text.splitlines(keepends=True)
                       if not line.startswith("identity:"))
    assert stripped != text
    path.write_text(stripped, encoding="utf-8")
    assert store.scenes.scene_identity_strict(cid, sid) is None
    fake = FakeLLM([[decision_reply({"over": True})]])
    _decide_in(cid, sid, fake, [_item()])
    assert _entries(cid, sid) == []
    assert path.read_text(encoding="utf-8") == stripped


def test_an_identity_read_that_raises_at_filing_files_nothing(fresh, monkeypatch):
    cid, sid = _campaign(fresh)
    real = store.scenes.scene_identity_strict
    reads = []

    def flaky(c, s):
        reads.append(s)
        if len(reads) > 1:
            raise OSError("the scene file is locked")
        return real(c, s)

    monkeypatch.setattr(store.scenes, "scene_identity_strict", flaky)
    _decide_in(cid, sid, FakeLLM([[decision_reply({"over": True})]]), [_item()])
    assert len(reads) == 2
    assert _entries(cid, sid) == []


def test_a_failing_site_fence_files_nothing(fresh):
    cid, sid = _campaign(fresh)
    before = store.revision.current(cid)
    _decide_in(cid, sid, FakeLLM([[decision_reply({"over": True})]]), [_item()],
               fence=lambda: False)
    assert _entries(cid, sid) == []
    assert store.revision.current(cid) == before


def test_a_contended_campaign_lock_files_nothing_and_does_not_wait(fresh):
    cid, sid = _campaign(fresh)
    held, release = threading.Event(), threading.Event()

    def holder():
        with store.locks.campaign_lock(cid):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(10)
        started = time.monotonic()

        async def body(s):
            await _held(s)

        _run(cid, sid, body)
        waited = time.monotonic() - started
    finally:
        release.set()
        thread.join(10)
    assert _entries(cid, sid) == []
    assert waited < 5


# ---- §6 test 7: the scene pools ----


def test_scene_decisions_evict_only_scene_decisions(fresh):
    cid, sid = _campaign(fresh)
    store.config.write_config(prompt_log_depth="3")
    breakdown = {"sections": [], "total_tokens": 0, "dropped_tokens": 0, "budget_tokens": 0}
    turns = [store.prompt_log.record(cid, sid, "chat", breakdown, model="m")
             for _ in range(3)]

    async def body(s):
        await _held(s)

    for _ in range(4):
        _run(cid, sid, body)
    rows = _entries(cid, sid)
    assert sorted(e["id"] for e in rows if "operation" not in e) == sorted(turns)
    assert len([e for e in rows if e.get("operation") == DECIDE]) == 3


# ---- §6 test 8: size ----


def test_six_calls_keep_four_in_full_and_hold_no_fifth_prompt(fresh):
    cid, sid = _campaign(fresh)
    kept = []

    async def body(s):
        for _ in range(6):
            await _held(s)
        kept.extend(h.messages for h in s.calls)

    _run(cid, sid, body)
    assert [m is not None for m in kept] == [True] * 4 + [False] * 2
    _row, payload = _only(cid, sid)
    messages = [s for s in payload["sections"] if s["id"] != "decision"]
    assert {s["id"].split("_", 1)[0] for s in messages} == {"c0", "c1", "c2", "c3"}
    body_ = _envelope(payload)
    assert (len(body_["calls"]), body_["elided_calls"]) == (6, 2)


def test_an_outcome_over_the_cap_drops_whole_records_from_the_end(fresh, monkeypatch):
    cid, sid = _campaign(fresh)
    monkeypatch.setattr(decision_capture, "MAX_OUTCOME_CHARS", 900)

    async def body(s):
        for n in range(6):
            await _held(s, f"npc-{n}")

    _run(cid, sid, body)
    _row, payload = _only(cid, sid)
    text = payload["sections"][-1]["text"]
    assert len(text) <= 900
    body_ = json.loads(text)
    assert body_["truncated"] is True
    parts = [c["part"] for c in body_["calls"]]
    assert 0 < len(parts) < 6 and parts == [f"npc-{n}" for n in range(len(parts))]


def test_notes_count_against_the_cap(fresh, monkeypatch):
    cid, sid = _campaign(fresh)
    monkeypatch.setattr(decision_capture, "MAX_OUTCOME_CHARS", 900)

    async def body(s):
        await _held(s)
        await _held(s)
        s.note("", "draw", {"u": 0.25, "selected": "mara", "mass": "x" * 500})

    _run(cid, sid, body)
    _row, payload = _only(cid, sid)
    body_ = _envelope(payload)
    assert body_["notes"][""]["draw"]["selected"] == "mara"
    assert body_["truncated"] is True and len(body_["calls"]) < 2


# ---- §6 test 9: the diff route ----


def _decision_entry(client) -> tuple[str, str, str]:
    cid, sid = _break_scene(client, posts=4)
    _key(client)
    _use(client, _judge(YES))
    _post(client, cid, sid, force="true")
    (row,) = _entries(cid, sid, "scene-break")
    return cid, sid, row["id"]


def _diff(client, cid, sid, eid, against="live"):
    return client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts/{eid}/diff",
                      params={"against": against})


def test_a_decision_entry_is_not_comparable_with_the_live_preview(client):
    cid, sid, eid = _decision_entry(client)
    got = _diff(client, cid, sid, eid)
    assert (got.status_code, got.json()) == (409, {"kind": "not_comparable"})


def test_a_legacy_speaker_entry_is_not_comparable_with_the_live_preview(client):
    cid, sid = _break_scene(client)
    breakdown = {"sections": [], "total_tokens": 0, "dropped_tokens": 0, "budget_tokens": 0}
    eid = store.prompt_log.record(cid, sid, "response-selector", breakdown, model="m")
    assert "operation" not in store.prompt_log.read_entry(cid, eid)
    got = _diff(client, cid, sid, eid)
    assert (got.status_code, got.json()) == (409, {"kind": "not_comparable"})


def test_two_decisions_of_one_task_still_compare(client):
    cid, sid, first = _decision_entry(client)
    _posts = store.scenes.append_message
    for i in range(4):
        _posts(cid, sid, "user" if i % 2 == 0 else "assistant", f"More {i}.")
    _use(client, _judge(YES))
    _post(client, cid, sid, force="true")
    second = next(e["id"] for e in _entries(cid, sid, "scene-break") if e["id"] != first)
    got = _diff(client, cid, sid, first, against=second)
    assert got.status_code == 200, got.text


# ---- §6 test 10: the write token ----


def test_a_capture_stamps_the_token_once_after_its_write(fresh, monkeypatch):
    cid, sid = _campaign(fresh)
    order = []
    record, bump = store.prompt_log.record, store.revision.bump

    def recording(*a, **k):
        order.append("record")
        return record(*a, **k)

    def bumping(c):
        order.append("bump")
        return bump(c)

    monkeypatch.setattr(store.prompt_log, "record", recording)
    monkeypatch.setattr(store.revision, "bump", bumping)
    _decide_in(cid, sid, FakeLLM([[decision_reply({"over": True})]]), [_item()])
    assert order == ["record", "bump"]


# ---- §6 test 12: privacy ----


def test_no_provider_text_reaches_the_payload_or_the_log(fresh):
    cid, sid = _campaign(fresh, fallback=False)
    masked = "sk-...abcd echoed by Winifred's provider"
    fake = FakeLLM([[""]], error=LLMError("bad_response", masked, status=400))
    with pytest.raises(LLMError):
        _decide_in(cid, sid, fake, [_item()])
    row, _payload = _only(cid, sid)
    data = _payload_bytes(cid, row["id"])
    assert b"sk-...abcd" not in data and b"Winifred's provider" not in data
    for secret in _secrets():
        assert secret.encode() not in data
    # The meter files the failure as it always has (#156); the capture adds
    # no line of its own to the log, so none can carry the provider's words.
    assert not [r for r in store.logs.scan(level="debug")
                if "decision_capture" in str(r.get("module", ""))]


def test_the_outcome_section_id_is_still_pinned():
    assert decision_capture.OUTCOME_SECTION_ID == "decision"
    assert character_turns.OUTCOME_SECTION_ID == decision_capture.OUTCOME_SECTION_ID
