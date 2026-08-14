"""`store.search` — the index-free keyword sweep over content and facts (#33).

What this file is really pinning down is the corpus and the scope rule: which
files a query reads, and which record a hit is attributed to. The ranking
formula is deliberately tested by ORDER rather than by score, so tuning the
weights stays possible without rewriting the suite.
"""

from __future__ import annotations

import json
import os

import pytest

from grimoire.store import (campaigns, characters, chronicle, entities, facts, frontmatter,
                            greetings, overlay, pcs, plot, relationships, scenes, search,
                            taglines, worlds)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def world(home):
    wid = worlds.create_world("Realm")
    return wid, worlds.world_root(wid)


@pytest.fixture
def campaign(world):
    wid, _ = world
    return campaigns.create_campaign("The Long Run", wid)


# ---- query parsing ----

def test_terms_are_lowercased_deduped_and_keep_quoted_phrases_whole():
    assert search.query_terms('Salt  "tide LEDGER" salt') == ["salt", "tide ledger"]


def test_a_blank_query_is_an_empty_result_not_an_error(home):
    out = search.search("   ")
    assert out["hits"] == [] and out["total"] == 0 and out["terms"] == []


# ---- the content corpus ----

def test_an_entity_body_is_searchable(world):
    wid, root = world
    entities.create_entity(root, "lore", "The Salt Pact",
                           body="Debts written in salt are owed to the sea.")
    hits = search.search("owed to the sea")["hits"]
    assert [(h["scope"], h["kind"], h["id"]) for h in hits] == [("world", "lore", "the-salt-pact")]
    assert hits[0]["root"] == wid and hits[0]["root_name"] == "Realm"


def test_entity_frontmatter_keys_are_searchable_too(world):
    _, root = world
    entities.create_entity(root, "lore", "The Salt Pact", body="Nothing here.", keys="brine")
    assert [h["id"] for h in search.search("brine")["hits"]] == ["the-salt-pact"]


def test_a_character_card_is_searchable_per_version(world):
    _, root = world
    cid, _ = characters.create_character(root, "Seraphine")
    characters.create_version(root, cid, "veiled",
                              {"data": {"name": "Seraphine", "description": "Keeps the tide ledger."}})
    hits = search.search("tide ledger")["hits"]
    assert [(h["kind"], h["id"], h["sub"]) for h in hits] == [("characters", cid, "veiled")]


def test_a_character_is_found_by_the_name_the_app_displays(world):
    """The meta name, not only the card's (#64).

    `character.md`'s name is what every list in the app shows, and a card is
    free to carry another one -- an era label, an alias, a name the import
    guessed. Searching the name on screen has to find the character.
    """
    _, root = world
    cid, _ = characters.create_character(root, "Winifred Hale")
    characters.create_version(root, cid, "masked",
                              {"data": {"name": "The Masked Debtor", "description": "Owes."}})
    hits = search.search("Winifred Hale")["hits"]
    # Both versions, including the one whose card calls her something else --
    # which is the version that could not be found before.
    assert {(h["kind"], h["id"], h["sub"]) for h in hits} == {
        ("characters", cid, "default"), ("characters", cid, "masked")}


def test_a_character_tagline_is_searchable(world):
    """The one-line identity is the shortest description a character has (#64),
    and until now it was the only part of one that no query could reach."""
    _, root = world
    cid, _ = characters.create_character(root, "Seraphine")
    taglines.write(root, cid, "The harbourmaster who counts in debts.")
    assert [h["id"] for h in search.search("harbourmaster")["hits"]] == [cid]


def test_a_tagline_rides_on_the_card_rows_rather_than_getting_one_of_its_own(world):
    """The same arrangement `pc.md` has with its versions: a two-version
    character is two rows, not two plus a third for the sidecar."""
    _, root = world
    cid, _ = characters.create_character(root, "Seraphine")
    characters.create_version(root, cid, "veiled", {"data": {"name": "Seraphine"}})
    taglines.write(root, cid, "The harbourmaster who counts in debts.")
    hits = search.search("harbourmaster")["hits"]
    assert all(h["kind"] == "characters" and h["id"] == cid for h in hits)
    assert sorted(h["sub"] for h in hits) == ["default", "veiled"]


def test_a_campaigns_own_tagline_is_a_campaign_scoped_hit(campaign, world):
    """A campaign that has made a character its own carries its own sidecar,
    and search reports the file that holds the bytes -- so the campaign's line
    is the campaign's hit."""
    _, wroot = world
    cid, _ = characters.create_character(wroot, "Seraphine")
    taglines.write(wroot, cid, "The harbourmaster of the world.")
    croot = campaigns.campaign_root(campaign)
    overlay.materialize_actor(campaign, "characters", cid)
    taglines.write(croot, cid, "The harbourmaster who turned smuggler.")
    hits = search.search("harbourmaster")["hits"]
    assert {(h["scope"], h["id"]) for h in hits} == {("world", cid), ("campaign", cid)}


