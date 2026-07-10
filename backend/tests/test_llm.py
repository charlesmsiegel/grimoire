from grimoire.llm import LLMError
from grimoire.openrouter import OpenRouterError


def test_openrouter_error_is_llm_error():
    err = OpenRouterError("auth", "bad key")
    assert isinstance(err, LLMError)
    assert err.kind == "auth"
    assert err.detail == "bad key"


def test_llm_error_detail_defaults_to_kind():
    assert LLMError("network").detail == "network"


from grimoire.llm import LLMClient


class FakeProvider:
    def __init__(self, tag):
        self.tag = tag
        self.calls = []

    async def stream(self, messages, *args):
        self.calls.append(args)
        yield self.tag


def _cfg(provider):
    return {"provider": provider, "model": "or-model", "openrouter_key": "sk-or-x",
            "claude_model": "opus"}


async def test_dispatches_to_openrouter():
    op, cl = FakeProvider("or"), FakeProvider("cl")
    client = LLMClient(openrouter=op, claude=cl)
    chunks = [c async for c in client.stream([], _cfg("openrouter"))]
    assert chunks == ["or"]
    assert op.calls == [("or-model", "sk-or-x")]
    assert cl.calls == []


async def test_dispatches_to_claude():
    op, cl = FakeProvider("or"), FakeProvider("cl")
    client = LLMClient(openrouter=op, claude=cl)
    chunks = [c async for c in client.stream([], _cfg("claude"))]
    assert chunks == ["cl"]
    assert cl.calls == [("opus",)]
    assert op.calls == []


async def test_missing_provider_key_defaults_to_openrouter():
    op, cl = FakeProvider("or"), FakeProvider("cl")
    client = LLMClient(openrouter=op, claude=cl)
    cfg = {"model": "or-model", "openrouter_key": "sk-or-x"}  # pre-upgrade config
    assert [c async for c in client.stream([], cfg)] == ["or"]
