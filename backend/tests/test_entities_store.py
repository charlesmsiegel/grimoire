import os
import re
import time
from pathlib import Path

import pytest

from grimoire.store import entities, tokens

REPO = Path(__file__).resolve().parents[2]


def test_create_read_and_stable_id(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "locations", "Drowned Library", "Halls of brine.")
    assert eid == "drowned-library"
    got = entities.read_entity(tmp_path, "locations", eid)
    assert got["meta"]["name"] == "Drowned Library"
    assert got["body"].strip() == "Halls of brine."
    # renaming keeps the id; only the name frontmatter changes
    entities.update_entity(tmp_path, "locations", eid, name="The Drowned Library")
    assert eid == "drowned-library"
    assert entities.read_entity(tmp_path, "locations", eid)["meta"]["name"] == "The Drowned Library"


def test_collision_suffix(tmp_path: Path):
    a = entities.create_entity(tmp_path, "locations", "Echo")
    b = entities.create_entity(tmp_path, "locations", "Echo")
    assert a == "echo"
    assert b == "echo-2"


def test_synced_refs_includes_greetings(tmp_path: Path):
    (tmp_path / "locations").mkdir()
    (tmp_path / "locations" / "inn.md").write_text("---\nname: Inn\n---\n", encoding="utf-8")
    (tmp_path / "greetings").mkdir()
    (tmp_path / "greetings" / "gala.md").write_text("---\nname: Gala\n---\n", encoding="utf-8")
    assert entities.synced_refs(tmp_path) == [("locations", "inn"), ("greetings", "gala")]
    # greetings are synced but not generic-CRUD entities
    assert "greetings" in entities.SYNCED_KINDS
    assert "greetings" not in entities.ENTITY_KINDS


def test_characters_is_not_a_generic_kind(tmp_path: Path):
    assert "characters" not in entities.ENTITY_KINDS
    with pytest.raises(entities.UnknownKind):
        entities.create_entity(tmp_path, "characters", "X")


def test_unknown_kind_raises(tmp_path: Path):
    with pytest.raises(entities.UnknownKind):
        entities.create_entity(tmp_path, "weapons", "Sword")


def test_missing_entity_raises(tmp_path: Path):
    with pytest.raises(entities.EntityNotFound):
        entities.read_entity(tmp_path, "lore", "nope")


