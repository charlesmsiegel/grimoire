"""Confidence routing of absorb proposals (#110) and the speaker authority that
weights it (#112)."""

import pytest

from grimoire.store import (absorb, appearances, campaigns, characters, entities, pcs,
                            scenes, worlds)
from grimoire.store.absorb import routing


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid), worlds.world_root(wid)


def _scene(cid, wroot):
    """A played scene: one PC, one NPC who speaks, one NPC who never does."""
    sera = characters.create_character(wroot, "Seraphine Vale", "default",
                                       characters.blank_card("Seraphine Vale"))[0]
    mara = characters.create_character(wroot, "Mara", "default",
                                       characters.blank_card("Mara"))[0]
    croot = campaigns.campaign_root(cid)
    win, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))
    sid = scenes.create_scene(cid, "The Pier at Dusk")
    appearances.appear(cid, sid, "characters", sera, "default", "npc")
    appearances.appear(cid, sid, "characters", mara, "default", "npc")
    appearances.appear(cid, sid, "pcs", win, "default", "player")
    scenes.append_message(cid, sid, "user", "Whose crates are those?", speaker="Winifred")
    scenes.append_reply(cid, sid, [
        {"speaker": "Seraphine Vale", "content": "Mine, until midnight."},
        {"speaker": None, "content": "Fog rolls in off the water."},
    ])
    return sid, sera, mara, win


# ------------------------------------------------------------------- the bands

def test_the_band_edges_hold_the_properties_the_weights_are_chosen_for():
    """Three invariants the constants exist to satisfy. Asserted rather than
    left to the docstring: each is a property of two numbers that live apart,
    so tuning either one alone silently drops it."""
    # A citation the transcript cannot corroborate is collapsed however sure
    # the model claims to be — certainty must not buy a fabrication out.
    assert routing.band(1.0 * routing.WEIGHTS[routing.UNATTRIBUTED]) == "low"
    # A row that cited nobody can never be ranked as a strong one...
    assert routing.band(1.0 * routing.WEIGHTS[routing.UNCITED]) != "high"
    # ...but with no certainty given it still pre-checks, which is what keeps
    # rows that legitimately have no speaker behaving as they did before.
    assert routing.band(routing.ASSUMED_CERTAINTY * routing.WEIGHTS[routing.UNCITED]) == "medium"
    # Hearsay is not collapsed merely for being hearsay.
    assert routing.band(routing.ASSUMED_CERTAINTY * routing.WEIGHTS[routing.OTHER]) == "medium"


def test_band_edges_belong_to_the_more_visible_side():
    assert routing.band(routing.HIGH) == "high"
    assert routing.band(routing.LOW) == "medium"
    assert routing.band(routing.LOW - 1e-9) == "low"


# --------------------------------------------------------------- the authority

def test_narration_outranks_a_character_speaking_about_themself(monkeypatch, tmp_path):
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, sera, _, _ = _scene(cid, wroot)
    index = routing.speaker_index(cid, sid)
    subject = (f"characters:{sera}",)

    assert routing.authority(index, "Grimoire", subject) == routing.NARRATION
    assert routing.authority(index, "Seraphine Vale", subject) == routing.SELF
    narrated = routing.review(index, {"speaker": "Grimoire", "certainty": 0.8}, subject)
    claimed = routing.review(index, {"speaker": "Seraphine Vale", "certainty": 0.8}, subject)
    assert narrated["score"] > claimed["score"]


def test_a_players_own_unlabelled_post_is_narration(monkeypatch, tmp_path):
    """"You" is a role label, not a name: the player narrating their own turn.
    Checked before the cast, or a character called "You" would shadow it."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    assert routing.authority(routing.speaker_index(cid, sid), "You") == routing.NARRATION


def test_a_claim_about_someone_else_is_hearsay_not_first_hand(monkeypatch, tmp_path):
    """The tier turns on WHOSE record is being changed. The same speaker is
    first-hand about their own state and third-party about Mara's."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, sera, mara, _ = _scene(cid, wroot)
    index = routing.speaker_index(cid, sid)
    assert routing.authority(index, "Seraphine Vale", (f"characters:{sera}",)) == routing.SELF
    assert routing.authority(index, "Seraphine Vale", (f"characters:{mara}",)) == routing.OTHER