def test_a_pc_persona_is_searchable_and_carries_its_tags(world):
    _, root = world
    pid, _ = pcs.create_pc(root, "Winifred", ["coastal"],
                           persona={"name": "Winifred", "description": "A debtor with a memory."})
    assert [h["id"] for h in search.search("debtor")["hits"]] == [pid]
    # `pc.md`'s tags ride along on the version rather than getting a row of
    # their own, so a tag search finds exactly one hit for the PC.
    tagged = search.search("coastal")["hits"]
    assert [(h["kind"], h["id"]) for h in tagged] == [("pcs", pid)]


def test_a_greeting_is_searchable(world):
    _, root = world
    cid, _ = characters.create_character(root, "Seraphine")
    greetings.create_greeting(root, "At the ledger", cid, "default",
                              body="She does not look up.")
    assert [h["kind"] for h in search.search("does not look up")["hits"]] == ["greetings"]


def test_a_scene_transcript_is_searchable(campaign):
    sid = scenes.create_scene(campaign, "The Long Quay")
    scenes.append_message(campaign, sid, "user", "I walk the quay looking for Mara.")
    hits = search.search("looking for Mara")["hits"]
    assert [(h["scope"], h["kind"], h["id"]) for h in hits] == [("campaign", "scenes", sid)]
    # The `**Speaker:**` markers are dropped from the snippet: a transcript
    # window is otherwise mostly asterisks.
    assert "**" not in hits[0]["snippet"]


def test_the_world_and_campaign_meta_files_are_records_too(world):
    wid, root = world
    # There is no route that edits a world's body today; the store is
    # hand-editable markdown, and search reads whatever is on disk.
    meta, _body = frontmatter.parse_frontmatter(
        worlds.world_meta_path(wid).read_text(encoding="utf-8"))
    worlds.world_meta_path(wid).write_text(
        frontmatter.dump_frontmatter(meta, "A drowned coast of salt and debt."),
        encoding="utf-8")
    hits = search.search("drowned coast")["hits"]
    assert [(h["kind"], h["id"]) for h in hits] == [("world", wid)]


# ---- the fact corpus ----

def test_chronicle_timeline_plot_facts_and_relationships_are_all_searchable(campaign, world):
    _, wroot = world
    sid = scenes.create_scene(campaign, "The Tide Comes In")
    chronicle.absorb(campaign, {"id": sid, "one_line": "Winifred touched the ledger.",
                                "summary": "A hand on the tide book.", "keywords": ["brine"]})
    chronicle.append_timeline(campaign, [{"date": "day one", "text": "The ledger was opened."}])
    plot.set_movement(campaign, "the-debt", "The debt in salt", "open",
                      "Her name is in the book.", sid)
    facts.record(campaign, "The keep answers to the tide.", "day one", sid)
    relationships.set_feeling(campaign, "pcs:winifred", "characters:seraphine",
                              1, 0, 2, "Owes her a page.")

    def kinds(q):
        return {h["kind"] for h in search.search(q)["hits"]}

    assert kinds("touched the ledger") == {"chronicle"}
    assert kinds("brine") == {"chronicle"}
    assert kinds("ledger was opened") == {"timeline"}
    assert kinds("name is in the book") == {"plot"}
    assert kinds("answers to the tide") == {"facts"}
    assert kinds("owes her a page") == {"relationships"}


def test_a_garbled_fact_file_costs_its_own_rows_and_nothing_else(campaign):
    root = campaigns.campaign_root(campaign)
    (root / "plot.json").write_text("{not json", encoding="utf-8")
    chronicle.append_timeline(campaign, [{"date": "day one", "text": "The ledger was opened."}])
    assert [h["kind"] for h in search.search("ledger was opened")["hits"]] == ["timeline"]


def test_a_relationship_is_named_by_its_actors(campaign, world):
    _, wroot = world
    characters.create_character(wroot, "Seraphine")
    pcs.create_pc(wroot, "Winifred", [])
    relationships.set_feeling(campaign, "pcs:winifred", "characters:seraphine",
                              1, 0, 2, "Owes her a page.")
    hit = search.search("owes her a page")["hits"][0]
    assert hit["name"] == "Winifred → Seraphine"


# ---- scope: a campaign is searched as the files it holds ----

