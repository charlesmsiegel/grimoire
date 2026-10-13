"""11-S3 phase B: compose renders only what a step's view allows (spec 11 §6).

The narrator gets `history_recall` (annotated) and `history_narrator_only`;
an NPC gets `history_known` and `history_suspected`, built from `visible()`
alone. A view for another reader is refused, the basis codes never reach a
message, and with no view nothing renders at all.
"""

from __future__ import annotations

import pytest

from evals.epistemic import synth
from grimoire.store import campaigns, config, tokens, worlds
from grimoire.store.context import assemble, epistemic, layout
from grimoire.store.context import history as history_section
from grimoire.store.context import pack as packer
from grimoire.store.history import model

BEFORE = "QUILLNEEDLE"
SHARED = "LANTERNNEEDLE"
VAULT = "VAULTNEEDLE"
A_SUMMARY = "SUMMARYNEEDLE"
B_SUMMARY = "VAULTSUMMARYNEEDLE"
BASIS_CODES = ("presence_interval", "not_recorded_present", "summary_needs_grant",
               "override_group", "override_all", "pc_speaker", "scene_cast", "no_record",
               "unverifiable", "decision_abstained")


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    out = campaigns.create_campaign("Saltmarch", wid)
    config.write_config(history_recall_depth="3", recap_depth="0")
    return out


@pytest.fixture
def stage(cid):
    """The pier (Mara throughout, Winifred from its second post), the vault
    (nobody of theirs), and the harbour now, with both of them on stage."""
    pier = synth.Stage(cid, "The Pier")
    mara, maid, mvid = synth.npc(cid, "Mara")
    win, waid, wvid = synth.npc(cid, "Winifred")
    synth.enter(pier, maid, mvid)
    pier.say(f"Seraphine reaches the pier at dusk with the {BEFORE}.")
    synth.enter(pier, waid, wvid)
    pier.reply(mara, "Mara", f"Mara hands over the {SHARED}.")
    vault = synth.Stage(cid, "The Vault")
    vault.say(f"Seraphine opens the {VAULT} alone.")
    now = synth.Stage(cid, "The Harbour")
    synth.enter(now, maid, mvid)
    synth.enter(now, waid, wvid)
    now.say("Seraphine asks about the pier.")
    items = (pier.item(summary=A_SUMMARY), vault.item(summary=B_SUMMARY))
    return {"cid": cid, "pier": pier, "vault": vault, "now": now, "mara": mara,
            "win": win, "items": items}


def _sections(detail):
    return {s["id"]: s for s in detail["sections"] if s["id"] in history_section.FRAMES}


def _compose(s, actor, view):
    return assemble.compose_turn(s["cid"], s["now"].sid, actor_ref=actor, history_view=view)


def _view(s, actor, items=None):
    return synth.for_compose(s["cid"], s["now"].sid, actor, s["items"] if items is None else items)


# ------------------------------------------------------------ the actor


def test_an_actor_prompt_holds_only_what_it_witnessed(stage):
    messages, detail = _compose(stage, stage["win"], _view(stage, stage["win"]))
    sent = str(messages)
    assert SHARED in sent
    for needle in (BEFORE, VAULT, A_SUMMARY, B_SUMMARY, "The Vault", "Known to:",
                   "Suspected by:", "kept from the cast", "Secret knowledge"):
        assert needle not in sent, needle
    rows = _sections(detail)
    assert "What Winifred remembers or knows" in rows["history_known"]["text"]
    assert "You were there:" in rows["history_known"]["text"]
    # The pier's title is not hers: she was there for part of it only.
    assert "The Pier" not in rows["history_known"]["text"]
    assert not rows.get("history_suspected", {}).get("text")


def test_a_whole_scene_grant_brings_the_summary_and_title_labelled(stage):
    stage["pier"].absorb(A_SUMMARY)
    synth.grant(stage["pier"], stage["win"], "Winifred")
    _messages, detail = _compose(stage, stage["win"], _view(stage, stage["win"]))
    text = _sections(detail)["history_known"]["text"]
    assert A_SUMMARY in text and "**The Pier**" in text and BEFORE in text
    assert "You know this:" in text


