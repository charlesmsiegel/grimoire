"""`tokens.counting`: which tokenizer produced a count, and whether it is the
model's own -- what lets the context inspector mark its numbers as estimates.

The encoder is patched rather than loaded: whether tiktoken can fetch its BPE
file is the network's business, and the question here is only what the answer
is once that is known.
"""

import pytest

from grimoire import store
from grimoire.store import tokens


class _Enc:
    def encode(self, text: str) -> list[int]:
        return [0] * len(text.split())


@pytest.fixture
def loaded(monkeypatch):
    monkeypatch.setattr(tokens, "_encoder", _Enc)


@pytest.mark.parametrize("model", ["gpt-4", "openai/gpt-4", "gpt-3.5-turbo",
                                   "openai/gpt-3.5-turbo"])
def test_a_model_tiktoken_maps_to_the_loaded_encoding_is_exact(loaded, model):
    pytest.importorskip("tiktoken")
    assert tokens.counting(model) == {"tokenizer": "cl100k_base", "exact": True}


@pytest.mark.parametrize("model", [
    "anthropic/claude-sonnet-4.5",   # another vendor's tokenizer entirely
    "opus",                          # a Claude connection's alias
    "llama3.1:8b",                   # what an Ollama endpoint calls its model
    "openai/gpt-4o",                 # OpenAI's, but on o200k_base
    "someorg/gpt-4-tune",            # a foreign prefix is not stripped
    "",                              # an endpoint's unnamed default
])
def test_any_other_model_is_an_estimate_even_with_the_encoder_loaded(loaded, model):
    assert tokens.counting(model) == {"tokenizer": "cl100k_base", "exact": False}


def test_without_an_encoder_every_count_is_the_heuristic(monkeypatch):
    """Android installs no tiktoken, and a desktop that could not fetch the
    encoding counts by length too -- neither is anybody's own tokenizer."""
    monkeypatch.setattr(tokens, "_encoder", lambda: None)
    assert tokens.counting("gpt-4") == {"tokenizer": "heuristic", "exact": False}


def test_without_tiktoken_installed_no_model_is_native(monkeypatch):
    monkeypatch.setattr(tokens, "tiktoken", None)
    assert tokens._native_encoding("gpt-4") == ""


def _scene(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    return cid, sid


def test_the_live_context_names_its_tokenizer(client, monkeypatch):
    monkeypatch.setattr(tokens, "_encoder", lambda: None)
    cid, sid = _scene(client)
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert body["token_count"] == {"tokenizer": "heuristic", "exact": False}


def test_a_frozen_turn_keeps_the_tokenizer_it_was_counted_with(client, monkeypatch):
    """Frozen per entry, against the model the turn asked for: a later change
    of connection must not relabel a past turn's counts."""
    monkeypatch.setattr(tokens, "_encoder", _Enc)
    cid, sid = _scene(client)
    eid = store.prompt_log.record(cid, sid, "chat", {"sections": [], "total_tokens": 3,
                                                     "dropped_tokens": 0, "budget_tokens": 0},
                                  model="llama3.1:8b")
    assert eid is not None
    entry = client.get(f"/api/campaigns/{cid}/scenes/{sid}/prompts/{eid}").json()
    assert entry["token_count"] == {"tokenizer": "cl100k_base", "exact": False}
