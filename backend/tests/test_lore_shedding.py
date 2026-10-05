"""Per-entry shedding of World info under a budget (spec §6), per-entry macro
expansion, and the reasons the inspector rows carry (spec §10).

The packer half is held over plain section dicts with a character-count
tokenizer, so the order every unit goes in is exact; the assembly half over a
real store, with budgets small enough (1 token) that the outcome does not hang
on the tokenizer: everything that can give way does, and what is left is what
the rules protect.
"""

from __future__ import annotations

import json
import random
import re
import shutil

import pytest

from grimoire import prompts
from grimoire.store import (
    appearances,
    campaigns,
    characters,
    config,
    context,
    dice,
    entities,
    pins,
    scenes,
    worlds,
)
from grimoire.store.context import assemble, macros, semantic
from grimoire.store.context import pack as context_pack

# ---- the packer, over plain sections -----------------------------------------

def _unit(ref: str, pos: int, *, priority: int = 100, direct: bool = True, age: int = 0,
          keep: bool = False, pinned: bool = False) -> dict:
    return {"ref": ref, "priority": priority, "keep": keep, "pinned": pinned,
            "direct": direct, "age": age, "pos": pos}


def _shedding(units: list[dict], bodies: dict[str, str], *, tier=context_pack.SPOTLIGHT,
              pinned: bool = False, calls: list | None = None) -> dict:
    """A World-info-shaped section whose text is its kept bodies, in unit order."""
    order = [u["ref"] for u in units]

    def render(kept: frozenset) -> str:
        if calls is not None:
            calls.append(kept)
        return "\n\n".join(bodies[r] for r in order if r in kept)

    return {"id": "world_info", "label": "World info", "tier": tier, "pinned": pinned,
            "text": render(frozenset(order)), "shed": {"units": units, "render": render}}


def _lock(text: str = "lock") -> dict:
    return {"id": "lock", "label": "Lock", "text": text, "tier": context_pack.LOCK_IN}


def test_section_without_shed_packs_as_before():
    """A section list with no `shed` key: the same drops, in the same order, and
    no new key on anything it returns."""
    sections = [_lock("L" * 10),
                {"id": "a", "label": "A", "text": "a" * 40, "tier": context_pack.SPOTLIGHT},
                {"id": "b", "label": "B", "text": "b" * 30, "tier": context_pack.BACKGROUND},
                {"id": "c", "label": "C", "text": "c" * 20, "tier": context_pack.SPOTLIGHT,
                 "pinned": True},
                {"id": "d", "label": "D", "text": "d" * 50, "tier": context_pack.SPOTLIGHT}]
    out = context_pack.pack(sections, [], budget=10 + 40 + 20 + 3 * 2, count=len)
    assert [s["dropped"] for s in out["sections"]] == [False, False, True, False, True]
    assert all(set(s) == set(src) | {"dropped"}
               for s, src in zip(out["sections"], sections, strict=True))
    assert out["history_trimmed"] == 0


def test_sheds_lowest_priority_first_then_ties():
    """§6's whole order: priority, then recursion/presence before a direct
    match, then match age (keyless -1 oldest, the seed newest of all), then
    reverse prompt order."""
    units = [_unit("lore:low", 0, priority=50, age=3),
             _unit("lore:pulled", 1, direct=False, age=-1),     # recursion hit
             _unit("lore:old", 2, age=0),                        # matched in post 0
             _unit("lore:new", 3, age=4),                        # matched in post 4
             _unit("lore:seed", 4, age=6),                       # seed: one past the last post
             _unit("lore:keyless-a", 5, age=-1),
             _unit("lore:keyless-b", 6, age=-1)]
    bodies = {u["ref"]: u["ref"].upper() * 3 for u in units}
    calls: list = []
    section = _shedding(units, bodies, calls=calls)
    out = context_pack.pack([_lock(), section], [], budget=1, count=len)
    got = out["sections"][1]
    assert got["shed_refs"] == ["lore:low", "lore:pulled", "lore:keyless-b", "lore:keyless-a",
                                "lore:old", "lore:new", "lore:seed"]
    assert got["dropped"] is True

    # Stopping as soon as it fits: a ceiling that has room for exactly the two
    # newest matches keeps them and nothing else.
    keep = "\n\n".join([bodies["lore:new"], bodies["lore:seed"]])
    section = _shedding(units, bodies)
    out = context_pack.pack([_lock(), section], [], budget=len("lock") + 2 + len(keep),
                            count=len)
    got = out["sections"][1]
    assert got["shed_refs"] == ["lore:low", "lore:pulled", "lore:keyless-b", "lore:keyless-a",
                                "lore:old"]
    assert got["text"] == keep and got["dropped"] is False