def test_inherited_world_content_is_reported_once_under_the_world(campaign, world):
    """A campaign inherits until it diverges, so an untouched record is the
    world's file and is reported there — not once per campaign that can see it."""
    _, root = world
    entities.create_entity(root, "lore", "The Salt Pact", body="Owed to the sea.")
    hits = search.search("owed to the sea")["hits"]
    assert [(h["scope"], h["root"]) for h in hits] == [("world", root.name)]


def test_a_campaign_fork_is_its_own_record_with_the_same_id(campaign, world):
    from grimoire.store import overlay
    _, root = world
    eid = entities.create_entity(root, "lore", "The Salt Pact", body="Owed to the sea.")
    overlay.update_entity(campaign, "lore", eid, body="Owed to the sea, and to the keep.")
    hits = search.search("owed to the sea")["hits"]
    assert {(h["scope"], h["id"]) for h in hits} == {("world", eid), ("campaign", eid)}


def test_scope_and_root_narrow_the_sweep(campaign, world):
    _, root = world
    eid = entities.create_entity(root, "lore", "The Salt Pact", body="Owed to the sea.")
    scenes.create_scene(campaign, "Owed to the sea")
    assert {h["scope"] for h in search.search("owed to the sea", scope="world")["hits"]} == {"world"}
    assert [h["id"] for h in search.search("owed", scope="world", root=root.name)["hits"]] == [eid]
    assert search.search("owed", scope="world", root="nope")["hits"] == []


def test_an_unknown_scope_or_kind_is_refused(home):
    with pytest.raises(search.BadScope):
        search.search("x", scope="galaxy")
    with pytest.raises(search.BadKind):
        search.search("x", kinds=("sausages",))


# ---- filtering, facets, ranking ----

def test_kinds_filter_the_hits_while_facets_still_count_the_rest(campaign, world):
    _, root = world
    entities.create_entity(root, "lore", "The Salt Pact", body="Salt.")
    scenes.create_scene(campaign, "Salt")
    out = search.search("salt", kinds=("lore",))
    assert [h["kind"] for h in out["hits"]] == ["lore"]
    assert out["total"] == 1
    # The chips have to be able to say what dropping the filter would find.
    assert out["facets"]["scenes"] == 1


def test_a_name_hit_outranks_a_body_mention(campaign, world):
    _, root = world
    entities.create_entity(root, "lore", "Brine", body="Nothing to say.")
    entities.create_entity(root, "lore", "The Deep", body="Brine, everywhere brine.")
    assert [h["name"] for h in search.search("brine")["hits"]] == ["Brine", "The Deep"]


def test_terms_are_anded_across_the_name_and_the_body(world):
    _, root = world
    entities.create_entity(root, "lore", "The Salt Pact", body="Owed to the sea.")
    assert [h["id"] for h in search.search("salt sea")["hits"]] == ["the-salt-pact"]
    assert search.search("salt herring")["hits"] == []


def test_a_quoted_phrase_must_appear_whole(world):
    _, root = world
    entities.create_entity(root, "lore", "The Salt Pact", body="Owed to the sea in salt.")
    assert search.search('"salt owed"')["hits"] == []
    assert len(search.search('"owed to the sea"')["hits"]) == 1


def test_matching_is_substring_and_case_insensitive(world):
    _, root = world
    entities.create_entity(root, "lore", "The Salt Pact", body="Ledgers, plural.")
    assert len(search.search("LEDGER")["hits"]) == 1


def test_the_limit_caps_the_payload_and_says_so(world):
    _, root = world
    for n in range(5):
        entities.create_entity(root, "lore", f"Brine {n}", body="brine")
    out = search.search("brine", limit=2)
    assert len(out["hits"]) == 2 and out["total"] == 5 and out["truncated"] is True


def test_the_snippet_frames_the_match(world):
    _, root = world
    entities.create_entity(root, "lore", "The Deep",
                           body="a " * 200 + "the drowned keeper" + " b" * 200)
    snippet = search.search("drowned keeper")["hits"][0]["snippet"]
    assert "drowned keeper" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) <= search.SNIPPET_CHARS + 2


def test_repeat_queries_agree(world):
    _, root = world
    entities.create_entity(root, "lore", "The Salt Pact", body="Owed to the sea.")
    entities.create_entity(root, "lore", "The Deep", body="Owed to the sea.")
    first = search.search("owed to the sea")["hits"]
    assert [h["id"] for h in first] == [h["id"] for h in search.search("owed to the sea")["hits"]]


def test_an_edit_is_visible_to_the_next_query(world):
    """The extraction memo is keyed on the file's stat signature, so a rewrite
    must not be answered out of the cache."""
    _, root = world
    eid = entities.create_entity(root, "lore", "The Salt Pact", body="Owed to the sea.")
    assert search.search("keep")["hits"] == []
    entities.update_entity(root, "lore", eid, body="Owed to the keep.")
    assert [h["id"] for h in search.search("keep")["hits"]] == [eid]


