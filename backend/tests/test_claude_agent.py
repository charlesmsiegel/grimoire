import types

import grimoire.claude_agent as claude_agent
import pytest
from grimoire.claude_agent import ClaudeAgentClient, ClaudeAgentError

# The six SDK names are read once, at import; patching sys.modules would leave
# grimoire.claude_agent still bound to the real objects, so the fake has to be
# installed over the module globals instead.
_SDK_NAMES = ("AssistantMessage", "ClaudeAgentOptions", "CLINotFoundError",
              "ProcessError", "TextBlock", "query")


class _TextBlock:
    def __init__(self, text):
        self.text = text


class _AssistantMessage:
    def __init__(self, blocks):
        self.content = blocks


class _CLINotFoundError(Exception):
    pass


class _ProcessError(Exception):
    pass


def install_fake_sdk(monkeypatch, replies=(), error=None):
    """Bind stand-in SDK objects onto grimoire.claude_agent; returns call args."""
    captured = {}

    async def query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        if error is not None:
            raise error
        for reply in replies:
            yield reply

    mod = types.SimpleNamespace(
        query=query,
        ClaudeAgentOptions=lambda **kw: types.SimpleNamespace(**kw),
        AssistantMessage=_AssistantMessage,
        TextBlock=_TextBlock,
        CLINotFoundError=_CLINotFoundError,
        ProcessError=_ProcessError,
    )
    for name in _SDK_NAMES:
        monkeypatch.setattr(claude_agent, name, getattr(mod, name))
    monkeypatch.setattr(claude_agent, "_SDK_IMPORT_ERROR", None)
    return captured


async def test_stream_yields_assistant_text(monkeypatch):
    replies = [
        _AssistantMessage([_TextBlock("Hel"), _TextBlock("lo")]),
        types.SimpleNamespace(),  # non-assistant message (e.g. ResultMessage) is ignored
    ]
    install_fake_sdk(monkeypatch, replies=replies)
    client = ClaudeAgentClient()
    chunks = [c async for c in client.stream([{"role": "user", "content": "hi"}], "opus")]
    assert "".join(chunks) == "Hello"


async def test_system_messages_become_system_prompt(monkeypatch):
    captured = install_fake_sdk(monkeypatch)
    client = ClaudeAgentClient()
    messages = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "go on"},
    ]
    [c async for c in client.stream(messages, "opus")]
    assert captured["options"].system_prompt == "be brief"
    assert captured["options"].model == "opus"
    assert captured["options"].allowed_tools == []
    assert captured["options"].max_turns == 1
    prompt = captured["prompt"]
    assert "hi" in prompt and "hello" in prompt and "go on" in prompt
    assert prompt.rstrip().endswith("[assistant]")


async def test_missing_sdk_is_normalized(monkeypatch):
    # Both names, not just `query`: with the extra installed _SDK_IMPORT_ERROR is
    # None, and stream() would hit `raise None` -> TypeError instead of the error
    # under test. Setting both simulates the absent extra in either environment.
    monkeypatch.setattr(claude_agent, "query", None)
    monkeypatch.setattr(claude_agent, "_SDK_IMPORT_ERROR",
                        ImportError("No module named 'claude_agent_sdk'"))
    client = ClaudeAgentClient()
    with pytest.raises(ClaudeAgentError) as exc:
        [c async for c in client.stream([{"role": "user", "content": "hi"}], "opus")]
    assert exc.value.kind == "missing_dependency"


async def test_cli_not_found_is_missing_dependency(monkeypatch):
    install_fake_sdk(monkeypatch, error=_CLINotFoundError("claude not installed"))
    client = ClaudeAgentClient()
    with pytest.raises(ClaudeAgentError) as exc:
        [c async for c in client.stream([{"role": "user", "content": "hi"}], "opus")]
    assert exc.value.kind == "missing_dependency"
    # The detail, not just the kind: the absent-extra path raises that same kind
    # with the pip-install message, so without this the test passes on an
    # environment with no `claude` extra even if the fake were never installed.
    assert exc.value.detail == "claude not installed"


async def test_process_error_is_bad_response(monkeypatch):
    install_fake_sdk(monkeypatch, error=_ProcessError("exit 1: not logged in"))
    client = ClaudeAgentClient()
    with pytest.raises(ClaudeAgentError) as exc:
        [c async for c in client.stream([{"role": "user", "content": "hi"}], "opus")]
    assert exc.value.kind == "bad_response"
    assert "not logged in" in exc.value.detail


async def test_unexpected_error_is_network(monkeypatch):
    install_fake_sdk(monkeypatch, error=RuntimeError("boom"))
    client = ClaudeAgentClient()
    with pytest.raises(ClaudeAgentError) as exc:
        [c async for c in client.stream([{"role": "user", "content": "hi"}], "opus")]
    assert exc.value.kind == "network"


async def test_non_text_messages_are_reported_as_liveness(monkeypatch):
    """The SDK sends tool/thinking/result messages that carry no text; the
    facade's idle bound has to count them as activity, not silence (#243)."""
    replies = [
        types.SimpleNamespace(),                 # e.g. a thinking/result message
        _AssistantMessage([_TextBlock("Hello")]),
    ]
    install_fake_sdk(monkeypatch, replies=replies)
    chunks = [c async for c in ClaudeAgentClient().stream([], "opus")]
    assert "".join(chunks) == "Hello"
    assert chunks.count("") >= 1