def test_a_suspicion_goes_to_its_own_section_and_tier(stage):
    synth.grant(stage["vault"], stage["win"], "Winifred", "suspects")
    _messages, detail = _compose(stage, stage["win"], _view(stage, stage["win"]))
    rows = _sections(detail)
    assert VAULT in rows["history_suspected"]["text"]
    assert "What Winifred suspects, unconfirmed" in rows["history_suspected"]["text"]
    assert VAULT not in rows["history_known"]["text"]
    assert rows["history_suspected"]["tier"] == packer.HISTORY_ASIDE
    assert rows["history_known"]["tier"] == packer.HISTORY_RECALL


def test_an_actor_whose_own_unaware_withholds_an_item_gets_no_narrator_section(stage):
    synth.grant(stage["vault"], stage["mara"], "Mara", "unaware")
    view = _view(stage, stage["mara"])
    messages, detail = _compose(stage, stage["mara"], view)
    sent = str(messages)
    for needle in (VAULT, B_SUMMARY, "kept from the cast", "Secret knowledge", "Known to:"):
        assert needle not in sent, needle
    assert "history_narrator_only" not in _sections(detail)


def test_an_actor_with_nothing_visible_keeps_a_held_back_row(stage):
    view = _view(stage, stage["mara"], (stage["items"][1],))
    messages, detail = _compose(stage, stage["mara"], view)
    row = _sections(detail)["history_known"]
    assert row["text"] == "" and VAULT not in str(messages)
    [item] = row["history"]["epistemic"]["items"]
    assert item["slices"][0]["access"] == "unknown"


# ------------------------------------------------------------ the narrator


def test_the_narrator_annotates_who_knows_in_present_order(stage):
    _messages, detail = _compose(stage, None, _view(stage, None))
    text = _sections(detail)["history_recall"]["text"]
    assert "Known to: Mara, Winifred" in text
    head, _, _rest = text.partition("Known to:")
    assert "**The Pier**" in head.splitlines()[-1]  # directly after the header line
    assert VAULT in text  # nobody on stage has the vault: unattributed, rendered as 09


def test_a_withheld_item_is_the_narrators_alone_and_in_one_section(stage):
    synth.grant(stage["vault"], stage["mara"], "Mara", "unaware")
    _messages, detail = _compose(stage, None, _view(stage, None))
    rows = _sections(detail)
    aside = rows["history_narrator_only"]["text"]
    assert VAULT in aside and "kept from the cast" in aside and "Secret knowledge" in aside
    assert VAULT not in rows["history_recall"]["text"]
    assert rows["history_narrator_only"]["tier"] == packer.HISTORY_ASIDE


def test_an_unattributed_item_renders_byte_identical_to_09(stage):
    vault = stage["items"][1]
    view = _view(stage, None, (vault,))
    assert [c.state for c in view.items] == ["unattributed"]
    part = history_section.parts(view, {})["history_recall"]
    ours, _ = history_section.section(view.evidence, lambda t: t, len, {}, config.read_config(),
                                      part)
    theirs, _ = history_section.section(model.Evidence((vault,), ceiling=1200),
                                        lambda t: t, len, {}, config.read_config())
    assert ours == theirs and VAULT in ours


def test_the_two_narrator_sections_split_one_ceiling(stage):
    synth.grant(stage["vault"], stage["mara"], "Mara", "unaware")
    config.write_config(history_recall_budget="200")
    view = synth.for_compose(stage["cid"], stage["now"].sid, None, stage["items"], ceiling=200)
    _messages, detail = _compose(stage, None, view)
    rows = _sections(detail)
    spent = sum(tokens.count_tokens(rows[k]["text"]) for k in history_section.NARRATOR_SECTIONS
                if k in rows)
    assert spent <= 200


# ------------------------------------------------------------ refusals


def test_a_view_for_another_reader_is_refused(stage):
    with pytest.raises(ValueError):
        _compose(stage, stage["win"], _view(stage, None))
    with pytest.raises(ValueError):
        _compose(stage, stage["win"], _view(stage, stage["mara"]))
    with pytest.raises(ValueError):
        _compose(stage, None, _view(stage, stage["win"]))
    elsewhere = synth.for_compose(stage["cid"], stage["pier"].sid, None, stage["items"])
    with pytest.raises(ValueError):
        _compose(stage, None, elsewhere)