def test_a_record_with_no_personal_subject_never_reaches_first_hand(monkeypatch, tmp_path):
    """A lore entry, a plot thread and the weather belong to nobody, so a
    character asserting something about one is a third-party claim — which is
    the placement rule #112 asks for, applied to the score."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    assert routing.authority(routing.speaker_index(cid, sid), "Seraphine Vale") == routing.OTHER


def test_a_speaker_the_transcript_never_had_is_uncorroborated(monkeypatch, tmp_path):
    """Not "weak evidence" — evidence the citation was invented. It outranks
    nothing, including an absent citation."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    index = routing.speaker_index(cid, sid)
    assert routing.authority(index, "The Harbourmaster") == routing.UNATTRIBUTED
    assert routing.authority(index, "") == routing.UNCITED
    assert (routing.WEIGHTS[routing.UNATTRIBUTED] < routing.WEIGHTS[routing.UNCITED])


def test_a_present_but_silent_cast_member_is_still_uncorroborated(monkeypatch, tmp_path):
    """Mara is in the scene and never speaks. A quote attributed to her is a
    quote the transcript does not contain, and cast membership must not stand
    in for having said something."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    assert routing.authority(routing.speaker_index(cid, sid), "Mara") == routing.UNATTRIBUTED


def test_a_citation_may_shorten_a_speakers_name(monkeypatch, tmp_path):
    """The transcript labels her "Seraphine Vale"; a model citing "Seraphine"
    means her, by the same unambiguous-prefix rule the transcript parser uses."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, sera, _, _ = _scene(cid, wroot)
    index = routing.speaker_index(cid, sid)
    assert routing.authority(index, "Seraphine", (f"characters:{sera}",)) == routing.SELF


def test_a_sub_speaker_label_still_names_its_speaker(monkeypatch, tmp_path):
    """A line stored under "Seraphine Vale (aside)" renders under that label,
    and a citation naming the character plainly has to still resolve."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, sera, _, _ = _scene(cid, wroot)
    scenes.append_reply(cid, sid, [{"speaker": "Seraphine Vale (aside)",
                                    "content": "Not that they'd believe me."}])
    index = routing.speaker_index(cid, sid)
    assert routing.authority(index, "Seraphine Vale", (f"characters:{sera}",)) == routing.SELF


def test_an_unreadable_scene_leaves_every_citation_uncorroborated(monkeypatch, tmp_path):
    """This runs after the extraction call was paid for. A missing scene has to
    degrade to "nothing corroborates this", not to a 500."""
    cid, _ = _campaign(monkeypatch, tmp_path)
    index = routing.speaker_index(cid, "999--nope")
    assert index == {"labels": [], "refs": {}}
    assert routing.authority(index, "Seraphine Vale") == routing.UNATTRIBUTED


# ------------------------------------------------------------- the review block

def test_review_reports_what_the_model_said_and_what_was_proven(monkeypatch, tmp_path):
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, sera, _, _ = _scene(cid, wroot)
    index = routing.speaker_index(cid, sid)
    row = {"quote": "Mine, until midnight.", "speaker": "Seraphine Vale", "certainty": 0.9}
    out = routing.review(index, row, (f"characters:{sera}",))
    assert out == {"certainty": 0.9, "quote": "Mine, until midnight.",
                   "speaker": "Seraphine Vale", "authority": routing.SELF,
                   "score": pytest.approx(0.72), "band": "high"}


def test_a_missing_certainty_is_reported_as_missing_and_still_banded(monkeypatch, tmp_path):
    """The two fields are allowed to disagree: `certainty` is None because the
    model said nothing, and the band still has to be decided from something."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    out = routing.review(routing.speaker_index(cid, sid), {})
    assert out["certainty"] is None and out["authority"] == routing.UNCITED
    assert out["band"] == "medium"


def test_a_confident_fabrication_is_still_collapsed(monkeypatch, tmp_path):
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    out = routing.review(routing.speaker_index(cid, sid),
                         {"speaker": "The Harbourmaster", "certainty": 1.0})
    assert out["authority"] == routing.UNATTRIBUTED and out["band"] == "low"


def test_review_re_clamps_a_certainty_that_escaped_the_parser(monkeypatch, tmp_path):
    """`parse._certainty` already clamps, but a score outside 0-1 would move
    both band edges for one row, so it is proven here rather than assumed."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    index = routing.speaker_index(cid, sid)
    assert routing.review(index, {"speaker": "Grimoire", "certainty": 40})["score"] == 1.0
    assert routing.review(index, {"speaker": "Grimoire", "certainty": float("nan")}
                          )["certainty"] is None
    assert routing.review(index, {"speaker": "Grimoire", "certainty": True}
                          )["certainty"] is None


def test_two_present_actors_sharing_a_name_name_neither(monkeypatch, tmp_path):
    """Keyed by display name, the second would overwrite the first and a
    citation would resolve to whichever the cast listed last — reading a
    speaker as the subject of a record they are not."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, sera, _, _ = _scene(cid, wroot)
    twin = characters.create_character(wroot, "Seraphine Vale", "default",
                                       characters.blank_card("Seraphine Vale"))[0]
    appearances.appear(cid, sid, "characters", twin, "default", "npc")
    index = routing.speaker_index(cid, sid)
    assert routing.authority(index, "Seraphine Vale", (f"characters:{sera}",)) == routing.OTHER
    assert routing.authority(index, "Seraphine Vale", (f"characters:{twin}",)) == routing.OTHER


