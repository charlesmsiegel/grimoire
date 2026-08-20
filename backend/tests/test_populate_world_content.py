from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".claude" / "skills" /
                      "populate-world-content" / "scripts"))
import populate_world_content as pwc

from grimoire.store import assets, campaigns, characters, entities, greetings, overlay, tags, worlds


def _world(monkeypatch, tmp_path) -> tuple[str, Path]:
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Ashgrove")
    return wid, worlds.world_root(wid)


def test_build_index_lists_existing_entities_tags_and_greetings(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "locations", "Blind Lion")
    entities.create_entity(root, "lore", "Gangs")
    tags.add_tag(root, "Farmer")
    greetings.create_greeting(root, "Hariel", "hariel", "default", "hi")

    idx = pwc.build_index(root)

    assert {"kind": "locations", "id": "blind-lion", "name": "Blind Lion"} in idx["entities"]
    assert {"kind": "lore", "id": "gangs", "name": "Gangs"} in idx["entities"]
    assert {"id": "farmer", "display_name": "Farmer"} in idx["tags"]
    g = idx["greetings"][0]
    assert g["name"] == "Hariel" and g["character"] == "hariel" and g["version"] == "default"


def test_index_cli_prints_json(monkeypatch, tmp_path, capsys):
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "locations", "Blind Lion")

    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "index", "--world", wid])
    assert pwc.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["entities"][0]["name"] == "Blind Lion"


