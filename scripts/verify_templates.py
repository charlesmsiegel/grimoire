"""Prove the prompt builders and templates/ agree byte-for-byte.

The store modules render prompts from templates/ — this harness verifies the
WIRING (each builder passes the documented variables to the right template)
and the DATA CONTRACT (the gather() mirror below assembles the same data from
public store reads that context._assemble gathers; templates/README.md
documents that contract). It builds a throwaway store (GRIMOIRE_HOME -> temp
dir) exercising every context section, then compares direct template renders
against the live builders. It never pins template text to literals, so
editing a prompt in templates/ cannot fail it. Run after touching either
side:

    backend/.venv/Scripts/python.exe scripts/verify_templates.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend" / "src"))
os.environ["GRIMOIRE_HOME"] = tempfile.mkdtemp(prefix="grimoire-verify-")

from jinja2 import Environment, FileSystemLoader, StrictUndefined  # noqa: E402

env = Environment(loader=FileSystemLoader(str(REPO / "templates")),
                  undefined=StrictUndefined)

FAILURES: list[str] = []
CHECKS = 0


def render(_template: str, **vars) -> str:
    return env.get_template(_template).render(**vars)


def check(label: str, expected: str, actual: str) -> None:
    global CHECKS
    CHECKS += 1
    if expected == actual:
        return
    i = next((n for n, (a, b) in enumerate(zip(expected, actual)) if a != b),
             min(len(expected), len(actual)))
    FAILURES.append(
        f"{label}: mismatch at char {i}\n"
        f"  expected …{expected[max(0, i - 60):i + 60]!r}…\n"
        f"  actual   …{actual[max(0, i - 60):i + 60]!r}…")


def check_messages(label: str, expected: list[dict], actual: list[dict]) -> None:
    global CHECKS
    if [m["role"] for m in expected] != [m["role"] for m in actual]:
        CHECKS += 1
        FAILURES.append(f"{label}: role shape {[m['role'] for m in expected]} != "
                        f"{[m['role'] for m in actual]}")
        return
    for n, (e, a) in enumerate(zip(expected, actual)):
        check(f"{label}[{n}] ({e['role']})", e["content"], a["content"])


# ---------------------------------------------------------------- pure checks

from grimoire.store import (absorb, chronicle, context, dossiers, relationships,  # noqa: E402
                            rolling_summary, scenario, suggest, taglines, voice_anchors,
                            voice_drift)

card = {"name": "Seraphine Vale", "description": "Tall, sharp-eyed smuggler.",
        "personality": "Wry and wary.", "scenario": "Runs the night dock."}
exp = taglines.build_prompt(card)
check("tagline system", exp[0]["content"], render("tagline/system.j2"))
check("tagline user", exp[1]["content"], render("tagline/user.j2", card=card))
sparse = {"name": "Bob", "description": "", "scenario": "A bar."}  # missing keys too
exp = taglines.build_prompt(sparse)
check("tagline user (sparse)", exp[1]["content"], render("tagline/user.j2", card=sparse))

# Scenario-card extraction (#217). Both cards on purpose: the full one renders
# every heading, and the bare one proves an absent field contributes none —
# `user.j2` branches per field, and a comparison that only ever took the
# populated branch would not see the other one move.
SCENARIO_CARD = {"data": {
    "name": "Saltmarch",
    "description": "A drowned town where Mara keeps the tide-gate.",
    "personality": "Wary, tidal.",
    "scenario": "The gate has not opened in nine days.",
    "creator_notes": "Play it slow.",
    "mes_example": "<START>\n**Mara:** The gate stays shut.",
    "first_mes": "Mara is waiting at the tide-gate.",
    "alternate_greetings": [
        "The square is empty.",
        "![](data:image/png;base64,AAAA)\n\nWinifred counts the stalls.",
        # Past GREETING_PROMPT_CHARS, so the clip is exercised rather than
        # asserted about: every body in this fixture used to be short enough
        # that `_clip` was the identity, and a harness that only ever takes the
        # no-op branch proves nothing about the branch that moves.
        "Winifred opens the ledger. " + "The tide came in. " * 120,
    ],
    "character_book": {"entries": [
        {"keys": ["gate"], "name": "The Tide-Gate", "content": "Iron and barnacle.",
         "enabled": True},
        {"keys": ["ledger"], "name": "The Ledger",   # ...and past ENTRY_PROMPT_CHARS
         "content": "Six stalls and a scale. " + "It lists every crossing. " * 60,
         "enabled": True}]},
}}
for label, scard in (("full", SCENARIO_CARD), ("bare", {"data": {"name": "Saltmarch"}})):
    exp = scenario.build_prompt(scard)
    check(f"scenario system ({label})", exp[0]["content"], render("scenario/system.j2"))
    check(f"scenario user ({label})", exp[1]["content"],
          render("scenario/user.j2", card=scard["data"], fields=scenario.PROMPT_FIELDS,
                 entries=scenario.prompt_entries(scard),
                 greetings=scenario.prompt_greetings(scard)))
scenario_user = scenario.build_prompt(SCENARIO_CARD)[1]["content"]
assert "Existing entries:" in scenario_user, \
    "scenario user no longer lists the card's own world-info -- the model cannot re-file it"
assert "base64" not in scenario_user, \
    "scenario user is carrying an opener's embedded image into the prompt"
# The two clipped bodies really were clipped, so the comparison above covered
# the clipping helper rather than a fixture that happened to fit.
assert scenario_user.count(" …") == 2, \
    f"the scenario fixture no longer exercises _clip (found {scenario_user.count(' …')} clips)"
assert len(scenario_user) < 4000, \
    "scenario user is unbounded again -- a big card would blow the context window"

transcript = "**You:** Where is it?\n\n**Seraphine Vale:** Gone."
for prior in ("", "She ran the dock and owed the Guild."):
    exp = dossiers.build_prompt("Seraphine Vale", prior, transcript)
    check(f"dossier system (prior={bool(prior)})", exp[0]["content"], render("dossier/system.j2"))
    check(f"dossier user (prior={bool(prior)})", exp[1]["content"],
          render("dossier/user.j2", name="Seraphine Vale", prior=prior, transcript=transcript))

voice_card = {**card, "mes_example": "<START>\n**Seraphine Vale:** Try me.",
              "system_prompt": "Voice Seraphine with dry wit."}
exp = voice_anchors.build_prompt(voice_card)
check("voice anchor system", exp[0]["content"], render("voice_anchor/system.j2"))
check("voice anchor user", exp[1]["content"], render("voice_anchor/user.j2", card=voice_card))
# The sparse card exercises the "(none)" fallbacks — a card with no example
# dialogue is the common case for a character that has never been played.
exp = voice_anchors.build_prompt(sparse)
check("voice anchor user (sparse)", exp[1]["content"], render("voice_anchor/user.j2", card=sparse))

anchor = "Clipped. Never uses contractions.\nAnswers questions with questions."
exp = voice_drift.build_prompt("Seraphine Vale", anchor, transcript)
check("voice drift system", exp[0]["content"], render("voice_drift/system.j2"))
check("voice drift user", exp[1]["content"],
      render("voice_drift/user.j2", name="Seraphine Vale", anchor=anchor, transcript=transcript))

# Both folds, because the user template branches on `prior` and the two branches
# label the transcript differently -- a from-scratch fold that said "posts since
# that summary" would be asking the model to fold onto a summary it never got.
# ...and both fact heads, since a scene with no location, date or cast yet must
# render no head at all rather than an empty one.
ROLLING_FACTS = {"location": "Night Dock", "date": "2026-07-05",
                 "cast": ["characters/seraphine", "pcs/hero"]}
for prior in ("", "Seraphine held the dock; the ledger was still missing."):
    for facts in (None, {"location": "", "date": "", "cast": []}, ROLLING_FACTS):
        exp = rolling_summary.build_prompt(prior, transcript, facts)
        check(f"rolling summary system (prior={bool(prior)})", exp[0]["content"],
              render("rolling_summary/system.j2"))
        check(f"rolling summary user (prior={bool(prior)}, facts={bool(facts)})",
              exp[1]["content"],
              render("rolling_summary/user.j2", prior=prior, transcript=transcript,
                     facts=facts))

EMPTY_SNAP = {"now": "", "friendly": "", "holidays_today": [], "upcoming": None,
              "birthdays": [], "story_so_far": [], "open_threads": [], "cast": [],
              "available_locations": []}
FULL_SNAP = {"now": "2026-07-05", "friendly": "July 5, 2026", "holidays_today": ["Founding Day"],
             "upcoming": {"name": "The Regatta", "in_days": 3},
             "birthdays": [{"name": "Hero", "age": 26, "when": "today"},
                           {"name": "Mora", "age": 51, "when": "in 2 days"}],
             "story_so_far": [{"one_line": "Hero followed Seraphine into the fog.",
                               "location": "Night Dock", "date": "2026-07-05"},
                              {"one_line": "Hero met Kessler.", "location": "", "date": "2026-07-01"}],
             "open_threads": [{"title": "Find the ledger", "status": "open",
                               "latest_beat": "Hero learned it exists.", "dormancy": 0},
                              {"title": "The Guild debt", "status": "advanced", "latest_beat": "",
                               "dormancy": 3}],
             "cast": [{"token": "characters:mora", "name": "Mora",
                       "tagline": "A fortune-teller who deals in secrets.",
                       "status": "appeared", "role": "npc"},
                      {"token": "characters:silent-jim", "name": "Silent Jim", "tagline": "",
                       "status": "unseen", "role": "npc"},
                      {"token": "pcs:hero", "name": "Hero", "tagline": "", "status": "present",
                       "role": "player"}],
             "available_locations": [{"id": "night-dock", "name": "Night Dock"}]}
GREETINGS = [{"id": "g1", "name": "Storm greeting", "excerpt": "Rain hammers the piers."},
             {"id": "g2", "name": "Quiet morning", "excerpt": "The dock sleeps."},
             {"id": "g3", "name": "Debt call", "excerpt": "A collector knocks."}]
for label, snap, cands, off, direction in (
        ("empty", EMPTY_SNAP, None, False, ""),
        ("full", FULL_SNAP, None, False, ""),
        ("full+greetings", FULL_SNAP, GREETINGS, False, ""),
        ("offscreen", FULL_SNAP, None, True, ""),
        ("direction", FULL_SNAP, None, False, "something at sea"),
        ("direction+greetings", FULL_SNAP, GREETINGS, False, "something at sea")):
    exp = suggest.build_prompt(snap, cands, offscreen=off, direction=direction)
    check(f"suggestions system ({label})", exp[0]["content"],
          render("scene_suggestions/system.j2", s=snap, offscreen=off,
                 greeting_candidates=cands, direction=direction))
    check(f"suggestions user ({label})", exp[1]["content"],
          render("scene_suggestions/user.j2", s=snap, offscreen=off,
                 greeting_candidates=cands, direction=direction))

for label, facts, st, rel, plt, grp, cmt, fct in (
        ("bare", {}, None, None, None, None, None, None),
        ("full", {"location": "Night Dock", "date": "2026-07-05",
                  "cast": ["characters/seraphine-vale", "pcs/hero"]},
         {"Seraphine Vale": "Wounded. Knows: The ledger is real."},
         "Seraphine Vale → Hero: trust 2, affection 3, tension 4 (suspects a tail)",
         "find-the-ledger: Find the ledger (open) — Hero learned it exists.",
         "- groups/salt-circle (Salt Circle): Goals: Expand.",
         "the-deadline: Midnight deadline (threat, open), due midnight "
         "— Hero was given until midnight.",
         "f1: The warehouse belongs to the Salt Circle. (the third night)")):
    exp = absorb.build_prompt(transcript, facts, st, rel, plt, grp, cmt, fct)
    check(f"absorb system ({label})", exp[0]["content"], render("absorb/system.j2"))
    check(f"absorb user ({label})", exp[1]["content"],
          render("absorb/user.j2", facts=facts, state_snapshot=st, rel_snapshot=rel,
                 plot_snapshot=plt, group_snapshot=grp, commitment_snapshot=cmt,
                 fact_snapshot=fct, transcript=transcript))
    if grp:
        assert "Groups:" in exp[1]["content"], f"absorb user ({label}) missing Groups: head line"
    if cmt:
        assert "Open commitments:" in exp[1]["content"], \
            f"absorb user ({label}) missing Open commitments: head line"
    if fct:
        assert "Standing facts:" in exp[1]["content"], \
            f"absorb user ({label}) missing Standing facts: head line"

msgs = [{"role": "user", "content": "hi"},
        {"role": "user", "speaker": "Hero", "content": "yo"},
        {"role": "assistant", "speaker": "Seraphine Vale", "content": "Try me."},
        {"role": "assistant", "content": "Fog rolls in."}]
check("transcript", chronicle.transcript_text(msgs), render("snippets/transcript.j2", messages=msgs))

for st in ({"current_state": "Wounded.", "knows": "The ledger is real.", "suspects": "Bob lied."},
           {"current_state": "", "knows": "The ledger is real.", "suspects": ""},
           {"current_state": "Hiding.", "knows": "", "suspects": ""}):
    check(f"state snapshot line ({st['current_state'] or 'no-cs'})",
          absorb._snapshot_line(st), render("snippets/state_snapshot_line.j2", st=st))

routes_src = "\n".join(p.read_text(encoding="utf-8")
                       for p in sorted((REPO / "backend/src/grimoire/routes").glob("*.py")))
assert 'prompts.render("scene/director_note.j2")' in routes_src, \
    "the routes package no longer renders the director-note template"
assert 'prompts.render("scene/regenerate_guidance.j2"' in routes_src, \
    "the routes package no longer renders the regenerate-guidance template"
assert 'prompts.render(\n        "scene/roll_result.j2"' in routes_src \
    or 'prompts.render("scene/roll_result.j2"' in routes_src, \
    "the routes package no longer renders the roll-result continuation template (#162)"
assert 'prompts.render("scene/roll_declined.j2")' in routes_src, \
    "the routes package no longer renders the roll-declined continuation template (#162)"

# ------------------------------------------------------------- store fixture

from grimoire.store import appearances as ap  # noqa: E402
from grimoire.store import (audit, calendars, campaigns, characters, checks,  # noqa: E402
                            commitments, config,
                            dossiers as dstore, entities, facts as fstore, groupstate,
                            length_drift, lengths, modules, pcs,
                            playstate, plot, response_presets, scenes, sheets, styles,
                            taglines as tstore, turnstate, voice_anchors as vastore,
                            voice_drift as vdstore, weather as wstore, worlds)

# recap_depth=1 narrows the recap window to the newest absorbed scene, which is
# what leaves an older one outside it for archive retrieval (#127) to recall —
# the archive section is empty by construction while every record is in recap.
# turnstate_depth is non-zero for the same reason the fixture writes a playstate
# and a group state: the transient-state sections (#120) ship disabled, and a
# section that renders "" on both sides of the comparison proves nothing.
# speaker_turn_taking is on for exactly that reason too (#29) — it ships off,
# and the multi-NPC scenes below are what make its section non-empty.
config.write_config(system_prompt="Global GM rules: be vivid, be fair.", recap_depth="1",
                    turnstate_depth="4", speaker_turn_taking="on")

# a bound mechanics module (#162 Task 6): one sheet type, one check, one
# always-on rules doc -- so mechanics_rules/mechanics_sheets/mechanics_checks
# (and their three system.j2 sections) are all non-empty below.
mod_dir = Path(os.environ["GRIMOIRE_HOME"]) / "modules" / "keeper-arts"
(mod_dir / "rules").mkdir(parents=True)
(mod_dir / "module.md").write_text("---\nname: Keeper Arts\n---\n", encoding="utf-8")
(mod_dir / "sheets.json").write_text(json.dumps({
    "groups": {},
    "sheet_types": {
        "keeper": {"label": "Keeper", "kind": "characters", "groups": [],
                  "fields": [{"key": "grit", "label": "Grit", "type": "resource", "max": 5}]},
    },
}), encoding="utf-8")
(mod_dir / "checks.json").write_text(json.dumps({
    "steady-hand": {"label": "Steady Hand", "requires": [], "roll": "1d20"},
}), encoding="utf-8")
(mod_dir / "rules" / "core.md").write_text("---\nalways: true\n---\nKeep every roll honest.\n",
                                           encoding="utf-8")

wid = worlds.create_world("W")
cid = campaigns.create_campaign("Run", wid, module="keeper-arts")
croot = campaigns.campaign_root(cid)
sid = scenes.create_scene(cid, "S1")

ncard = characters.blank_card("Seraphine Vale")
ncard["data"].update({"description": "Tall, sharp-eyed smuggler.", "personality": "Wry and wary.",
                      "scenario": "Runs the night dock.", "system_prompt": "Voice Seraphine with dry wit.",
                      "mes_example": "<START>\n**Seraphine Vale:** Try me.",
                      "post_history_instructions": "Keep replies under four paragraphs."})
sera, _ = characters.create_character(croot, "Seraphine Vale", "default", ncard)
ap.appear(cid, sid, "characters", sera, "default", "npc")
playstate.write_state(croot, sera, playstate.compose_body(
    "Wounded and hiding.", "The ledger is real.\nIt names the harbormaster.", "The Guild watches her."))
sheets.write(cid, "characters", sera, "keeper", {"grit": {"current": 2, "max": 5}},
             expected=None)

persona = pcs.blank_persona("Hero")
persona.update({"pronouns": "she/her", "summary": "A debt-ridden courier.",
                "description": "Quick, kind, unlucky.", "birthdate": "2000-07-05"})
pid, _ = pcs.create_pc(croot, "Hero", [], persona=persona)
ap.appear(cid, sid, "pcs", pid, "default", "player")

kcard = characters.blank_card("Doc Kessler")
kcard["data"].update({"description": "A weary back-alley medic."})
kessler, _ = characters.create_character(croot, "Doc Kessler", "default", kcard)
sid0 = scenes.create_scene(cid, "S0")
ap.appear(cid, sid0, "characters", kessler, "default", "npc")
dstore.write(croot, kessler, "Kessler patches up smugglers and quietly owes the Guild.")

# An anchored, PRESENT NPC carrying an unresolved voice-drift flag, so
# post_history.j2 renders the voice corrective in every scene comparison below
# rather than the empty string. BOTH halves are required: context._assemble
# honours a flag only while the character still has an anchor, so dropping the
# anchor here would silently reduce the comparison to "" == "".
vastore.write(croot, sera, "Clipped. Never uses contractions. Answers a question with a question.")
vdstore.write(croot, sera, "She used contractions and hedged twice; Seraphine never softens a refusal.")

mcard = characters.blank_card("Mora")
mora, _ = characters.create_character(croot, "Mora", "default", mcard)
tstore.write(croot, mora, "A fortune-teller who deals in secrets.")

dock = entities.create_entity(croot, "locations", "Night Dock",
                              "Fog-slick piers stacked with contraband.", keys="dock, pier")
entities.create_entity(croot, "locations", "Bonded Warehouse",
                       "Crates to the ceiling, one door.", keys="warehouse")
entities.create_entity(croot, "lore", "Salt Pact", "The Pact taxes every crossing.")
entities.create_entity(croot, "lore", "The Ledger", "The ledger lists a decade of bribes.",
                       keys="ledger")
entities.create_entity(croot, "lore", "Sera secret", "She was exiled from the Guild.",
                       owners=f"characters:{sera}")
# Secrecy (#49): a `secret` entry so the World info section renders its labelled
# block here, and a `gm-only` one that must never appear in any render at all.
entities.create_entity(croot, "lore", "The Ledger's true owner",
                       "The Guildmaster keeps the ledger himself.",
                       keys="ledger", secrecy="secret")
entities.create_entity(croot, "lore", "Referee note", "The warehouse burns on day nine.",
                       secrecy="gm-only")
circle = entities.create_entity(croot, "groups", "Salt Circle",
                                "A quiet cabal moving contraband.")  # keyless -> always-on
groupstate.write_state(croot, circle, "## Goals\nCorner the ledger before the Guild does.")
# A secret group WITH state, so the Group state section renders both of its
# blocks here: state is the half of a group worth gating (FIELDS ends in
# `secrets`), and it must not depend on World info to carry the heading.
cabal = entities.create_entity(croot, "groups", "The Quiet Office",
                               "Nobody admits it exists.", secrecy="secret")
groupstate.write_state(croot, cabal, "## Secrets\nThey hold the Guildmaster's debt.")
scenes.set_location(cid, sid, dock)
sid = scenes.set_datetime(cid, sid, "2026-07-05")["id"]  # first date set renames the scene

scenes.append_message(cid, sid, "user", "Where is the ledger?")
scenes.append_message(cid, sid, "assistant", "Seraphine glances toward the warehouse.",
                      speaker="Seraphine Vale")
scenes.append_message(cid, sid, "assistant", "Fog rolls in off the water.")
scenes.append_message(cid, sid, "user", "I follow her.", speaker="Hero")

# The transient ledger (#120), filed against the last post of the scene as
# `_persist_reply` would. Two entries holding the same mood, so `streaks` has
# something to see as well as `current`.
turnstate.record(cid, sid, 1, {f"characters:{sera}": {"mood": "wary"}})
turnstate.record(cid, sid, 3, {f"characters:{sera}": {"mood": "wary", "intent": "reach the warehouse first",
                                                      "posture": "half-turned toward the door"}})

relationships.set_feeling(cid, f"characters:{sera}", f"pcs:{pid}", 2, 3, 4, "suspects a tail")
relationships.set_bond(cid, f"characters:{sera}", f"pcs:{pid}", "reluctant allies")
plot.set_movement(cid, "find-the-ledger", "Find the ledger", "open", "Hero learned it exists.", sid)
commitments.set_movement(cid, "the-deadline", "Midnight deadline", "threat", "open",
                         "midnight", "Hero was given until midnight.", sid)
fstore.record(cid, "The warehouse belongs to the Salt Circle.", "the third night", sid)
chronicle.absorb(cid, {"id": sid0, "one_line": "Hero met Kessler.",
                       "summary": "Hero met Doc Kessler in his clinic and traded a favor for gossip.",
                       "keywords": ["clinic"], "cast": [f"characters/{kessler}"],
                       "location": "", "date": "2026-07-01"})
# Older than the recap window and keyed on a word the live scene says out loud,
# so this one — and only this one — comes back through the archive section.
# `chronicle.recent` orders by id and scene ids carry an ordinal prefix, so the
# id has to sort below the real scenes' (`001--…`, `002--…`) to be "older".
chronicle.absorb(cid, {"id": "000--2026-06-20--harbor-run",
                       "one_line": "The warehouse changed hands.",
                       "summary": "The bonded warehouse changed hands after a bad night on the pier.",
                       "keywords": ["warehouse"], "cast": [], "location": "", "date": "2026-06-20"})


def _secrecy_of(meta: dict) -> str:
    """`entities.normalize_secrecy`, re-implemented rather than imported: this
    harness is the independent copy of the data contract, so it spells the rule
    out the way the templates/README.md description does."""
    level = (meta.get("secrecy") or "public").strip().lower()
    return level if level in ("public", "secret", "gm-only") else "public"


def gather(scene_id: str, pcless: bool, wi_seed: str = "", full_recap: int = 0) -> dict:
    """Mirror context._assemble's data gathering through public store reads —
    this is the data contract documented in templates/README.md."""
    cfg = config.read_config()
    scene = scenes.read_scene(cid, scene_id)
    cast = ap.scene_cast(cid, scene_id)

    npc_cards, states = [], []
    for a in cast:
        if a["role"] != "npc":
            continue
        vid = ap.locked_version(cid, a["kind"], a["id"])
        npc_cards.append(characters.read_card(croot, a["id"], vid)["data"])
        st = playstate.read_state(croot, a["id"])
        if st and (st["current_state"] or st["knows"] or st["suspects"]):
            name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            states.append({"name": name, **st})

    # Mirror of context.world_state._transient_states: the ledger, decayed to
    # `turnstate_depth` posts of the tail, labelled with the CAST name.
    depth = max(int(cfg.get("turnstate_depth", "0")), 0)
    live = turnstate.current(cid, scene_id, len(scene["messages"]), depth)
    transient_states = []
    for a in cast:
        if a["role"] != "npc" or a["kind"] != "characters":
            continue
        held = live.get(f"characters:{a['id']}") or {}
        rows = [{"label": f, "value": held[f]} for f in turnstate.FIELDS if held.get(f)]
        if rows:
            transient_states.append({"name": a["name"], "fields": rows})

    players, player_names = [], []
    for a in cast:
        if a["role"] != "player":
            continue
        vid = ap.locked_version(cid, a["kind"], a["id"])
        if a["kind"] == "pcs":
            p = pcs.read_persona(croot, a["id"], vid)
            players.append({"kind": "pcs", **p})
            player_names.append(p.get("name", a["id"]))
        else:
            d = characters.read_card(croot, a["id"], vid)["data"]
            players.append({"kind": "characters", **d})
            player_names.append(d.get("name", a["id"]))

    refs, ref_names = [], []
    if pcless:
        for a in ap.roster(cid):
            if a["role"] != "player":
                continue
            if a["kind"] == "pcs":
                p = pcs.read_persona(croot, a["id"], a["version"])
                refs.append({"kind": "pcs", **p})
                ref_names.append(p.get("name", a["id"]))
            else:
                d = characters.read_card(croot, a["id"], a["version"])["data"]
                refs.append({"kind": "characters", **d})
                ref_names.append(d.get("name", a["id"]))

    tokens = [f"{a['kind']}:{a['id']}" for a in cast]
    relationship_lines = relationships.render_present(
        cid, tokens, lambda t: relationships.actor_name(cid, t))

    depth = full_recap or max(int(cfg.get("recap_depth", "5")), 0)
    records = chronicle.recent(cid, depth) if depth > 0 else []
    story_entries = [((r.get("summary") or r.get("one_line") or "") if full_recap
                      else (r.get("one_line") or r.get("summary") or "")).strip()
                     for r in records]

    history_ids = scenes.get_location_history(cid, scene_id)
    current_loc = history_ids[-1] if history_ids else None
    current_setting = ""
    current_setting_secret = False
    if current_loc:
        loc_meta = entities.read_entity(croot, "locations", current_loc)
        loc_secrecy = _secrecy_of(loc_meta["meta"])
        if loc_secrecy != "gm-only":
            current_setting = loc_meta["body"].strip()
            current_setting_secret = loc_secrecy == "secret"

    scan = max(int(cfg.get("context_scan_depth", "8")), 0)
    recent_text = "\n".join(m["content"] for m in scene["messages"][-scan:]) if scan else ""
    if wi_seed:
        recent_text = (recent_text + "\n" + wi_seed).strip()

    # Archive retrieval (#127): absorbed scenes OUTSIDE the recap window whose
    # keywords the scan window says, newest id first, capped at archive_depth.
    # The recap window and the scene being played are excluded so no scene can
    # arrive twice.
    seen = {r.get("id", "") for r in records} | {scene_id}
    archive_entries = []
    for r in chronicle.read_chronicle(cid).values():
        rid = r.get("id", "")
        keys = [str(k).strip() for k in (r.get("keywords") or []) if str(k).strip()]
        text = (r.get("summary") or r.get("one_line") or "").strip()
        if not rid or rid in seen or not keys or not text:
            continue
        if any(re.search(rf"\b{re.escape(k)}\b", recent_text, re.IGNORECASE) for k in keys):
            archive_entries.append({"id": rid, "date": (r.get("date") or "").strip(), "text": text})
    archive_entries.sort(key=lambda h: h["id"], reverse=True)
    archive_entries = archive_entries[:max(int(cfg.get("archive_depth", "3")), 0)]

    entries = []
    for kind in ("lore", "locations", "items", "groups", "creatures"):
        for meta in entities.list_entities(croot, kind):
            if kind == "locations" and meta["id"] == current_loc:
                continue
            e = entities.read_entity(croot, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            owners = [o.strip() for o in e["meta"].get("owners", "").split(",") if o.strip()]
            if kind == "locations" and not keys:
                continue
            entries.append({"body": e["body"].strip(), "keys": keys, "owners": owners,
                            "secrecy": _secrecy_of(e["meta"]), "kind": kind, "id": meta["id"],
                            "name": e["meta"].get("name", meta["id"])})
    present = set(tokens) | ({f"locations:{current_loc}"} if current_loc else set())
    activated = context.activate(entries, recent_text, frozenset(present))
    world_info_bodies = [e["body"] for e in activated if e["secrecy"] != "secret"]
    secret_world_info_bodies = [e["body"] for e in activated if e["secrecy"] == "secret"]
    recalled_lore_bodies = []   # recall is off in this harness, as by default
    secret_recalled_lore_bodies = []
    group_states, secret_group_states = [], []
    for e in activated:
        if e["kind"] != "groups":
            continue
        st = groupstate.read_state(croot, e["id"])
        if st and any(st[k] for k in groupstate.FIELDS):
            bucket = secret_group_states if e["secrecy"] == "secret" else group_states
            bucket.append({"name": e["name"], **st})

    mid = modules.resolve(cid)
    mechanics_rules, mechanics_sheets, mechanics_checks = [], [], []
    if mid is not None:
        pack = modules.load_pack(mid)
        sheets_def = pack["sheets"] if isinstance(pack["sheets"], dict) else {}
        actors = [(a["kind"], a["id"], a["name"]) for a in cast]
        if current_loc:
            try:
                loc = entities.read_entity(croot, "locations", current_loc)
                actors.append(("locations", current_loc, loc["meta"].get("name", current_loc)))
            except entities.EntityNotFound:
                pass
        present_types = set()
        for kind, eid, label in actors:
            sh = sheets.read(cid, kind, eid)
            if sh is None:
                continue
            type_id = sh["sheet_type"]
            st = sheets_def.get("sheet_types", {}).get(type_id) if isinstance(type_id, str) else None
            type_label = (st.get("label", type_id) if isinstance(st, dict)
                          else (type_id if isinstance(type_id, str) else ""))
            if sh["errors"]:
                mechanics_sheets.append({"ref": f"{kind}:{eid}", "label": label,
                                         "type_label": type_label, "lines": ["(sheet invalid)"]})
                continue
            if isinstance(type_id, str):
                present_types.add(type_id)
            defaults = sheets.default_fields(sheets_def, type_id) if isinstance(type_id, str) else {}
            merged = {**defaults, **sh["fields"]}
            line_entries = []
            for f in (modules.assembled_fields(sheets_def, type_id) if isinstance(type_id, str) else []):
                key = f.get("key")
                if not isinstance(key, str) or not key:
                    continue
                v = merged.get(key)
                if f.get("type") == "resource" and isinstance(v, dict):
                    line_entries.append(f"{key} {v.get('current')}/{v.get('max')}")
                else:
                    line_entries.append(f"{key} {v}")
            for name, value in sh["derived"].items():
                line_entries.append(f"{name} {value}")
            lines = [" · ".join(line_entries[i:i + 4]) for i in range(0, len(line_entries), 4)]
            mechanics_sheets.append({"ref": f"{kind}:{eid}", "label": label,
                                     "type_label": type_label, "lines": lines})

        always_docs, type_docs, key_docs = [], [], []
        for doc in pack["rules"]:
            if doc["always"]:
                always_docs.append(doc)
            elif set(doc["sheet_types"]) & present_types:
                type_docs.append(doc)
            elif doc["keys"] and any(re.search(rf"\b{re.escape(k)}\b", recent_text, re.IGNORECASE)
                                     for k in doc["keys"]):
                key_docs.append(doc)
        for doc in always_docs + type_docs + key_docs[:6]:
            rule = modules.read_rule(mid, doc["id"])
            if rule is not None:
                mechanics_rules.append(rule["body"].strip())
        mechanics_checks = checks.available_checks(cid, scene_id)

    today = None
    time_history = scenes.get_time_history(cid, scene_id)
    if time_history:
        facts = calendars.today_facts(calendars.read_calendar(croot), time_history[-1])
        today = {"friendly": facts["friendly"], "weekday": facts["weekday"],
                 "secondary_friendly": facts["secondary_friendly"],
                 "holidays_today": facts["holidays_today"], "upcoming": facts["upcoming"],
                 "cast": context.cast_datetime_facts(cid, scene_id, time_history[-1])}

    # Mirrors context._weather_data. Derived rather than fixtured: a constant
    # would disagree with the real assembly for every scene that has no
    # location or no moment, which is most of the scenarios below.
    weather_now = None
    location_history = scenes.get_location_history(cid, scene_id)
    if location_history and time_history:
        got = wstore.current_weather(cid, location_history[-1], time_history[-1])
        if got:
            weather_now = {k: got[k] for k in ("condition", "temperature", "wind")}
            weather_now["notes"] = got.get("notes") or []

    present_chars = {a["id"] for a in cast if a["kind"] == "characters"}
    roster = ap.roster(cid)
    roster_ids = {a["id"] for a in roster if a["kind"] == "characters"}
    offscene_active = []
    for a in roster:
        if a["kind"] != "characters" or a["role"] != "npc" or a["id"] in present_chars:
            continue
        body = dstore.read(croot, a["id"])
        if body:
            name = characters.read_character(croot, a["id"])["meta"]["name"]
            offscene_active.append({"name": name, "dossier": body})
    offscene_known = []
    for char_id in characters.character_refs(croot):
        if char_id in roster_ids or char_id in present_chars:
            continue
        tag = tstore.read(croot, char_id)
        if not tag:
            continue
        ch = characters.read_character(croot, char_id)
        offscene_known.append({"id": char_id, "name": ch["meta"]["name"], "tagline": tag,
                               "versions": [v["id"] for v in ch["versions"]]})
    # context.cast._scope_known cuts this tier to `offscene_known_limit` and
    # ranks the survivors by relevance, which is a judgement about a scene and
    # not a data read this mirror can honestly reproduce. The fixture store
    # below stays well under the ceiling so the two agree without it -- asserted
    # rather than assumed, because a fixture that grew past the ceiling would
    # otherwise fail as an unexplained byte mismatch in the section join.
    limit = int(cfg.get("offscene_known_limit", config.DEFAULT_OFFSCENE_KNOWN_LIMIT) or 0)
    assert not limit or len(offscene_known) <= limit, (
        f"verify fixture has {len(offscene_known)} tier-3 characters, over the "
        f"offscene_known_limit of {limit}; this mirror does not implement the cut")

    campaign_meta = campaigns.read_campaign(cid)["meta"]
    # Mirrors context._assemble: one per-field cascade resolves both the prose
    # style and the length budget.
    budget = response_presets.resolve(scene_meta=scene["meta"],
                                      campaign_meta=campaign_meta, config=cfg)
    try:
        resolved_style = styles.read_style(budget["style_id"]) if budget["style_id"] else None
    except styles.StyleNotFound:
        resolved_style = None
    return {"global_system_prompt": cfg.get("system_prompt", ""),
            "budget": {k: budget[k] for k in lengths.KNOBS},
            "prose_style_name": resolved_style["meta"]["name"] if resolved_style else "",
            "prose_style_body": resolved_style["body"].strip() if resolved_style else "",
            "npc_cards": npc_cards,
            "states": states, "transient_states": transient_states,
            # Mirrors context._assemble: derived from the present NPCs' card
            # names and the raw transcript, and None while the toggle is off.
            "speaker": (context.speaker.nominate(
                [d.get("name", "") for d in npc_cards if d.get("name")],
                [dict(m) for m in scene["messages"]])
                if config.speaker_turn_taking() else None),
            "transient_tracker": depth > 0, "transient_fields": list(turnstate.FIELDS),
            "relationship_lines": relationship_lines, "players": players,
            "ref_names": ref_names, "refs": refs, "story_entries": story_entries,
            "archive_entries": archive_entries,
            "plot_lines": plot.render_open(cid, with_id=False),
            "commitment_lines": commitments.render_open(cid, with_id=False), "today": today,
            "weather": weather_now,
            "current_setting": current_setting,
            "current_setting_secret": current_setting_secret,
            "world_info_bodies": world_info_bodies,
            "secret_world_info_bodies": secret_world_info_bodies,
            "recalled_lore_bodies": recalled_lore_bodies,
            "secret_recalled_lore_bodies": secret_recalled_lore_bodies,
            "group_states": group_states,
            "secret_group_states": secret_group_states,
            "offscene_active": offscene_active, "offscene_known": offscene_known,
            "player_names": player_names, "pcless": pcless,
            "story_full": bool(full_recap), "opener": False,
            "mechanics_rules": mechanics_rules, "mechanics_sheets": mechanics_sheets,
            "mechanics_checks": mechanics_checks}


def rendered_system(data: dict, opener: bool = False) -> str:
    """Mirror of context.assemble._render_sections + scene/system.j2: render
    each section, drop the empty ones, join with blank lines.

    The order is spelled out here rather than read off `context.SECTIONS`,
    which is the point — the prompt's section order is now a single list in
    code, and this is the independent copy that makes reordering it a
    deliberate two-sided change instead of a silent one.
    """
    names = []
    if opener:
        names.append("scene/opener_instruction/"
                     + ("offscreen" if data["pcless"] else "standard") + ".j2")
    names += ["scene/sections/global_system_prompt.j2",
              "scene/sections/prose_style.j2",
              "scene/sections/natural_prose.j2",
              "scene/sections/card_system_prompts.j2",
              "scene/sections/character_descriptions.j2",
              "scene/sections/character_state.j2",
              "scene/sections/transient_state.j2",
              "scene/sections/active_speaker.j2",
              "scene/sections/relationships.j2",
              "scene/sections/player_personas.j2"]
    if data["pcless"]:
        names += ["scene/sections/offscreen_scene.j2", "scene/sections/absent_players.j2"]
    names += ["scene/sections/message_examples.j2",
              "scene/sections/story_so_far/" + ("full" if data["story_full"] else "compact") + ".j2",
              "scene/sections/archive.j2",
              "scene/sections/plot_threads.j2",
              "scene/sections/commitments.j2",
              "scene/sections/today.j2",
              "scene/sections/weather.j2",
              "scene/sections/current_setting.j2",
              "scene/sections/world_info.j2",
              "scene/sections/recalled_lore.j2",
              "scene/sections/group_state.j2",
              "scene/sections/mechanics_rules.j2",
              "scene/sections/mechanics_sheets.j2",
              "scene/sections/off_scene_cast_active.j2",
              "scene/sections/off_scene_cast_known.j2",
              "scene/sections/mechanics_response_format.j2",
              "scene/sections/response_format.j2"]
    # The tracker instruction is the one section deliberately absent from an
    # opener (Section.except_opener) — the opener is adopted by hand, so a
    # machine-readable block there is the user's to delete.
    if not opener:
        names.append("scene/sections/transient_tracker.j2")
    names.append("scene/sections/response_budget.j2")
    sections = [s for s in (render(n, **data).strip() for n in names) if s]
    return render("scene/system.j2", sections=sections).strip()


def rendered_messages(scene_id: str, data: dict, note: str | None = None,
                      opener_prompt: str | None = None) -> list[dict]:
    out = []
    system = rendered_system(data, opener=opener_prompt is not None)
    if system:
        out.append({"role": "system", "content": system})
    if opener_prompt is None:
        for m in scenes.read_scene(cid, scene_id)["messages"]:
            line = render("scene/history_line.j2", m=m)
            if out and out[-1]["role"] == m["role"] and out[-1]["role"] != "system":
                out[-1]["content"] += "\n\n" + line
            else:
                out.append({"role": m["role"], "content": line})
    if note is not None:
        out.append({"role": "user", "content": note})
    if opener_prompt is not None:
        out.append({"role": "user", "content": opener_prompt})
    # Mirrors context._assemble exactly — this script's whole point is proving
    # the templates render what the real path renders, so the corrective is
    # measured from the same scene rather than injected from a fixture.
    drift = length_drift.measure(scenes.read_scene(cid, scene_id)["messages"],
                                 scenes.get_turn_sizes(cid, scene_id),
                                 [d.get("name", "") for d in data["npc_cards"] if d.get("name")],
                                 data["budget"])
    correction = (render("scene/length_correction.j2", drift=drift, budget=data["budget"])
                  if drift else "")
    # Same mirroring for the voice corrective: read off the scene's own cast and
    # flag files, not injected, so a change to either half shows up here.
    voice_notes = [{"name": characters.read_character(croot, a["id"])["meta"]["name"],
                    "note": vdstore.read(croot, a["id"])}
                   for a in ap.scene_cast(cid, scene_id)
                   if a["kind"] == "characters" and a["role"] == "npc"
                   and vdstore.read(croot, a["id"]) and vastore.read(croot, a["id"])]
    voice = render("scene/voice_correction.j2", voice_notes=voice_notes) if voice_notes else ""
    post = render("scene/post_history.j2", npc_cards=data["npc_cards"],
                  voice_correction=voice, length_correction=correction)
    if post:
        out.append({"role": "system", "content": post})
    if opener_prompt is not None:  # the shape rules always ride last on openers
        npc_names = [d.get("name", "") for d in data["npc_cards"] if d.get("name")]
        out.append({"role": "system",
                    "content": render("scene/opener_shape.j2", npc_names=npc_names)})
    return out


# --------------------------------------------------- context builder checks

data = gather(sid, pcless=False)
# A byte-for-byte check over an empty section proves nothing, and the archive
# section is empty unless the fixture keeps a keyed record outside the recap
# window — so say so here rather than let the check quietly go vacuous.
assert data["archive_entries"], "fixture no longer exercises the archive section"
check_messages("chat", context.build_messages(cid, sid), rendered_messages(sid, data))
# The fixture's present NPC is both anchored and flagged, so the voice corrective
# really is inside the post-history the comparison above covers. Asserted rather
# than assumed: without an anchor the corrective renders "", and comparing "" to
# "" passes while proving nothing about scene/voice_correction.j2.
assert any("drifted out of voice" in m["content"] for m in context.build_messages(cid, sid)), \
    "the voice corrective is missing from the assembled prompt -- check the fixture (#59)"
note = render("scene/director_note.j2")
check_messages("director", context.build_director_messages(cid, sid, note),
               rendered_messages(sid, data, note=note))

opener_prompt = "A storm rolls in while they reach the warehouse ledger."
odata = {**gather(sid, pcless=False, wi_seed=opener_prompt, full_recap=context.OPENER_RECAP_DEPTH),
         "opener": True}
check_messages("opener", context.build_opener_messages(cid, sid, opener_prompt),
               rendered_messages(sid, odata, opener_prompt=opener_prompt))

sid_off = scenes.create_scene(cid, "Offscreen", pcless=True)
ap.appear(cid, sid_off, "characters", sera, "default", "npc")
ap.appear(cid, sid_off, "characters", kessler, "default", "npc")
scenes.append_message(cid, sid_off, "assistant", "Kessler locks the clinic door.",
                      speaker="Doc Kessler")
off_data = gather(sid_off, pcless=True)
check_messages("offscreen chat", context.build_messages(cid, sid_off),
               rendered_messages(sid_off, off_data))
off_odata = {**gather(sid_off, pcless=True, wi_seed="The pact at the pier.",
                      full_recap=context.OPENER_RECAP_DEPTH), "opener": True}
check_messages("offscreen opener",
               context.build_opener_messages(cid, sid_off, "The pact at the pier."),
               rendered_messages(sid_off, off_odata, opener_prompt="The pact at the pier."))

# ------------------------------------------------ store-level simple prompts

snap = suggest.build_snapshot(cid)
exp = suggest.build_prompt(snap, None)
check("suggestions system (store)", exp[0]["content"],
      render("scene_suggestions/system.j2", s=snap, offscreen=False,
             greeting_candidates=None, direction=""))
check("suggestions user (store)", exp[1]["content"],
      render("scene_suggestions/user.j2", s=snap, offscreen=False,
             greeting_candidates=None, direction=""))

TYPED = "the morning after, back at the marsh house"
iexp = suggest.build_intent_prompt(cid, TYPED)
isnap = suggest.build_snapshot(cid)
check("intent system (store)", iexp[0]["content"],
      render("scene_intent/system.j2", s=isnap, offscreen=False,
             greeting_candidates=None, direction="", typed=TYPED))
check("intent user (store)", iexp[1]["content"],
      render("scene_intent/user.j2", s=isnap, offscreen=False,
             greeting_candidates=None, direction="", typed=TYPED))

ioff_exp = suggest.build_intent_prompt(cid, TYPED, offscreen=True)
ioff_snap = suggest.build_snapshot(cid, offscreen=True)
check("intent user (store, offscreen)", ioff_exp[1]["content"],
      render("scene_intent/user.j2", s=ioff_snap, offscreen=True,
             greeting_candidates=None, direction="", typed=TYPED))

scene_msgs = scenes.read_scene(cid, sid)["messages"]
tr = render("snippets/transcript.j2", messages=scene_msgs)
check("transcript (store)", chronicle.transcript_text(scene_msgs), tr)
facts = chronicle.scene_facts(cid, sid)
st_snap = absorb.state_snapshot(cid, sid)
rel_snap = absorb.relationships_snapshot(cid, sid)
plot_snap = absorb.plot_snapshot(cid)
grp_snap = absorb.group_snapshot(cid)
cmt_snap = absorb.commitment_snapshot(cid)
fct_snap = absorb.fact_snapshot(cid)
exp = absorb.build_prompt(tr, facts, st_snap, rel_snap, plot_snap, grp_snap, cmt_snap, fct_snap)
check("absorb user (store)", exp[1]["content"],
      render("absorb/user.j2", facts=facts, state_snapshot=st_snap, rel_snapshot=rel_snap,
             plot_snapshot=plot_snap, group_snapshot=grp_snap,
             commitment_snapshot=cmt_snap, fact_snapshot=fct_snap, transcript=tr))
for name, line in st_snap.items():
    st = playstate.read_state(croot, sera)
    check(f"state snapshot line (store, {name})", line,
          render("snippets/state_snapshot_line.j2", st=st))

blocks, _excluded = audit.sheet_blocks(cid, sid)
roll_log = audit.roll_lines(cid, sid)
audit_exp = audit.build_prompt(tr, blocks, roll_log)
check("audit system (store)", audit_exp[0]["content"], render("audit/system.j2"))
check("audit user (store)", audit_exp[1]["content"],
      render("audit/user.j2", sheet_blocks=blocks, roll_lines=roll_log, transcript=tr))

threads = plot.open_threads(cid)
check("plot lines (context form)", "\n".join(plot.render_open(cid, with_id=False)),
      "\n".join(render("snippets/plot_thread_line/context.j2", t=t) for t in threads))
check("plot lines (absorb form)", "\n".join(plot.render_open(cid, with_id=True)),
      "\n".join(render("snippets/plot_thread_line/absorb.j2", t=t) for t in threads))

owed = commitments.open_commitments(cid)
check("commitment lines (context form)", "\n".join(commitments.render_open(cid, with_id=False)),
      "\n".join(render("snippets/commitment_line/context.j2", c=c) for c in owed))
check("commitment lines (absorb form)", "\n".join(commitments.render_open(cid, with_id=True)),
      "\n".join(render("snippets/commitment_line/absorb.j2", c=c) for c in owed))

standing = fstore.active(cid)
check("fact lines", "\n".join(fstore.render_active(cid)),
      "\n".join(render("snippets/fact_line.j2", f=f) for f in standing))

# ---------------------------------------------------------------------------

if FAILURES:
    print(f"{len(FAILURES)}/{CHECKS} checks FAILED\n")
    print("\n\n".join(FAILURES))
    sys.exit(1)
print(f"all {CHECKS} checks passed — builders and templates/ agree byte-for-byte")
