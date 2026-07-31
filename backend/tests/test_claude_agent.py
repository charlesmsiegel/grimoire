import types

import pytest

import grimoire.claude_agent as claude_agent
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