def test_the_basis_never_reaches_the_prompt(stage):
    synth.grant(stage["vault"], stage["win"], "Winifred", "suspects")
    for actor in (None, stage["win"], stage["mara"]):
        messages, detail = _compose(stage, actor, _view(stage, actor))
        sent = str(messages)
        for code in BASIS_CODES:
            assert code not in sent, (actor, code)
        assert any(r.get("history", {}).get("epistemic") for r in _sections(detail).values())


# ------------------------------------------------------------ off and layout


def test_depth_on_with_no_view_is_byte_identical_to_depth_off(stage):
    on = assemble.compose_turn(stage["cid"], stage["now"].sid, actor_ref=stage["win"])
    config.write_config(history_recall_depth="0")
    off = assemble.compose_turn(stage["cid"], stage["now"].sid, actor_ref=stage["win"])
    assert on == off


@pytest.mark.parametrize("actor", [None, "win"])
def test_turning_recalled_history_off_removes_all_four(stage, actor):
    ref = stage[actor] if actor else None
    synth.grant(stage["vault"], stage["mara"], "Mara", "unaware")
    synth.grant(stage["vault"], stage["win"], "Winifred", "suspects")
    config.write_config(prompt_layout_enabled="on")
    layout.write_layout([{"id": "history_recall", "enabled": False}])
    messages, detail = _compose(stage, ref, _view(stage, ref))
    assert not _sections(detail)
    assert SHARED not in str(messages) and VAULT not in str(messages)


def test_the_coupled_sections_follow_their_parent():
    catalog = assemble.SECTIONS
    ids = [s.id for s in catalog]
    at = ids.index("history_recall")
    assert ids[at + 1:at + 4] == ["history_narrator_only", "history_known", "history_suspected"]
    stored = [{"id": "history_known", "enabled": False, "label": "x"},
              {"id": "history_recall", "enabled": True}]
    merged = [s.id for s in layout.merge(catalog, stored)]
    assert merged.index("history_known") == merged.index("history_recall") + 2
    assert all(row["id"] not in layout.COUPLED for row in layout.describe(catalog, stored))
    off = [s.id for s in layout.merge(catalog, [{"id": "history_recall", "enabled": False}])]
    assert not set(layout.COUPLED) & set(off)


def test_enabled_ids_answers_a_coupled_id_through_its_parent(cid):
    config.write_config(prompt_layout_enabled="on")
    layout.write_layout([{"id": "history_recall", "enabled": False},
                         {"id": "history_known", "enabled": True}])
    assert layout.enabled_ids(("history_known", "history_recall")) == frozenset()
    layout.write_layout([{"id": "history_known", "enabled": False}])
    assert layout.enabled_ids(("history_known",)) == {"history_known"}


# ------------------------------------------------------------ shedding


def test_each_section_sheds_per_item(stage):
    synth.grant(stage["vault"], stage["win"], "Winifred", "suspects")
    synth.grant(stage["pier"], stage["win"], "Winifred")
    cfg = config.read_config()
    for actor in (None, stage["win"]):
        view = _view(stage, actor)
        for sid, part in history_section.parts(view, {}, "Winifred").items():
            if not part.items:
                continue
            _text, extra = history_section.section(view.evidence, lambda t: t, len, {}, cfg,
                                                   part)
            assert [u["ref"] for u in extra["shed"]["units"]] == [
                i.scene.key for i in part.items], sid


def test_aside_gives_way_before_recalled_history():
    assert packer.DROP_ORDER.index(packer.HISTORY_ASIDE) < packer.DROP_ORDER.index(
        packer.HISTORY_RECALL)


def test_the_narrator_view_pass_through_keeps_09_rows(cid):
    sid = synth.Stage(cid, "Now").sid
    item = model.EvidenceItem(model.SceneRef("001--old", None), "Old", summary="Kept.")
    view = epistemic.narrator_view(cid, sid, model.Evidence((item,), ceiling=1200))
    _messages, detail = assemble.compose_turn(cid, sid, history_view=view)
    assert "Kept." in _sections(detail)["history_recall"]["text"]