def test_keep_and_pinned_never_shed_section_kept():
    units = [_unit("lore:keep", 0, priority=0, keep=True),
             _unit("lore:pin", 1, priority=0, pinned=True),
             _unit("lore:other", 2, priority=900)]
    bodies = {"lore:keep": "K" * 30, "lore:pin": "P" * 30, "lore:other": "O" * 30}
    out = context_pack.pack([_lock(), _shedding(units, bodies)], [], budget=1, count=len)
    got = out["sections"][1]
    assert got["shed_refs"] == ["lore:other"]
    assert got["dropped"] is False
    assert got["text"] == "K" * 30 + "\n\n" + "P" * 30


def test_a_section_level_pin_is_still_a_shedding_candidate():
    """`_pinned_sections` pins World info when ANY entry in it is pinned. With
    `shed` that no longer protects the neighbours -- protection is per unit."""
    units = [_unit("lore:pin", 0, pinned=True), _unit("lore:a", 1), _unit("lore:b", 2)]
    bodies = {"lore:pin": "P" * 10, "lore:a": "A" * 40, "lore:b": "B" * 40}
    out = context_pack.pack([_lock(), _shedding(units, bodies, pinned=True)], [], budget=1,
                            count=len)
    got = out["sections"][1]
    assert got["shed_refs"] == ["lore:b", "lore:a"]
    assert got["dropped"] is False and got["text"] == "P" * 10


def test_all_shed_marks_dropped():
    units = [_unit("lore:a", 0), _unit("lore:b", 1)]
    bodies = {"lore:a": "A" * 40, "lore:b": "B" * 40}
    section = _shedding(units, bodies)
    original = section["text"]
    out = context_pack.pack([_lock(), section], [], budget=1, count=len)
    got = out["sections"][1]
    assert got["dropped"] is True
    assert got["shed_refs"] == ["lore:b", "lore:a"]
    # The text a dropped row shows is the section as it was before the cut.
    assert got["text"] == original


def test_unbounded_budget_untouched():
    def render(_kept):
        raise AssertionError("an unbounded budget must not re-render anything")

    units = [_unit("lore:a", 0)]
    section = {"id": "world_info", "label": "World info", "tier": context_pack.SPOTLIGHT,
               "text": "A" * 400, "shed": {"units": units, "render": render}}
    out = context_pack.pack([_lock(), section], [], budget=0, count=len)
    got = out["sections"][1]
    assert got["text"] == "A" * 400 and got["dropped"] is False
    assert "shed_refs" not in got


def test_a_fitting_prompt_sheds_nothing():
    units = [_unit("lore:a", 0)]
    out = context_pack.pack([_lock(), _shedding(units, {"lore:a": "A" * 10})], [],
                            budget=1000, count=len)
    assert "shed_refs" not in out["sections"][1]


# ---- assembly ----------------------------------------------------------------

@pytest.fixture
def scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    for name in ("Mara", "Winifred"):
        card = characters.blank_card(name)
        card["data"]["description"] = f"{name} of Saltmarch."
        characters.create_character(root, name, "main", card)
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Scene")
    return cid, sid, campaigns.campaign_root(cid)


def _row(breakdown: dict, section_id: str) -> dict:
    return next(r for r in breakdown["sections"] if r["id"] == section_id)


def _system(cid: str, sid: str) -> str:
    return context.build_messages(cid, sid)[0]["content"]