def test_hash_changes_only_with_content(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Salt Pact", "Old.")
    h1 = entities.entity_hash(tmp_path, "lore", eid)
    entities.update_entity(tmp_path, "lore", eid, body="Old.")  # no change
    assert entities.entity_hash(tmp_path, "lore", eid) == h1
    entities.update_entity(tmp_path, "lore", eid, body="New.")
    assert entities.entity_hash(tmp_path, "lore", eid) != h1
    assert entities.entity_hash(tmp_path, "lore", "absent") is None


def test_traversal_ids_are_rejected(tmp_path: Path):
    # an id that would escape the kind dir is treated as not-found, never read
    with pytest.raises(entities.EntityNotFound):
        entities.read_entity(tmp_path, "locations", "../../secret")
    with pytest.raises(entities.EntityNotFound):
        entities.delete_entity(tmp_path, "locations", "..")
    assert entities.entity_hash(tmp_path, "locations", "../x") is None


def test_keys_round_trip(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Salt Pact", "the pact", keys="pact, salt")
    assert entities.read_entity(tmp_path, "lore", eid)["meta"]["keys"] == "pact, salt"
    # update can change keys without touching the body
    entities.update_entity(tmp_path, "lore", eid, keys="pact")
    got = entities.read_entity(tmp_path, "lore", eid)
    assert got["meta"]["keys"] == "pact"
    assert got["body"].strip() == "the pact"
    # entities without keys read as empty string
    e2 = entities.create_entity(tmp_path, "lore", "No Keys", "x")
    assert entities.read_entity(tmp_path, "lore", e2)["meta"].get("keys", "") == ""


def test_owners_round_trip(tmp_path: Path):
    eid = entities.create_entity(
        tmp_path, "lore", "Tanaka's exile", "He was cast out.",
        keys="exile", owners="characters:master-tanaka, locations:old-dojo",
    )
    got = entities.read_entity(tmp_path, "lore", eid)
    assert got["meta"]["owners"] == "characters:master-tanaka, locations:old-dojo"
    assert got["meta"]["keys"] == "exile"


def test_sd_prompt_round_trip(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "locations", "The Crypt", "cold", sd_prompt="a dark crypt")
    got = entities.read_entity(tmp_path, "locations", eid)
    assert got["meta"]["sd_prompt"] == "a dark crypt"
    entities.update_entity(tmp_path, "locations", eid, sd_prompt="an even darker crypt")
    assert entities.read_entity(tmp_path, "locations", eid)["meta"]["sd_prompt"] == "an even darker crypt"
    # entities without sd_prompt read as empty string, and it's omitted from meta (mirrors keys/owners)
    e2 = entities.create_entity(tmp_path, "locations", "No Prompt")
    assert entities.read_entity(tmp_path, "locations", e2)["meta"].get("sd_prompt", "") == ""


def test_owners_absent_when_empty(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "World fact", "Always true.")
    got = entities.read_entity(tmp_path, "lore", eid)
    assert "owners" not in got["meta"]  # mirror keys: omit when empty


def test_update_owners(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Fact", "x")
    entities.update_entity(tmp_path, "lore", eid, owners="pcs:hero")
    assert entities.read_entity(tmp_path, "lore", eid)["meta"]["owners"] == "pcs:hero"
    # body/name untouched
    assert entities.read_entity(tmp_path, "lore", eid)["body"].strip() == "x"


def test_new_kinds_are_generic_entities(tmp_path: Path):
    for kind, name in (("items", "Salt Knife"), ("groups", "Salt Circle"), ("creatures", "Marsh Wyrm")):
        eid = entities.create_entity(tmp_path, kind, name, "body text", keys="salt")
        got = entities.read_entity(tmp_path, kind, eid)
        assert got["meta"]["name"] == name
        assert got["meta"]["keys"] == "salt"
    assert entities.entity_counts(tmp_path) == {
        "locations": 0, "lore": 0, "items": 1, "groups": 1, "creatures": 1}


def test_all_refs_and_counts(tmp_path: Path):
    entities.create_entity(tmp_path, "lore", "A")
    entities.create_entity(tmp_path, "locations", "B")
    assert set(entities.all_refs(tmp_path)) == {("lore", "a"), ("locations", "b")}
    assert entities.entity_counts(tmp_path) == {
        "locations": 1, "lore": 1, "items": 0, "groups": 0, "creatures": 0}


# ---- secrecy (#49) ----------------------------------------------------------

def test_secrecy_round_trip(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "The Twist", "x", secrecy="secret")
    assert entities.read_entity(tmp_path, "lore", eid)["meta"]["secrecy"] == "secret"
    entities.update_entity(tmp_path, "lore", eid, secrecy="gm-only")
    assert entities.read_entity(tmp_path, "lore", eid)["meta"]["secrecy"] == "gm-only"
    # back to public: the key goes away rather than being written out
    entities.update_entity(tmp_path, "lore", eid, secrecy="public")
    got = entities.read_entity(tmp_path, "lore", eid)
    assert "secrecy" not in got["meta"]
    assert got["body"].strip() == "x"                 # body untouched throughout


def test_secrecy_public_leaves_the_file_as_it_always_was(tmp_path: Path):
    """An unmarked record must be byte-identical to a pre-secrecy one — the
    world->campaign sync hashes whole files, so a stray `secrecy: public` would
    show every entity as edited."""
    plain = entities.create_entity(tmp_path, "lore", "Plain", "x")
    marked = entities.create_entity(tmp_path, "lore", "Plain public", "x", secrecy="public")
    read = (tmp_path / "lore" / f"{plain}.md").read_text(encoding="utf-8")
    assert "secrecy" not in read
    assert "secrecy" not in (tmp_path / "lore" / f"{marked}.md").read_text(encoding="utf-8")


def test_secrecy_omitted_on_update_keeps_the_stored_level(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Twist", "x", secrecy="secret")
    entities.update_entity(tmp_path, "lore", eid, body="edited")   # secrecy=None
    got = entities.read_entity(tmp_path, "lore", eid)
    assert got["meta"]["secrecy"] == "secret"
    assert got["body"].strip() == "edited"


def test_secrecy_survives_in_list_summaries(tmp_path: Path):
    entities.create_entity(tmp_path, "lore", "Twist", "x", secrecy="secret")
    assert entities.list_entities(tmp_path, "lore")[0]["secrecy"] == "secret"


def test_normalize_secrecy_is_lenient(tmp_path: Path):
    assert entities.normalize_secrecy(None) == "public"
    assert entities.normalize_secrecy("") == "public"
    assert entities.normalize_secrecy("  SECRET ") == "secret"
    assert entities.normalize_secrecy("sercet") == "public"      # typo reads as public
    # a garbled level is never written back out as one
    eid = entities.create_entity(tmp_path, "lore", "Typo", "x", secrecy="sercet")
    assert "secrecy" not in entities.read_entity(tmp_path, "lore", eid)["meta"]

# ---- per-record token cost (#51) ----

def _age(p: Path) -> None:
    """Move `p`'s mtime out of `statcache`'s racy window, so a signature taken
    on it is trusted and `record_tokens` is allowed to cache."""
    old = time.time() - 60
    os.utime(p, (old, old))


def test_tokens_measure_the_body_the_prompt_would_get(tmp_path: Path):
    body = "The tide reads the ledger aloud at every turning of the year."
    eid = entities.create_entity(tmp_path, "lore", "Saltmarch Rite", body, keys="tide")
    got = entities.read_entity(tmp_path, "lore", eid)
    # exactly the counter the context inspector reports, over exactly the
    # string `context.world_state._world_info` hands the assembler
    assert got["tokens"] == tokens.count_tokens(body)
    assert got["tokens"] > 0
    listed = entities.list_entities(tmp_path, "lore")
    assert [e["tokens"] for e in listed] == [got["tokens"]]


def test_tokens_ignore_frontmatter(tmp_path: Path):
    lean = entities.create_entity(tmp_path, "lore", "A", "salt")
    fat = entities.create_entity(tmp_path, "lore", "A name that is very much longer", "salt",
                                 keys="a, b, c, d, e, f, g, h", owners="pcs:mara, pcs:winifred")
    # frontmatter never enters the prompt, so it must not enter the badge either
    assert (entities.read_entity(tmp_path, "lore", lean)["tokens"]
            == entities.read_entity(tmp_path, "lore", fat)["tokens"])


def test_an_empty_body_costs_nothing(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Placeholder", "")
    assert entities.read_entity(tmp_path, "lore", eid)["tokens"] == 0
    assert entities.list_entities(tmp_path, "lore")[0]["tokens"] == 0


def test_a_frontmatter_tokens_key_does_not_report_its_own_cost(tmp_path: Path):
    d = tmp_path / "lore"
    d.mkdir()
    (d / "forged.md").write_text("---\nname: Forged\ntokens: '0'\n---\nsalt and rope\n",
                                 encoding="utf-8")
    real = tokens.count_tokens("salt and rope")
    assert entities.list_entities(tmp_path, "lore")[0]["tokens"] == real
    assert entities.read_entity(tmp_path, "lore", "forged")["tokens"] == real


def test_tokens_are_stable_across_a_relist_and_move_with_the_body(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Rite", "one line")
    first = entities.list_entities(tmp_path, "lore")[0]["tokens"]
    assert entities.list_entities(tmp_path, "lore")[0]["tokens"] == first
    entities.update_entity(tmp_path, "lore", eid, body="one line, and then a good many more of them")
    assert entities.list_entities(tmp_path, "lore")[0]["tokens"] > first


def test_an_unchanged_file_is_not_re_encoded(tmp_path: Path, monkeypatch):
    """The sweep reason the count is memoized: re-listing a world must not pay
    tiktoken again for every record whose bytes did not move."""
    p = tmp_path / "lore" / "rite.md"
    entities.create_entity(tmp_path, "lore", "Rite", "salt and rope")
    _age(p)
    calls = []
    real = tokens.count_tokens
    monkeypatch.setattr(tokens, "count_tokens", lambda t: calls.append(t) or real(t))

    first = entities.list_entities(tmp_path, "lore")[0]["tokens"]
    assert calls == ["salt and rope"]           # a cold read encodes once
    assert entities.list_entities(tmp_path, "lore")[0]["tokens"] == first
    assert calls == ["salt and rope"]           # and the second sweep not at all

    entities.update_entity(tmp_path, "lore", "rite", body="salt, rope and a longer tail")
    _age(p)
    assert entities.list_entities(tmp_path, "lore")[0]["tokens"] > first
    assert calls[-1] == "salt, rope and a longer tail"  # new bytes, new encode


def test_a_record_count_is_not_a_term_in_the_section_total(tmp_path: Path):
    """The badge counts one body; the World info section counts the activated
    bodies JOINED. BPE is not additive, so the badges summed sit at or below the
    section's own count -- close enough to reason with, and not a figure to
    reconcile. Asserting equality here is the tempting wrong move, so the real
    relationship is pinned instead.
    """
    bodies = ["salt", "rope", "brine"]
    for i, b in enumerate(bodies):
        entities.create_entity(tmp_path, "lore", f"Entry {i}", b)
    badges = [e["tokens"] for e in entities.list_entities(tmp_path, "lore")]
    # exactly how `templates/scene/sections/world_info.j2` renders them
    section = tokens.count_tokens("\n\n".join(bodies))
    assert sum(badges) <= section
    assert section - sum(badges) <= len(bodies) - 1   # at most one per join


def test_the_count_is_of_the_stored_text_macros_unexpanded(tmp_path: Path):
    """A ceiling, deliberately. `_render_sections` expands macros before the
    model sees a body, so `{{random:...}}` collapses to one option and the
    stored form can measure well over what is actually sent. There is no single
    right number -- the expansion is per scene -- and over-reporting is the safe
    direction for a figure someone trims against.
    """
    stored = "{{user}} owes the tide. The wind is {{random:screaming,silent,rising}}."
    eid = entities.create_entity(tmp_path, "lore", "Debt", stored)
    badge = entities.read_entity(tmp_path, "lore", eid)["tokens"]
    assert badge == tokens.count_tokens(stored)
    assert badge > tokens.count_tokens("Seraphine owes the tide. The wind is rising.")


def test_a_gm_only_body_is_still_measured(tmp_path: Path):
    """Secrecy (#49) gates whether the body is SENT; it does not change what the
    body would cost. The count stays the size of the text, because that is what
    the editor needs to answer "what does publishing this buy me" -- the UI
    marks it as never charged rather than reporting zero, which would be
    indistinguishable from an empty entry.
    """
    body = "The tide reads the ledger aloud at every turning of the year."
    eid = entities.create_entity(tmp_path, "lore", "Rite", body, secrecy=entities.GM_ONLY)
    got = entities.read_entity(tmp_path, "lore", eid)
    assert got["meta"]["secrecy"] == entities.GM_ONLY
    assert got["tokens"] == tokens.count_tokens(body)
    # ...and marking it public again does not move the number
    entities.update_entity(tmp_path, "lore", eid, secrecy=entities.PUBLIC)
    assert entities.read_entity(tmp_path, "lore", eid)["tokens"] == got["tokens"]


def test_require_entity_answers_from_a_stat_and_names_what_is_wrong(tmp_path: Path):
    """The gate the entity image writes take per request (#373). It has to
    distinguish the two failures the routes report differently -- an unknown
    kind is not a missing record -- and it must not read the file, because the
    read it would replace costs a frontmatter parse and a token encode."""
    eid = entities.create_entity(tmp_path, "locations", "Saltmarch", "Low tide, high walls.")
    assert entities.require_entity(tmp_path, "locations", eid) == tmp_path / "locations" / f"{eid}.md"

    with pytest.raises(entities.EntityNotFound):
        entities.require_entity(tmp_path, "locations", "nobody")
    with pytest.raises(entities.UnknownKind):
        entities.require_entity(tmp_path, "potions", eid)
    # an id the resolvers refuse is refused here too, rather than resolving a
    # path outside the kind directory and reporting it as real
    with pytest.raises(entities.EntityNotFound):
        entities.require_entity(tmp_path, "locations", "../locations/" + eid)

    # and a deleted record stops satisfying it
    entities.delete_entity(tmp_path, "locations", eid)
    with pytest.raises(entities.EntityNotFound):
        entities.require_entity(tmp_path, "locations", eid)


def test_the_frontend_ships_the_same_kind_list():
    """`ENTITY_KINDS` in `frontend/src/api/types.ts` is this tuple, in order.

    The frontend holds a second copy for two jobs the server cannot do at
    build time: the compile-time union the tabs, labels and per-kind field
    table are written against, and the fallback the import dialogs' Category
    dropdown shows when `GET /api/entity-kinds` does not answer (#138).

    Both jobs rot silently. A kind added here and forgotten there gets no tab
    and no label -- so a row imported into it, through the dropdown the
    endpoint correctly widened, lands somewhere this build cannot show. That
    is a worse failure than the drift the endpoint fixed, and nothing else in
    either suite notices it.
    """
    src = (REPO / "frontend" / "src" / "api" / "types.ts").read_text(encoding="utf-8")
    m = re.search(r"export const ENTITY_KINDS = \[(.*?)\] as const;", src, re.DOTALL)
    assert m, "ENTITY_KINDS is not declared in types.ts in the shape this guard reads"
    assert re.findall(r'"([^"]+)"', m.group(1)) == list(entities.ENTITY_KINDS), \
        "frontend/src/api/types.ts ENTITY_KINDS has drifted from entities.ENTITY_KINDS"
