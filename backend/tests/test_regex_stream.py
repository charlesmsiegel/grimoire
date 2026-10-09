"""Display rules while a reply streams (regex spec 5.3, "Streaming").

`DisplayStream` turns the raw text a turn has produced into `keep`/`tail`
frames: the client truncates to `keep` and appends `tail`, which is what lets a
closing `</think>` retract text already on screen. The route tests drive both
emitters -- the per-contribution one and the legacy `_fence_stream` -- through
the real SSE body.
"""

from __future__ import annotations

import json

import pytest

from grimoire import routes, store
from grimoire.routes import character_turns, streaming
from grimoire.store.regex import rules, stream
from tests.inference_fixtures import put_settings
from tests.llm_fakes import FailingOpenRouter, FakeLLM

pytestmark = pytest.mark.upgraded_birth


def _primary(client, model="primary"):
    """The seeded `openrouter` provider keyed, and the Primary role on it at
    `model` (format 2: a provider names no model of its own)."""
    got = client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    assert got.status_code == 200, got.text
    put_settings(client, {"roles": {"primary": {"selection": {
        "provider": "openrouter", "model": model}}}})


THINK = {"name": "Strip thinking", "pattern": r"<think>[\s\S]*?</think>", "replacement": ""}


def entry(rule: dict, *, off: bool = False) -> dict:
    return {"level": "global", "rule": rules.normalise(rule), "off": off, "source": ""}