def test_units_carry_what_the_order_reads(scene):
    """The units `_render_sections` hands the packer come off the engine's
    hits: priority and keep from the record, `direct`/`age` from the hit, and
    positions in template order (public, then secret)."""
    cid, sid, croot = scene
    config.write_config(lore_recursion_depth="1")
    entities.create_entity(croot, "lore", "Harbour", "The harbour lies below the lighthouse.",
                           keys="harbour", fields={"priority": "40"})
    entities.create_entity(croot, "lore", "Lighthouse", "The keeper counts ships.",
                           keys="lighthouse", secrecy="secret")
    entities.create_entity(croot, "lore", "Tides", "The tides run twice a day.",
                           fields={"keep": "true"})
    entities.create_entity(croot, "lore", "Ferry", "The ferryman skims the toll.",
                           keys="ferry")
    scenes.append_message(cid, sid, "user", "Calm.")
    scenes.append_message(cid, sid, "user", "We reach the harbour.")
    a = assemble._assemble(cid, sid, wi_seed="Take the ferry.")
    section = next(s for s in assemble._render_sections(a, cid, sid) if s["id"] == "world_info")
    units = {u["ref"]: u for u in section["shed"]["units"]}
    assert units["lore:harbour"] == {"ref": "lore:harbour", "priority": 40, "keep": False,
                                     "pinned": False, "direct": True, "age": 1, "pos": 1}
    assert units["lore:ferry"]["age"] == 2 and units["lore:ferry"]["direct"] is True
    assert units["lore:tides"]["age"] == -1 and units["lore:tides"]["keep"] is True
    # Recursion: not direct, and the secret renders after every public entry.
    assert units["lore:lighthouse"]["direct"] is False
    assert units["lore:lighthouse"]["pos"] == 3
    assert section["shed"]["render"](frozenset(units)) == section["text"]


def test_pinned_entry_does_not_protect_its_neighbours(scene):
    """One pinned entry and three low-priority ones, over budget. Before
    shedding, the pin held the whole section up; now only the pinned entry
    stays."""
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Oath", "The oath binds the harbour.")
    for name in ("Gull", "Rope", "Tar"):
        entities.create_entity(croot, "lore", name, f"The {name.lower()} story. " * 30,
                               fields={"priority": "10"})
    scenes.append_message(cid, sid, "user", "Calm.")
    pins.set_rule(cid, "lore:oath", pins.PIN, sid=sid)
    config.write_config(context_budget="1")
    system = _system(cid, sid)
    assert "The oath binds the harbour." in system
    for name in ("gull", "rope", "tar"):
        assert f"The {name} story." not in system
    row = _row(context.context_breakdown(cid, sid), "world_info")
    assert row["pinned"] is True and row["dropped"] is False
    assert {e["ref"]: e["shed"] for e in row["entries"]} == {
        "lore:oath": False, "lore:gull": True, "lore:rope": True, "lore:tar": True}


def test_secret_sheds_with_its_entry(scene):
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Tides", "The tides run twice a day.",
                           fields={"keep": "true"})
    entities.create_entity(croot, "lore", "Undercurrent", "The harbour master is in debt.",
                           secrecy="secret")
    scenes.append_message(cid, sid, "user", "Calm.")
    assert "Secret knowledge" in _system(cid, sid)
    config.write_config(context_budget="1")
    system = _system(cid, sid)
    assert "The tides run twice a day." in system
    assert "harbour master is in debt" not in system
    assert "Secret knowledge" not in system


def test_a_kept_secret_survives_its_public_neighbour(scene):
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Tides", "The tides run twice a day.")
    entities.create_entity(croot, "lore", "Undercurrent", "The harbour master is in debt.",
                           secrecy="secret", fields={"keep": "true"})
    scenes.append_message(cid, sid, "user", "Calm.")
    config.write_config(context_budget="1")
    system = _system(cid, sid)
    assert "The tides run twice a day." not in system
    assert "Secret knowledge" in system and "harbour master is in debt" in system


def test_recalled_lore_still_drops_whole(scene, monkeypatch):
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Gull", "Gulls nest on the breakwater. " * 20,
                           keys="unsaid-gull", fields={"keep": "true"})
    monkeypatch.setattr(semantic, "recall_scored", lambda c, _t: [(e, 0.5) for e in c])
    scenes.append_message(cid, sid, "user", "Calm.")
    assert _row(context.context_breakdown(cid, sid), "recalled_lore")["dropped"] is False
    config.write_config(context_budget="1")
    row = _row(context.context_breakdown(cid, sid), "recalled_lore")
    assert row["dropped"] is True
    assert [e["shed"] for e in row["entries"]] == [False]


