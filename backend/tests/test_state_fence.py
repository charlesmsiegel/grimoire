"""The trailing ``state`` fence: a legacy block a model may still end a reply
with. It is never stored, so the grammar that recognises it and the stream
redactor that keeps it off the player's screen both survive the retired
turn-state ledger. (Parsed values are no longer kept; only the strip matters.)"""

import json

from grimoire import routes, store
from grimoire.store import state_fence
from tests.llm_fakes import FakeOpenRouter


def _block(payload: str) -> str:
    return f"```state\n{payload}\n```"


# ---- the block grammar -----------------------------------------------------

def test_a_trailing_block_is_split_off_the_narration():
    text = "Winifred sets the lamp down.\n\n" + _block('{"Winifred": {"mood": "guarded"}}')
    narration, states = state_fence.split_block(text)
    assert narration.strip() == "Winifred sets the lamp down."
    assert states == {"Winifred": {"mood": "guarded"}}


def test_a_reply_with_no_block_is_returned_unchanged():
    assert state_fence.split_block("Just prose.") == ("Just prose.", {})


def test_a_block_with_narration_after_it_is_left_in_place():
    # Deleting from the middle of a reply is the one failure that loses story.
    text = _block('{"Winifred": {"mood": "guarded"}}') + "\n\nShe turns back."
    narration, states = state_fence.split_block(text)
    assert narration == text
    assert states == {}


def test_an_unterminated_trailing_opener_is_stripped_with_no_data():
    text = 'She waits.\n\n```state\n{"Winifred": {"mood": "gu'
    narration, states = state_fence.split_block(text)
    assert narration.strip() == "She waits."
    assert states == {}


def test_malformed_json_costs_the_data_not_the_reply():
    narration, states = state_fence.split_block("She waits.\n\n" + _block("{not json"))
    assert narration.strip() == "She waits."
    assert states == {}


def test_the_state_envelope_form_is_accepted_too():
    _, states = state_fence.split_block(_block('{"state": {"Winifred": {"mood": "tense"}}}'))
    assert states == {"Winifred": {"mood": "tense"}}


def test_unknown_fields_and_non_string_values_are_dropped():
    _, states = state_fence.split_block(
        _block('{"Winifred": {"mood": "wry", "hp": 4, "intent": null, "posture": "  leaning  in  "}}'))
    assert states == {"Winifred": {"mood": "wry", "posture": "leaning in"}}


def test_an_over_long_value_is_dropped_rather_than_truncated():
    long = "x" * (state_fence.MAX_VALUE + 1)
    _, states = state_fence.split_block(_block(json.dumps({"W": {"mood": long, "intent": "wait"}})))
    assert states == {"W": {"intent": "wait"}}


def test_a_character_with_nothing_usable_is_dropped_entirely():
    _, states = state_fence.split_block(_block('{"Winifred": {"hp": 3}, "Mara": {"mood": "calm"}}'))
    assert states == {"Mara": {"mood": "calm"}}


def test_the_last_of_several_blocks_is_the_one_that_counts():
    text = (_block('{"Mara": {"mood": "early"}}') + "\n\nShe moves.\n\n"
            + _block('{"Mara": {"mood": "late"}}'))
    narration, states = state_fence.split_block(text)
    assert states == {"Mara": {"mood": "late"}}
    assert "early" in narration and "late" not in narration

# ---- the stream redactor ---------------------------------------------------

def _stream(chunks):
    r = state_fence.StreamRedactor()
    return "".join(r.feed(c) for c in chunks) + r.finish()


def test_the_redactor_passes_ordinary_prose_through():
    assert _stream(["She ", "sets the ", "lamp down."]) == "She sets the lamp down."


def test_the_redactor_swallows_the_block_even_split_across_deltas():
    assert _stream(["She waits.\n\n", "``", "`sta", "te\n{\"W\": ", "{}}\n```"]) == "She waits.\n\n"


def test_backticks_that_are_not_an_opener_are_released():
    assert _stream(["a ``", "code`` span"]) == "a ``code`` span"
    assert _stream(["```py", "thon\nx = 1\n```"]) == "```python\nx = 1\n```"


def test_a_trailing_backtick_run_survives_end_of_stream():
    assert _stream(["done ``", "`"]) == "done ```"


def test_statement_is_not_an_opener():
    assert _stream(["```statement of intent"]) == "```statement of intent"


def test_nothing_escapes_after_a_trailing_block():
    # The fence starts a line — an inline one is not a block at all, and
    # `test_an_inline_fence_is_never_withheld` covers that case.
    r = state_fence.StreamRedactor()
    assert r.feed("hi\n```state\n") == "hi\n"
    assert r.feed('{"W": {}}') == ""
    assert r.feed("\n```") == ""
    assert r.finish() == ""


