"""Tests for the late-stage ``{{user}}`` resolution in the Context Builder.

The macro engine (``grimoire.characters.macros``) leaves ``{{user}}``
literal at ingest. The Context Builder substitutes it after assembly
against the active PC's name (or ``"the player"`` when no PC is active).
"""

from __future__ import annotations

from grimoire.context.builder import _resolve_runtime_macros
from grimoire.types.llm import Message, MessageRole


def _msg(role: MessageRole, content: str) -> Message:
    return Message(role=role, content=content)


def test_user_macro_substituted_with_active_pc_name() -> None:
    messages = [
        _msg(MessageRole.SYSTEM, "Scene contains {{user}} and Beatrice."),
        _msg(MessageRole.USER, "I greet {{user}}."),
    ]
    out = _resolve_runtime_macros(messages, "Aria")
    blob = "\n".join(m.content for m in out)
    assert "{{user}}" not in blob
    assert "Aria" in blob


def test_user_macro_substituted_with_the_player_when_no_pc() -> None:
    messages = [_msg(MessageRole.SYSTEM, "Scene contains {{user}}.")]
    out = _resolve_runtime_macros(messages, "")
    assert "{{user}}" not in out[0].content
    assert "the player" in out[0].content


def test_user_macro_substituted_with_the_player_when_pc_name_whitespace() -> None:
    messages = [_msg(MessageRole.SYSTEM, "Hello {{user}}.")]
    out = _resolve_runtime_macros(messages, "   ")
    assert "the player" in out[0].content


def test_resolve_runtime_macros_idempotent() -> None:
    messages = [_msg(MessageRole.SYSTEM, "Hi {{user}}.")]
    once = _resolve_runtime_macros(messages, "Beatrice")
    twice = _resolve_runtime_macros(once, "Beatrice")
    assert [m.content for m in once] == [m.content for m in twice]


def test_resolve_runtime_macros_preserves_messages_without_macro() -> None:
    messages = [
        _msg(MessageRole.SYSTEM, "No macros here."),
        _msg(MessageRole.ASSISTANT, "Nothing to substitute."),
    ]
    out = _resolve_runtime_macros(messages, "Aria")
    assert [m.content for m in out] == ["No macros here.", "Nothing to substitute."]


def test_resolve_runtime_macros_preserves_role_and_metadata() -> None:
    msg = Message(
        role=MessageRole.USER,
        content="{{user}} did it",
        metadata={"tag": "x"},
    )
    out = _resolve_runtime_macros([msg], "Aria")
    assert out[0].role == MessageRole.USER
    assert out[0].metadata == {"tag": "x"}
    assert out[0].content == "Aria did it"


def test_handles_multiple_occurrences_in_one_message() -> None:
    messages = [_msg(MessageRole.SYSTEM, "{{user}} and again {{user}}.")]
    out = _resolve_runtime_macros(messages, "Aria")
    assert out[0].content == "Aria and again Aria."


def test_does_not_touch_other_macros() -> None:
    # Only {{user}} is resolved at runtime — other macros should have been
    # handled at ingest. Anything left over is passed through.
    messages = [_msg(MessageRole.SYSTEM, "{{user}} {{char}} {{unknown}}")]
    out = _resolve_runtime_macros(messages, "Aria")
    assert out[0].content == "Aria {{char}} {{unknown}}"