def _choices(monkeypatch) -> list:
    """Every `{{random}}` draw, in order, so a test can tell a re-draw apart."""
    drawn: list = []
    real = random.choice

    def counted(options):
        got = real(options)
        drawn.append(got)
        return got

    monkeypatch.setattr(macros.random, "choice", counted)
    return drawn


def test_macros_expand_once_per_entry_and_identically(scene, monkeypatch):
    """Review Focus 4. Per-entry expansion draws the same options, in the same
    order, as expanding the joined section did; and shedding re-renders from
    text already expanded, so a survivor's draw never moves."""
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Bell", "The bell tolls {{random:once,twice,thrice}}.",
                           fields={"keep": "true"})
    entities.create_entity(croot, "lore", "Gull", "Gulls cry {{random:north,south,east,west}}. "
                           + "Feathers drift. " * 30)
    entities.create_entity(croot, "lore", "Fog", "Fog is {{random:thin,thick}} today.",
                           secrecy="secret")
    scenes.append_message(cid, sid, "user", "Calm.")
    drawn = _choices(monkeypatch)

    a = assemble._assemble(cid, sid)
    random.seed(7)
    new = next(s for s in assemble._render_sections(a, cid, sid) if s["id"] == "world_info")
    per_entry = list(drawn)
    # The pre-change path: the same assembly with no per-entry data renders
    # the template from the raw bodies and expands the joined section.
    old_a = {k: v for k, v in a.items() if k != "lore"}
    drawn.clear()
    random.seed(7)
    old = next(s for s in assemble._render_sections(old_a, cid, sid) if s["id"] == "world_info")
    assert new["text"] == old["text"]
    assert per_entry == drawn
    assert "{{" not in new["text"]

    # Re-rendering a subset draws nothing and reuses the survivors' text.
    drawn.clear()
    kept = new["shed"]["render"](frozenset({"lore:bell"}))
    assert drawn == []
    bell = next(line for line in new["text"].split("\n\n") if line.startswith("The bell"))
    assert kept == bell

    # End to end: under a budget that sheds the others, the bell's line is the
    # one the unbounded prompt carries under the same seed, from as many draws.
    random.seed(11)
    drawn.clear()
    unbounded = _system(cid, sid)
    draws = len(drawn)
    config.write_config(context_budget="1")
    random.seed(11)
    drawn.clear()
    budgeted = _system(cid, sid)
    assert len(drawn) == draws
    bell = re.search(r"The bell tolls \w+\.", unbounded).group(0)
    assert bell in budgeted
    assert "Gulls cry" not in budgeted and "Fog is" not in budgeted


def test_no_budget_prompt_is_identical_to_the_section_level_render(scene):
    """With no budget the per-entry path is invisible: the system message is
    what the section-level expansion produced."""
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Bell", "{{user}} hears the bell {{random:a,b,c}}.")
    entities.create_entity(croot, "lore", "Fog", "Fog over {{user}}.", secrecy="secret")
    entities.create_entity(croot, "lore", "Empty", "")
    entities.create_entity(croot, "lore", "Hollow", "{{nothing}}")   # expands to ""
    scenes.append_message(cid, sid, "user", "Calm.")
    a = assemble._assemble(cid, sid)
    random.seed(3)
    new = [s["text"] for s in assemble._render_sections(a, cid, sid)]
    random.seed(3)
    old = [s["text"] for s in assemble._render_sections(
        {k: v for k, v in a.items() if k != "lore"}, cid, sid)]
    assert new == old


def test_random_and_roll_together_still_expand_identically(scene, monkeypatch):
    """Piece by piece, `{{random}}` and `{{roll}}` interleave where the joined
    section drew every random first. That cannot move a draw: a roll seeds
    itself from the OS, never from `random`. With that source pinned, the
    section is byte-identical to the section-level expansion."""
    cid, sid, croot = scene
    entities.create_entity(croot, "lore", "Bell", "Rolls {{roll:1d20}}, tolls {{random:a,b,c}}.")
    entities.create_entity(croot, "lore", "Fog", "Fog {{random:x,y,z}} for {{roll:3d6}}.",
                           secrecy="secret")
    scenes.append_message(cid, sid, "user", "Calm.")
    seeds = iter(range(1000))
    monkeypatch.setattr(dice.secrets, "randbits", lambda _bits: next(seeds))
    a = assemble._assemble(cid, sid)
    random.seed(5)
    new = next(s for s in assemble._render_sections(a, cid, sid) if s["id"] == "world_info")
    seeds = iter(range(1000))
    random.seed(5)
    old = next(s for s in assemble._render_sections(
        {k: v for k, v in a.items() if k != "lore"}, cid, sid) if s["id"] == "world_info")
    assert new["text"] == old["text"]
    assert "{{" not in new["text"]