def test_apply_tags_reuses_existing_id_and_tracks_touched_file(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    existing_id = tags.add_tag(root, "Farmer")
    world_rel = pwc._world_rel(root)
    results = pwc.new_results()

    specs = [{"display_name": "farmer"}, {"display_name": "Merchant"}]
    result = pwc.apply_tags(root, specs, results)

    assert result["farmer"] == existing_id
    assert len(tags.read_tags(root)) == 2  # no duplicate "farmer"/"farmer-2" entry
    assert result["Merchant"] in tags.read_tags(root)
    assert f"{world_rel}/tags.md" in results["touched_files"]


def test_apply_entities_skips_existing_name_case_insensitively(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "locations", "Blind Lion")
    results = pwc.new_results()

    pwc.apply_entities(root, [
        {"kind": "locations", "name": "blind lion", "body": "dup"},
        {"kind": "locations", "name": "Guild Hall", "body": "new"},
    ], results, wid)

    names = {e["name"] for e in entities.list_entities(root, "locations")}
    assert names == {"Blind Lion", "Guild Hall"}
    assert len(results["skipped"]) == 1 and len(results["created"]) == 1
    assert any(p.endswith("locations/guild-hall.md") for p in results["touched_files"])


def test_apply_entities_rejects_creatures_by_default(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    results = pwc.new_results()

    pwc.apply_entities(root, [{"kind": "creatures", "name": "Dragon", "body": "x"}], results, wid)

    assert entities.list_entities(root, "creatures") == []
    assert results["errors"][0]["reason"] == "creatures not allowed outside fantasy worlds"


def test_apply_entities_allows_creatures_when_opted_in(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    results = pwc.new_results()

    pwc.apply_entities(root, [{"kind": "creatures", "name": "Dragon", "body": "x"}], results, wid,
                       allow_creatures=True)

    assert {e["name"] for e in entities.list_entities(root, "creatures")} == {"Dragon"}
    assert results["errors"] == []


def test_apply_reclassifications_is_idempotent_across_reruns(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    lore_id = entities.create_entity(
        root, "lore", "Gangs",
        "Gangs in Gilderock: the Erune family, the Avengers.")
    spec = {
        "lore_id": lore_id, "new_kind": "groups",
        "name": "Erune family", "body": "Mafia family."}

    r1 = pwc.new_results()
    pwc.apply_reclassifications(root, [spec], r1, wid)
    groups = [g["name"] for g in entities.list_entities(root, "groups")]
    assert groups == ["Erune family"]
    assert entities.list_entities(root, "lore") == []
    assert r1["created"][0]["kind"] == "groups"

    # Same spec applied again — lore_id already gone, target already exists
    r2 = pwc.new_results()
    pwc.apply_reclassifications(root, [spec], r2, wid)
    groups = [g["name"] for g in entities.list_entities(root, "groups")]
    assert groups == ["Erune family"]  # not duplicated
    # Deleting an already-gone lore entry is not an error
    assert r2["errors"] == []
    assert r2["skipped"][0]["reason"] == "target already exists"


def test_reclassification_sweeps_the_freed_lore_id_out_of_world_and_campaigns(monkeypatch, tmp_path):
    """A reclassification DELETES a world lore record, and the id it frees is
    handed straight back to the next record of that name. `forget_world_record`
    is the sweep every world-side deleter in this codebase owes the overlay for
    exactly that reason (#225): without it the world's own asset directory
    survives the delete, and each dependent campaign keeps the state it filed
    against a record it only ever inherited -- both of which a later record
    under the same slug then adopts."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Ashgrove")
    root = worlds.world_root(wid)
    lore_id = entities.create_entity(root, "lore", "Gangs", "The Erune family.")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    # world-side: an image filed beside the record, which delete_entity leaves
    assets.put_image(root, lore_id, "default", "gallery_1", b"world-bytes", "png", base="lore")
    # campaign-side: state filed against a record this campaign only inherited
    assets.put_image(croot, lore_id, "default", "gallery_1", b"campaign-bytes", "png", base="lore")

    results = pwc.new_results()
    pwc.apply_reclassifications(root, [{
        "lore_id": lore_id, "new_kind": "groups",
        "name": "Erune family", "body": "Mafia family."}], results, wid)

    assert results["errors"] == []
    assert not (root / "lore" / lore_id).exists()      # world-side leftovers gone
    assert not (croot / "lore" / lore_id).exists()     # campaign-side state gone
    assert results["swept"] == [
        {"stage": "reclassifications", "kind": "lore", "id": lore_id}]
    # The world-side removals are real changes under the world's path, so they
    # have to be accounted for or verify_manifest's git cross-check reports
    # them as "changes apply did not account for" and blocks the commit.
    assert f"worlds/{wid}/lore/{lore_id}/assets/default/gallery_1.png" in results["touched_files"]
    # and the id really is reusable now, with nothing of the old record on it
    assert entities.create_entity(root, "lore", "Gangs") == lore_id
    assert assets.list_images(root, lore_id, "default", base="lore") == []


def test_reclassification_rerun_does_not_sweep_again(monkeypatch, tmp_path):
    """The sweep is tied to a delete that actually happened. A rerun's delete
    raises EntityNotFound, so nothing is swept -- which is what keeps the rerun
    a true no-op (and, in cmd_run, a `noop` rather than a fresh commit)."""
    wid, root = _world(monkeypatch, tmp_path)
    lore_id = entities.create_entity(root, "lore", "Gangs", "The Erune family.")
    spec = {"lore_id": lore_id, "new_kind": "groups", "name": "Erune family", "body": "x"}

    pwc.apply_reclassifications(root, [spec], pwc.new_results(), wid)
    r2 = pwc.new_results()
    pwc.apply_reclassifications(root, [spec], r2, wid)

    assert r2["swept"] == []
    assert r2["touched_files"] == set()


def _card(first_mes="", alts=None, name="Adriana"):
    return {"data": {"name": name, "first_mes": first_mes,
                      "alternate_greetings": alts or [], "description": "", "personality": "",
                      "scenario": "", "mes_example": "", "tags": [], "extensions": {}},
            "spec": "chara_card_v3", "spec_version": "3.0"}


def test_apply_greeting_imports_titles_in_order(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Adriana", "default", _card("Hello.", ["Alt one.", "Alt two."]))
    results = pwc.new_results()

    ref_map = pwc.apply_greeting_imports(root, [
        {"character": "adriana", "version": "default", "titles": ["Guild induction", "Lost in the city"]},
    ], results)

    by_id = {g["id"]: g["name"] for g in greetings.list_greetings(root)}
    assert by_id[ref_map["new:adriana:default:0"]] == "Guild induction"
    assert by_id[ref_map["new:adriana:default:1"]] == "Lost in the city"
    assert "Adriana (alt 2)" in by_id.values()  # no title supplied for idx 2 -> kept raw import name


def test_apply_greeting_imports_resolves_new_refs_idempotently_on_rerun(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Adriana", "default", _card("Hello.", ["Alt one."]))
    spec = [{"character": "adriana", "version": "default", "titles": ["Guild induction", "Lost in the city"]}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert len(greetings.list_greetings(root)) == 2

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)  # same spec again

    assert len(greetings.list_greetings(root)) == 2  # not duplicated
    assert r2["skipped"][0]["reason"] == "already imported"
    assert ref_map_2 == ref_map_1  # refs resolve to the SAME greetings both times
    assert ref_map_2["new:adriana:default:0"] == ref_map_1["new:adriana:default:0"]


def test_apply_greeting_imports_idempotent_with_partial_titles_on_rerun(monkeypatch, tmp_path):
    """Critical test: partial-title scenario that breaks mtime-based ordering.
    3 greetings created (main + 2 alts), but only 2 titles supplied (indices 0-1).
    Index 2 keeps raw name 'Adriana (alt 2)'. On first run, refs map correctly.
    On second run with mtime-based ordering, the modified greetings (0 & 1) have
    new mtimes while the unmodified one (2) keeps original (earlier) mtime,
    completely reordering the sort. ID-based ordering fixes this."""
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Adriana", "default", _card("Hello.", ["Alt one.", "Alt two."]))
    spec = [{"character": "adriana", "version": "default", "titles": ["Guild induction", "Lost in the city"]}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert len(greetings.list_greetings(root)) == 3

    by_id_1 = {g["id"]: g["name"] for g in greetings.list_greetings(root)}
    # Verify first run mapping: titled greetings renamed, untitled keeps raw name
    assert by_id_1[ref_map_1["new:adriana:default:0"]] == "Guild induction"
    assert by_id_1[ref_map_1["new:adriana:default:1"]] == "Lost in the city"
    assert by_id_1[ref_map_1["new:adriana:default:2"]] == "Adriana (alt 2)"

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)  # same spec again

    assert len(greetings.list_greetings(root)) == 3  # not duplicated
    assert r2["skipped"][0]["reason"] == "already imported"
    # The critical assertion: ref_map_2 must equal ref_map_1, which means
    # the ordering didn't change despite partial renames having different mtimes
    assert ref_map_2 == ref_map_1
    assert ref_map_2["new:adriana:default:0"] == ref_map_1["new:adriana:default:0"]
    assert ref_map_2["new:adriana:default:1"] == ref_map_1["new:adriana:default:1"]
    assert ref_map_2["new:adriana:default:2"] == ref_map_1["new:adriana:default:2"]


def test_apply_greeting_imports_idempotent_when_char_id_diverges_from_card_name(monkeypatch, tmp_path):
    """Critical test: the character's store id ("charlotte-claymore") does NOT
    slugify-match the card's own `data.name` field ("Charlotte"), which is what
    import_from_character actually uses to build greeting names/ids ("charlotte",
    "charlotte-alt-1", "charlotte-alt-2"). Combined with partial titles (only
    2 of 3 greetings renamed), this broke both a prior mtime-based ordering
    fix and a prior id-pattern-matching fix (the pattern was matched against
    char_id, which never matches these ids). Ordering by recomputed body
    content is immune to both the id/char_id mismatch and title renames."""
    wid, root = _world(monkeypatch, tmp_path)
    char_id, vid = characters.create_character(
        root, "Charlotte Claymore", "default",
        _card("Hello.", ["Alt one.", "Alt two."], name="Charlotte"))
    assert char_id == "charlotte-claymore"  # store id diverges from the card's own name
    spec = [{"character": char_id, "version": vid,
             "titles": ["Guild induction", "Lost in the city"]}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert len(greetings.list_greetings(root)) == 3

    by_id_1 = {g["id"]: g["name"] for g in greetings.list_greetings(root)}
    assert by_id_1[ref_map_1["new:charlotte-claymore:default:0"]] == "Guild induction"
    assert by_id_1[ref_map_1["new:charlotte-claymore:default:1"]] == "Lost in the city"
    assert by_id_1[ref_map_1["new:charlotte-claymore:default:2"]] == "Charlotte (alt 2)"

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)  # same spec again

    assert len(greetings.list_greetings(root)) == 3  # not duplicated
    assert r2["skipped"][0]["reason"] == "already imported"
    assert ref_map_2 == ref_map_1
    assert ref_map_2["new:charlotte-claymore:default:0"] == ref_map_1["new:charlotte-claymore:default:0"]
    assert ref_map_2["new:charlotte-claymore:default:1"] == ref_map_1["new:charlotte-claymore:default:1"]
    assert ref_map_2["new:charlotte-claymore:default:2"] == ref_map_1["new:charlotte-claymore:default:2"]


def test_apply_greeting_imports_errors_when_existing_greetings_dont_match_card(monkeypatch, tmp_path):
    """Round 3: body-content matching itself has a gap when the card (or, as
    simulated here, a greeting's stored body) drifts from what it was at
    import time -- e.g. a user commits a legitimate character text edit
    between two swarm runs. If one expected body no longer matches, a naive
    match silently drops that index and shifts every later one into the wrong
    slot; if every body fails to match, it silently looks like "nothing
    imported yet" and reimports, duplicating. Neither is acceptable -- this
    must surface as an error and touch nothing."""
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Adriana", "default", _card("Hello.", ["Alt one.", "Alt two."]))
    spec = [{"character": "adriana", "version": "default", "titles": ["Guild induction", "Lost in the city"]}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert len(greetings.list_greetings(root)) == 3

    # Simulate drift: alt-1's stored body no longer matches what the card
    # would currently bake for it (as if the card had been edited and this
    # greeting's body were still the old text -- body-matching can no longer
    # tell which existing greeting is which).
    alt1_gid = ref_map_1["new:adriana:default:1"]
    greetings.update_greeting(root, alt1_gid, body="Something else entirely.")

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)

    assert len(greetings.list_greetings(root)) == 3  # not duplicated
    assert ref_map_2 == {}  # nothing resolved for this spec -- errored, not guessed or shifted
    assert r2["skipped"] == []
    assert r2["created"] == []
    assert len(r2["errors"]) == 1
    err = r2["errors"][0]
    assert err["stage"] == "greeting_imports"
    assert err["character"] == "adriana" and err["version"] == "default"
    assert "don't match" in err["reason"]


def test_apply_greeting_imports_stable_for_duplicate_bodies_on_rerun(monkeypatch, tmp_path):
    """Round 5: two card positions that bake to the IDENTICAL body. Body
    matching pins every position whose body is unique, but it cannot tell twins
    apart -- the tiebreak decides, and the old tiebreak was whatever order
    `list_greetings` happened to return, which is sorted by the greeting's
    `name`. This script RENAMES greetings when it applies titles, so that order
    changes between the run that imported and every run after: with titles
    chosen to reverse the name-sort, the two twins silently swap which
    `new:*:idx` ref they answer to, with no error logged. Sorting candidates by
    the immutable id instead makes the assignment stable."""
    wid, root = _world(monkeypatch, tmp_path)
    # first_mes and the single alternate are the same text -> identical bodies.
    characters.create_character(root, "Adriana", "default", _card("Hello.", ["Hello."]))
    # Titles picked so that applying them REVERSES the name-sort order: before
    # the rename list_greetings yields "Adriana" then "Adriana (alt 1)"; after
    # it yields "Alpha follow-up" (the alt) then "Zulu opener" (the first_mes).
    spec = [{"character": "adriana", "version": "default",
             "titles": ["Zulu opener", "Alpha follow-up"]}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert r1["errors"] == []
    assert len(greetings.list_greetings(root)) == 2
    by_id_1 = {g["id"]: g["name"] for g in greetings.list_greetings(root)}
    assert by_id_1[ref_map_1["new:adriana:default:0"]] == "Zulu opener"
    assert by_id_1[ref_map_1["new:adriana:default:1"]] == "Alpha follow-up"
    # the rename really did invert the name-sort the old tiebreak depended on
    assert [g["id"] for g in greetings.list_greetings(root)] != sorted(by_id_1)

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)

    assert len(greetings.list_greetings(root)) == 2  # not duplicated
    assert r2["errors"] == []
    assert r2["skipped"][0]["reason"] == "already imported"
    # The whole point: not merely "no error" -- the same keys resolve to the
    # same greetings, so the twin that got titles[0] still answers to :0.
    assert ref_map_2 == ref_map_1
    assert ref_map_2["new:adriana:default:0"] == ref_map_1["new:adriana:default:0"]
    assert ref_map_2["new:adriana:default:1"] == ref_map_1["new:adriana:default:1"]


def test_apply_greeting_imports_stable_when_duplicate_twins_are_not_id_sorted(monkeypatch, tmp_path):
    """Round 5, third drift source: sorting candidates by id makes reruns agree
    with each other, but the IMPORTING run indexed its ref_map by
    `import_from_character`'s creation order, and those two orders are not the
    same. `… (alt 10)` slugifies to `…-alt-10`, which sorts before `…-alt-2`,
    so twins at creation positions 2 and 10 come out swapped on the rerun. The
    fix is for the importing run to index through the same resolver, so run 1
    and run 2 agree by construction rather than by coincidence."""
    wid, root = _world(monkeypatch, tmp_path)
    # Alternates 1..10; the 2nd and the 10th are the same text (creation
    # positions 2 and 10, since first_mes takes position 0).
    alts = [f"Alt {i} text." for i in range(1, 11)]
    alts[1] = alts[9] = "Twinned alt text."
    characters.create_character(root, "Adriana", "default", _card("Hello.", alts))
    spec = [{"character": "adriana", "version": "default",
             "titles": [f"Title {i}" for i in range(11)]}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert r1["errors"] == []
    assert len(greetings.list_greetings(root)) == 11
    # the id sort really does disagree with creation order here
    assert sorted(["adriana-alt-2", "adriana-alt-10"]) == ["adriana-alt-10", "adriana-alt-2"]
    assert {ref_map_1["new:adriana:default:2"], ref_map_1["new:adriana:default:10"]} == \
        {"adriana-alt-2", "adriana-alt-10"}

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)

    assert len(greetings.list_greetings(root)) == 11  # not duplicated
    assert r2["errors"] == []
    assert ref_map_2 == ref_map_1
    assert ref_map_2["new:adriana:default:2"] == ref_map_1["new:adriana:default:2"]
    assert ref_map_2["new:adriana:default:10"] == ref_map_1["new:adriana:default:10"]
    # and the title really did land on the greeting :2 resolves to, both times
    by_id = {g["id"]: g["name"] for g in greetings.list_greetings(root)}
    assert by_id[ref_map_2["new:adriana:default:2"]] == "Title 2"
    assert by_id[ref_map_2["new:adriana:default:10"]] == "Title 10"


def test_apply_greeting_imports_tolerates_crlf_card_bodies_on_rerun(monkeypatch, tmp_path):
    """Round 5: a card whose greeting text has CRLF line breaks (SillyTavern
    and Chub exports routinely do). Cards are JSON so they round-trip "\\r\\n"
    exactly, but a greeting body does not: `atomic.write_text` writes in text
    mode (`newline=None`, deliberately, so a user's CRLF store isn't rewritten
    to LF) and `read_greeting` reads with universal newlines, and that pair
    turns a stored "\\r\\n" into "\\n\\n" on a CRLF platform and "\\n" on an LF
    one. Comparing a freshly-baked body against the stored one therefore fails
    on a store where NOTHING drifted, hard-erroring every rerun."""
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(
        root, "Adriana", "default",
        _card("Hello.\r\nSecond line.", ["Alt one.\r\nAlt line two.", "Alt two."]))
    spec = [{"character": "adriana", "version": "default",
             "titles": ["Guild induction", "Lost in the city"]}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert r1["errors"] == []
    assert len(greetings.list_greetings(root)) == 3

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)  # identical spec, unchanged card

    # Nothing changed on disk -- only the newline representation round-tripped,
    # so this must NOT be reported as drift.
    assert r2["errors"] == []
    assert len(greetings.list_greetings(root)) == 3  # not duplicated
    assert r2["skipped"][0]["reason"] == "already imported"
    assert ref_map_2 == ref_map_1
    assert set(ref_map_2) == {"new:adriana:default:0", "new:adriana:default:1",
                              "new:adriana:default:2"}


def test_apply_greeting_imports_does_not_let_a_loose_body_steal_a_tight_slot(monkeypatch, tmp_path):
    r"""Final review: the matching loop was greedy in `candidates`' id order, so
    a candidate that could sit at either of two positions could take the one
    slot a different candidate had as its ONLY option -- leaving that one with
    nothing and reporting the whole character as unmatchable, on a store where
    nothing had drifted at all.

    The pairs below are what makes that concrete. Only greeting bodies lose
    newline detail on their write/read round trip (`_stored_body_forms`), and
    they lose a DIFFERENT amount per platform, so two card positions whose text
    differs only in CRLF-vs-LF collapse to one stored body on one platform and
    stay two on the other -- which is precisely "one candidate with a single
    possible position, one with two, over the same pair of slots".

    Two pairs, because the asymmetry runs opposite ways: positions 2/10 are the
    tight pair where "\r\n" and "\n" round-trip differently (CRLF platforms),
    positions 3/11 where "\r\n" and "\n\n" do (LF platforms). Both pairs are
    also arranged so the loose candidate's id sorts FIRST (`…-alt-10` <
    `…-alt-2`), which is what let it grab the slot under the old loop.
    """
    wid, root = _world(monkeypatch, tmp_path)
    alts = [f"Alt {i} text." for i in range(1, 13)]   # 12 alternates -> 13 positions
    alts[1] = "Anchor line.\r\nSecond line."          # position 2  (id adriana-alt-2)
    alts[9] = "Anchor line.\nSecond line."            # position 10 (id adriana-alt-10)
    alts[2] = "Other line.\r\nTail line."             # position 3  (id adriana-alt-3)
    alts[10] = "Other line.\n\nTail line."            # position 11 (id adriana-alt-11)
    characters.create_character(root, "Adriana", "default", _card("Hello.", alts))
    spec = [{"character": "adriana", "version": "default", "titles": []}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)

    assert r1["errors"] == []          # under the old loop: "could not be resolved back"
    assert len(greetings.list_greetings(root)) == 13
    assert set(ref_map_1) == {f"new:adriana:default:{i}" for i in range(13)}
    assert len(set(ref_map_1.values())) == 13     # a real 1:1 assignment, no slot reused
    assert pwc._existing_greetings_in_creation_order(root, "adriana", "default") is not None

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)   # identical card, identical spec

    assert r2["errors"] == []
    assert len(greetings.list_greetings(root)) == 13         # not duplicated
    assert r2["skipped"][0]["reason"] == "already imported"
    assert ref_map_2 == ref_map_1     # and the assignment is rerun-stable


def test_apply_greeting_imports_errors_on_insertion_drift(monkeypatch, tmp_path):
    """Round 4: a new alternate greeting inserted into the MIDDLE of the card's
    alternates list between two runs. Total existing-greeting count (2) still
    equals... nothing in particular, but the OLD (count-based) ambiguity check
    (`len(ordered) != len(candidates)`) would have compared 2 matched against 2
    candidates and called it clean, silently mapping `new:*:1` to the wrong
    greeting (the original alt, which is now actually at expected index 2, not
    1) and leaving `new:*:1` (the new alt) unresolved. The value-based fix must
    catch this: the new alt's expected body has no existing greeting, so its
    slot in `ordered` stays None."""
    wid, root = _world(monkeypatch, tmp_path)
    char_id, vid = characters.create_character(
        root, "Adriana", "default", _card("Hello.", ["Original alt text."]))
    spec = [{"character": char_id, "version": vid, "titles": []}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert len(greetings.list_greetings(root)) == 2  # first_mes + 1 alt

    # Insert a brand-new alt BEFORE the original one: first_mes unchanged,
    # alternates go from ["Original alt text."] to
    # ["New inserted alt text.", "Original alt text."]. Both existing
    # greetings' bodies still appear somewhere in the new expected list, but
    # the total expected count is now 3.
    card = characters.read_card(root, char_id, vid)
    card["data"]["alternate_greetings"] = ["New inserted alt text.", "Original alt text."]
    characters.update_version(root, char_id, vid, card)

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)

    assert len(greetings.list_greetings(root)) == 2  # no new greeting created
    assert ref_map_2 == {}  # nothing resolved for this spec -- errored, not shifted
    assert r2["skipped"] == []
    assert r2["created"] == []
    assert len(r2["errors"]) == 1
    err = r2["errors"][0]
    assert err["stage"] == "greeting_imports"
    assert err["character"] == char_id and err["version"] == vid
    assert "don't match" in err["reason"]
    # ref_map_1 is untouched by this assertion -- just documents what existed
    assert len(ref_map_1) == 2


def test_apply_greeting_imports_errors_on_same_count_replacement_drift(monkeypatch, tmp_path):
    """Round 4: the sharpest test of value-based vs. count-based matching. One
    alternate's text is replaced by different text, so the expected-body COUNT
    stays identical (3 before, 3 after) but one distinct body ("Alt one.")
    disappears from the expected set entirely while a new one ("Replaced alt
    text.") appears. A count-only check (`len(ordered) == len(candidates)`)
    would wrongly call this clean since 3 still equals 3; the value-based fix
    must still catch it because the greeting whose stored body is "Alt one."
    can no longer find a matching slot in expected_index_map."""
    wid, root = _world(monkeypatch, tmp_path)
    char_id, vid = characters.create_character(
        root, "Adriana", "default", _card("Hello.", ["Alt one.", "Alt two."]))
    spec = [{"character": char_id, "version": vid, "titles": []}]

    r1 = pwc.new_results()
    ref_map_1 = pwc.apply_greeting_imports(root, spec, r1)
    assert len(greetings.list_greetings(root)) == 3  # first_mes + 2 alts

    # Replace alt-1's text (index 1: "Alt one.") with different text, leaving
    # the total alternates count (and thus total expected-body count) the same.
    card = characters.read_card(root, char_id, vid)
    card["data"]["alternate_greetings"] = ["Replaced alt text.", "Alt two."]
    characters.update_version(root, char_id, vid, card)

    r2 = pwc.new_results()
    ref_map_2 = pwc.apply_greeting_imports(root, spec, r2)

    assert len(greetings.list_greetings(root)) == 3  # no new greeting created
    assert ref_map_2 == {}  # nothing resolved for this spec -- errored, not guessed
    assert r2["skipped"] == []
    assert r2["created"] == []
    assert len(r2["errors"]) == 1
    err = r2["errors"][0]
    assert err["stage"] == "greeting_imports"
    assert err["character"] == char_id and err["version"] == vid
    assert "don't match" in err["reason"]
    assert len(ref_map_1) == 3


def test_apply_greeting_edges_unions_and_rejects_cycles(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    g1 = greetings.create_greeting(root, "First", "adriana", "default", "a")
    g2 = greetings.create_greeting(root, "Second", "adriana", "default", "b")
    g3 = greetings.create_greeting(root, "Third", "adriana", "default", "c")
    greetings.set_edges(root, g1, leads_to=[g2])  # pre-existing edge must survive
    results = pwc.new_results()
    ref_map = {f"id:{g1}": g1, f"id:{g2}": g2, f"id:{g3}": g3}

    pwc.apply_greeting_edges(root, [
        {"greeting_ref": f"id:{g1}", "leads_to": [f"id:{g3}"], "excludes": []},
        {"greeting_ref": f"id:{g3}", "leads_to": [f"id:{g1}"], "excludes": []},  # would cycle g1->g3->g1
    ], ref_map, results)

    edges = greetings.edges_of(greetings.read_plotmap(root), g1)
    assert set(edges["leads_to"]) == {g2, g3}
    edges3 = greetings.edges_of(greetings.read_plotmap(root), g3)
    assert edges3["leads_to"] == []
    assert any(s["reason"] == "would create a cycle" for s in results["skipped"])


def test_apply_greeting_edges_unions_excludes_like_leads_to(monkeypatch, tmp_path):
    """Critical test: excludes must be unioned with pre-existing entries,
    not replaced. This mirrors the leads_to union test but exercises excludes."""
    wid, root = _world(monkeypatch, tmp_path)
    g1 = greetings.create_greeting(root, "First", "adriana", "default", "a")
    g2 = greetings.create_greeting(root, "Second", "adriana", "default", "b")
    g3 = greetings.create_greeting(root, "Third", "adriana", "default", "c")
    g4 = greetings.create_greeting(root, "Fourth", "adriana", "default", "d")
    # Set pre-existing excludes on g1
    greetings.set_edges(root, g1, excludes=[g2])
    results = pwc.new_results()
    ref_map = {f"id:{g1}": g1, f"id:{g2}": g2, f"id:{g3}": g3, f"id:{g4}": g4}

    # Apply edges spec that adds g3 to g1's excludes
    pwc.apply_greeting_edges(root, [
        {"greeting_ref": f"id:{g1}", "leads_to": [], "excludes": [f"id:{g3}"]},
    ], ref_map, results)

    # Both pre-existing (g2) and new (g3) excludes must be present
    edges = greetings.edges_of(greetings.read_plotmap(root), g1)
    assert set(edges["excludes"]) == {g2, g3}, f"Expected {{g2, g3}}, got {set(edges['excludes'])}"


def test_apply_greeting_edges_errors_on_unresolvable_greeting_ref(monkeypatch, tmp_path):
    """Test error handling when greeting_ref itself cannot be resolved."""
    wid, root = _world(monkeypatch, tmp_path)
    g1 = greetings.create_greeting(root, "First", "adriana", "default", "a")
    results = pwc.new_results()
    ref_map = {f"id:{g1}": g1}

    # Spec references a greeting that doesn't exist
    pwc.apply_greeting_edges(root, [
        {"greeting_ref": "id:nonexistent", "leads_to": [f"id:{g1}"], "excludes": []},
    ], ref_map, results)

    # Should have an error for unresolvable greeting_ref
    assert len(results["errors"]) == 1
    assert results["errors"][0]["reason"] == "unresolvable ref"
    assert results["errors"][0]["ref"] == "id:nonexistent"


def test_apply_greeting_edges_errors_on_unresolvable_leads_to_ref(monkeypatch, tmp_path):
    """Test error handling when a ref in leads_to cannot be resolved."""
    wid, root = _world(monkeypatch, tmp_path)
    g1 = greetings.create_greeting(root, "First", "adriana", "default", "a")
    results = pwc.new_results()
    ref_map = {f"id:{g1}": g1}

    # Spec has an unresolvable ref in leads_to
    pwc.apply_greeting_edges(root, [
        {"greeting_ref": f"id:{g1}", "leads_to": ["id:nonexistent"], "excludes": []},
    ], ref_map, results)

    # Should have an error for the unresolvable leads_to ref
    assert len(results["errors"]) == 1
    assert results["errors"][0]["reason"] == "unresolvable ref"
    assert results["errors"][0]["ref"] == "id:nonexistent"
    # No edges should have been added
    edges = greetings.edges_of(greetings.read_plotmap(root), g1)
    assert edges["leads_to"] == []


def test_resolve_ref_handles_id_and_new_and_unknown(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    g1 = greetings.create_greeting(root, "First", "adriana", "default", "a")
    ref_map = {f"id:{g1}": g1, "new:adriana:default:0": "some-new-id"}

    assert pwc.resolve_ref(f"id:{g1}", ref_map, root) == g1
    assert pwc.resolve_ref("new:adriana:default:0", ref_map, root) == "some-new-id"
    assert pwc.resolve_ref("id:does-not-exist", ref_map, root) is None


def test_apply_greeting_gating_resolves_existing_and_new_tags_case_insensitively(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    tags.add_tag(root, "Merchant")  # pre-existing, NOT re-listed by this manifest's tags[]
    g1 = greetings.create_greeting(root, "First", "adriana", "default", "a",
                                    requires_tags=[], present=["adriana"])
    results = pwc.new_results()

    pwc.apply_greeting_gating(root, [
        {"greeting_ref": f"id:{g1}", "requires_tags": ["merchant"], "present": ["breath"]},
    ], {f"id:{g1}": g1}, results)

    meta = greetings.read_greeting(root, g1)["meta"]
    assert meta["requires_tags"] == ["merchant"]
    assert set(meta["present"]) == {"adriana", "breath"}


def test_apply_greeting_gating_flags_unknown_tag_name(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    g1 = greetings.create_greeting(root, "First", "adriana", "default", "a")
    results = pwc.new_results()

    pwc.apply_greeting_gating(root, [
        {"greeting_ref": f"id:{g1}", "requires_tags": ["Nonexistent"], "present": []},
    ], {f"id:{g1}": g1}, results)

    assert greetings.read_greeting(root, g1)["meta"]["requires_tags"] == []
    assert any(e["reason"] == "unknown tag display_name" for e in results["errors"])


def test_apply_manifest_full_pipeline_and_idempotent_rerun(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Adriana", "default", _card("Hello.", ["Bye."]))
    manifest = {
        "world": wid,
        "entities": [{"kind": "locations", "name": "Blind Lion", "body": "A tavern."}],
        "reclassifications": [],
        "tags": [{"display_name": "Farmer"}],
        "greeting_imports": [{"character": "adriana", "version": "default",
                               "titles": ["Guild induction", "A farewell"]}],
        "greeting_edges": [{"greeting_ref": "new:adriana:default:0",
                             "leads_to": ["new:adriana:default:1"], "excludes": []}],
        "greeting_gating": [{"greeting_ref": "new:adriana:default:1",
                              "requires_tags": ["Farmer"], "present": []}],
    }

    r1 = pwc.apply_manifest(root, manifest, wid)
    assert r1["errors"] == []
    assert len(entities.list_entities(root, "locations")) == 1
    assert len(greetings.list_greetings(root)) == 2
    assert len(tags.read_tags(root)) == 1
    g1_id = next(g["id"] for g in greetings.list_greetings(root) if g["name"] == "Guild induction")
    edges = greetings.edges_of(greetings.read_plotmap(root), g1_id)
    assert len(edges["leads_to"]) == 1

    r2 = pwc.apply_manifest(root, manifest, wid)  # exact same manifest again

    assert r2["errors"] == []  # <- the property the first draft's test never actually checked
    assert r2["touched_files"] == []  # nothing changed the second time
    assert len(entities.list_entities(root, "locations")) == 1  # not duplicated
    assert len(greetings.list_greetings(root)) == 2  # not duplicated
    assert len(tags.read_tags(root)) == 1  # not duplicated
    edges_again = greetings.edges_of(greetings.read_plotmap(root), g1_id)
    assert edges_again == edges  # edge still there, not lost, not doubled


def test_apply_manifest_skips_malformed_items_and_still_applies_the_good_ones(monkeypatch, tmp_path):
    """A manifest is LLM-authored JSON, so a missing key is expected input, not
    a programmer error. It used to raise KeyError out of the middle of a stage:
    items earlier in the same list were already on disk, the stages after it
    never ran, and cmd_run never printed anything at all."""
    wid, root = _world(monkeypatch, tmp_path)
    manifest = {
        "world": wid,
        "entities": [
            {"kind": "locations", "name": "Blind Lion", "body": "A tavern."},
            {"kind": "locations", "body": "no name at all"},          # missing `name`
            {"kind": "locations", "name": "   ", "body": "blank"},    # blank `name`
            {"kind": "locations", "name": "Guild Hall", "body": "still applied"},
        ],
        "tags": [{"display_name": "Farmer"}, {"source": "only a source"}],
    }

    results = pwc.apply_manifest(root, manifest, wid)

    names = {e["name"] for e in entities.list_entities(root, "locations")}
    assert names == {"Blind Lion", "Guild Hall"}   # the good ones, both of them
    assert list(tags.read_tags(root).values()) == ["Farmer"]
    reasons = [e["reason"] for e in results["errors"]]
    assert len(reasons) == 3 and all(r.startswith("malformed manifest item") for r in reasons)
    assert any("'name'" in r for r in reasons) and any("'display_name'" in r for r in reasons)


def test_apply_manifest_validates_every_stage_without_crashing(monkeypatch, tmp_path):
    """One malformed item in each of the six stages: each is recorded and
    skipped, none raises, and a stage whose whole section is the wrong JSON type
    is reported rather than iterated."""
    wid, root = _world(monkeypatch, tmp_path)
    manifest = {
        "world": wid,
        "entities": [{"name": "No kind"}],                            # missing `kind`
        "reclassifications": [{"new_kind": "groups", "name": "No lore id"}],
        "tags": ["Farmer"],                                            # not an object
        "greeting_imports": [{"character": "adriana", "titles": [1, 2]}],  # no version, bad titles
        "greeting_edges": [{"greeting_ref": "id:x", "leads_to": "id:y"}],  # leads_to not a list
        "greeting_gating": {"greeting_ref": "id:x"},                   # section is not a list
    }

    results = pwc.apply_manifest(root, manifest, wid)

    by_stage = {e["stage"]: e["reason"] for e in results["errors"]}
    assert set(by_stage) == {"entities", "reclassifications", "tags", "greeting_imports",
                             "greeting_edges", "greeting_gating"}
    assert by_stage["greeting_gating"] == "manifest section is not a list"
    assert results["created"] == [] and results["touched_files"] == []


def test_apply_manifest_accepts_null_optional_fields(monkeypatch, tmp_path):
    """`null` for an optional key means "not supplied" -- every stage reaches
    those through `.get(key, default)` / `or None` anyway, so rejecting a null
    would refuse manifests the apply path handles perfectly well."""
    wid, root = _world(monkeypatch, tmp_path)

    results = pwc.apply_manifest(root, {
        "world": wid,
        "entities": [{"kind": "locations", "name": "Blind Lion", "body": "x",
                      "fields": None, "source": None}]}, wid)

    assert results["errors"] == []
    assert [e["name"] for e in entities.list_entities(root, "locations")] == ["Blind Lion"]


def test_verify_manifest_catches_dangling_character_ref(monkeypatch, tmp_path):
    """Test that greeting's own character field is checked for dangling refs.

    Explicitly set present=[] to avoid the default behavior of defaulting to
    [character], which would cause the present check to also fire and make
    this test non-isolated."""
    wid, root = _world(monkeypatch, tmp_path)
    greetings.create_greeting(root, "First", "nobody-character", "default", "a", present=[])

    result = pwc.verify_manifest(root)
    assert result["ok"] is False
    assert any("character references unknown character" in p for p in result["problems"])


def test_verify_manifest_catches_dangling_requires_tags(monkeypatch, tmp_path):
    """Test that greeting's requires_tags are checked for dangling refs."""
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Some Char", "default", {"data": {"name": "Some Char"}})
    greetings.create_greeting(root, "First", "some-char", "default", "a",
                               requires_tags=["ghost-tag"])

    result = pwc.verify_manifest(root)
    assert result["ok"] is False
    assert any("ghost-tag" in p and "requires_tags" in p for p in result["problems"])


def test_verify_manifest_catches_dangling_present(monkeypatch, tmp_path):
    """Test that greeting's present list is checked for dangling refs."""
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Some Char", "default", {"data": {"name": "Some Char"}})
    greetings.create_greeting(root, "First", "some-char", "default", "a",
                               present=["nobody"])

    result = pwc.verify_manifest(root)
    assert result["ok"] is False
    assert any("present references unknown character" in p for p in result["problems"])


def test_verify_manifest_catches_dangling_entity_owners(monkeypatch, tmp_path):
    """Test that entity owners are checked for dangling refs."""
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "lore", "Bad owner", owners="characters:nobody")

    result = pwc.verify_manifest(root)
    assert result["ok"] is False
    assert any("characters:nobody" in p and "owners" in p for p in result["problems"])


def test_verify_manifest_catches_dangling_plotmap_target(monkeypatch, tmp_path):
    """Test that plotmap edge targets are checked for dangling refs."""
    wid, root = _world(monkeypatch, tmp_path)
    characters.create_character(root, "Char", "default", {"data": {"name": "Char"}})
    g1 = greetings.create_greeting(root, "First", "char", "default", "a")
    # Set edges pointing to a non-existent greeting
    greetings.set_edges(root, g1, leads_to=["nonexistent-greeting"])

    result = pwc.verify_manifest(root)
    assert result["ok"] is False
    assert any("nonexistent-greeting" in p and "references unknown greeting" in p for p in result["problems"])


def test_verify_manifest_catches_dangling_plotmap_source(monkeypatch, tmp_path):
    """Test that plotmap edge sources (keys) are checked for dangling refs."""
    import json

    from grimoire.store import atomic

    wid, root = _world(monkeypatch, tmp_path)
    # Directly write a plotmap with a source key that doesn't correspond to a real greeting
    plotmap = {"nonexistent-source": {"leads_to": [], "excludes": []}}
    plotmap_path = root / "plotmap.json"
    atomic.write_text(plotmap_path, json.dumps(plotmap, indent=2, sort_keys=True) + "\n")

    result = pwc.verify_manifest(root)
    assert result["ok"] is False
    assert any("nonexistent-source" in p and "edge source" in p for p in result["problems"])


def test_verify_manifest_checks_git_cross_check_both_directions(monkeypatch, tmp_path):
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "locations", "Blind Lion")
    world_rel = pwc._world_rel(root)
    touched = [f"{world_rel}/locations/blind-lion.md"]

    ok_result = pwc.verify_manifest(root, touched_files=touched, git_changed=set(touched))
    assert ok_result == {"ok": True, "problems": []}

    extra_change = pwc.verify_manifest(root, touched_files=touched,
                                        git_changed=set(touched) | {f"{world_rel}/lore/surprise.md"})
    assert extra_change["ok"] is False and any("surprise.md" in p for p in extra_change["problems"])

    missing_change = pwc.verify_manifest(root, touched_files=touched, git_changed=set())
    assert missing_change["ok"] is False
    assert any("claimed to touch" in p for p in missing_change["problems"])


def _git_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=tmp_path, check=True)


def _write_manifest(tmp_path: Path, wid: str, **overrides) -> Path:
    """Write manifest JSON to a file outside the git repo.

    The manifest is the tool's input (not stored state) and must be kept
    outside the git repository to avoid false dirty-check failures.
    """
    base = {"world": wid, "entities": [], "reclassifications": [], "tags": [],
            "greeting_imports": [], "greeting_edges": [], "greeting_gating": []}
    base.update(overrides)
    # Write to a sibling directory of tmp_path (not inside it, where git repo lives)
    # pytest cleans up the parent temp directory automatically
    temp_dir = tmp_path.parent / f"{tmp_path.name}-manifests"
    temp_dir.mkdir(exist_ok=True)
    p = temp_dir / "manifest.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


def test_run_aborts_on_dirty_tree_anywhere_in_the_repo(monkeypatch, tmp_path, capsys):
    wid, root = _world(monkeypatch, tmp_path)
    entities.create_entity(root, "locations", "Pre-existing")
    _git_repo(tmp_path)
    (tmp_path / "worlds" / "some-other-world.md").write_text("unrelated dirty file", encoding="utf-8")

    manifest_path = _write_manifest(tmp_path, wid)
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", wid,
                                       "--manifest", str(manifest_path)])
    assert pwc.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "aborted" and out["reason"] == "repo is dirty"