def test_a_scene_transition_line_reads_as_narration(monkeypatch, tmp_path):
    """Transitions are stored under an internal speaker the transcript never
    shows and render as unlabelled narration. Left untranslated, that marker
    would be a label the model could never have seen but this check would
    accept."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    index = routing.speaker_index(cid, sid)
    assert scenes.TRANSITION_SPEAKER not in index["labels"]
    assert routing.authority(index, scenes.TRANSITION_SPEAKER) == routing.UNATTRIBUTED


def test_lore_is_not_a_character(monkeypatch, tmp_path):
    """A guard on the subject convention: `subjects` holds actor tokens, so an
    entity id passed by mistake cannot be matched by a speaker name."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    entities.create_entity(wroot, "lore", "The Ledger", "It lists bribes.", keys="ledger")
    index = routing.speaker_index(cid, sid)
    assert routing.authority(index, "Seraphine Vale", ("lore:the-ledger",)) == routing.OTHER


# ------------------------------------------------ what materialize does with it

def _materialized(cid, sid, parsed):
    from grimoire.store import absorb
    return {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}


def test_every_staged_row_carries_a_review_block(monkeypatch, tmp_path):
    """A row without one is indistinguishable from the dossier/voice/sheet rows
    staged elsewhere, which the panel pre-approves — so a section that forgot to
    stamp would quietly opt itself out of routing."""
    from grimoire.store import commitments, entities, groupstate, playstate, relationships
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, sera, mara, win = _scene(cid, wroot)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(wroot, "lore", "The Ledger", "It lists bribes.", keys="ledger")
    gid = entities.create_entity(wroot, "groups", "Salt Circle", "A cabal.")
    groupstate.write_state(croot, gid, "## Goals\nReach the ledger.")
    playstate.write_state(croot, sera, "Wary.")
    relationships.set_feeling(cid, f"characters:{sera}", f"pcs:{win}", 1, 1, 3, "wary")
    commitments.set_movement(cid, "the-deadline", "The deadline", "threat", "open", "",
                             "Sworn on the stair.", sid)

    edits = absorb.materialize(cid, sid, {
        "character_state_edits": [{"id": sera, "current_state": "Bleeding."}],
        "group_state_edits": [{"id": gid, "goals": "Burn the ledger."}],
        "lore_edits": [{"id": "the-ledger", "append": "It names the harbourmaster."}],
        "authored_edits": [{"id": sera, "field": "personality", "text": "colder"}],
        "relationship_deltas": [{"from": f"characters:{sera}", "to": f"pcs:{win}",
                                 "trust": 4, "affection": 3, "tension": 1, "note": "warm"}],
        "bond_changes": [{"a": f"characters:{sera}", "b": f"pcs:{win}", "type": "allies"}],
        "plot_movements": [{"title": "The forged map", "beat": "It surfaced."}],
        "commitment_movements": [{"id": "the-deadline", "beat": "She let it pass."}],
        "new_characters": [{"name": "The Harbourmaster", "description": "[character(\"x\")]"}],
        "new_locations": [{"name": "The Long Pier", "body": "Rotting planks."}],
        "new_lore": [{"name": "The Salt Circle", "body": "A cabal."}],
    })
    kinds = {e["kind"] for e in edits}
    # Every section this fixture feeds actually produced a row, or the sweep
    # below proves nothing about the sections that silently dropped out.
    assert kinds == {"character_state", "group_state", "lore", "authored", "relationship",
                     "bond", "plot", "commitment", "new_character", "new_location", "new_lore"}
    for e in edits:
        assert set(e["review"]) == {"certainty", "quote", "speaker", "authority",
                                    "score", "band"}, e["id"]


