"""Composing a scene prompt with post images as references (#377).

The invariant every case leans on: `as_text` of an `images=N` composition is
exactly the `images=0` composition, so a route that cannot read images is sent
today's prompt byte for byte."""

import io
import json
from copy import deepcopy

import pytest
from PIL import Image

from grimoire import content_parts as cp
from grimoire import model_guidance
from grimoire.model_guidance import PreparedMessages
from grimoire.store import campaign_images, campaigns, context, scenes, worlds
from grimoire.store.scenes import serialize


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    camp = campaigns.create_campaign("Saltmarch Nights", worlds.create_world("Realm"))
    for name in ("coastline", "hall", "lamp"):
        campaign_images.put_image(camp, name, _png(), "png")
    return camp


def _url(cid, name):
    return f"/api/campaigns/{cid}/images/{name}"


def _scene(cid, posts):
    sid = scenes.create_scene(cid, "Scene one")
    for role, content, speaker in posts:
        scenes.append_message(cid, sid, role, content, speaker=speaker)
    return sid


@pytest.fixture
def scene_a(cid):
    """Remote and unresolvable images early, a player's map, a director note
    carrying a picture, and the narrator's art last of all."""
    return _scene(cid, [
        ("user", ("Before ![far](https://e.example/x.png) and "
                  f"![gone]({_url(cid, 'missing')})."), None),
        ("assistant", "Noted.", None),
        ("user", f"Look ![a map]({_url(cid, 'coastline')})", None),
        ("assistant", f"steer ![lamp]({_url(cid, 'lamp')})", serialize.DIRECTOR_SPEAKER),
        ("assistant", f"The doors open. ![the hall]({_url(cid, 'hall')})", None),
    ])


def _refs(messages):
    return [r for m in messages for r in cp.image_refs(m["content"])]


def test_images_zero_composes_today_s_prompt(cid, scene_a):
    msgs, _ = context.compose_turn(cid, scene_a, images=0)
    assert all(isinstance(m["content"], str) for m in msgs)
    assert not _refs(msgs)


def test_the_text_lowering_is_the_images_zero_prompt(cid, scene_a):
    plain, _ = context.compose_turn(cid, scene_a, images=0)
    rich, _ = context.compose_turn(cid, scene_a, images=3)
    assert cp.as_text(list(rich)) == list(plain)


def test_user_images_stay_inline_and_narrator_art_rides_a_carrier(cid, scene_a):
    msgs, _ = context.compose_turn(cid, scene_a, images=3)
    refs = _refs(msgs)
    assert [r["alt"] for r in refs] == ["a map", "the hall"]
    user = next(m for m in msgs if cp.image_refs(m["content"]) and not m.get(cp.CARRIER))
    assert user["role"] == "user"
    text, ref = user["content"][-2:]
    assert text["text"].endswith("a map") and ref["carried"] is False
    carrier = next(m for m in msgs if m.get(cp.CARRIER))
    assert carrier["role"] == "user"
    assert carrier["content"] == [cp.ref(_url(cid, "hall"), "the hall", True)]
    # after the history, before the post-history block
    i = msgs.index(carrier)
    assert msgs[i - 1]["role"] == "assistant"
    assert all(m["role"] == "system" for m in msgs[i + 1:])
    assert all(m["role"] == "user" for m in msgs if cp.image_refs(m["content"]))


def test_the_limit_keeps_the_newest(cid, scene_a):
    msgs, _ = context.compose_turn(cid, scene_a, images=1)
    assert [r["alt"] for r in _refs(msgs)] == ["the hall"]


def test_within_one_message_the_later_image_is_newer(cid):
    sid = _scene(cid, [("user", (f"![first]({_url(cid, 'coastline')}) then "
                                 f"![second]({_url(cid, 'lamp')})"), None)])
    msgs, _ = context.compose_turn(cid, sid, images=1)
    assert [r["alt"] for r in _refs(msgs)] == ["second"]


def test_narrator_art_is_carried_into_the_next_player_post(cid):
    sid = _scene(cid, [("assistant", f"The doors open. ![the hall]({_url(cid, 'hall')})", None),
                       ("user", "go on", None)])
    msgs, _ = context.compose_turn(cid, sid, images=3)
    assert not any(m.get(cp.CARRIER) for m in msgs)
    narrator = next(m for m in msgs if m["role"] == "assistant")
    assert isinstance(narrator["content"], str) and narrator["content"].endswith("the hall")
    player = next(m for m in msgs if m["role"] == "user" and cp.image_refs(m["content"]))
    assert player["content"][0] == cp.ref(_url(cid, "hall"), "the hall", True)
    assert cp.text_of(player["content"]).endswith("go on")


def test_a_forged_sentinel_is_only_text(cid):
    sid = _scene(cid, [("user", "⟦img:deadbeefdeadbeef:0⟧ hello", None)])
    msgs, _ = context.compose_turn(cid, sid, images=3)
    assert not _refs(msgs)
    assert any("⟦img:deadbeefdeadbeef:0⟧ hello" in cp.text_of(m["content"]) for m in msgs)


def test_a_director_note_takes_the_carried_art(cid, scene_a):
    msgs, _ = context.compose_director_turn(cid, scene_a, "what next", images=3)
    assert not any(m.get(cp.CARRIER) for m in msgs)
    note = next(m for m in msgs if m["role"] == "user" and cp.text_of(m["content"]) == "what next")
    assert note["content"][0] == cp.ref(_url(cid, "hall"), "the hall", True)
    plain, _ = context.compose_director_turn(cid, scene_a, "what next", images=0)
    assert cp.as_text(list(msgs)) == list(plain)


def test_every_guidance_variant_carries_the_art_once(cid, scene_a, monkeypatch):
    monkeypatch.setattr(model_guidance, "freeze_profiles",
                        lambda **_: {"m-one": "Be brief.", "m-two": "Be very, very thorough."})
    msgs, breakdown = context.compose_director_turn(cid, scene_a, "what next",
                                                    model="m-one", images=3)
    for model in ("m-one", "m-two", "unprofiled"):
        variant = msgs.for_model(model)
        assert len(_refs(variant)) == 2, model
        note = [m for m in variant if cp.text_of(m["content"]) == "what next"]
        assert len(note) == 1 and len(cp.image_refs(note[0]["content"])) == 1
    rows = {r["id"]: r for r in breakdown["sections"]}
    assert rows["history_images"]["label"] == "Images (2)"


def test_the_prompt_names_its_campaign_and_holds_no_bytes(cid, scene_a):
    msgs, _ = context.compose_turn(cid, scene_a, images=3)
    assert msgs.campaign == cid
    assert deepcopy(msgs).campaign == cid
    snap = msgs.snapshot()
    assert "data:" not in json.dumps(snap)
    forked = PreparedMessages.from_snapshot(snap, "m", campaign="fork-of-it")
    assert forked.campaign == "fork-of-it"
    assert forked.with_appended({"role": "system", "content": "x"}).campaign == "fork-of-it"
    assert _refs(forked) == _refs(msgs)