def test_run_does_not_commit_when_manifest_has_errors(monkeypatch, tmp_path, capsys):
    """Partial apply with errors: leaves valid writes staged+uncommitted for inspection."""
    wid, root = _world(monkeypatch, tmp_path)
    _git_repo(tmp_path)
    # Mix of valid entity (gets written) + invalid entity (causes error)
    manifest_path = _write_manifest(tmp_path, wid,
                                     entities=[
                                         {"kind": "locations", "name": "Good Place", "body": "valid"},
                                         {"kind": "not-a-real-kind", "name": "Bad", "body": "invalid"}
                                     ])
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", wid,
                                       "--manifest", str(manifest_path)])
    assert pwc.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["committed"] is False  # error-gated: refuses commit despite partial success
    assert out["errors"]  # has the invalid-kind error
    assert len(out["created"]) == 1  # "Good Place" was written
    # Git status shows the world's files are dirty (not committed), not the manifest
    status = subprocess.run(["git", "status", "--short"], cwd=tmp_path, capture_output=True, text=True).stdout
    # Verify the world's path appears in git status (proves world files are staged, not manifest)
    assert any(f"worlds/{wid}" in line for line in status.splitlines()), f"World path not in status: {status}"


def test_run_applies_verifies_and_commits_on_clean_repo(monkeypatch, tmp_path, capsys):
    wid, root = _world(monkeypatch, tmp_path)
    _git_repo(tmp_path)

    manifest_path = _write_manifest(tmp_path, wid,
                                     entities=[{"kind": "locations", "name": "Blind Lion", "body": ""}])
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", wid,
                                       "--manifest", str(manifest_path)])
    assert pwc.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["committed"] is True
    assert out["verify"]["ok"] is True

    log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert wid in log
    status = subprocess.run(["git", "status", "--short"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert status.strip() == ""


def test_run_reruns_as_noop_not_a_false_commit(monkeypatch, tmp_path, capsys):
    wid, root = _world(monkeypatch, tmp_path)
    _git_repo(tmp_path)
    manifest_path = _write_manifest(tmp_path, wid,
                                     entities=[{"kind": "locations", "name": "Blind Lion", "body": ""}])
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", wid,
                                       "--manifest", str(manifest_path)])
    assert pwc.main() == 0
    capsys.readouterr()  # clear output from first run
    first_log = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True).stdout

    assert pwc.main() == 0  # rerun, same manifest, nothing left to change
    out = json.loads(capsys.readouterr().out)
    assert out["committed"] == "noop"
    second_log = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert first_log == second_log  # no empty/duplicate commit was created