class Clock:
    """A clock that moves only when told to."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def apply_frame(buffer: str, frame: dict) -> str:
    return buffer[: frame["keep"]] + frame["tail"]


# ---- DisplayStream -----------------------------------------------------------

def test_think_block_retracted():
    clock = Clock()
    ds = stream.DisplayStream([entry(THINK)], clock=clock)
    buffer, history, frames = "", [], []
    for delta in ("<think>a", "b</think>", "Hi"):
        clock.now += 0.2
        frame = ds.feed(delta)
        if frame:
            frames.append((len(buffer), frame))
            buffer = apply_frame(buffer, frame)
        history.append(buffer)
    final = ds.finish()
    if final:
        frames.append((len(buffer), final))
        buffer = apply_frame(buffer, final)

    # Until the block closes there is nothing to match, so it is on screen...
    assert history[0] == "<think>a"
    assert history[1] == ""          # ...and the close pulls all of it back
    assert buffer == "Hi"
    assert any(frame["keep"] < shown for shown, frame in frames)


def test_throttle():
    clock = Clock()
    ds = stream.DisplayStream([entry(THINK)], clock=clock)
    frames = []
    for i in range(30):          # 0.3 s of deltas, 0.01 s apart
        clock.now = i * 0.01
        frame = ds.feed("x")
        if frame:
            frames.append(frame)
    assert len(frames) <= 3
    buffer = ""
    for frame in frames:
        buffer = apply_frame(buffer, frame)
    assert len(buffer) < 30      # something was held back...
    final = ds.finish()
    assert final is not None     # ...and finish sends the rest
    assert apply_frame(buffer, final) == "x" * 30
    assert ds.finish() is None


def test_first_feed_is_not_held_back():
    ds = stream.DisplayStream([entry(THINK)], clock=Clock())
    assert ds.feed("Hello") == {"keep": 0, "tail": "Hello"}


def test_inactive_when_no_display_rules():
    assert stream.DisplayStream([]).active is False
    prompt_only = {**THINK, "applies": ["prompt"]}
    assert stream.DisplayStream([entry(prompt_only)]).active is False
    assert stream.DisplayStream([entry(THINK, off=True)]).active is False
    assert stream.DisplayStream([entry({**THINK, "enabled": False})]).active is False
    assert stream.DisplayStream([entry({**THINK, "targets": ["user"]})]).active is False
    assert stream.DisplayStream([entry(THINK)]).active is True


def test_finish_is_none_when_nothing_changed():
    ds = stream.DisplayStream([entry(THINK)], clock=Clock())
    assert ds.finish() is None
    ds.feed("Hi")
    assert ds.finish() is None


# ---- the SSE body ------------------------------------------------------------

def put_rules(client, url, *rule_list):
    response = client.put(url, json={"rules": list(rule_list)})
    assert response.status_code == 200, response.text


def seed(client, names=("Mara",)):
    _primary(client)
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in names:
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert response.status_code == 200, response.text
    return cid, sid


def frames_of(response) -> list[dict]:
    assert response.status_code == 200, response.text
    return [json.loads(line[len("data: "):]) for line in response.text.splitlines()
            if line.startswith("data: ")]


def send(client, cid, sid, turns, **body):
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM(turns)
    return frames_of(client.post(
        f"/api/campaigns/{cid}/scenes/{sid}/chat",
        json={"content": "Hello", "speaker_ref": "characters:mara", **body}))


def shown(frames: list[dict]) -> str:
    """What a client would be showing for a single-part body."""
    buffer = ""
    for frame in frames:
        if "delta" in frame:
            buffer += frame["delta"]
        elif "display" in frame:
            buffer = apply_frame(buffer, frame["display"])
    return buffer


SCRIPT = ["<think>x", "</think>Hel", "lo"]


def test_turn_stream_sends_display_not_delta(client):
    cid, sid = seed(client)
    put_rules(client, f"/api/campaigns/{cid}/regex", THINK)
    frames = send(client, cid, sid, [SCRIPT])

    assert any("display" in f for f in frames)
    assert not any("delta" in f for f in frames)
    assert shown(frames) == "Hello"
    # The transcript keeps what the model wrote.
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] == "<think>x</think>Hello"


def test_turn_stream_unchanged_without_rules(client):
    cid, sid = seed(client)
    plain = send(client, cid, sid, [SCRIPT])
    assert [f["delta"] for f in plain if "delta" in f] == SCRIPT
    assert not any("display" in f for f in plain)

    # A rule that cannot apply to a streaming reply is the same as none.
    put_rules(client, f"/api/campaigns/{cid}/regex",
              {**THINK, "applies": ["prompt"]}, {**THINK, "enabled": False},
              {**THINK, "targets": ["user"]})
    inert = send(client, cid, sid, [SCRIPT])

    def comparable(frames):
        # Ids differ run to run; the shape and every payload otherwise must not.
        return [{k: (None if k in ("run", "response_start", "response_end") else v)
                 for k, v in f.items()} for f in frames]

    assert comparable(inert) == comparable(plain)


def test_legacy_stream_sends_display_from_zero(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    put_rules(client, f"/api/campaigns/{cid}/regex", THINK)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([SCRIPT])
    frames = frames_of(client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                                   json={"content": "Hello"}))
    assert any("display" in f for f in frames)
    assert not any("delta" in f for f in frames)
    assert shown(frames) == "Hello"
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] == "<think>x</think>Hello"


def test_legacy_stream_unchanged_without_rules(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([SCRIPT])
    frames = frames_of(client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                                   json={"content": "Hello"}))
    assert [f["delta"] for f in frames if "delta" in f] == SCRIPT
    assert not any("display" in f for f in frames)


def parts_of(frames: list[dict]) -> list[str]:
    """The buffer each `response_start` part ends at, `keep` counted per part."""
    buffers: list[str] = []
    for frame in frames:
        if "response_start" in frame:
            buffers.append("")
        elif "display" in frame:
            assert frame["display"]["keep"] <= len(buffers[-1])
            buffers[-1] = apply_frame(buffers[-1], frame["display"])
    return buffers


def test_multi_part_keep_relative_to_part(client):
    cid, sid = seed(client, ("Mara", "Winifred"))
    put_rules(client, f"/api/campaigns/{cid}/regex", THINK)
    frames = send(client, cid, sid, [
        ['<think>a</think>Mara speaks.\n```handoff\n{"next":"characters:winifred"}\n```'],
        ["<think>b", "</think>", "Winifred answers."],
    ])

    starts = [i for i, f in enumerate(frames) if "response_start" in f]
    assert len(starts) == 2
    second = [f["display"] for f in frames[starts[1]:] if "display" in f]
    assert second[0]["keep"] == 0
    assert [b.strip() for b in parts_of(frames)] == ["Mara speaks.", "Winifred answers."]


def test_display_frames_replay_from_an_index(client):
    """A re-attaching client reads the run's buffer from a frame index and
    applies the frames in order, as it would have live."""
    cid, sid = seed(client)
    put_rules(client, f"/api/campaigns/{cid}/regex", THINK)
    live = send(client, cid, sid, [SCRIPT])
    run_id = next(f["run"]["id"] for f in live if "run" in f)
    url = f"/api/campaigns/{cid}/scenes/{sid}/runs/{run_id}/stream"

    whole = frames_of(client.get(url))
    assert [f for f in whole if "display" in f] == [f for f in live if "display" in f]
    assert shown(whole) == "Hello"

    first = next(i for i, f in enumerate(whole) if "display" in f)
    rest = frames_of(client.get(url, params={"from": first + 1}))
    assert rest == whole[first + 1:]


# An open block is hidden to the end of what has arrived, so the display text
# does not move while it grows and no frame goes out for those deltas.
OPEN_THINK = {"name": "Hide open thinking", "pattern": r"<think>[\s\S]*?(?:</think>|$)",
              "replacement": ""}
HIDDEN = ["<think>abc", "", "def", "", "ghi"]


def beats(response) -> int:
    assert response.status_code == 200, response.text
    return response.text.count(streaming._HEARTBEAT)


def test_a_held_back_display_frame_does_not_count_as_proof_of_life(client, monkeypatch):
    monkeypatch.setattr(streaming, "HEARTBEAT_GAP", 3600.0)   # only a first beat is due
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([HIDDEN])
    body = {"content": "Hello", "speaker_ref": "characters:mara"}
    url = f"/api/campaigns/{cid}/scenes/{sid}/chat"

    # Plain deltas are frames, so the first empty one finds the stream recently
    # heard from and earns nothing...
    assert beats(client.post(url, json=body)) == 0
    # ...but while the hidden stretch sends no bytes it must.
    put_rules(client, f"/api/campaigns/{cid}/regex", OPEN_THINK)
    assert beats(client.post(url, json=body)) == 1


def test_a_held_back_display_frame_does_not_count_as_proof_of_life_legacy(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    monkeypatch.setattr(streaming, "HEARTBEAT_GAP", 3600.0)
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([HIDDEN])
    url = f"/api/campaigns/{cid}/scenes/{sid}/chat"

    assert beats(client.post(url, json={"content": "Hello"})) == 0
    put_rules(client, f"/api/campaigns/{cid}/regex", OPEN_THINK)
    assert beats(client.post(url, json={"content": "Hello"})) == 1


FAILS = ["Hel", "lo ", "<think>x</think>wor", "ld"]


def failing_body(client, cid, sid, **body):
    client.app.dependency_overrides[routes.get_llm] = lambda: FailingOpenRouter(FAILS)
    return frames_of(client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                                 json={"content": "Hello", **body}))


def test_a_failed_turn_flushes_what_the_throttle_held_back(client):
    cid, sid = seed(client)
    put_rules(client, f"/api/campaigns/{cid}/regex", THINK)
    frames = failing_body(client, cid, sid, speaker_ref="characters:mara")

    error = next(i for i, f in enumerate(frames) if "error" in f)
    assert not any("delta" in f for f in frames)
    # All of it was on screen before the error, not just the first frame's worth.
    assert shown(frames[:error]) == "Hello world"


def test_a_failed_legacy_turn_flushes_what_the_throttle_held_back(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    cid, sid = seed(client)
    put_rules(client, f"/api/campaigns/{cid}/regex", THINK)
    frames = failing_body(client, cid, sid)

    error = next(i for i, f in enumerate(frames) if "error" in f)
    assert shown(frames[:error]) == "Hello world"


def test_a_failed_turn_without_rules_sends_the_deltas_and_nothing_more(client):
    cid, sid = seed(client)
    frames = failing_body(client, cid, sid, speaker_ref="characters:mara")
    assert [f["delta"] for f in frames if "delta" in f] == FAILS
    assert not any("display" in f for f in frames)


# Hidden text that arrives as one unbroken run of non-empty deltas: no empty
# delta ever comes along to carry a heartbeat.
DENSE = ["<think>abc", "def", "ghi"]


def test_a_hidden_stretch_of_text_still_earns_a_heartbeat(client, monkeypatch):
    monkeypatch.setattr(streaming, "HEARTBEAT_GAP", 3600.0)   # only a first beat is due
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([DENSE])
    body = {"content": "Hello", "speaker_ref": "characters:mara"}
    url = f"/api/campaigns/{cid}/scenes/{sid}/chat"

    # Every plain delta is a frame, so no beat is due...
    assert beats(client.post(url, json=body)) == 0
    # ...but text a display rule hides sends nothing, so the stream says it is alive.
    put_rules(client, f"/api/campaigns/{cid}/regex", OPEN_THINK)
    assert beats(client.post(url, json=body)) == 1


def test_a_hidden_stretch_of_text_still_earns_a_heartbeat_legacy(client, monkeypatch):
    monkeypatch.setattr(character_turns, "enabled", lambda: False)
    monkeypatch.setattr(streaming, "HEARTBEAT_GAP", 3600.0)
    cid, sid = seed(client)
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeLLM([DENSE])
    url = f"/api/campaigns/{cid}/scenes/{sid}/chat"

    assert beats(client.post(url, json={"content": "Hello"})) == 0
    put_rules(client, f"/api/campaigns/{cid}/regex", OPEN_THINK)
    assert beats(client.post(url, json={"content": "Hello"})) == 1