def test_a_card_that_will_not_parse_costs_only_itself(world):
    _, root = world
    cid, _ = characters.create_character(root, "Seraphine")
    (root / "characters" / cid / "broken.json").write_text("{oh no", encoding="utf-8")
    entities.create_entity(root, "lore", "The Salt Pact", body="Owed to the sea.")
    assert [h["kind"] for h in search.search("owed to the sea")["hits"]] == ["lore"]


def test_a_v1_card_without_a_data_block_is_read_flat(world):
    _, root = world
    cid, _ = characters.create_character(root, "Seraphine")
    (root / "characters" / cid / "old.json").write_text(
        json.dumps({"name": "Seraphine", "description": "The drowned keeper."}), encoding="utf-8")
    assert [h["sub"] for h in search.search("drowned keeper")["hits"]] == ["old"]


def test_a_snippet_quotes_the_prose_not_the_frontmatter(world):
    """A record's frontmatter is searchable, but a snippet from a short record
    would otherwise run straight off the end of the body and into its key
    list — quoting machinery back at the reader as if it were writing."""
    _, root = world
    entities.create_entity(root, "lore", "The Salt Pact", body="Owed to the sea.", keys="brine")
    assert search.search("owed")["hits"][0]["snippet"] == "Owed to the sea."
    # A metadata-only match still snippets from where its match actually is.
    assert "brine" in search.search("brine")["hits"][0]["snippet"]


def test_the_snippet_frames_the_rarest_term_not_the_first_one(world):
    """Framing the earliest match means "the salt pact" opens its window at
    character 0 on the word "the" — the head of the document, with nothing
    distinctive in it and often no marked term visible at all."""
    _, root = world
    entities.create_entity(root, "lore", "The Deep",
                           body="the " * 80 + "a drowned keeper waits " + "and " * 80)
    snippet = search.search("the drowned keeper")["hits"][0]["snippet"]
    assert "drowned keeper" in snippet


def test_a_search_does_not_evict_the_shared_stat_cache(world):
    """A sweep touches every file in the store. Through the process-wide cache
    that would evict every entity and card hash in it and hand the next sync
    sweep a cold one — search made cheap at everyone else's expense."""
    from grimoire.store import statcache
    _, root = world
    for n in range(20):
        entities.create_entity(root, "lore", f"Brine {n}", body="brine")
    # Past `statcache`'s racy window, or nothing is cached at all: a file whose
    # mtime is within the last second is deliberately computed and not stored,
    # so a store written a moment ago memoizes nothing.
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (0, 0))
    statcache._cache.clear()
    statcache._cache[("sentinel", ())] = "kept"
    search.search("brine")
    assert statcache._cache == {("sentinel", ()): "kept"}
    assert search._POOL, "the sweep memoized nothing at all"


def test_case_folding_is_the_store_s_rule_not_lowercasing(world):
    """`"Straße".lower()` is `"straße"`, so a lower-cased search for "strasse"
    misses the record entirely. `casefold` maps both to "strasse" — the rule
    `facts.restates`, `plot.open_threads` and `commitments` already follow."""
    _, root = world
    entities.create_entity(root, "lore", "Straße", body="A road by any spelling.")
    assert [h["name"] for h in search.search("STRASSE")["hits"]] == ["Straße"]
    assert search.search("any spelling")["hits"][0]["name"] == "Straße"


def test_the_snippet_frames_the_match_even_when_folding_changes_the_length(world):
    """`casefold` maps "ß" to "ss", so an offset found in a folded copy is not
    an offset into the original — it drifts by one per such character before
    the match. Two hundred of them (a passage of German prose) drift the window
    clean past the term it was supposed to frame."""
    _, root = world
    entities.create_entity(root, "lore", "The Road",
                           body="Straße " * 200 + "and then a pact of salt")
    assert "pact" in search.search("pact")["hits"][0]["snippet"]


def test_the_query_parser_is_linear_in_the_word_count(world):
    """Pasting a document into the search box is an ordinary accident. Deduping
    terms against a list made this quadratic — a 60k-word paste spent six
    seconds here before the sweep even started."""
    import time
    q = " ".join(f"w{i}" for i in range(20000))
    started = time.perf_counter()
    assert len(search.query_terms(q)) == 20000
    assert time.perf_counter() - started < 0.5


def test_a_store_with_no_worlds_or_campaigns_yet_searches_to_nothing(home):
    """A first run has neither directory. Enumeration must read that as an
    empty library, not as an error on every keystroke of the first search
    anyone tries."""
    assert not (home / "worlds").exists()
    out = search.search("anything at all")
    assert out["hits"] == [] and out["total"] == 0 and out["facets"] == {}
