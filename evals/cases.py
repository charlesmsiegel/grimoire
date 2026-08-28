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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from grimoire.store import absorb as absorb_store
from grimoire.store import (
    appearances,
    assets,
    campaigns,
    characters,
    checks,
    chronicle,
    commitments,
    config,
    context,
    entities,
    facts,
    groupstate,
    image_descriptions,
    lengths,
    pcs,
    playstate,
    plot,
    relationships,
    response_presets,
    scenes,
    sheets,
    steering,
    worlds,
)
from grimoire.store.context import art

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
    # A described picture of the pier, so the `available_art` section has
    # something to render. Its wording deliberately shares two content words
    # with the player's post below ("crates", "step") -- `context/art` offers a
    # candidate only when two land, and a check that silently graded an EMPTY
    # section would pass for the wrong reason.
    assets.put_image(wroot, pier, "default", "gallery_1", b"art", "png", base="locations")
    image_descriptions.set_description(
        wroot, pier, "default", "gallery_1",
        "Crates stacked on the planks, and a step down to the black water.",
        base="locations")
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
            "cast_names": _npc_names(cid, sid),
            "art_handle": art.handle_for("locations", pier, "gallery_1")}


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
        # The art offer, whole. Replay can only ever prove the INSTRUCTION is
        # in the prompt -- whether a model reaches for a picture when one fits
        # is a question only --live can put -- but that is the half a template
        # edit can break silently, and this is where it stops being silent.
        + graders.grade_prompt_section(
            ctx["messages"], "available_art", "scene/sections/available_art.j2",
            available_art=[{"handle": ctx["art_handle"],
                            "description": "Crates stacked on the planks, "
                                           "and a step down to the black water."}])
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
    facts.record(cid, "Seraphine Vale holds the lease on the Night Dock warehouse.",
                 "since the spring floods", sid)

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
    # A reroll's steering prompt, so the extraction is primed with the one
    # correction the player had to make mid-scene.
    steering.record(cid, sid, "Seraphine already knows Winifred saw the ledger — she was there.")
    return {"cid": cid, "sid": sid}


def grade_absorb(ctx: dict, output: str) -> list[Check]:
    # Every section the contract names must still be ASKED FOR. This is the
    # check that makes absorb's replay mode react to a template edit at all:
    # drop a key from templates/absorb/system.j2 and the model stops returning
    # it, but a recorded reply from before the edit still has it.
    # The per-edit routing fields (#110/#112) are asked for alongside the
    # sections. They live INSIDE each row, so the contract derived from
    # `parse_output("{}")` cannot see them and dropping the ask from the
    # template would otherwise go unnoticed until a live run.
    # The steering contract rides along: the system paragraph that makes the
    # notes signal-never-evidence must still be asked, or a template edit that
    # drops it goes unnoticed until a live run cites the player's own words.
    # The needle is a phrase unique to that paragraph — "Player steering
    # notes" also heads the user-side block, so it would still match with the
    # contract gone.
    prompt = graders.grade_prompt(
        ctx["messages"],
        {f"asks_{k}": f'"{k}"'
         for k in graders.ABSORB_TEXT + graders.ABSORB_LISTS + absorb_store.CITATION_FIELDS}
        | {"asks_steering_contract": "Treat them as pointers, not as story"})

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

# The secrecy levels (#49) ride along on this case: same containment question,
# a different mechanism. Both are keyless and unowned, so they are always-on
# for every scene in the fixture -- the gm-only one is therefore absent from
# the prompt because the LEVEL excluded it, not because it failed to activate.
GM_ONLY = "the warehouse fire is set for the ninth"
GM_ONLY_BODY = f"Referee note: {GM_ONLY}."
HELD = "the harbourmaster took the Guild's coin"
HELD_BODY = f"Unspoken on the docks: {HELD}."


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
    entities.create_entity(wroot, "lore", "Referee note", GM_ONLY_BODY, secrecy="gm-only")
    entities.create_entity(wroot, "lore", "The harbourmaster", HELD_BODY, secrecy="secret")
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
        # gm-only: excluded by the LEVEL. The entry is keyless and unowned, so
        # it would otherwise be always-on — its absence cannot be a fixture that
        # simply never fired.
        Check("secrecy.gm_only", GM_ONLY not in absent,
              "assembled prompt carried a gm-only entry"),
        # Rendering the section itself, so a reworded or deleted heading fails
        # here rather than silently everywhere: with no public bodies the
        # template emits exactly the secret block the real prompt embeds.
    ] + graders.grade_prompt_section(ctx["messages"], "secrecy_block",
                                     "scene/sections/world_info.j2",
                                     world_info_bodies=[],
                                     secret_world_info_bodies=[HELD_BODY]) \
      + graders.grade_containment(output, SECRET)