def test_a_state_edit_cited_to_its_own_character_outranks_one_cited_to_another(
        monkeypatch, tmp_path):
    """The end-to-end shape of #112: the same quote, the same certainty, two
    different speakers, and the tier follows whose record is being rewritten."""
    from grimoire.store import playstate
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, sera, mara, win = _scene(cid, wroot)
    croot = campaigns.campaign_root(cid)
    playstate.write_state(croot, sera, "Wary.")
    playstate.write_state(croot, mara, "Calm.")
    cite = {"quote": "I have not slept in three days.", "certainty": 0.9}

    edits = _materialized(cid, sid, {"character_state_edits": [
        {"id": sera, "current_state": "Exhausted.", "speaker": "Seraphine Vale", **cite},
        {"id": mara, "current_state": "Exhausted.", "speaker": "Seraphine Vale", **cite}]})
    own = edits[f"character_state:{sera}"]["review"]
    hearsay = edits[f"character_state:{mara}"]["review"]
    assert (own["authority"], own["band"]) == (routing.SELF, "high")
    assert (hearsay["authority"], hearsay["band"]) == (routing.OTHER, "medium")
    assert own["quote"] == "I have not slept in three days."


def test_narrated_weather_rows_share_one_citation(monkeypatch, tmp_path):
    """One parsed weather row fans out into a row per changed axis. They rest on
    the same narrated line, so they have to be scored alike — a per-axis rebuild
    could not even see the citation, which lives on the parent row."""
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    lid = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch Docks")
    scenes.set_location(cid, sid, lid)
    sid = scenes.set_datetime(cid, sid, "2026-06-14T09:00").get("id", sid)
    edits = [e for e in absorb.materialize(cid, sid, {"weather_edits": [
        {"condition": "storm", "wind": "gale", "speaker": "Grimoire", "certainty": 0.95,
         "quote": "A gale drove the rain sideways."}]}) if e["kind"] == "weather"]
    assert {e["field"] for e in edits} == {"condition", "wind"}
    assert {(e["review"]["authority"], e["review"]["band"]) for e in edits} == \
        {(routing.NARRATION, "high")}


def test_apply_never_sees_the_review_block(monkeypatch, tmp_path):
    """The invariant the whole design rests on: routing is display and default
    checkbox state, never permission. Asserted against the module graph rather
    than by reading, because the failure is silent — an `apply` that started
    consulting a band would let a model's self-report gate a write."""
    import ast
    import pathlib
    src = pathlib.Path(absorb.apply.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "routing" not in (imported | names)
    # The key by its literal spelling, not the bare word: `apply.py` says
    # "reviewer" throughout, and a substring check on that would pass or fail on
    # its prose rather than on what it reads off an edit.
    assert '"review"' not in src and "'review'" not in src


def test_a_review_block_is_not_permission_to_write(monkeypatch, tmp_path):
    """The behavioural half of the same claim: a `low` row that a reviewer ticks
    anyway applies exactly like any other. Routing must not become a second,
    invisible approval gate on the save path."""
    from grimoire.store import entities
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, _, _, _ = _scene(cid, wroot)
    entities.create_entity(wroot, "lore", "The Ledger", "It lists bribes.", keys="ledger")
    staged = _materialized(cid, sid, {"lore_edits": [
        {"id": "the-ledger", "append": "It names the harbourmaster.",
         "speaker": "Nobody At All", "certainty": 0.1}]})["lore:the-ledger"]
    assert staged["review"]["band"] == "low"

    applied, failures = absorb.apply_edits(cid, [staged], sid)
    assert (applied, failures) == (["lore:the-ledger"], [])
    assert "harbourmaster" in entities.read_entity(campaigns.campaign_root(cid),
                                                   "lore", "the-ledger")["body"]


def test_a_malformed_card_name_does_not_take_the_absorb_down(monkeypatch, tmp_path):
    """Cards are hand-editable and `scene_cast` returns `name:` as it finds it.
    A mapping-valued name reaches the index unhashable, and this runs after the
    extraction call was paid for — so one bad card must cost that actor's
    resolution, not the whole review."""
    from grimoire.store.appearances import cast as appearances_cast
    cid, wroot = _campaign(monkeypatch, tmp_path)
    sid, sera, _, _ = _scene(cid, wroot)
    healthy = routing.speaker_index(cid, sid)
    monkeypatch.setattr(appearances_cast, "scene_cast",
                        lambda *a, **k: [{"kind": "characters", "id": sera,
                                          "role": "npc", "name": {"a": 1}}])

    poisoned = routing.speaker_index(cid, sid)
    assert poisoned["refs"] == {}
    # The transcript half is untouched, so the citation is still corroborated —
    # only the mapping from that speaker to a record is lost.
    assert poisoned["labels"] == healthy["labels"]
    assert routing.authority(poisoned, "Seraphine Vale",
                             (f"characters:{sera}",)) == routing.OTHER
