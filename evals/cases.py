"""The eval cases: fixture campaigns, the real prompt each one assembles, and
the graders applied to the resulting output.

A case is four things:

  build()          populates the CURRENT GRIMOIRE_HOME (the caller points it at
                   a throwaway directory) and returns whatever the rest of the
                   case needs — ids, the resolved budget, the cast.
  prompt(ctx)      the messages list, built by the SAME production builder the
                   app calls (context.build_messages, absorb.build_prompt). A
                   case that hand-rolls its prompt would score a string the app
                   never sends.
  grade(ctx, out)  the checks, mostly delegated to graders.py.
  recordings       the checked-in outputs to score in replay mode, each
                   declaring whether it must pass or exactly which checks it
                   must trip.

Every case carries at least one counterexample. A grader that cannot be made to
fail is not a grader, and a suite of only-passing fixtures degrades into a very
slow way of asserting True.

Every case also grades its assembled PROMPT, not just the output. Replay scores
a fixed recording, so nothing it does to the output can react to a template
edit; the prompt.* checks are what make a deleted instruction fail offline. See
evals/README.md, "What replay can and cannot catch".

All names here are invented placeholders drawn from the codebase's existing
fixture vocabulary (Realm, Saltmarch, Seraphine Vale, Mara, Winifred) — see
CLAUDE.md on why no real campaign content may appear in this repo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from grimoire.store import (appearances, campaigns, characters, checks, chronicle,
                            commitments, config, context, entities, groupstate,
                            lengths, pcs, playstate, plot, relationships,
                            response_presets, scenes, sheets, worlds)
from grimoire.store import absorb as absorb_store

from . import graders
from .graders import Check

RECORDINGS = Path(__file__).resolve().parent / "recordings"

# The variant that `--record` overwrites with real model output. Every other
# variant is a hand-authored counterexample and is never touched by a live run.
BASELINE = "compliant"


@dataclass(frozen=True)
class Recording:
    """A checked-in output and what it must score.

    `expect_fail` names the EXACT set of checks a counterexample must trip;
    empty means the recording must pass cleanly. Naming them, rather than
    settling for "this must fail somehow", is what makes a counterexample
    prove the grader it was written for: `scene-length.bloated` violates four
    knobs at once, so a bare fail-expectation stays green even if the word
    counter stops working entirely, hidden behind its three neighbours.
    """
    variant: str
    expect_fail: tuple[str, ...] = ()
    ext: str = "md"

    @property
    def expect_pass(self) -> bool:
        return not self.expect_fail

    def path(self, case_id: str) -> Path:
        return RECORDINGS / f"{case_id}.{self.variant}.{self.ext}"


@dataclass(frozen=True)
class Case:
    id: str
    hypothesis: str                              # the model behaviour being tested
    build: Callable[[], dict]
    prompt: Callable[[dict], list[dict]]
    grade: Callable[[dict, str], list[Check]]
    recordings: tuple[Recording, ...]

    @property
    def baseline(self) -> Recording:
        return next(r for r in self.recordings if r.variant == BASELINE)


# --------------------------------------------------------------- shared pieces

_SERA_CARD = {
    "description": "Tall, sharp-eyed smuggler with salt-cracked hands.",
    "personality": "Wry, wary, slow to trust and slower to explain.",
    "scenario": "Works the night dock at Saltmarch, moving cargo nobody logs.",
    "mes_example": "<START>\n**Seraphine Vale:** Try me.",
}


def _world_with_sera() -> tuple[str, Path, str]:
    """A world holding Seraphine Vale and the Saltmarch pier. Returned as
    (world id, world root, character id)."""
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    card = characters.blank_card("Seraphine Vale")
    card["data"].update(_SERA_CARD)
    sera, _ = characters.create_character(wroot, "Seraphine Vale", "default", card)
    return wid, wroot, sera


def _budget(cid: str, sid: str) -> dict:
    """The resolved length budget for a scene, via the same cascade
    context._assemble runs."""
    resolved = response_presets.resolve(
        scene_meta=scenes.read_scene_meta(cid, sid),
        campaign_meta=campaigns.read_campaign(cid)["meta"],
        config=config.read_config())
    return {k: resolved[k] for k in lengths.KNOBS}


def _npc_names(cid: str, sid: str) -> list[str]:
    return [a["name"] for a in appearances.scene_cast(cid, sid) if a["role"] == "npc"]


# ------------------------------------------------- case 1: scene length budget

def build_scene_length() -> dict:
    """A two-hander at the pier under the `terse` preset — the tightest budget
    in lengths.PRESETS, so a violation is unambiguous rather than marginal."""
    wid, wroot, sera = _world_with_sera()
    pier = entities.create_entity(wroot, "locations", "Saltmarch Pier",
                                  "Fog-slick planks stacked with unlogged crates.",
                                  keys="pier, dock")
    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    # Campaign scope, not global: this also proves the resolution cascade
    # actually reaches the prompt, which is half of what the knobs are for.
    campaigns.set_campaign_response(cid, {"response_preset": "terse"})
    croot = campaigns.campaign_root(cid)

    persona = pcs.blank_persona("Winifred")
    persona.update({"pronouns": "she/her", "summary": "A courier working off a debt.",
                    "description": "Quick, kind, unlucky."})
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=persona)

    sid = scenes.create_scene(cid, "The Pier at Dusk")
    appearances.appear(cid, sid, "characters", sera, "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    scenes.set_location(cid, sid, pier)
    scenes.append_message(cid, sid, "user",
                          "I step out of the fog and ask her whose crates those are.",
                          speaker="Winifred")

    return {"cid": cid, "sid": sid, "budget": _budget(cid, sid),
            "players": frozenset(appearances.player_names(cid, sid)),
            "cast_names": _npc_names(cid, sid)}


def grade_scene_length(ctx: dict, output: str) -> list[Check]:
    budget = ctx["budget"]
    # Whole sections, rendered from the templates themselves: this covers all
    # FIVE resolved knobs reaching the model, where naming the word count alone
    # would leave the structural limits unguarded. Delete either section from
    # system.j2, or break a variable feeding it, and replay fails offline.
    return (
        graders.grade_prompt_section(ctx["messages"], "budget",
                                     "scene/sections/response_budget.j2",
                                     budget=budget)
        # The marker convention the whole length measurement rests on: without
        # it the model writes undifferentiated prose, split_reply sees one
        # block, and every structural knob silently reads as satisfied.
        + graders.grade_prompt_section(ctx["messages"], "reply_format",
                                       "scene/sections/response_format.j2",
                                       player_names=sorted(ctx["players"]))
        + graders.grade_length(output, budget, ctx["players"], ctx["cast_names"]))


# ---------------------------------------------------- case 2: roll-fence shape

_PACK_ID = "keeper-arts"

_PACK_SHEETS = {
    # The checks below gate on the "physical" group, so it has to be a real
    # field group the sheet type joins — a sheet type listing a group the pack
    # doesn't define is a pack error, and an errored pack resolves to "no
    # mechanics", which would quietly empty the available-checks section this
    # case is built to exercise.
    "groups": {
        "physical": {"fields": [{"key": "grit", "label": "Grit",
                                 "type": "resource", "max": 5}]},
    },
    "sheet_types": {
        "keeper": {"label": "Keeper", "kind": "characters", "groups": ["physical"],
                   "fields": [{"key": "nerve", "label": "Nerve", "type": "number",
                               "default": 2, "min": 0, "max": 5}]},
    },
}

_PACK_CHECKS = {
    "steady-hand": {"label": "Steady Hand", "requires": ["physical"], "roll": "1d20"},
    "read-the-room": {"label": "Read the Room", "requires": ["physical"], "roll": "1d20"},
}


def _write_pack(home: Path) -> None:
    root = home / "modules" / _PACK_ID
    (root / "rules").mkdir(parents=True)
    (root / "module.md").write_text("---\nname: Keeper Arts\n---\n", encoding="utf-8")
    (root / "sheets.json").write_text(json.dumps(_PACK_SHEETS), encoding="utf-8")
    (root / "checks.json").write_text(json.dumps(_PACK_CHECKS), encoding="utf-8")
    (root / "rules" / "core.md").write_text(
        "---\nalways: true\n---\nCall for a check whenever an outcome is genuinely "
        "in doubt. Never narrate the result of a check you have not rolled.\n",
        encoding="utf-8")


def build_roll_fence() -> dict:
    """A sheeted NPC in a mechanics-bound campaign, facing a locked door — the
    canonical 'the model must stop and ask for a roll' setup."""
    from grimoire.store.paths import home

    _write_pack(home())
    wid, wroot, sera = _world_with_sera()
    vault = entities.create_entity(wroot, "locations", "Bonded Warehouse",
                                   "Crates to the ceiling and one iron door.",
                                   keys="warehouse, vault")
    cid = campaigns.create_campaign("Saltmarch Nights", wid, module=_PACK_ID)
    croot = campaigns.campaign_root(cid)

    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))
    sid = scenes.create_scene(cid, "The Iron Door")
    appearances.appear(cid, sid, "characters", sera, "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    scenes.set_location(cid, sid, vault)
    sheets.write(cid, "characters", sera, "keeper",
                 {"grit": {"current": 3, "max": 5}, "nerve": 2}, expected=None)
    scenes.append_message(cid, sid, "user",
                          "I hand her the picks. Can she get us through that door "
                          "before the patrol comes back?",
                          speaker="Winifred")

    available = checks.available_checks(cid, sid)
    return {"cid": cid, "sid": sid, "available_checks": available,
            "allowed_checks": {c for entry in available for c, _label in entry["checks"]},
            "allowed_actors": {entry["ref"] for entry in available}}


def grade_roll_fence(ctx: dict, output: str) -> list[Check]:
    # The rendered section carries both halves of the protocol: the fence shape
    # the model must emit, and the id/actor roster it may draw on. "Use only
    # the ids listed below" becomes an impossible instruction the moment the
    # roster stops being listed, and that is a prompt-side regression no
    # recorded reply can reveal.
    return (
        graders.grade_prompt_section(ctx["messages"], "roll_protocol",
                                     "scene/sections/mechanics_response_format.j2",
                                     mechanics_checks=ctx["available_checks"])
        + graders.grade_roll_fence(output, ctx["allowed_checks"], ctx["allowed_actors"]))


# ---------------------------------------------------------- case 3: absorb I/O

def build_absorb() -> dict:
    """A played scene with state, a relationship and an open thread — so the
    absorb prompt carries every snapshot and the parsed result has somewhere
    to land."""
    wid, wroot, sera = _world_with_sera()
    mcard = characters.blank_card("Mara")
    mcard["data"].update({"description": "A fortune-teller who deals in secrets."})
    mara, _ = characters.create_character(wroot, "Mara", "default", mcard)
    entities.create_entity(wroot, "lore", "The Ledger",
                           "The ledger lists a decade of harbour bribes.", keys="ledger")
    circle = entities.create_entity(wroot, "groups", "Salt Circle",
                                    "A quiet cabal moving contraband.")

    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    croot = campaigns.campaign_root(cid)
    groupstate.write_state(croot, circle, "## Goals\nReach the ledger before the Guild does.")
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))

    sid = scenes.create_scene(cid, "The Pier at Dusk")
    appearances.appear(cid, sid, "characters", sera, "default", "npc")
    appearances.appear(cid, sid, "characters", mara, "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    sid = scenes.set_datetime(cid, sid, "2026-07-05")["id"]
    playstate.write_state(croot, sera, playstate.compose_body(
        "Wary and short of sleep.", "The ledger is real.", "The Guild is watching the pier."))
    relationships.set_feeling(cid, f"characters:{sera}", f"pcs:{pid}", 2, 3, 1, "owes her a favour")
    plot.set_movement(cid, "find-the-ledger", "Find the ledger", "open",
                      "Winifred learned it exists.", sid)
    commitments.set_movement(cid, "the-midnight-deadline", "Seraphine's midnight deadline",
                             "threat", "open", "midnight",
                             "Seraphine gave Winifred until midnight and no further.", sid)

    scenes.append_message(cid, sid, "user", "Whose crates are those?", speaker="Winifred")
    scenes.append_reply(cid, sid, [
        {"speaker": "Seraphine Vale", "content": "Mine, until midnight. After that, nobody's."},
        {"speaker": None, "content": "Fog rolls in off the water and swallows the far pilings."},
    ])
    scenes.append_message(cid, sid, "user", "I tell her I know about the ledger.",
                          speaker="Winifred")
    scenes.append_reply(cid, sid, [
        {"speaker": "Seraphine Vale", "content": "Then you know why I don't sleep."},
    ])
    return {"cid": cid, "sid": sid}


def grade_absorb(ctx: dict, output: str) -> list[Check]:
    # Every section the contract names must still be ASKED FOR. This is the
    # check that makes absorb's replay mode react to a template edit at all:
    # drop a key from templates/absorb/system.j2 and the model stops returning
    # it, but a recorded reply from before the edit still has it.
    prompt = graders.grade_prompt(
        ctx["messages"],
        {f"asks_{k}": f'"{k}"' for k in graders.ABSORB_TEXT + graders.ABSORB_LISTS})

    out, parsed = graders.grade_absorb(output)
    if not all(c.ok for c in out):
        return prompt + out

    # Two separate claims, deliberately not merged. materialize not RAISING is
    # a format property; producing an applicable edit is a content one, and
    # only holds because this fixture is built to move something (a changed
    # character state, an open thread, a live relationship). A green
    # absorb.materialize with a red absorb.applicable says "the schema is fine,
    # the model had nothing to say about a scene that plainly did".
    try:
        edits = absorb_store.materialize(ctx["cid"], ctx["sid"], parsed)
    except Exception as exc:                                    # noqa: BLE001
        return prompt + out + [
            Check("absorb.materialize", False, f"{type(exc).__name__}: {exc}")]
    return prompt + out + [
        Check("absorb.materialize", True),
        Check("absorb.applicable", bool(edits),
              "parsed cleanly but produced no edits this fixture can apply")]


# ----------------------------------------------- case 4: owned-lore containment

# The distinctive phrase that must never reach a scene Seraphine is absent
# from. Long enough that a model cannot coin it by accident.
SECRET = "struck from the Guild rolls"
SECRET_BODY = f"Seraphine was quietly {SECRET} after the Saltmarch run went bad."


def build_owned_lore() -> dict:
    """Two scenes in one campaign: Seraphine on stage in one, absent from the
    other. The owned lore entry is keyless, so with its owner present it is
    always-on — which makes the absent scene a real containment test rather
    than a keyword that simply never fired."""
    wid, wroot, sera = _world_with_sera()
    mcard = characters.blank_card("Mara")
    mcard["data"].update({"description": "A fortune-teller who deals in secrets."})
    mara, _ = characters.create_character(wroot, "Mara", "default", mcard)
    entities.create_entity(wroot, "lore", "Seraphine's exile", SECRET_BODY,
                           owners=f"characters:{sera}")
    pier = entities.create_entity(wroot, "locations", "Saltmarch Pier",
                                  "Fog-slick planks stacked with unlogged crates.",
                                  keys="pier, dock")

    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    croot = campaigns.campaign_root(cid)
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))

    with_owner = scenes.create_scene(cid, "Seraphine at the Pier")
    appearances.appear(cid, with_owner, "characters", sera, "default", "npc")
    appearances.appear(cid, with_owner, "pcs", pid, "default", "player")
    scenes.set_location(cid, with_owner, pier)
    scenes.append_message(cid, with_owner, "user", "What did the Guild do to you?",
                          speaker="Winifred")

    without = scenes.create_scene(cid, "Mara Reads the Cards")
    appearances.appear(cid, without, "characters", mara, "default", "npc")
    appearances.appear(cid, without, "pcs", pid, "default", "player")
    scenes.set_location(cid, without, pier)
    scenes.append_message(cid, without, "user", "Ask her what she knows about Seraphine.",
                          speaker="Winifred")

    return {"cid": cid, "sid": without, "with_owner": with_owner}


def _prompt_text(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages)


def grade_owned_lore(ctx: dict, output: str) -> list[Check]:
    absent = _prompt_text(ctx["messages"])
    present = _prompt_text(context.build_messages(ctx["cid"], ctx["with_owner"]))
    return [
        # Positive control FIRST: if this fails, the containment check below is
        # passing for the wrong reason and the whole case is meaningless.
        Check("containment.control", SECRET in present,
              "owned lore never activated even with its owner on stage; "
              "the fixture, not the model, is broken"),
        Check("containment.prompt", SECRET not in absent,
              "assembled prompt leaked owned lore into a scene with no owner"),
    ] + graders.grade_containment(output, SECRET)


# ------------------------------------------------------------------- the suite

def _scene_prompt(ctx: dict) -> list[dict]:
    return context.build_messages(ctx["cid"], ctx["sid"])


def _absorb_prompt(ctx: dict) -> list[dict]:
    cid, sid = ctx["cid"], ctx["sid"]
    transcript = chronicle.transcript_text(scenes.read_scene(cid, sid)["messages"])
    return absorb_store.build_prompt(transcript, chronicle.scene_facts(cid, sid),
                                     absorb_store.state_snapshot(cid, sid),
                                     absorb_store.relationships_snapshot(cid, sid),
                                     absorb_store.plot_snapshot(cid),
                                     absorb_store.group_snapshot(cid),
                                     absorb_store.commitment_snapshot(cid))


CASES: tuple[Case, ...] = (
    Case(id="scene-length",
         hypothesis="a reply respects the resolved length budget (terse: "
                    "150 words, 3 blocks, 1 paragraph, 2 speakers, 1 block each)",
         build=build_scene_length, prompt=_scene_prompt, grade=grade_scene_length,
         recordings=(
             Recording(BASELINE),
             # Long, five blocks, a doubled speaker and a two-paragraph block:
             # the realistic shape of an unbudgeted reply, so it trips four
             # knobs at once and all four are named.
             Recording("bloated", ("length.reply_words", "length.blocks",
                                   "length.paragraphs", "length.blocks_per_speaker")),
             Recording("collapsed", ("length.reply_words",)))),
    Case(id="roll-fence",
         hypothesis="a roll-requiring prompt emits a closed, parseable ```roll "
                    "fence naming a check and actor the bound module defines",
         build=build_roll_fence, prompt=_scene_prompt, grade=grade_roll_fence,
         recordings=(
             Recording(BASELINE),
             # No fence at all short-circuits: with nothing to inspect, the
             # remaining fence checks are not reported rather than failed.
             Recording("no-fence", ("fence.present",)),
             Recording("unknown-check", ("fence.check_known",)),
             Recording("unclosed", ("fence.closed",)))),
    Case(id="absorb",
         hypothesis="absorb returns JSON with every section the contract names, "
                    "and it materializes into applicable edits",
         build=build_absorb, prompt=_absorb_prompt, grade=grade_absorb,
         recordings=(
             Recording(BASELINE, ext="json"),
             Recording("truncated", ("absorb.json",), "json"),
             Recording("no-summary", ("absorb.summary",), "json"),
             # Valid JSON that parse_output would launder into a clean-looking
             # result: a null summary, a string where a list belongs, and three
             # sections simply absent. Scored on the raw object, every one of
             # those is visible.
             Recording("laundered", ("absorb.summary", "absorb.keywords",
                                     "absorb.bond_changes", "absorb.new_lore",
                                     "absorb.weather_edits"), "json"))),
    Case(id="owned-lore",
         hypothesis="lore owned by an absent character stays out of both the "
                    "assembled prompt and the reply",
         build=build_owned_lore, prompt=_scene_prompt, grade=grade_owned_lore,
         recordings=(
             Recording(BASELINE),
             Recording("leaked", ("containment.output",)))),
)

BY_ID = {c.id: c for c in CASES}
