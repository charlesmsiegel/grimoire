"""Author's notes in composed turns (play controls V): the opener, director and
ordinary turns, the per-character calls, the frozen snapshot a reroll replays,
and the inspector row."""

import io

from PIL import Image

from grimoire import content_parts as cp
from grimoire import prompts, routes, store
from grimoire.store import (
    authors_notes,
    campaign_images,
    campaigns,
    context,
    scenes,
    worlds,
)
from grimoire.store.scenes import serialize as scenes_serialize
from tests.llm_fakes import FakeLLM

TEXT = "Keep the storm audible in every scene."
NOTE_MSG = prompts.render("scene/authors_note.j2", level="campaign", name="", text=TEXT)


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Mara")
    return wid, cid, sid


def _play(cid, sid, n=3):
    for i in range(1, n + 1):
        scenes.append_message(cid, sid, "user", f"P{i}")
        scenes.append_message(cid, sid, "assistant", f"R{i}")


def _note_at(msgs, content=NOTE_MSG):
    return next(n for n, m in enumerate(msgs) if m["content"] == content)


def _has_note(msgs):
    return any(isinstance(m["content"], str) and "[Author's note" in m["content"] for m in msgs)


def seed(client, module=None):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid, module=module)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("Mara", "Winifred"):
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert response.status_code == 200, response.text
    return cid, sid


def _joined(request):
    return "\n".join(m["content"] for m in request["messages"] if isinstance(m["content"], str))


# --- placement -------------------------------------------------------------


def test_depth_four_lands_before_fourth_most_recent_post(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid)
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 4, "every": 1})
    msgs = context.build_messages(cid, sid)
    i = _note_at(msgs)
    assert msgs[i]["role"] == "system" and msgs[i + 1]["content"].endswith("P2")
    assert msgs[i - 1]["content"].endswith("R1")


def test_depth_zero_after_last_post_and_large_depth_clamps(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid)
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 0, "every": 1})
    msgs = context.build_messages(cid, sid)
    i = _note_at(msgs)
    assert i == len(msgs) - 1 and msgs[i - 1]["content"].endswith("R3")
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 50, "every": 1})
    msgs = context.build_messages(cid, sid)
    assert _note_at(msgs) == 1 and msgs[2]["content"].endswith("P1")


def test_excluded_posts_and_director_notes_do_not_count_toward_depth(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid)
    scenes.append_message(cid, sid, "user", "X")
    scenes.append_message(cid, sid, "assistant", "Y")
    scenes.append_message(cid, sid, "assistant", "steer", speaker=scenes_serialize.DIRECTOR_SPEAKER)
    scenes.write.set_excluded(cid, sid, 6, True)
    scenes.write.set_excluded(cid, sid, 7, True)
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 2, "every": 1})
    msgs = context.build_messages(cid, sid)
    i = _note_at(msgs)
    assert msgs[i + 1]["content"].endswith("P3")


def test_every_three_applies_on_three_not_four(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid)
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 0, "every": 3})
    assert NOTE_MSG in [m["content"] for m in context.build_messages(cid, sid)]
    scenes.append_message(cid, sid, "user", "P4")
    assert NOTE_MSG not in [m["content"] for m in context.build_messages(cid, sid)]


def test_an_excluded_post_still_counts_toward_the_cadence(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid)
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 0, "every": 3})
    scenes.write.set_excluded(cid, sid, 0, True)
    assert NOTE_MSG in [m["content"] for m in context.build_messages(cid, sid)]


def test_scene_note_follows_the_order_campaign_then_scene(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid)
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 0, "every": 1})
    authors_notes.set_scene(cid, scenes.ensure_identity(cid, sid),
                            {"text": "Rain.", "depth": 0, "every": 1})
    msgs = context.build_messages(cid, sid)
    assert [m["content"] for m in msgs[-2:]] == [NOTE_MSG, "[Author's note: Rain.]"]


def test_macros_in_a_note_are_expanded(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid)
    authors_notes.set_campaign(cid, {"text": "Hear the {{random:storm}}.", "depth": 0,
                                     "every": 1})
    msgs = context.build_messages(cid, sid)
    assert msgs[-1]["content"] == "[Author's note: Hear the storm.]"


# --- per-character calls, the snapshot, the director turn and the opener ----