def test_run_handles_git_commit_failure(monkeypatch, tmp_path, capsys):
    """Git commit failure is detected and reported (I5: test git-failure branch)."""
    wid, root = _world(monkeypatch, tmp_path)
    _git_repo(tmp_path)

    # Manifest with entity that will be created
    manifest_path = _write_manifest(tmp_path, wid,
                                     entities=[{"kind": "locations", "name": "Blind Lion", "body": ""}])
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", wid,
                                       "--manifest", str(manifest_path)])
    # First run should succeed and create the location
    assert pwc.main() == 0
    capsys.readouterr()

    # Create a second manifest with a new entity
    manifest_path2 = _write_manifest(tmp_path, wid,
                                      entities=[
                                          {"kind": "locations", "name": "Blind Lion", "body": ""},
                                          {"kind": "locations", "name": "New Place", "body": ""}
                                      ])
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", wid,
                                       "--manifest", str(manifest_path2)])

    # Lock the git index to force commit to fail on the write attempt
    git_index_lock = tmp_path / ".git" / "index.lock"
    git_index_lock.write_text("locked")

    # Second run should fail due to git commit failing
    assert pwc.main() == 1
    out = json.loads(capsys.readouterr().out)
    # Verify we detected the commit failure
    assert out["committed"] is False
    assert "git_error" in out
    assert len(out["git_error"]) > 0


