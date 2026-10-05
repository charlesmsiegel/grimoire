"""Golden prompts for the world-info byte-identity promise (spec §1).

With no activation field set and `lore_recursion_depth` at its default, every
narrator prompt must be byte-for-byte what it was before the activation engine
replaced `activate`'s joined-text rule. `fixtures/lore_golden.json` holds the
prompts the PREVIOUS code assembled for each scenario below; it was written
before the engine was wired in, and this test holds today's code to it.

Each scenario reproduces one shape the context suites build -- keyed, keyless,
owned (present and absent), secret and gm-only lore; a public, secret and owned
current setting; pins and excludes; macros under a seeded RNG; the off-scene
cast's shared heading; a stored director note, a director turn and an opener;
an NPC actor call; and a budget that drops World info whole.

The NPC scenario uses only character-owned and unowned lore. Public lore owned
by a location, item, group or creature is the one place NPC prompts are meant
to change (§8.2), so it is not frozen here.

Regenerating is deliberate, exactly like the frozen campaign's snapshot: only
when a prompt moved on purpose and the new text was reviewed --

    cd backend && PYTHONPATH=src .venv/bin/python -m tests.test_lore_golden --write

(on Windows: `cd backend; $env:PYTHONPATH="src"; .venv\\Scripts\\python.exe -m
tests.test_lore_golden --write`).
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from grimoire.store import (
    appearances,
    campaigns,
    characters,
    config,
    context,
    dossiers,
    entities,
    pcs,
    pins,
    scenes,
    taglines,
    worlds,
)

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "lore_golden.json"
SEED = 20261005


def _card(name: str, description: str) -> dict:
    card = characters.blank_card(name)
    card["data"]["description"] = description
    return card


def _world(*names: str) -> tuple[str, Path]:
    # Explicit, so the suite's tracker default (`conftest.py`) and the `--write`
    # entry point (which has no conftest) assemble the same prompt.
    config.write_config(tracker="off")
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    for name in names:
        characters.create_character(root, name, "main", _card(name, f"{name} of Saltmarch."))
    return wid, root


def _messages(messages) -> list[dict]:
    """A prompt as plain dicts; the RNG is seeded per build, so a `{{random}}`
    draws the same option every time."""
    return [dict(m) for m in messages]


def _build(fn: Callable, *args, **kwargs) -> list[dict]:
    random.seed(SEED)
    return _messages(fn(*args, **kwargs))


# ---- scenarios ---------------------------------------------------------------

def mixed() -> dict:
    """Keyed hits and misses (one only outside the scan window), keyless,
    owned present and absent, secret and gm-only, plus every world-info kind."""
    wid, _ = _world("Mara", "Winifred")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "Scene")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    config.write_config(context_scan_depth="3")
    for role, text in (("user", "We walked past the lighthouse at dawn."),
                       ("assistant", "The wind came off the water."),
                       ("user", "Mara asked about the harbour and the ferry."),
                       ("assistant", "Mara studies her ledger."),
                       ("user", "Tell me about the lantern, the docks and the serpent.")):
        scenes.append_message(cid, sid, role, text)
    lore = (("Harbour", "The harbour freezes in deep winter.", "harbour", "", ""),
            ("Lighthouse", "The lighthouse keeper counts ships.", "lighthouse", "", ""),
            ("Tides", "The tides run twice a day.", "", "", ""),
            ("Mara debts", "Mara keeps a list of old debts.", "", "characters:mara", ""),
            ("Mara oath", "Mara swore never to sell the ledger.", "ledger", "characters:mara", ""),
            ("Winifred past", "Winifred once sailed with smugglers.", "", "characters:winifred", ""),
            ("Ferry toll", "The ferryman skims the toll.", "ferry", "", "secret"),
            ("Undercurrent", "The harbour master is in debt.", "", "", "secret"),
            ("Referee note", "The warehouse burns on day nine.", "", "", "gm-only"),
            ("Referee plan", "The harbour closes by winter.", "harbour", "", "gm-only"))
    for name, body, keys, owners, secrecy in lore:
        entities.create_entity(croot, "lore", name, body, keys=keys, owners=owners,
                               secrecy=secrecy)
    entities.create_entity(croot, "locations", "Saltmarch Docks", "Tarred planks and rope.",
                           keys="docks")
    entities.create_entity(croot, "locations", "Back Alley", "A narrow lane.")  # keyless: never always-on
    entities.create_entity(croot, "items", "Lantern", "A brass lantern.", keys="lantern")
    entities.create_entity(croot, "groups", "Harbour Guild", "The dockworkers' guild.",
                           keys="guild")
    entities.create_entity(croot, "creatures", "Sea Serpent", "A long grey shape.",
                           keys="serpent")
    return {"narrator": _build(context.build_messages, cid, sid)}


def current_setting() -> dict:
    """A public, a secret and an owned current setting, with lore the current
    location owns and a keyed location that is not the current one."""
    wid, _ = _world("Mara")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    out = {}
    pier = entities.create_entity(croot, "locations", "The Pier", "Fog-slick planks.")
    vault = entities.create_entity(croot, "locations", "The Vault",
                                   "A ledger sits open on the desk.", secrecy="secret")
    study = entities.create_entity(croot, "locations", "Mara Study", "Charts pinned to the wall.",
                                   owners="characters:mara")
    entities.create_entity(croot, "lore", "Pier secret", "A blade is hidden under the planks.",
                           owners=f"locations:{pier}")
    entities.create_entity(croot, "locations", "Saltmarch Docks", "Tarred planks and rope.",
                           keys="docks")
    for label, loc in (("public", pier), ("secret", vault), ("owned", study)):
        sid = scenes.create_scene(cid, label)
        appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
        scenes.set_location(cid, sid, loc)
        scenes.append_message(cid, sid, "user", "We look toward the docks.")
        out[label] = _build(context.build_messages, cid, sid)
    return out


def pins_and_excludes() -> dict:
    """A pin beats keys and the owner gate and surfaces a keyless location; an
    exclude removes a keyword hit and a cast member's owned lore; a pinned
    gm-only entry stays out."""
    wid, _ = _world("Mara", "Winifred")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "Scene")
    for who in ("mara", "winifred"):
        appearances.appear(cid, sid, "characters", who, "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "The harbour bell rings.")
    entities.create_entity(croot, "lore", "Tide oath", "The oath binds the tide.", keys="dragon")
    entities.create_entity(croot, "lore", "Absent past", "An absent owner's story.",
                           owners="characters:nobody")
    entities.create_entity(croot, "lore", "Harbour", "The harbour freezes in deep winter.",
                           keys="harbour")
    entities.create_entity(croot, "lore", "Mara debts", "Mara keeps a list of old debts.",
                           owners="characters:mara")
    entities.create_entity(croot, "lore", "Winifred past", "Winifred once sailed with smugglers.",
                           owners="characters:winifred")
    entities.create_entity(croot, "lore", "Referee note", "The warehouse burns on day nine.",
                           secrecy="gm-only")
    alley = entities.create_entity(croot, "locations", "Back Alley", "A narrow lane.")
    for ref, mode in (("lore:tide-oath", pins.PIN), ("lore:absent-past", pins.PIN),
                      (f"locations:{alley}", pins.PIN), ("lore:referee-note", pins.PIN),
                      ("lore:harbour", pins.EXCLUDE), ("characters:mara", pins.EXCLUDE)):
        pins.set_rule(cid, ref, mode, sid=sid)
    return {"narrator": _build(context.build_messages, cid, sid)}


def macros_in_lore() -> dict:
    """A lore body with `{{user}}` and `{{random:...}}`, under a seeded RNG."""
    wid, _ = _world("Mara")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    pid, _vid = pcs.create_pc(croot, "Seraphine", [], persona=pcs.blank_persona("Seraphine"))
    sid = scenes.create_scene(cid, "Scene")
    appearances.appear(cid, sid, "pcs", pid, "default", "player", narrate=False)
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "I listen for the bell.")
    entities.create_entity(croot, "lore", "Bell",
                           "{{user}} hears the bell ring {{random:once,twice,thrice}}.",
                           keys="bell")
    entities.create_entity(croot, "lore", "Tides", "{{user}} knows the tide tables.")
    return {"narrator": _build(context.build_messages, cid, sid)}


def off_scene_heading() -> dict:
    """Both off-scene tiers under their shared heading, beside world info."""
    wid, wroot = _world("Mara", "Winifred", "Seraphine")
    taglines.write(wroot, "seraphine", "A distant person of no immediate consequence.")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "Scene")
    other = scenes.create_scene(cid, "Elsewhere")
    appearances.appear(cid, other, "characters", "winifred", "main", "npc")
    dossiers.write(croot, "winifred", "Winifred keeps the harbour accounts.")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc")
    scenes.append_message(cid, sid, "user", "The harbour is quiet.")
    entities.create_entity(croot, "lore", "Harbour", "The harbour freezes in deep winter.",
                           keys="harbour")
    return {"narrator": _build(context.build_messages, cid, sid)}


def director() -> dict:
    """A stored director note in the transcript (it is in the scan window),
    then a director turn whose note seeds activation."""
    from grimoire.store.scenes import serialize
    wid, _ = _world("Mara")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "Scene")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "We wait on the quay.")
    scenes.append_message(cid, sid, "assistant", "Bring up the lighthouse.",
                          speaker=serialize.DIRECTOR_SPEAKER)
    scenes.append_message(cid, sid, "assistant", "A beam sweeps the water.")
    entities.create_entity(croot, "lore", "Lighthouse", "The lighthouse keeper counts ships.",
                           keys="lighthouse")
    entities.create_entity(croot, "lore", "Ferry toll", "The ferryman skims the toll.",
                           keys="ferry")
    return {"narrator": _build(context.build_messages, cid, sid),
            "director": _build(context.build_director_messages, cid, sid,
                               "Describe the ferry crossing.")}


def scan_depth_zero() -> dict:
    """Depth 0 empties the transcript window; a director note still seeds."""
    wid, _ = _world("Mara")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "Scene")
    config.write_config(context_scan_depth="0")
    scenes.append_message(cid, sid, "user", "The harbour bell rings.")
    entities.create_entity(croot, "lore", "Harbour", "The harbour freezes in deep winter.",
                           keys="harbour")
    entities.create_entity(croot, "lore", "Ferry toll", "The ferryman skims the toll.",
                           keys="ferry")
    entities.create_entity(croot, "lore", "Tides", "The tides run twice a day.")
    return {"narrator": _build(context.build_messages, cid, sid),
            "director": _build(context.build_director_messages, cid, sid, "Take the ferry.")}


def opener() -> dict:
    """An empty scene: the opener prompt is the whole activation window."""
    wid, _ = _world("Mara")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "Scene")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    entities.create_entity(croot, "lore", "Harbour", "The harbour freezes in deep winter.",
                           keys="harbour")
    entities.create_entity(croot, "lore", "Lighthouse", "The lighthouse keeper counts ships.",
                           keys="lighthouse")
    entities.create_entity(croot, "lore", "Mara debts", "Mara keeps a list of old debts.",
                           owners="characters:mara")
    return {"opener": _build(context.build_opener_messages, cid, sid,
                             "Open at the harbour at dusk.")}


def actor_call() -> dict:
    """An NPC actor call: its own observed history is its window, its own lore
    reaches it and nobody else's, and unowned secrets stay out. Character-owned
    and unowned lore only (see the module docstring)."""
    wid, _ = _world("Mara", "Winifred")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "Scene")
    for who in ("mara", "winifred"):
        appearances.appear(cid, sid, "characters", who, "main", "npc", narrate=False)
    appearances.leave(cid, sid, "characters", "mara")
    scenes.append_message(cid, sid, "user", "The lighthouse burns in the night.")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "The harbour bell rings at dawn.")
    entities.create_entity(croot, "lore", "Lighthouse", "The lighthouse keeper counts ships.",
                           keys="lighthouse")
    entities.create_entity(croot, "lore", "Harbour", "The harbour freezes in deep winter.",
                           keys="harbour")
    entities.create_entity(croot, "lore", "Mara debts", "Mara keeps a list of old debts.",
                           owners="characters:mara", secrecy="secret")
    entities.create_entity(croot, "lore", "Mara oath", "Mara swore never to sell the ledger.",
                           keys="harbour", owners="characters:mara")
    entities.create_entity(croot, "lore", "Winifred past", "Winifred once sailed with smugglers.",
                           owners="characters:winifred")
    entities.create_entity(croot, "lore", "Undercurrent", "The harbour master is in debt.",
                           secrecy="secret")
    random.seed(SEED)
    prepared, _ = context.compose_turn(cid, sid, actor_ref="characters:mara",
                                       describe=False)
    return {"actor": _messages(prepared),
            "narrator": _build(context.build_messages, cid, sid)}


def budget_drops_world_info() -> dict:
    """A ceiling that forces the packer to drop World info, whole."""
    wid, _ = _world("Mara")
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "Scene")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "The harbour bell rings.")
    entities.create_entity(croot, "lore", "Harbour", "The harbour freezes in deep winter. " * 40,
                           keys="harbour")
    entities.create_entity(croot, "lore", "Tides", "The tides run twice a day. " * 20)
    breakdown = context.context_breakdown(cid, sid)
    row = next(s for s in breakdown["sections"] if s["label"] == "World info")
    config.write_config(context_budget=str(breakdown["total_tokens"] - row["tokens"] // 2))
    return {"narrator": _build(context.build_messages, cid, sid)}


SCENARIOS: dict[str, Callable[[], dict]] = {
    "mixed": mixed,
    "current_setting": current_setting,
    "pins_and_excludes": pins_and_excludes,
    "macros_in_lore": macros_in_lore,
    "off_scene_heading": off_scene_heading,
    "director": director,
    "scan_depth_zero": scan_depth_zero,
    "opener": opener,
    "actor_call": actor_call,
    "budget_drops_world_info": budget_drops_world_info,
}


# ---- the test ----------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_prompt_is_byte_identical_without_new_fields(name, monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    got = SCENARIOS[name]()
    assert set(got) == set(golden[name])
    for label, messages in got.items():
        assert messages == golden[name][label], f"{name}/{label} moved"


def test_the_matrix_covers_what_it_claims():
    """A golden matrix that froze empty sections would pass forever, so pin the
    shapes it is there to hold: each scenario put something in World info, and
    each exclusion it claims to freeze actually excluded."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(golden) == set(SCENARIOS)

    def system(name: str, label: str = "narrator") -> str:
        return golden[name][label][0]["content"]

    mixed_sys = system("mixed")
    for body in ("The harbour freezes", "The tides run", "Mara keeps a list",
                 "Mara swore", "The ferryman skims", "Tarred planks", "A brass lantern",
                 "A long grey shape"):
        assert body in mixed_sys, body
    for body in ("lighthouse keeper", "Winifred once sailed", "warehouse burns",
                 "closes by winter", "A narrow lane", "dockworkers' guild"):
        assert body not in mixed_sys, body
    assert "Secret knowledge" in mixed_sys

    pins_sys = system("pins_and_excludes")
    assert "The oath binds" in pins_sys and "An absent owner's" in pins_sys
    assert "A narrow lane" in pins_sys and "Winifred once sailed" in pins_sys
    assert "freezes in deep winter" not in pins_sys and "Mara keeps a list" not in pins_sys
    assert "warehouse burns" not in pins_sys

    assert "Seraphine hears the bell ring" in system("macros_in_lore")
    assert "{{" not in system("macros_in_lore")
    assert system("off_scene_heading").count("# Other characters in this world") == 1
    assert "lighthouse keeper" in system("director")
    assert "The ferryman skims" in system("director", "director")
    assert "freezes in deep winter" not in system("scan_depth_zero")
    assert "The ferryman skims" in system("scan_depth_zero", "director")
    assert "freezes in deep winter" in system("opener", "opener")
    assert "lighthouse keeper" not in system("opener", "opener")

    actor = system("actor_call", "actor")
    assert "Mara keeps a list" in actor and "Mara swore" in actor
    assert "freezes in deep winter" in actor
    assert "lighthouse keeper" not in actor          # said while Mara was away
    assert "Winifred once sailed" not in actor and "harbour master is in debt" not in actor
    assert "lighthouse keeper" in system("actor_call")

    budget = system("budget_drops_world_info")
    assert "freezes in deep winter" not in budget and "The tides run" not in budget

    for scenario, labels in golden.items():
        for label, messages in labels.items():
            assert messages and messages[0]["role"] == "system", (scenario, label)


# ---- regeneration --------------------------------------------------------------

def _write() -> int:
    """Regenerate the golden file, every scenario in a scratch store. The
    tracker default is pinned off, as `conftest.py` pins it for the suite."""
    config.DEFAULT_TRACKER = "off"
    out = {}
    for name in sorted(SCENARIOS):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GRIMOIRE_HOME"] = str(Path(tmp) / "home")
            out[name] = SCENARIOS[name]()
    GOLDEN.write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    sys.stdout.write(f"wrote {GOLDEN} ({len(out)} scenarios)\n")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: python -m tests.test_lore_golden --write")
    sys.exit(_write())
