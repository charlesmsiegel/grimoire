"""`tokens.counting`: which tokenizer produced a count, and whether it is the
model's own -- what lets the context inspector mark its numbers as estimates.

The loaded encoder is patched rather than loaded: whether tiktoken can fetch its BPE
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
    monkeypatch.setattr(tokens, "_loaded", _Enc)


@pytest.mark.parametrize("model", ["openai/gpt-4", "openai/gpt-3.5-turbo"])
def test_an_openai_model_on_openrouter_and_the_loaded_encoding_is_native(loaded, model):
    pytest.importorskip("tiktoken")
    assert tokens.counting(model, "openrouter") == {"tokenizer": "cl100k_base", "native": True}


@pytest.mark.parametrize("kind", ["openai_compatible", "claude", ""])
def test_the_same_model_id_on_any_other_connection_is_an_estimate(loaded, kind):
    """A LiteLLM gateway or a local server can serve anything as `openai/gpt-4`;
    only OpenRouter's ids name the model that answers. An unknown kind -- a
    fallback attempt's snapshot -- is never native either."""
    pytest.importorskip("tiktoken")
    assert tokens.counting("openai/gpt-4", kind) == {"tokenizer": "cl100k_base", "native": False}


@pytest.mark.parametrize("model", [
    "anthropic/claude-sonnet-4.5",   # another vendor's tokenizer entirely
    "opus",                          # a Claude connection's alias
    "llama3.1:8b",                   # what an Ollama endpoint calls its model
    "openai/gpt-4o",                 # OpenAI's, but on o200k_base
    "someorg/gpt-4-tune",            # a foreign prefix is not stripped
    "gpt-4",                         # a bare name: an endpoint may serve anything as it
    "",                              # an endpoint's unnamed default
])
def test_any_other_model_is_an_estimate_even_with_the_encoder_loaded(loaded, model):
    """Including a bare `gpt-4`: LocalAI's default images and aliasing gateways
    serve local models under that name, so an unprefixed OpenAI name proves
    nothing about which tokenizer is on the other end."""
    assert tokens.counting(model, "openrouter") == {"tokenizer": "cl100k_base", "native": False}


def test_without_an_encoder_every_count_is_the_heuristic(monkeypatch):
    """Android installs no tiktoken, and a desktop that could not fetch the
    encoding counts by length too -- neither is anybody's own tokenizer."""
    monkeypatch.setattr(tokens, "_loaded", lambda: None)
    assert tokens.counting("openai/gpt-4", "openrouter") == {"tokenizer": "heuristic",
                                                             "native": False}


@pytest.mark.parametrize("counted_with", ["heuristic", "mixed"])
def test_the_counter_the_compose_used_wins_over_the_loaders_state_now(loaded, counted_with):
    """A load that succeeds AFTER the counting -- the retry window expiring
    between compose and capture -- must not relabel length counts as
    cl100k_base, let alone as native."""
    assert tokens.counting("openai/gpt-4", "openrouter", counted_with) == {
        "tokenizer": counted_with, "native": False}


def test_count_tokens_records_which_counter_it_used(monkeypatch):
    monkeypatch.setattr(tokens, "_encoder", _Enc)
    tokens.count_tokens("Seraphine keeps the ledger")
    assert tokens.last_counter() == "cl100k_base"
    monkeypatch.setattr(tokens, "_encoder", lambda: None)
    tokens.count_tokens("Seraphine keeps the ledger")
    assert tokens.last_counter() == "heuristic"


def test_a_compose_reports_the_counter_it_counted_with(monkeypatch):
    from grimoire.store.context import assemble
    count = assemble._token_memo()
    monkeypatch.setattr(tokens, "_encoder", _Enc)
    count("Mara walks the Saltmarch road")
    assert count.counted_with() == "cl100k_base"
    monkeypatch.setattr(tokens, "_encoder", lambda: None)
    count("Winifred keeps the tide-ledger")
    assert count.counted_with() == "mixed"


def test_without_tiktoken_installed_no_model_is_native(monkeypatch):
    monkeypatch.setattr(tokens, "tiktoken", None)
    assert tokens._native_encoding("openai/gpt-4") == ""


def test_describing_counts_never_starts_an_encoder_load(monkeypatch):
    """`prompt_log.record` asks under the campaign lock, on a path that must not
    wait -- a load there is a download, or a wait on another thread's."""
    class _Exploding:
        def get_encoding(self, name):
            raise AssertionError("counting() tried to load the encoder")
    monkeypatch.setattr(tokens, "tiktoken", _Exploding())
    monkeypatch.setattr(tokens, "_loader", tokens._Loader())
    assert tokens.counting("openai/gpt-4", "openrouter") == {"tokenizer": "heuristic",
                                                             "native": False}


def _scene(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Mara"}).json()["id"]
    return cid, sid


def test_the_live_context_names_the_counter_its_compose_used(client, monkeypatch):
    # Counted by length, while the loader would say it is ready: the label must
    # come from the counting, not from the loader afterwards.
    monkeypatch.setattr(tokens, "_encoder", lambda: None)
    monkeypatch.setattr(tokens, "_loaded", _Enc)
    cid, sid = _scene(client)
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert body["token_count"] == {"tokenizer": "heuristic", "native": False}


def test_a_frozen_turn_keeps_the_tokenizer_it_was_counted_with(client, monkeypatch):
    """Frozen per entry, against the model and connection the turn used: a
    later change of either must not relabel a past turn's counts."""
    pytest.importorskip("tiktoken")
    monkeypatch.setattr(tokens, "_loaded", _Enc)
    cid, sid = _scene(client)
    blank = {"sections": [], "total_tokens": 3, "dropped_tokens": 0, "budget_tokens": 0}
    native = store.prompt_log.record(cid, sid, "chat", {**blank, "counted_with": "cl100k_base"},
                                     model="openai/gpt-4", kind="openrouter")
    local = store.prompt_log.record(cid, sid, "chat", {**blank, "counted_with": "cl100k_base"},
                                    model="openai/gpt-4", kind="openai_compatible")
    url = f"/api/campaigns/{cid}/scenes/{sid}/prompts"
    assert client.get(f"{url}/{native}").json()["token_count"] == {"tokenizer": "cl100k_base",
                                                                   "native": True}
    assert client.get(f"{url}/{local}").json()["token_count"] == {"tokenizer": "cl100k_base",
                                                                  "native": False}