# -------------------------------------------- case 5: group-scene turn taking

#: The four NPCs in the room, as (description, personality). Distinct first
#: tokens on purpose: `speaker._named` drops a label two present actors answer
#: to, so a shared first name would make the nomination read as unnamed and hide
#: what this case measures.
#:
#: No card says anything about when its character speaks. An earlier draft gave
#: all four "Speaks up when spoken to", which is a fixture arguing with its own
#: hypothesis: this case measures whether the Active speaker section is what
#: decides who talks, and a card that also answers that question makes a green
#: run unattributable.
_CROWD = {
    "Seraphine Vale": ("Tall, sharp-eyed smuggler with salt-cracked hands.",
                       "Wry, wary, slow to trust and slower to explain."),
    "Mara": ("A fortune-teller who deals in secrets and never in change.",
             "Oblique. Answers the question under the question."),
    "Rowan": ("The pier's night watch, bored and armed.",
              "Blunt, literal, and tired of both."),
    "Tobin": ("A ledger clerk who counts crates nobody logged.",
              "Precise, anxious, keeps the receipts."),
}
#: Who the nomination must land on: the NPC whose last block is furthest back.
#: Named so the case's own control check can say the transcript still has the
#: shape this case was written around.
_OVERDUE = "Tobin"

#: The scene's model blocks, oldest first. Tobin speaks once and never again;
#: Seraphine takes the last three — the monologue #82 describes, in the
#: smallest transcript that gives every NPC a strictly different silence.
_OPENING_ROUND = (
    ("Tobin", "The manifest was short two crates when I signed it. I said so."),
    ("Rowan", "He did say so. I was standing right there."),
    ("Mara", "Saying so and doing something are different trades."),
    ("Seraphine Vale", "The manifest is short because I made it short."),
    ("Seraphine Vale", "Two crates went to a man who does not take no for an answer."),
    ("Seraphine Vale", "And before anyone asks: no, I am not naming him."),
)