def _log(tmp_path: Path) -> str:
    return subprocess.run(["git", "log", "--oneline"], cwd=tmp_path,
                          capture_output=True, text=True).stdout


def _status(tmp_path: Path) -> str:
    return subprocess.run(["git", "status", "--short"], cwd=tmp_path,
                          capture_output=True, text=True).stdout.strip()


def _head_files(tmp_path: Path) -> list[str]:
    return subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True).stdout.splitlines()


def test_run_aborts_when_the_world_does_not_exist(monkeypatch, tmp_path, capsys):
    """`world_root` only checks the id is filesystem-safe. A typo'd --world used
    to CREATE `worlds/<typo>/` from nothing, commit it and report success --
    while `list_worlds` skipped it forever for having no world.md, so the
    content was unreachable from the app AND missing from its real world. The
    manifest/--world cross-check cannot catch it: both names come from the same
    mistaken source."""
    wid, root = _world(monkeypatch, tmp_path)
    _git_repo(tmp_path)
    before = _log(tmp_path)

    manifest_path = _write_manifest(tmp_path, "ashgrov",   # typo: the world is "ashgrove"
                                     entities=[{"kind": "locations", "name": "Blind Lion", "body": ""}])
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", "ashgrov",
                                       "--manifest", str(manifest_path)])

    assert pwc.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "aborted", "reason": "no such world", "world": "ashgrov"}
    assert not (tmp_path / "worlds" / "ashgrov").exists()   # nothing written
    assert _log(tmp_path) == before                          # nothing committed
    assert _status(tmp_path) == ""


