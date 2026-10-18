"""The exported composers retain their positional options and frozen outputs."""

import pytest

from grimoire import content_parts as cp
from grimoire import model_guidance
from grimoire.store import campaign_images, context, scenes
from tests.test_context import _campaign
from tests.test_post_images_compose import _png


@pytest.fixture
def public_scene(monkeypatch, tmp_path):
    # Use the suite's supported no-encoder path; this contract must not fetch
    # tokenizer assets or depend on which encoder is installed on a device.
    monkeypatch.setattr(context.tokens, "_encoder", lambda: None)
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    campaign_images.put_image(cid, "hall", _png(), "png")
    url = f"/api/campaigns/{cid}/images/hall"
    scenes.append_message(cid, sid, "user", "Mara opens the gate.")
    scenes.append_message(cid, sid, "assistant", f"Winifred waits. ![the hall]({url})")
    # Use the shipped image-suite convention: these are frozen model-guidance
    # profiles, not an LLM fake. Both variants must survive the public wrapper.
    monkeypatch.setattr(model_guidance, "freeze_profiles",
                        lambda **_: {"m-one": "Be brief.", "m-two": "Be very thorough."})
    return cid, sid, url


def _same_complete_prompt(positional, keyword, cid, url):
    assert positional.campaign == keyword.campaign == cid
    assert list(positional) == list(keyword)
    # Include every frozen profile and its breakdown, not just the primary list.
    assert positional.snapshot() == keyword.snapshot()
    assert positional.snapshot()["primary_model"] == "m-one"
    assert positional.settings == keyword.settings
    assert positional.settings["words"] == 37
    for model in ("m-one", "m-two", "unprofiled"):
        assert positional.for_model(model) == keyword.for_model(model)
        refs = [ref for message in positional.for_model(model)
                for ref in cp.image_refs(message["content"])]
        assert refs == [cp.ref(url, "the hall", True)]
    assert positional.for_model("m-one") != positional.for_model("m-two")


@pytest.mark.parametrize("director", [False, True], ids=["narrator", "director"])
def test_exported_composers_preserve_all_existing_positional_options(public_scene, director):
    cid, sid, url = public_scene
    turn = {"response_continuation_words": "37"}
    appended = (("Regenerate guidance", "system", "Keep the harbour in view."),
                ("Roll result", "user", "Continue at the gate."))
    options = {"turn": turn, "describe": True, "model": "m-one", "actor_ref": None,
               "eligible_speakers": None, "successor_by_decision": False,
               "appended": appended, "images": 1, "plan": None, "history_view": None}
    if director:
        note = "Let Mara inspect the hall."
        expected, expected_detail = context.compose_director_turn(cid, sid, note, **options)
        messages, detail = context.compose_director_turn(
            cid, sid, note, turn, True, "m-one", None, None, False, appended, 1, None, None)
        note_index = next(index for index, message in enumerate(messages)
                          if cp.text_of(message["content"]) == note)
        assert messages[note_index]["role"] == "user"
        assert cp.image_refs(messages[note_index]["content"]) == [cp.ref(url, "the hall", True)]
        assert note_index < len(messages) - len(appended)
    else:
        expected, expected_detail = context.compose_turn(cid, sid, **options)
        messages, detail = context.compose_turn(
            cid, sid, turn, appended, True, "m-one", None, None, False, 1, None, None)
    _same_complete_prompt(messages, expected, cid, url)
    assert detail == expected_detail
    assert messages[-2:] == [{"role": role, "content": text} for _label, role, text in appended]
    labels = {label for label, _role, _text in appended}
    assert [(row["label"], row["text"]) for row in detail["sections"] if row["label"] in labels] == [
        (label, text) for label, _role, text in appended]


@pytest.mark.parametrize("director", [False, True], ids=["narrator", "director"])
def test_exported_wrappers_preserve_all_existing_positional_options(public_scene, director):
    cid, sid, url = public_scene
    turn = {"response_continuation_words": "37"}
    if director:
        note = "Let Mara inspect the hall."
        expected = context.build_director_messages(cid, sid, note, turn=turn, model="m-one", images=1)
        messages = context.build_director_messages(cid, sid, note, turn, "m-one", 1)
        notes = [message for message in messages if cp.text_of(message["content"]) == note]
        assert len(notes) == 1 and notes[0]["role"] == "user"
        assert cp.image_refs(notes[0]["content"]) == [cp.ref(url, "the hall", True)]
    else:
        appended = (("Regenerate guidance", "system", "Keep the harbour in view."),)
        expected = context.build_messages(cid, sid, turn=turn, appended=appended, model="m-one", images=1)
        messages = context.build_messages(cid, sid, turn, appended, "m-one", 1)
        assert messages[-1] == {"role": "system", "content": "Keep the harbour in view."}
    _same_complete_prompt(messages, expected, cid, url)
    assert messages.breakdown is expected.breakdown is None
