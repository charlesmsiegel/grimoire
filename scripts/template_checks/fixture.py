"""The throwaway store every scene check reads: one campaign that exercises
every context section, built once under the temporary `GRIMOIRE_HOME`."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from grimoire.store import appearances as ap
from grimoire.store import authors_notes as anstore
from grimoire.store import (
    campaigns,
    characters,
    chronicle,
    commitments,
    config,
    entities,
    groupstate,
    pcs,
    playstate,
    plot,
    relationships,
    scenes,
    sheets,
    worlds,
)
from grimoire.store import dossiers as dstore
from grimoire.store import facts as fstore
from grimoire.store import taglines as tstore
from grimoire.store import voice_anchors as vastore
from grimoire.store import voice_drift as vdstore
from grimoire.store.tracker import records as trecords
from grimoire.store.tracker import walk as twalk

# A campaign author's note (play controls V), so every composition below carries
# one: at depth 1 it sits before the closing player post, in the offscreen
# scene's single assistant line it sits at the start, and an opener places it
# after its prompt. No macros, so the mirror needs no expansion.
AUTHORS_NOTE = {"text": "Keep the storm audible in every scene.", "depth": 1, "every": 1}


@dataclass(frozen=True)
class Fixture:
    """The ids the checks address the fixture store by."""

    wid: str
    cid: str
    croot: Path
    sid: str            # the played scene, renamed when its date was set
    sera: str           # Seraphine: present, anchored and flagged
    kessler: str        # Doc Kessler: met in an earlier scene, dossier only


def _write_module() -> None:
    """A bound mechanics module (#162 Task 6): one sheet type, one check, one
    always-on rules doc -- so mechanics_rules/mechanics_sheets/mechanics_checks
    (and their three system.j2 sections) are all non-empty below."""
    mod_dir = Path(os.environ["GRIMOIRE_HOME"]) / "modules" / "keeper-arts"
    (mod_dir / "rules").mkdir(parents=True)
    (mod_dir / "module.md").write_text("---\nname: Keeper Arts\n---\n", encoding="utf-8")
    (mod_dir / "sheets.json").write_text(json.dumps({
        "groups": {},
        "sheet_types": {
            "keeper": {"label": "Keeper", "kind": "characters", "groups": [],
                       "fields": [{"key": "grit", "label": "Grit", "type": "resource",
                                   "max": 5}]},
        },
    }), encoding="utf-8")
    (mod_dir / "checks.json").write_text(json.dumps({
        "steady-hand": {"label": "Steady Hand", "requires": [], "roll": "1d20"},
    }), encoding="utf-8")
    (mod_dir / "rules" / "core.md").write_text(
        "---\nalways: true\n---\nKeep every roll honest.\n", encoding="utf-8")


def _seraphine(cid: str, croot: Path, sid: str) -> str:
    ncard = characters.blank_card("Seraphine Vale")
    ncard["data"].update({"description": "Tall, sharp-eyed smuggler.",
                          "personality": "Wry and wary.",
                          "scenario": "Runs the night dock.",
                          "system_prompt": "Voice Seraphine with dry wit.",
                          "mes_example": "<START>\n**Seraphine Vale:** Try me.",
                          "post_history_instructions": "Keep replies under four paragraphs."})
    sera, _ = characters.create_character(croot, "Seraphine Vale", "default", ncard)
    ap.appear(cid, sid, "characters", sera, "default", "npc")
    playstate.write_state(croot, sera, playstate.compose_body(
        "Wounded and hiding.", "The ledger is real.\nIt names the harbormaster.",
        "The Guild watches her."))
    sheets.write(cid, "characters", sera, "keeper", {"grit": {"current": 2, "max": 5}},
                 expected=None)
    return sera


def _hero(cid: str, croot: Path, sid: str) -> str:
    persona = pcs.blank_persona("Hero")
    persona.update({"pronouns": "she/her", "summary": "A debt-ridden courier.",
                    "description": "Quick, kind, unlucky.", "birthdate": "2000-07-05",
                    # the profile fields (#65), so pc_block renders its last lines
                    "goals": "Clear the debt.", "player_notes": "Never speak for her."})
    pid, _ = pcs.create_pc(croot, "Hero", [], persona=persona)
    ap.appear(cid, sid, "pcs", pid, "default", "player")
    return pid


def _kessler(cid: str, croot: Path) -> tuple[str, str]:
    """Doc Kessler, in a scene of his own; returns his id and that scene's."""
    kcard = characters.blank_card("Doc Kessler")
    kcard["data"].update({"description": "A weary back-alley medic."})
    kessler, _ = characters.create_character(croot, "Doc Kessler", "default", kcard)
    sid0 = scenes.create_scene(cid, "S0")
    ap.appear(cid, sid0, "characters", kessler, "default", "npc")
    dstore.write(croot, kessler, "Kessler patches up smugglers and quietly owes the Guild.")
    return kessler, sid0


def _world_entities(croot: Path) -> str:
    """The setting's entities; returns the Night Dock's id."""
    dock = entities.create_entity(croot, "locations", "Night Dock",
                                  "Fog-slick piers stacked with contraband.", keys="dock, pier")
    entities.create_entity(croot, "locations", "Bonded Warehouse",
                           "Crates to the ceiling, one door.", keys="warehouse")
    entities.create_entity(croot, "lore", "Salt Pact", "The Pact taxes every crossing.")
    entities.create_entity(croot, "lore", "The Ledger", "The ledger lists a decade of bribes.",
                           keys="ledger")
    return dock


def _secrets(croot: Path, sera: str) -> None:
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


def _transcript(cid: str, sid: str) -> None:
    """The played scene's posts, the last a player's."""
    scenes.append_message(cid, sid, "user", "Where is the ledger?")
    scenes.append_message(cid, sid, "assistant", "Seraphine glances toward the warehouse.",
                          speaker="Seraphine Vale")
    scenes.append_message(cid, sid, "assistant", "Fog rolls in off the water.")
    scenes.append_message(cid, sid, "user", "I follow her.", speaker="Hero", post_id="c" * 32)


def _tracker(cid: str, sid: str, sera: str, pid: str) -> None:
    """One tracker record, on the closing player post, so the Scene state section
    renders here: two characters, both with a value anyone present can see and
    Seraphine with a private one -- which the narrator's render labels, and which
    is the branch of the template a leak would hide in.
    The campaign opts in itself rather than leaning on the shipped default, which
    the test suite switches off."""
    campaigns.set_campaign_tracker(cid, "on")
    tracker_key = twalk.ordered_keys(cid, sid)[-1][1]
    trecords.save(cid, scenes.ensure_identity(cid, sid), tracker_key, {
        f"characters:{sera}": {"present": True, "fields": {
            "pose": {"value": "leaning on a piling", "aware": "present"},
            "intent": {"value": "lose the tail before the warehouse", "aware": []}}},
        f"pcs:{pid}": {"present": True, "fields": {
            "holding": {"value": "a shuttered lantern", "aware": "present"},
            "condition": {"value": ["soaked", "winded"], "aware": "present"}}},
    }, changed=[], fields_digest="fixture", model="fixture")


def _ledger(cid: str, sid: str, sid0: str, sera: str, pid: str, kessler: str) -> None:
    """Relationships, threads, commitments, facts and the chronicle."""
    relationships.set_feeling(cid, f"characters:{sera}", f"pcs:{pid}", 2, 3, 4, "suspects a tail")
    relationships.set_bond(cid, f"characters:{sera}", f"pcs:{pid}", "reluctant allies")
    plot.set_movement(cid, "find-the-ledger", "Find the ledger", "open",
                      "Hero learned it exists.", sid)
    commitments.set_movement(cid, "the-deadline", "Midnight deadline", "threat", "open",
                             "midnight", "Hero was given until midnight.", sid)
    fstore.record(cid, "The warehouse belongs to the Salt Circle.", "the third night", sid)
    chronicle.absorb(cid, {"id": sid0, "one_line": "Hero met Kessler.",
                           "summary": ("Hero met Doc Kessler in his clinic and traded a favor "
                                       "for gossip."),
                           "keywords": ["clinic"], "cast": [f"characters/{kessler}"],
                           "location": "", "date": "2026-07-01"})
    # Older than the recap window and keyed on a word the live scene says out loud,
    # so this one — and only this one — comes back through the archive section.
    # `chronicle.recent` orders by id and scene ids carry an ordinal prefix, so the
    # id has to sort below the real scenes' (`001--…`, `002--…`) to be "older".
    chronicle.absorb(cid, {"id": "000--2026-06-20--harbor-run",
                           "one_line": "The warehouse changed hands.",
                           "summary": ("The bonded warehouse changed hands after a bad night "
                                       "on the pier."),
                           "keywords": ["warehouse"], "cast": [], "location": "",
                           "date": "2026-06-20"})


def build() -> Fixture:
    """Build the fixture store, in the order the checks were written against."""
    # recap_depth=1 narrows the recap window to the newest absorbed scene, which is
    # what leaves an older one outside it for archive retrieval (#127) to recall —
    # the archive section is empty by construction while every record is in recap.
    # speaker_turn_taking is on for exactly that reason too (#29) — it ships off,
    # and the multi-NPC scenes below are what make its section non-empty.
    config.write_config(system_prompt="Global GM rules: be vivid, be fair.", recap_depth="1",
                        speaker_turn_taking="on")
    _write_module()

    # A world profile (#38), so the World overview section renders here.
    wid = worlds.create_world("W", genre="Coastal gothic", tone="Wry dread",
                              themes=["salt", "debt"], description="A drowned coast of toll-roads.")
    cid = campaigns.create_campaign("Run", wid, module="keeper-arts")
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "S1")

    sera = _seraphine(cid, croot, sid)
    pid = _hero(cid, croot, sid)
    kessler, sid0 = _kessler(cid, croot)

    # An anchored, PRESENT NPC carrying an unresolved voice-drift flag, so
    # post_history.j2 renders the voice corrective in every scene comparison below
    # rather than the empty string. BOTH halves are required: context._assemble
    # honours a flag only while the character still has an anchor, so dropping the
    # anchor here would silently reduce the comparison to "" == "".
    vastore.write(croot, sera,
                  "Clipped. Never uses contractions. Answers a question with a question.")
    vdstore.write(croot, sera,
                  "She used contractions and hedged twice; Seraphine never softens a refusal.")

    mcard = characters.blank_card("Mora")
    mora, _ = characters.create_character(croot, "Mora", "default", mcard)
    tstore.write(croot, mora, "A fortune-teller who deals in secrets.")

    dock = _world_entities(croot)
    _secrets(croot, sera)
    scenes.set_location(cid, sid, dock)
    sid = scenes.set_datetime(cid, sid, "2026-07-05")["id"]  # first date set renames the scene

    _transcript(cid, sid)
    anstore.set_campaign(cid, AUTHORS_NOTE)
    _tracker(cid, sid, sera, pid)
    _ledger(cid, sid, sid0, sera, pid, kessler)
    return Fixture(wid=wid, cid=cid, croot=croot, sid=sid, sera=sera, kessler=kessler)