def test_run_prints_json_and_refuses_the_commit_for_a_malformed_manifest(monkeypatch, tmp_path, capsys):
    """The malformed item must not take the run down with it: the valid items
    still apply, the error is reported on stdout as JSON like any other, and
    the error gate keeps it all out of git."""
    wid, root = _world(monkeypatch, tmp_path)
    _git_repo(tmp_path)
    manifest_path = _write_manifest(tmp_path, wid, entities=[
        {"kind": "locations", "name": "Blind Lion", "body": "valid"},
        {"kind": "locations", "body": "no name at all"},
        {"kind": "locations", "name": "Guild Hall", "body": "valid too"}])
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", wid,
                                       "--manifest", str(manifest_path)])

    assert pwc.main() == 1
    out = json.loads(capsys.readouterr().out)      # JSON, not a traceback

    assert out["committed"] is False
    assert len(out["created"]) == 2                 # both valid entities applied
    assert len(out["errors"]) == 1
    assert out["errors"][0]["stage"] == "entities"
    assert out["errors"][0]["reason"].startswith("malformed manifest item")


def test_run_aborts_on_an_unexplained_change_outside_the_world(monkeypatch, tmp_path, capsys):
    """The only thing allowed to write outside the world being processed is the
    reclassification sweep. Anything else there is unattributable -- and since
    the repo was verified clean at the top, it was written by this run. Refuse
    the commit rather than either sweeping it in silently or leaving it to
    surface as the NEXT world's bare "repo is dirty"."""
    wid, root = _world(monkeypatch, tmp_path)
    _git_repo(tmp_path)
    manifest_path = _write_manifest(tmp_path, wid,
                                     entities=[{"kind": "locations", "name": "Blind Lion", "body": ""}])
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", wid,
                                       "--manifest", str(manifest_path)])
    real_apply = pwc.apply_manifest

    def apply_and_scribble(root_, manifest, wid_, allow_creatures=False):
        out = real_apply(root_, manifest, wid_, allow_creatures)
        (tmp_path / "stray.md").write_text("written outside the world", encoding="utf-8")
        return out

    monkeypatch.setattr(pwc, "apply_manifest", apply_and_scribble)

    assert pwc.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["committed"] is False
    assert out["swept_files"] == ["stray.md"]
    assert any(e["stage"] == "run" and e["paths"] == ["stray.md"] for e in out["errors"])


