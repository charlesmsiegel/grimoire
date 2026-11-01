"""Play-scope and chat references (Sprint 107): their own families, never parsed as another."""

from __future__ import annotations

import pytest

from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef, parse_ref


def test_scope_and_chat_refs_round_trip_exactly():
    scope, chat = ScopeRef("quick-1"), ChatRef("quick-1", "thread-1")
    assert str(scope) == "campaign:quick-1"
    assert str(chat) == "campaign:quick-1#chat/thread-1"
    assert ScopeRef.parse(str(scope)) == scope
    assert ChatRef.parse(str(chat)) == chat
    assert chat.scope == scope


@pytest.mark.parametrize("text", [
    "campaign:Quick#chat/t", "campaign:q#chat/T", " campaign:q#chat/t", "campaign:q#chat/t/x",
    "campaign:q#chat/", "campaign:q#Chat/t", "campaign:q#thread/t", "campaign:q/chat/t", "chat:q/t",
])
def test_chat_ref_parsing_rejects_rather_than_normalizes(text):
    with pytest.raises(ValueError):
        ChatRef.parse(text)


@pytest.mark.parametrize("text", ["campaign:", "campaign:Q", "campaign:q/x", "campaign:q#chat/t", "q"])
def test_scope_ref_parsing_is_strict(text):
    with pytest.raises(ValueError):
        ScopeRef.parse(text)


def test_no_family_parses_as_another():
    chat, scope = ChatRef("q", "t"), ScopeRef("q")
    for text in (str(chat), str(scope)):
        with pytest.raises(ValueError):
            parse_ref(text)  # parse_ref is unchanged: never hands out a scope or chat
        with pytest.raises(ValueError):
            SourceRef.parse(text)
        with pytest.raises(ValueError):
            InstanceRef.parse(text)
    lookalike = InstanceRef("q", "chat", "t")
    assert str(lookalike) != str(chat)
    with pytest.raises(ValueError):
        ChatRef.parse(str(lookalike))
    with pytest.raises(ValueError):
        ScopeRef.parse(str(chat))
    for other in ("world:harbor/character/wren@r1", "campaign:q/character/a", "v1:arc:x.json"):
        with pytest.raises(ValueError):
            ChatRef.parse(other)


def test_constructors_enforce_the_grammar():
    with pytest.raises(ValueError):
        ChatRef("q", "Bad")
    with pytest.raises(ValueError):
        ScopeRef("q:x")
    with pytest.raises(ValueError):
        ChatRef("q", "t" * 129)