def test_character_note_only_in_its_own_call(client):
    cid, sid = seed(client)
    store.authors_notes.set_character(cid, "characters:winifred",
                                      {"text": "Winifred whispers.", "depth": 0, "every": 1})
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":"characters:winifred"}\n```'],
                    ['Winifred answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                    json={"content": "Hello", "speaker_ref": "characters:mara"})
    assert r.status_code == 200, r.text
    assert len(fake.requests) == 2
    assert "Winifred whispers." not in _joined(fake.requests[0])
    assert "[Author's note for Winifred: Winifred whispers.]" in _joined(fake.requests[1])
    narrator = store.context.compose_turn(cid, sid, describe=False, actor_ref="grimoire")[0]
    assert "Winifred whispers." not in str(narrator)
    assert "Winifred whispers." not in str(store.context.build_messages(cid, sid))


def test_reroll_replays_the_frozen_note(client):
    cid, sid = seed(client)
    store.authors_notes.set_campaign(cid, {"text": "Old steer.", "depth": 0, "every": 1})
    fake = FakeLLM([['First.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    r = client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    assert r.status_code == 200, r.text
    assert "Old steer." in _joined(fake.requests[0])
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    store.authors_notes.set_campaign(cid, {"text": "New steer.", "depth": 0, "every": 1})
    retry = FakeLLM([['Replacement.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: retry
    result = client.post(base + f"/responses/{rid}/regenerate", json={})
    assert "error" not in result.text, result.text
    assert retry.calls == 1
    assert "Old steer." in _joined(retry.requests[0])
    assert "New steer." not in _joined(retry.requests[0])


def test_director_turn_carries_the_note(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid)
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 1, "every": 1})
    msgs = context.compose_director_turn(cid, sid, "Make it rain.", describe=False)[0]
    i = _note_at(msgs)
    last_user = max(n for n, m in enumerate(msgs) if m["role"] == "user")
    assert i < last_user and msgs[last_user]["content"] == "Make it rain."


def test_opener_gets_every_one_notes_before_the_instruction(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 4, "every": 1})
    authors_notes.set_scene(cid, scenes.ensure_identity(cid, sid),
                            {"text": "Rain.", "depth": 4, "every": 2})
    msgs, detail = context.compose_opener(cid, sid, "A storm rolls in.")
    i = _note_at(msgs)
    assert msgs[i - 1] == {"role": "user", "content": "A storm rolls in."}
    assert i < len(msgs) - 1 and msgs[-1]["role"] == "system"
    assert "Rain." not in str(msgs)
    labels = [r["label"] for r in detail["sections"] if r["id"].startswith("appended_")]
    assert "Author's note — campaign" in labels


def test_opener_over_a_played_scene_applies_only_every_one_notes(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid, 2)   # turn 2 would apply an every-2 note on an ordinary turn
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 4, "every": 1})
    authors_notes.set_scene(cid, scenes.ensure_identity(cid, sid),
                            {"text": "Rain.", "depth": 4, "every": 2})
    assert "[Author's note: Rain.]" in [m["content"] for m in context.build_messages(cid, sid)]
    msgs = context.build_opener_messages(cid, sid, "A storm rolls in.")
    assert NOTE_MSG in [m["content"] for m in msgs]
    assert "Rain." not in str(msgs)


def test_garbled_file_composes_without_notes(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid)
    (campaigns.campaign_root(cid) / "authors_notes.json").write_text("{not json")
    msgs = context.build_messages(cid, sid)
    assert not _has_note(msgs)


def test_no_notes_prompt_is_byte_identical(monkeypatch, tmp_path):
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    _play(cid, sid)
    before = context.build_messages(cid, sid)
    authors_notes.set_campaign(cid, None)
    assert (campaigns.campaign_root(cid) / "authors_notes.json").exists()
    assert context.build_messages(cid, sid) == before



def test_notes_ride_the_image_reference_projection(monkeypatch, tmp_path):
    """With post images sent as references (#377) the note sits at the same
    point, and the text lowering is still the images=0 prompt."""
    _, cid, sid = _campaign(monkeypatch, tmp_path)
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "PNG")
    campaign_images.put_image(cid, "hall", buf.getvalue(), "png")
    scenes.append_message(cid, sid, "user", "P1")
    scenes.append_message(cid, sid, "assistant", f"R1 ![the hall](/api/campaigns/{cid}/images/hall)")
    scenes.append_message(cid, sid, "user", "P2")
    scenes.append_message(cid, sid, "assistant", "R2")
    authors_notes.set_campaign(cid, {"text": TEXT, "depth": 2, "every": 1})
    plain, _ = context.compose_turn(cid, sid, images=0)
    rich, _ = context.compose_turn(cid, sid, images=1)
    assert cp.as_text(list(rich)) == list(plain)
    i = _note_at(list(rich))
    assert rich[i]["role"] == "system"
    assert [r["alt"] for r in cp.image_refs(rich[i + 1]["content"])] == ["the hall"]