def build_turn_taking() -> dict:
    """A four-hander mid-monologue, with `speaker_turn_taking` switched on.

    The transcript makes the nomination UNIQUE rather than a tie-break: every
    NPC has spoken, each at a different distance back, and the last three
    blocks all belong to one of them. `speaker.nominate` therefore ranks on
    silence alone — Tobin 5 blocks back, Rowan 4, Mara 3, Seraphine 0 — and
    neither cast order nor the said-least tie-breaker gets a say. A fixture
    that leaned on either would keep scoring green while quietly measuring a
    different question the first time something was reordered.

    The closing player post names nobody. Direct address outranks silence, so a
    name there would make the nomination `"named"` and turn this into a case
    about following an address rather than about rotation.
    """
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    ids: dict[str, str] = {}
    for name, (description, personality) in _CROWD.items():
        card = characters.blank_card(name)
        card["data"].update({"description": description, "personality": personality})
        ids[name], _ = characters.create_character(wroot, name, "default", card)
    pier = entities.create_entity(wroot, "locations", "Saltmarch Pier",
                                  "Fog-slick planks stacked with unlogged crates.",
                                  keys="pier, dock")

    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    croot = campaigns.campaign_root(cid)
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))

    sid = scenes.create_scene(cid, "Four at the Pier")
    for name in _CROWD:
        appearances.appear(cid, sid, "characters", ids[name], "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    scenes.set_location(cid, sid, pier)

    scenes.append_message(cid, sid, "user", "I set the crate down and ask who "
                          "wants to explain the missing manifest.", speaker="Winifred")
    for name, line in _OPENING_ROUND:
        scenes.append_message(cid, sid, "assistant", line, speaker=name)
    scenes.append_message(cid, sid, "user",
                          "I put the lamp on the crate and wait for somebody else "
                          "to fill the silence.", speaker="Winifred")

    # The layer is off by default, so a case that forgot this would assemble a
    # prompt carrying no Active speaker section at all and still pass its output
    # checks by luck.
    config.write_config(speaker_turn_taking="on")

    npc_names = _npc_names(cid, sid)
    nomination = context.speaker.nominate(npc_names,
                                          scenes.read_scene(cid, sid)["messages"])
    return {"cid": cid, "sid": sid, "npc_names": npc_names, "nomination": nomination,
            "players": frozenset(appearances.player_names(cid, sid))}


def grade_turn_taking(ctx: dict, output: str) -> list[Check]:
    # None when the fixture stopped producing a nomination at all, which the
    # control check below reports and the two graders each handle: an empty
    # render fails `prompt.active_speaker`, and grade_turn_taking says there
    # was nothing to score against rather than raising.
    nomination = ctx["nomination"]
    control = Check(
        # Positive control FIRST, as in owned-lore: every check after this one
        # reads the nomination, so a fixture that stopped producing the intended
        # one would score some other question under this case's name.
        "turns.control",
        bool(nomination) and nomination["lead"] == _OVERDUE
        and nomination["reason"] == "rotation",
        f"fixture nominated {nomination!r}, wanted {_OVERDUE!r} by rotation; "
        "the fixture, not the model, is broken")
    # The prompt half, and the only half replay can judge: the section is
    # rendered from the nomination this case computed and required verbatim in
    # the assembled prompt. Switch the flag off, empty the template or break the
    # variable feeding it and this fails offline — the output checks cannot,
    # because a recording does not react to a template edit.
    section = graders.grade_prompt_section(ctx["messages"], "active_speaker",
                                           "scene/sections/active_speaker.j2",
                                           speaker=nomination)
    # The voice policy is pinned HERE rather than as a suite-wide requirement,
    # and the reason is that it renders conditionally: a scene with one bare NPC
    # correctly carries no voice section at all, so a global check would reject
    # exactly the prompts the section is designed not to clutter. This case is
    # the natural host rather than merely a convenient one -- it is the
    # several-NPCs-in-a-scene fixture, which is the condition the differentiation
    # rule exists for.
    #
    # What this buys, stated exactly: `grade_prompt_section` renders the CURRENT
    # template and requires the result in the assembled prompt, so both sides
    # move together and a REWORD cannot fail it. It catches the section ceasing
    # to be DELIVERED -- emptied, switched off, dropped, or its feeding variable
    # broken. Emptying voice_policy.j2 fails four cases here immediately.
    data = context._assemble(ctx["cid"], ctx["sid"])["data"]
    voice = graders.grade_prompt_section(ctx["messages"], "voice_policy",
                                         "scene/sections/voice_policy.j2",
                                         cast_blocks=data["cast_blocks"],
                                         named_npc_count=data["named_npc_count"])
    return [control] + section + voice + graders.grade_turn_taking(
        output, nomination, ctx["players"], ctx["npc_names"])


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
                                     absorb_store.commitment_snapshot(cid),
                                     absorb_store.fact_snapshot(cid),
                                     absorb_store.steering_snapshot(cid, sid))


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
    Case(id="turn-taking",
         hypothesis="in a four-hander with turn-taking on, the reply is carried "
                    "by the nominated speaker rather than by whoever has been "
                    "monologuing, and not every present NPC gets a block",
         build=build_turn_taking, prompt=_scene_prompt, grade=grade_turn_taking,
         recordings=(
             Recording(BASELINE),
             # The failure #82 exists for: the nomination is ignored and the
             # character who took the last three blocks takes a fourth. The
             # lead says nothing, so `turns.lead_carries` short-circuits and
             # only `lead_speaks` is reported.
             Recording("monologue", ("turns.lead_speaks",)),
             # The reply a block count cannot see, and the reason "carries" is
             # measured in words: the nomination is answered with one obliging
             # half-line and the hog takes the floor back in the very next
             # block. One block each — 1-1, and green, on any count of blocks.
             Recording("out-talked", ("turns.lead_carries",)),
             # The mirror image: everyone answers, in sequence. The lead still
             # out-words all of them, so this isolates the "do not give every
             # character a turn" half on its own.
             Recording("chorus", ("turns.some_stay_quiet",)))),
    Case(id="owned-lore",
         hypothesis="lore owned by an absent character stays out of both the "
                    "assembled prompt and the reply; a gm-only entry stays out "
                    "of the prompt whoever is on stage, and a secret one "
                    "arrives under its heading",
         build=build_owned_lore, prompt=_scene_prompt, grade=grade_owned_lore,
         recordings=(
             Recording(BASELINE),
             Recording("leaked", ("containment.output",)))),
)

BY_ID = {c.id: c for c in CASES}