@pytest.fixture
def edited_templates(tmp_path, monkeypatch):
    """A copy of the shipped templates whose shared secret heading -- World
    info's own fixed text, not any entry's -- carries macros."""
    root = tmp_path / "templates"
    shutil.copytree(prompts.DEFAULT_TEMPLATES_DIR, root)
    secrecy = root / "scene" / "_secrecy.j2"
    text = secrecy.read_text(encoding="utf-8")
    assert "Secret knowledge —" in text
    secrecy.write_text(text.replace(
        "Secret knowledge —",
        "{% raw %}Secret knowledge ({{random:red,amber,green}}) that {{user}} lacks{% endraw %}"
        " —"),
        encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_TEMPLATES", str(root))
    prompts._env.cache_clear()
    yield root
    prompts._env.cache_clear()


def test_template_text_in_world_info_is_still_expanded(scene, edited_templates,
                                                       monkeypatch):
    """The fixed text around the bodies goes through macros like every other
    section's, and in document order: the public body's draw, then the
    heading's, then the secret body's -- as the section-level expansion drew
    them."""
    cid, sid, croot = scene
    appearances.appear(cid, sid, "characters", "winifred", "main", "player", narrate=False)
    entities.create_entity(croot, "lore", "Bell", "The bell tolls {{random:once,twice}}.")
    entities.create_entity(croot, "lore", "Fog", "Fog is {{random:thin,thick}}.",
                           secrecy="secret")
    scenes.append_message(cid, sid, "user", "Calm.")
    drawn = _choices(monkeypatch)
    a = assemble._assemble(cid, sid)

    random.seed(5)
    new = next(s for s in assemble._render_sections(a, cid, sid) if s["id"] == "world_info")
    new_draws = list(drawn)
    drawn.clear()
    random.seed(5)
    old = next(s for s in assemble._render_sections(
        {k: v for k, v in a.items() if k != "lore"}, cid, sid) if s["id"] == "world_info")
    assert new["text"] == old["text"]
    assert new_draws == drawn
    assert "that Winifred lacks" in new["text"] and "{{" not in new["text"]
    # Three draws in document order: body, heading, body.
    heading = re.search(r"Secret knowledge \((\w+)\)", new["text"]).group(1)
    assert heading in ("red", "amber", "green") and new_draws[1] == heading

    # Shedding the public entry re-renders the heading from memory: no draw.
    drawn.clear()
    kept = new["shed"]["render"](frozenset({"lore:fog"}))
    assert drawn == []
    assert kept.startswith(f"Secret knowledge ({heading}) that Winifred lacks")
    assert kept.endswith(new["text"].rsplit("\n\n", 1)[1])


# ---- rows --------------------------------------------------------------------

def _all_reason_types(cid: str, sid: str, croot, monkeypatch) -> None:
    """A scene whose World info carries every reason type and a held-back
    entry, and whose Recalled lore carries a recall."""
    config.write_config(lore_recursion_depth="1")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    entities.create_entity(croot, "lore", "Harbour", "The harbour lies below the lighthouse.",
                           keys="harbour")
    entities.create_entity(croot, "lore", "Lighthouse", "The keeper counts ships.",
                           keys="lighthouse")                                   # recursion
    entities.create_entity(croot, "lore", "Tides", "The tides run twice a day.")  # keyless
    entities.create_entity(croot, "lore", "Oath", "The oath binds the pier.",
                           keys="unsaid-oath")                                  # pinned
    entities.create_entity(croot, "lore", "Bell", "The bell rang once.", keys="bell",
                           fields={"sticky": "3"})                              # sticky
    entities.create_entity(croot, "lore", "Ferry", "The ferryman skims the toll.",
                           keys="ferry", fields={"cooldown": "3"})              # held back
    entities.create_entity(croot, "items", "Lantern", "A brass lantern.", keys="unsaid-lantern",
                           fields={"holder": "characters:mara"})
    entities.create_entity(croot, "lore", "Oil", "The lantern burns whale oil.",
                           owners="items:lantern")                              # owner
    entities.create_entity(croot, "lore", "Gull", "Gulls nest on the breakwater.",
                           keys="unsaid-gull")                                  # recall
    monkeypatch.setattr(semantic, "recall_scored",
                        lambda c, _t: [(e, 0.52) for e in c if e["id"] == "gull"])
    pins.set_rule(cid, "lore:oath", pins.PIN, sid=sid)
    for text in ("The bell rings.", "The ferry leaves.", "The ferry again.",
                 "We reach the harbour."):
        scenes.append_message(cid, sid, "user", text)


def test_rows_carry_entries_names_and_held_back(scene, monkeypatch):
    cid, sid, croot = scene
    _all_reason_types(cid, sid, croot, monkeypatch)
    breakdown = context.context_breakdown(cid, sid)
    wi = _row(breakdown, "world_info")
    entries = {e["ref"]: e for e in wi["entries"]}
    assert {e["reason"]["type"] for e in entries.values()} == {
        "key", "recursion", "keyless", "pinned", "sticky"}
    harbour = entries["lore:harbour"]
    assert harbour == {"ref": "lore:harbour", "name": "Harbour", "kind": "lore",
                       "secrecy": "public", "priority": 100, "keep": False, "level": 0,
                       "reason": harbour["reason"], "shed": False}
    assert harbour["reason"]["post"] == 3
    assert entries["lore:lighthouse"]["level"] == 1
    oil = entries["lore:oil"]["reason"]
    assert oil["owner"] == "items:lantern"
    assert oil["owner_presence"] == {"type": "held_by", "via": "characters:mara"}
    # Every ref a reason names resolves -- the lantern never activated, and is
    # named all the same.
    assert wi["names"] == {"lore:harbour": "Harbour", "items:lantern": "Lantern",
                           "characters:mara": "Mara"}
    assert wi["held_back"] == [{"ref": "lore:ferry", "name": "Ferry",
                                "reason": {"type": "cooldown", "remaining": 2}}]

    recalled = _row(breakdown, "recalled_lore")
    assert [(e["ref"], e["reason"]["type"], e["reason"]["score"], e["shed"])
            for e in recalled["entries"]] == [("lore:gull", "recall", 0.52, False)]
    assert recalled["names"] == {}
    assert "held_back" not in recalled
    # Nothing callable anywhere a row can reach.
    json.dumps(breakdown)
    assert all("shed" not in r for r in breakdown["sections"])


def test_an_unresolved_ref_names_itself():
    hits = [type("H", (), {"reason": {"type": "recursion", "via": "lore:gone",
                                      "owner": "characters:mara",
                                      "owner_presence": {"type": "held_by",
                                                         "via": "pcs:nobody"}}})()]
    assert assemble._reason_names(hits, {"characters:mara": "Mara"}) == {
        "lore:gone": "lore:gone", "characters:mara": "Mara", "pcs:nobody": "pcs:nobody"}


def test_reasons_never_reach_the_prompt(scene, monkeypatch):
    cid, sid, croot = scene
    _all_reason_types(cid, sid, croot, monkeypatch)
    breakdown = context.context_breakdown(cid, sid)
    types = {e["reason"]["type"] for r in ("world_info", "recalled_lore")
             for e in _row(breakdown, r)["entries"]}
    assert types == {"key", "recursion", "keyless", "pinned", "sticky", "recall"}
    sent = json.dumps(context.build_messages(cid, sid))
    # Every entry's body is there: the reasons were in play this turn.
    for body in ("The keeper counts ships.", "The tides run", "The oath binds",
                 "The bell rang once.", "whale oil", "Gulls nest"):
        assert body in sent, body
    for text in ("key '", "pulled in", "owner present", "always on", "sticky —",
                 "similarity", "on cooldown", "lore:harbour", "items:lantern", "lore:ferry",
                 "held_by", "owner_presence", "from_post", "0.52", "\"recursion\"",
                 "\"keyless\"", "\"sticky\"", "\"recall\"", "\"cooldown\""):
        assert text not in sent, text