def test_run_commits_the_campaign_side_sweep_in_the_same_checkpoint(monkeypatch, tmp_path, capsys):
    """C2's scoping tension, end to end. The sweep a reclassification owes the
    overlay can write OUTSIDE the world -- here `detached.json`/`sync.md` for a
    campaign that had materialized its own copy of the reclassified lore record.
    Those writes are consequences of the same reclassification, so they belong
    in the same commit; left out of its pathspec they would sit uncommitted and
    abort the NEXT world's run on the whole-repo dirty check."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Ashgrove")
    root = worlds.world_root(wid)
    lore_id = entities.create_entity(root, "lore", "Gangs", "The Erune family.")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    overlay.materialize_entity(cid, "lore", lore_id)   # the campaign owns a copy
    _git_repo(tmp_path)

    manifest_path = _write_manifest(tmp_path, wid, reclassifications=[{
        "lore_id": lore_id, "new_kind": "groups",
        "name": "Erune family", "body": "Mafia family."}])
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", wid,
                                       "--manifest", str(manifest_path)])

    assert pwc.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["committed"] is True
    assert out["verify"]["ok"] is True
    # the sweep really did write campaign-side, and those paths were committed
    assert f"campaigns/{cid}/detached.json" in out["swept_files"]
    assert lore_id in json.loads((croot / "detached.json").read_text(encoding="utf-8"))[0]
    assert _status(tmp_path) == ""                     # nothing left dirty
    assert f"campaigns/{cid}/detached.json" in _head_files(tmp_path)

    # ...which is what lets the next run get past the whole-repo dirty check
    capsys.readouterr()
    first_log = _log(tmp_path)
    assert pwc.main() == 0
    assert json.loads(capsys.readouterr().out)["committed"] == "noop"
    assert _log(tmp_path) == first_log


def test_run_end_to_end_applies_every_stage_commits_once_and_reruns_as_noop(monkeypatch, tmp_path, capsys):
    """One manifest exercising all six stages against a real git repo: the
    checkpoint has to be complete (clean tree, the reclassified lore gone from
    HEAD rather than only from the working tree) and the rerun has to be a true
    no-op, not a second commit of nothing."""
    wid, root = _world(monkeypatch, tmp_path)
    lore_id = entities.create_entity(root, "lore", "Gangs", "The Erune family runs the docks.")
    characters.create_character(root, "Adriana", "default", _card("Hello.", ["Bye."]))
    _git_repo(tmp_path)
    assert f"worlds/{wid}/lore/{lore_id}.md" in _head_files(tmp_path)   # it IS in the baseline

    manifest_path = _write_manifest(
        tmp_path, wid,
        entities=[{"kind": "locations", "name": "Blind Lion", "body": "A tavern.",
                   "source": "chapter 2"}],
        reclassifications=[{"lore_id": lore_id, "new_kind": "groups", "name": "Erune family",
                            "body": "Mafia family.", "source": "chapter 2"}],
        tags=[{"display_name": "Farmer"}],
        greeting_imports=[{"character": "adriana", "version": "default",
                           "titles": ["Guild induction", "A farewell"]}],
        greeting_edges=[{"greeting_ref": "new:adriana:default:0",
                         "leads_to": ["new:adriana:default:1"], "excludes": []}],
        greeting_gating=[{"greeting_ref": "new:adriana:default:1",
                          "requires_tags": ["Farmer"], "present": []}])
    monkeypatch.setattr(sys, "argv", ["populate_world_content.py", "run", "--world", wid,
                                       "--manifest", str(manifest_path)])

    assert pwc.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["committed"] is True
    assert out["verify"] == {"ok": True, "problems": []}
    assert out["errors"] == []

    # every stage really landed
    assert [e["name"] for e in entities.list_entities(root, "locations")] == ["Blind Lion"]
    assert [e["name"] for e in entities.list_entities(root, "groups")] == ["Erune family"]
    assert entities.list_entities(root, "lore") == []
    assert list(tags.read_tags(root).values()) == ["Farmer"]
    named = {g["name"]: g["id"] for g in greetings.list_greetings(root)}
    assert set(named) == {"Guild induction", "A farewell"}
    assert greetings.edges_of(greetings.read_plotmap(root),
                              named["Guild induction"])["leads_to"] == [named["A farewell"]]
    assert greetings.read_greeting(root, named["A farewell"])["meta"]["requires_tags"] == ["farmer"]

    # the checkpoint is complete: clean tree, and the lore record is gone from
    # HEAD itself rather than only from the working tree
    assert _status(tmp_path) == ""
    head = _head_files(tmp_path)
    assert f"worlds/{wid}/lore/{lore_id}.md" not in head
    assert f"worlds/{wid}/groups/erune-family.md" in head
    assert f"worlds/{wid}/locations/blind-lion.md" in head

    capsys.readouterr()
    first_log = _log(tmp_path)
    assert pwc.main() == 0                                   # identical manifest again
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["committed"] == "noop"
    assert rerun["errors"] == []
    assert _log(tmp_path) == first_log                       # no second commit
    assert _status(tmp_path) == ""


def test_git_changed_paths_handles_renames(tmp_path):
    """Rename/copy files are parsed correctly in -z output (I4 regression test)."""
    # Init git repo
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    # Create and commit a file
    (tmp_path / "oldname.txt").write_text("content")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=tmp_path, check=True)

    # Rename the file and stage the rename
    (tmp_path / "oldname.txt").unlink()
    (tmp_path / "newname.txt").write_text("content")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    # Get changed paths - should include only the NEW path, not garbled old path
    paths = pwc._git_changed_paths(tmp_path, ".")
    assert paths == {"newname.txt"}
    # Make sure we don't have garbage from truncating the old path
    assert "name.txt" not in paths  # would be old garbage from line[3:] on oldname.txt
    assert "oldname.txt" not in paths