# ---- usage capture (#152) ----
async def test_the_result_message_fills_the_usage_holder(monkeypatch):
    result = types.SimpleNamespace(
        usage={"input_tokens": 900, "output_tokens": 45,
               "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20},
        total_cost_usd=0.031)
    install_fake_sdk(monkeypatch, replies=[_AssistantMessage([_TextBlock("Hi")]), result])

    usage = {}
    client = ClaudeAgentClient()
    chunks = [c async for c in client.stream([], "opus", usage=usage)]

    assert "".join(chunks) == "Hi"
    # Cache reads and cache writes are prompt tokens the account was charged
    # for; leaving them out would under-report a cached campaign by most of it.
    assert usage["prompt_tokens"] == 1020
    assert usage["completion_tokens"] == 45
    assert usage["cost_usd"] == 0.031


async def test_claude_dollars_are_marked_as_not_spent(monkeypatch):
    result = types.SimpleNamespace(usage={"input_tokens": 1, "output_tokens": 1},
                                   total_cost_usd=0.5)
    install_fake_sdk(monkeypatch, replies=[result])

    usage = {}
    client = ClaudeAgentClient()
    [c async for c in client.stream([], "opus", usage=usage)]
    assert usage["cost_basis"] == "equivalent", (
        "this path bills against a Claude subscription, so its dollars must "
        "not be summed into a spend total")


async def test_a_run_that_reports_no_usage_leaves_the_holder_empty(monkeypatch):
    install_fake_sdk(monkeypatch, replies=[_AssistantMessage([_TextBlock("Hi")])])
    usage = {}
    client = ClaudeAgentClient()
    [c async for c in client.stream([], "opus", usage=usage)]
    assert usage == {}


async def test_a_garbled_token_count_records_no_count_rather_than_zero(monkeypatch):
    """The key is present and its value is not a number. Recording 0 would be
    the ledger claiming the call used no prompt, which nobody said."""
    result = types.SimpleNamespace(usage={"input_tokens": "lots", "output_tokens": 4})
    install_fake_sdk(monkeypatch, replies=[result])

    usage = {}
    client = ClaudeAgentClient()
    [c async for c in client.stream([], "opus", usage=usage)]
    assert "prompt_tokens" not in usage
    assert usage["completion_tokens"] == 4


async def test_an_impossible_cost_is_dropped_rather_than_poisoning_a_total(monkeypatch):
    for cost in (float("inf"), float("nan"), -1.0, "free"):
        result = types.SimpleNamespace(usage={"input_tokens": 1, "output_tokens": 1},
                                       total_cost_usd=cost)
        install_fake_sdk(monkeypatch, replies=[result])
        usage = {}
        client = ClaudeAgentClient()
        [c async for c in client.stream([], "opus", usage=usage)]
        assert "cost_usd" not in usage, cost


# ---- the cache split (#148) ----

async def test_the_cache_split_is_recorded_beside_the_prompt_total(monkeypatch):
    """The three prompt keys are summed into `prompt_tokens` because that is
    what was billed as input. These say how that input divided — and this is
    the provider that caches without being asked, so on a long campaign the
    read is most of every prompt and a total that only knows the sum cannot
    show it."""
    result = types.SimpleNamespace(
        usage={"input_tokens": 900, "output_tokens": 45,
               "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20})
    install_fake_sdk(monkeypatch, replies=[_AssistantMessage([_TextBlock("Hi")]), result])

    usage = {}
    [c async for c in ClaudeAgentClient().stream([], "opus", usage=usage)]

    assert usage["prompt_tokens"] == 1020        # unchanged: still the whole input
    assert usage["cache_read_tokens"] == 100
    assert usage["cache_write_tokens"] == 20


async def test_a_ttl_split_cache_creation_is_read_when_the_flat_field_is_absent(monkeypatch):
    """Anthropic added per-TTL tiers without removing the flat total. Summing
    both would report every cache write twice, so the flat one wins and the
    split is only a fallback."""
    result = types.SimpleNamespace(
        usage={"input_tokens": 10, "output_tokens": 1,
               "cache_creation": {"ephemeral_5m_input_tokens": 500,
                                  "ephemeral_1h_input_tokens": 200}})
    install_fake_sdk(monkeypatch, replies=[result])

    usage = {}
    [c async for c in ClaudeAgentClient().stream([], "opus", usage=usage)]

    assert usage["cache_write_tokens"] == 700


async def test_the_flat_cache_creation_field_wins_over_the_split(monkeypatch):
    result = types.SimpleNamespace(
        usage={"input_tokens": 10, "output_tokens": 1,
               "cache_creation_input_tokens": 700,
               "cache_creation": {"ephemeral_5m_input_tokens": 500,
                                  "ephemeral_1h_input_tokens": 200}})
    install_fake_sdk(monkeypatch, replies=[result])

    usage = {}
    [c async for c in ClaudeAgentClient().stream([], "opus", usage=usage)]

    assert usage["cache_write_tokens"] == 700


async def test_a_run_that_cached_nothing_records_no_cache_keys(monkeypatch):
    result = types.SimpleNamespace(usage={"input_tokens": 10, "output_tokens": 1})
    install_fake_sdk(monkeypatch, replies=[result])

    usage = {}
    [c async for c in ClaudeAgentClient().stream([], "opus", usage=usage)]

    assert "cache_read_tokens" not in usage and "cache_write_tokens" not in usage
