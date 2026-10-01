"""Leading preparation is hidden before rolls, handoffs or storage see it."""
import json

import pytest

from grimoire import routes, store
from grimoire.store import response_protocol
from tests.llm_fakes import FakeLLM

PREPARATION = '```perception\n{"known": [], "heard_or_seen": ["Come in"], "unknown": ["private fact"]}\n```\n'
REPLY = 'Mara answers.\n```handoff\n{"next":null}\n```'


@pytest.mark.parametrize("cut", range(len(PREPARATION + REPLY) + 1))
def test_preparation_hidden_at_every_two_chunk_boundary(cut):
    watcher = response_protocol.ResponseWatcher(perception=True)
    text = PREPARATION + REPLY
    shown = watcher.feed(text[:cut]) + watcher.feed(text[cut:]) + watcher.finish()
    assert shown.strip() == "Mara answers."
    assert watcher.narration.strip() == "Mara answers."
    assert watcher.handoff == {"next": None}
    assert "private fact" in watcher.preparation_note


@pytest.mark.parametrize("text", [PREPARATION[:-5], '```percep', '```perception\nprivate fact'])
def test_incomplete_preparation_never_becomes_prose(text):
    watcher = response_protocol.ResponseWatcher(perception=True)
    assert watcher.feed(text) + watcher.finish() == ""
    assert watcher.narration == ""


def test_preparation_roll_text_is_not_a_roll_and_real_roll_still_pauses():
    watcher = response_protocol.ResponseWatcher(perception=True)
    text = '```perception\n{"unknown": ["```roll is not an observed action"]}\n```\nBefore.\n```roll\n{"check":"test"}\n```'
    shown = ''.join(watcher.feed(c) for c in text) + watcher.finish()
    assert shown.strip() == "Before."
    assert watcher.roll.complete
    assert watcher.roll.body.strip() == '{"check":"test"}'


@pytest.mark.parametrize("text", ["Ordinary reply.", "```python\nprint(1)\n```\nDone.", "A ```perception reference.", "`", "\nHello."])
def test_normal_prefixes_unchanged(text):
    watcher = response_protocol.ResponseWatcher(perception=True)
    shown = ''.join(watcher.feed(c) for c in text) + watcher.finish()
    assert watcher.narration == ("" if text == "`" else text)
    # Existing handoff watcher deliberately withholds a dangling backtick.
    if text != "`":
        assert shown == text


def test_preparation_hidden_from_scene_and_sse_without_extra_call(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    char = client.post(f"/api/campaigns/{cid}/characters", json={"name":"Mara"}).json()["character"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id":char})
    fake = FakeLLM([[PREPARATION[:8], PREPARATION[8:] + REPLY]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content":"Hello", "speaker_ref":"characters:mara"})
    deltas = [json.loads(line[6:]).get("delta", "") for line in result.text.splitlines() if line.startswith("data: ")]
    assert "private fact" not in ''.join(deltas)
    assert "perception" not in ''.join(deltas)
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["content"] == "Mara answers."
    assert fake.calls == 1



@pytest.mark.parametrize("block", [
    '  ```perception\n{}\n  ```\n',
    '\t``` PERCEPTION\r\n{}\r\n\t```\r\n',
])
def test_indented_preparation_keeps_following_prose(block):
    watcher = response_protocol.ResponseWatcher(perception=True)
    text = block + REPLY
    shown = ''.join(watcher.feed(c) for c in text) + watcher.finish()
    assert shown.strip() == "Mara answers."
    assert watcher.narration.strip() == "Mara answers."
    assert watcher.handoff == {"next": None}