def test_a_block_with_narration_after_it_is_released_whole():
    """The transcript keeps a mid-reply block (split_block only strips a
    trailing one), so a redactor that dropped it would end the streamed reply
    early and disagree with what was stored."""
    text = 'She waits.\n\n```state\n{"W": {"mood": "wry"}}\n```\n\nShe turns back.'
    assert _stream([text]) == text
    assert state_fence.split_block(text)[0] == text


def test_an_unterminated_block_is_still_swallowed():
    assert _stream(['She waits.\n\n```state\n{"W": {"mo']) == "She waits.\n\n"


# ---- the ledger ------------------------------------------------------------


# ---- CRLF and end-of-stream edges ------------------------------------------

def test_a_crlf_block_is_recognized():
    """A provider returning CRLF otherwise matched neither boundary — and the
    failure was total and silent: the block persisted into the transcript as
    narration and its state was never recorded."""
    text = 'She waits.\r\n\r\n```state\r\n{"W": {"mood": "wry"}}\r\n```\r\n'
    narration, states = state_fence.split_block(text)
    assert narration.strip() == "She waits."
    assert states == {"W": {"mood": "wry"}}


def test_a_crlf_block_is_swallowed_by_the_redactor_too():
    text = 'She waits.\r\n\r\n```state\r\n{"W": {"mood": "wry"}}\r\n```\r\n'
    assert _stream([text]).strip() == "She waits."


def test_a_complete_opener_held_at_end_of_stream_is_not_leaked():
    """`split_block` strips an EOF-terminated opener as an unterminated block,
    so emitting it would show the player text the transcript does not have —
    and on a reroll, show it instead of the reply the server just restored."""
    assert _stream(["She waits.\n\n```state"]).strip() == "She waits."
    assert state_fence.split_block("She waits.\n\n```state")[0].strip() == "She waits."


def test_a_partial_opener_held_at_end_of_stream_still_comes_back():
    for held in ("`", "``", "```", "```s", "```stat"):
        assert _stream([f"done {held}"]) == f"done {held}"


def test_a_character_actually_called_state_is_not_read_as_the_envelope():
    """`{"state": {"mood": "calm"}}` is the instructed bare form for a character
    whose display name is `state`. Unwrapping on the key alone turned it into a
    cast of one named `mood` and lost the block outright."""
    _, states = state_fence.split_block(_block('{"state": {"mood": "calm"}}'))
    assert states == {"state": {"mood": "calm"}}


def test_the_envelope_still_unwraps_when_its_values_are_field_maps():
    _, states = state_fence.split_block(
        _block('{"state": {"Winifred": {"mood": "calm"}, "Mara": {"mood": "wry"}}}'))
    assert states == {"Winifred": {"mood": "calm"}, "Mara": {"mood": "wry"}}


def test_an_inline_fence_is_never_withheld():
    """`_OPEN` needs a line boundary, so an inline fence is not a block —
    persistence keeps it. Withholding it detached it from the context that
    disqualified it, and `finish` then stripped it as if it were trailing."""
    text = "Use ```state\n{\"W\": {\"mood\": \"wry\"}}\n```"
    assert state_fence.split_block(text)[0] == text      # persistence keeps it
    assert _stream([text]) == text                     # so the stream must too


def test_an_indented_fence_at_a_line_start_is_still_a_block():
    text = 'She waits.\n\n  ```state\n{"W": {"mood": "wry"}}\n  ```'
    assert state_fence.split_block(text)[1] == {"W": {"mood": "wry"}}
    assert _stream([text]).strip() == "She waits."


def test_a_fence_after_a_newline_split_across_deltas_is_still_caught():
    assert _stream(["She waits.", "\n\n", "```state\n{}\n```"]).strip() == "She waits."


# ---- a landed turn ---------------------------------------------------------

def test_trailing_state_block_still_stripped_from_a_landed_turn(client):
    """A reply ending in a legacy ``state`` block lands without it. Nothing
    records the block any more, but a model told to write one by a card or by
    the history it is shown still may, and it must never reach a transcript."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    cid = store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))
    sid = store.scenes.create_scene(cid, "Mara")
    actor = client.post(f"/api/campaigns/{cid}/characters", json={"name": "Mara"}).json()["character"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor}).status_code == 200
    reply = 'She waits.\n\n```state\n{"Mara": {"mood": "calm"}}\n```'
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter([reply])

    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Hello"})

    assert response.status_code == 200, response.text
    assert "```state" not in response.text
    stored = " ".join(m["content"] for m in store.scenes.read_scene(cid, sid)["messages"])
    assert "She waits." in stored
    assert "```state" not in stored
    assert "calm" not in stored
