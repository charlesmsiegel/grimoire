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

import jinja2

from grimoire import decisions, inference, prompts
from grimoire.store import absorb as absorb_store
from grimoire.store import (
    appearances,
    authors_notes,
    calendars,
    campaigns,
    characters,
    checks,
    chronicle,
    clock,
    commitments,
    config,
    context,
    entities,
    events,
    facts,
    groupstate,
    overlay,
    pcs,
    playstate,
    plot,
    relationships,
    response_protocol,
    response_targets,
    scene_break,
    scenes,
    sheets,
    state_fence,
    steering,
    styles,
    suggest,
    voice_anchors,
    voice_drift,
    worlds,
)
from grimoire.store.absorb import parse as absorb_parse
from grimoire.store.continuity import canon, identity, pending, pressure, reconcile
from grimoire.store.continuity import drivers as continuity_drivers
from grimoire.store.regex import view as regex_view
from grimoire.store.tracker import records as tracker_records
from grimoire.store.tracker import walk as tracker_walk

from . import graders, slop
from .graders import Check

RECORDINGS = Path(__file__).resolve().parent / "recordings"

#: Renders a decide case's schema as `decide/system.j2` does (`tojson`), so the
#: prompt check compares the text the template would write.
_SCHEMA_ENV = jinja2.Environment(undefined=jinja2.StrictUndefined)

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
    #: The task the app meters this generation under (`store/routing.py`): a
    #: live run resolves it through the app's seam, so the case is graded on
    #: the model the app would send that prompt to.
    task: str = "chat"
    #: Set on a decide case (`decide-*`): the JSON Schema `decide` would send
    #: for this fixture (`decisions.schema`). A live run then resolves `task`
    #: as a decide operation and asks for the reply with `schema=`, so it
    #: measures what production sends (I9). Replay never calls it.
    schema: Callable[[dict], dict] | None = None

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
    """The assigned actor's continuation target through the production cascade."""
    resolved = response_targets.resolve(
        scene_meta=scenes.read_scene_meta(cid, sid),
        campaign_meta=campaigns.read_campaign(cid)["meta"],
        config=config.read_config())
    return resolved["continuation"]


def _npc_names(cid: str, sid: str) -> list[str]:
    return [a["name"] for a in appearances.scene_cast(cid, sid) if a["role"] == "npc"]


# ------------------------------------------------- case 1: scene length budget

def build_scene_length() -> dict:
    """One named NPC at the pier under a short continuation target."""
    wid, wroot, sera = _world_with_sera()
    pier = entities.create_entity(wroot, "locations", "Saltmarch Pier",
                                  "Fog-slick planks stacked with unlogged crates.",
                                  keys="pier, dock")
    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    # Campaign scope, not global: this also proves the resolution cascade
    # actually reaches the prompt, which is half of what the knobs are for.
    campaigns.set_campaign_response(cid, {"response_continuation_words": "40",
                                          "response_continuation_paragraphs": "1"})
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

    return {"cid": cid, "sid": sid, "actor_ref": f"characters:{sera}",
            "budget": _budget(cid, sid),
            "players": frozenset(appearances.player_names(cid, sid)),
            "cast_names": _npc_names(cid, sid)}


def grade_scene_length(ctx: dict, output: str) -> list[Check]:
    budget = ctx["budget"]
    watcher = response_protocol.ResponseWatcher(perception=True)
    watcher.feed(output)
    watcher.finish()
    narration, _ = state_fence.split_block(watcher.narration)
    prose = graders.length_drift.prose(narration)
    words = len(prose.split())
    paragraphs = max(len([p for p in prose.split("\n\n") if p.strip()]), 1)
    return [
        *graders.grade_prompt_section(ctx["messages"], "budget",
                                      "scene/sections/response_budget.j2",
                                      budget=budget,
                                      response_actor={"name": "Seraphine Vale"}),
        Check("length.words", 0 < words <= budget["words"],
              f"{words} words vs ceiling {budget['words']}"),
        Check("length.paragraphs", paragraphs <= budget["paragraphs"],
              f"{paragraphs} paragraphs vs ceiling {budget['paragraphs']}"),
    ]


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
    # The identity fields (`why_new`, `distinguished_from`) live inside plot
    # and commitment rows, so they are asked for like the citation fields. Their
    # quoted names alone would survive deleting the move / close / new
    # instruction around them, so that paragraph gets its own unique needle.
    prompt = graders.grade_prompt(
        ctx["messages"],
        {f"asks_{k}": f'"{k}"'
         for k in graders.ABSORB_TEXT + graders.ABSORB_LISTS + absorb_store.CITATION_FIELDS
         + absorb_store.IDENTITY_FIELDS}
        | {"asks_steering_contract": "Treat them as pointers, not as story",
           "asks_identity_contract": "Open a new one only when no listed record already "
                                     "stands for the same narrative question or obligation",
           # Inside each new_lore row, so the derived contract cannot see it:
           # without the ask every suggestion defaults to plain lore.
           "asks_new_lore_kind": '"kind" is one of '
                                 + ", ".join(f'"{k}"' for k in absorb_parse.NEW_LORE_KINDS)})

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

# The `known_by` variant (spec §8.1): owned by Seraphine, so it activates only
# with her on stage, and known by Mara, so of the two only Mara's own call may
# carry it. The narrator knows the world and gets it too.
KNOWN = "Seraphine still carries the forged seal"
KNOWN_BODY = f"Mara has seen it: {KNOWN}."


def build_owned_lore() -> dict:
    """Three scenes in one campaign: Seraphine on stage in one, absent from the
    second, and beside Mara in the third (the `known_by` variant). The owned
    lore entry is keyless, so with its owner present it is always-on — which
    makes the absent scene a real containment test rather than a keyword that
    simply never fired."""
    wid, wroot, sera = _world_with_sera()
    mcard = characters.blank_card("Mara")
    mcard["data"].update({"description": "A fortune-teller who deals in secrets."})
    mara, _ = characters.create_character(wroot, "Mara", "default", mcard)
    entities.create_entity(wroot, "lore", "Seraphine's exile", SECRET_BODY,
                           owners=f"characters:{sera}")
    entities.create_entity(wroot, "lore", "Referee note", GM_ONLY_BODY, secrecy="gm-only")
    entities.create_entity(wroot, "lore", "The harbourmaster", HELD_BODY, secrecy="secret")
    entities.create_entity(wroot, "lore", "The forged seal", KNOWN_BODY,
                           owners=f"characters:{sera}", secrecy="secret",
                           fields={"known_by": f"characters:{mara}"})
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

    # Both on stage, for the `known_by` variant: per-character calls are
    # composed for each of them in the grader.
    together = scenes.create_scene(cid, "Seraphine and Mara")
    appearances.appear(cid, together, "characters", sera, "default", "npc")
    appearances.appear(cid, together, "characters", mara, "default", "npc")
    appearances.appear(cid, together, "pcs", pid, "default", "player")
    scenes.set_location(cid, together, pier)
    scenes.append_message(cid, together, "user", "Which of you lies better?",
                          speaker="Winifred")

    return {"cid": cid, "sid": without, "with_owner": with_owner, "together": together,
            "sera": f"characters:{sera}", "mara": f"characters:{mara}"}


def _prompt_text(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages)


def _actor_prompt(ctx: dict, sid: str, actor_ref: str) -> str:
    return _prompt_text(context.compose_turn(ctx["cid"], sid, describe=False,
                                             actor_ref=actor_ref)[0])


def grade_owned_lore(ctx: dict, output: str) -> list[Check]:
    absent = _prompt_text(ctx["messages"])
    present = _prompt_text(context.build_messages(ctx["cid"], ctx["with_owner"]))
    together = ctx["together"]
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
        # `known_by`: the narrator half is the control -- the entry activated --
        # so the owner's own call missing it is the rule, not a miss.
        Check("known_by.narrator",
              KNOWN in _prompt_text(context.build_messages(ctx["cid"], together)),
              "known_by lore never reached the narrator with its owner on stage"),
        Check("known_by.knower", KNOWN in _actor_prompt(ctx, together, ctx["mara"]),
              "known_by lore missed the call of the actor it names"),
        Check("known_by.owner", KNOWN not in _actor_prompt(ctx, together, ctx["sera"]),
              "known_by lore reached its owner's call, who is not in known_by"),
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
                          "to fill the silence.", speaker="Winifred", post_id="d" * 32)

    # One tracker record on the closing post, so the Scene state section is in
    # this prompt: a narrator's compose, so every value renders and the private
    # one carries its label. The campaign opts in itself rather than leaning on
    # the shipped default, which the test suite switches off.
    campaigns.set_campaign_tracker(cid, "on")
    sera, pc = f"characters:{ids['Seraphine Vale']}", f"pcs:{pid}"
    tracker_records.save(cid, scenes.ensure_identity(cid, sid),
                         tracker_walk.ordered_keys(cid, sid)[-1][1], {
        sera: {"present": True, "fields": {
            "pose": {"value": "arms folded on a crate", "aware": "present"},
            "intent": {"value": "keep the buyer's name out of it", "aware": []}}},
        pc: {"present": True, "fields": {
            "holding": {"value": "a lamp", "aware": "present"}}},
    }, changed=[], fields_digest="eval", model="eval")
    # Spelled out rather than read back through `view.lines_for`, so the check
    # below is a second opinion on the filter and not a copy of it.
    tracker_lines = [
        {"name": "Seraphine Vale", "own": False, "values": [
            {"label": "Pose", "text": "arms folded on a crate", "private": False},
            {"label": "Intent", "text": "keep the buyer's name out of it", "private": True}]},
        {"name": "Winifred", "own": False, "values": [
            {"label": "Holding", "text": "a lamp", "private": False}]},
    ]

    # The layer is off by default, so a case that forgot this would assemble a
    # prompt carrying no Active speaker section at all and still pass its output
    # checks by luck.
    config.write_config(speaker_turn_taking="on")

    # Author's notes (play controls V): a campaign note every call carries, and
    # a character note only Seraphine's own call may -- hosted here because a
    # several-NPC scene is where a character's note can leak into somebody
    # else's call. No macros in either, so the render below is the sent text.
    authors_notes.set_campaign(cid, {"text": _CAMPAIGN_NOTE, "depth": 4, "every": 1})
    authors_notes.set_character(cid, sera, {"text": _CHARACTER_NOTE, "depth": 0, "every": 1})

    npc_names = _npc_names(cid, sid)
    nomination = context.speaker.nominate(npc_names,
                                          scenes.read_scene(cid, sid)["messages"])
    return {"cid": cid, "sid": sid, "npc_names": npc_names, "nomination": nomination,
            "players": frozenset(appearances.player_names(cid, sid)),
            "tracker_lines": tracker_lines,
            "sera_ref": sera, "other_ref": f"characters:{ids['Tobin']}"}


_CAMPAIGN_NOTE = "Keep the storm audible in every scene."
_CHARACTER_NOTE = "Seraphine never names the buyer."


def _authors_note_checks(ctx: dict) -> list[Check]:
    """The campaign note reaches the narrator's prompt verbatim, and the
    character note reaches Seraphine's own call and neither Tobin's nor the
    narrator's. Rendered from the template, so a reword moves both sides."""
    # `grade_prompt_section`'s own rule, spelled out: the template's `name`
    # variable collides with that function's `name` parameter.
    campaign = prompts.render("scene/authors_note.j2", level="campaign", name="",
                              text=_CAMPAIGN_NOTE).strip()
    delivered = [Check("prompt.authors_note",
                       bool(campaign) and campaign in graders.prompt_text(ctx["messages"]),
                       "scene/authors_note.j2 rendered nothing" if not campaign else
                       "the rendered campaign author's note is not in the assembled prompt")]
    own = prompts.render("scene/authors_note.j2", level="character", name="Seraphine Vale",
                         text=_CHARACTER_NOTE).strip()

    def call(ref: str) -> str:
        return graders.prompt_text(context.compose_turn(ctx["cid"], ctx["sid"], describe=False,
                                                        actor_ref=ref)[0])

    in_own = bool(own) and own in call(ctx["sera_ref"])
    in_other = _CHARACTER_NOTE in call(ctx["other_ref"])
    in_narrator = _CHARACTER_NOTE in graders.prompt_text(ctx["messages"])
    return [*delivered, Check(
        "prompt.authors_note_scoped", in_own and not in_other and not in_narrator,
        f"character note in its own call: {in_own}, in another character's: {in_other}, "
        f"in the narrator's: {in_narrator}")]


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
    # The scene state, hosted here for the same reason as the voice policy: a
    # several-character scene is what the section exists for. This prompt is
    # the narrator's, so every value is in it and the private one is labelled.
    tracker = graders.grade_prompt_section(ctx["messages"], "tracker_state",
                                           "scene/sections/tracker_state.j2",
                                           tracker_lines=ctx["tracker_lines"],
                                           tracker_narrator=True)
    notes = _authors_note_checks(ctx)
    return [control] + section + voice + tracker + notes + graders.grade_turn_taking(
        output, nomination, ctx["players"], ctx["npc_names"])


# ------------------------------------------------- case 6: natural prose

def build_natural_prose() -> dict:
    """A three-hander at the pier under `cinematic`, the widest preset.

    Three fixture properties are load-bearing:

    - `cinematic` rather than `terse`: rhythm statistics over three blocks are
      noise. Its 900 words are a target and its 7 blocks a maximum, not a
      promised shape, which is why slop.measurable exists rather than the
      preset's numbers being trusted as a sample size.
    - The optional Natural Prose (Legacy) guide and no voice anchors. This
      case exercises that specific phrase/rhythm policy, not every campaign's
      defaults. A conflicting authored voice would exempt expression that this
      fixture deliberately grades.
    - Every name invented, none colliding with slop.STOCK_NAMES, and all of
      them passed to the grader in `established` -- the template's rule is that
      names already in the scene, cast or world are reproduced exactly.
    """
    config.write_config(default_style_id="natural-prose-legacy")
    wid, wroot, sera = _world_with_sera()
    pier = entities.create_entity(wroot, "locations", "Saltmarch Pier",
                                  "Fog-slick planks stacked with unlogged crates.",
                                  keys="pier, dock")
    rowan_card = characters.blank_card("Rowan")
    rowan_card["data"].update({
        "description": "A dock hand with a bad shoulder and a good memory.",
        "personality": "Slow to speak, slower to forget a slight."})
    rowan, _ = characters.create_character(wroot, "Rowan", "default", rowan_card)

    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    campaigns.set_campaign_response(cid, {"response_preset": "cinematic"})
    croot = campaigns.campaign_root(cid)

    persona = pcs.blank_persona("Winifred")
    persona.update({"pronouns": "she/her", "summary": "A courier working off a debt.",
                    "description": "Quick, kind, unlucky."})
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=persona)

    sid = scenes.create_scene(cid, "The Pier at Dusk")
    appearances.appear(cid, sid, "characters", sera, "default", "npc")
    appearances.appear(cid, sid, "characters", rowan, "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    scenes.set_location(cid, sid, pier)
    scenes.append_message(cid, sid, "user",
                          "I come down the steps and ask them both what the "
                          "manifest is missing.",
                          speaker="Winifred")

    players = frozenset(appearances.player_names(cid, sid))
    # The full established set the template's precedence rule requires: cast,
    # players, and every named record this fixture wrote -- the world, the
    # location, the campaign and the scene. Assembled from what was created
    # rather than scraped from record bodies. None of these tokens collides
    # with slop.STOCK_NAMES today, so an omission here would not show up in the
    # recordings; it is written out in full because the rule, not the fixture,
    # is what the check is meant to honour.
    established = slop.established_tokens(
        list(players) + _npc_names(cid, sid)
        + ["Saltmarch Pier", "Realm", "Saltmarch Nights", "The Pier at Dusk"])
    return {"cid": cid, "sid": sid, "players": players, "established": established}


def grade_natural_prose(ctx: dict, output: str) -> list[Check]:
    # The legacy blacklist is opt-in. Verify the selected guide reaches the real
    # assembled prompt before grading its recordings; otherwise this case would
    # punish a model for rules the experiment intentionally stopped requesting.
    guide = styles.read_style("natural-prose-legacy")
    return (
        graders.grade_prompt_section(ctx["messages"], "natural_prose",
                                     "scene/sections/natural_prose.j2")
        + graders.grade_prompt_section(ctx["messages"], "prose_style",
                                       "scene/sections/prose_style.j2",
                                       prose_style_name=guide["meta"]["name"],
                                       prose_style_body=guide["body"])
        + graders.grade_slop(output, ctx["players"], ctx["established"],
                             guide["body"]))


# ------------------------------------------------ case 7: continuity identity

# Every seeded text and every proposed row is spelled out here, once: whether a
# stored record is a candidate depends on the exact words and on which scene
# its beats were seeded in, so `build` pins what each text is supposed to do
# (the clause that admits it, the offered sets) and a floor change fails there,
# loudly, rather than as an eval that quietly asks about nothing.

#: The open thread §28.10's case 1 rewords (the same text as `tests/review_runs`'
#: LEDGER_THREAD; evals does not import tests).
IDENTITY_LEDGER = ("find-the-ledger", "Find the ledger",
                   "Winifred learned the harbour ledger exists.")
#: The open thread §28.10's case 2 asks a distinct question about.
IDENTITY_SMUGGLING = ("the-saltmarch-smuggling", "Who runs the Saltmarch smuggling",
                      "Seraphine would not say who pays for the night cargo.")
#: The broad thread §28.10's case 3 continues.
IDENTITY_DEBTS = ("seraphines-debts", "Seraphine's debts",
                  "Seraphine owes money all along the Saltmarch waterfront.")
#: A commitment seeded in a scene that shares no one with the absorbed one, and
#: no content word with §28.10 case 4's commitment row: nothing of its own type is
#: close to that row, only threads are.
IDENTITY_DEADLINE = ("the-midnight-deadline", "Seraphine's midnight deadline",
                     "Seraphine gave Winifred until midnight and no further.")

#: The extraction's rows, in order: r1 to r3 are plot rows, the last a
#: commitment row.
IDENTITY_ROWS = {
    "plot_movements": [
        # §28.10 case 1: the same obligation, reworded -- admitted by the lexical floors.
        {"title": "Recover the harbour ledger",
         "beat": "Winifred went looking for the harbour ledger.", "status": "open"},
        # §28.10 case 2: the same topic, a distinct question -- admitted structurally.
        {"title": "Who bribes the Saltmarch harbourmaster",
         "beat": "Mara saw the harbourmaster pocket a purse after the night cargo landed.",
         "status": "open"},
        # §28.10 case 3: a concrete continuation of the broad thread -- structurally.
        {"title": "Seraphine's debt to Mara comes due",
         "beat": "Mara told Seraphine the debt is due at the next tide.", "status": "open"},
    ],
    # §28.10 case 4: lexically close only to a thread, which it is never offered.
    "commitment_movements": [
        {"title": "Pay Mara for finding the ledger", "kind": "promise", "status": "open",
         "beat": "Mara asked to be paid once the ledger turns up."},
    ],
}

#: Plot row index -> the verdict it should get and the check that reports it.
IDENTITY_VERDICTS = (
    {"decision": "existing", "id": IDENTITY_LEDGER[0], "check": "same_obligation"},
    {"decision": "new", "id": "", "check": "distinct"},
    {"decision": "new", "id": "", "check": "continuation"},
)

#: What `examine` offers each row, and through which clause, against the
#: fixture above. Pinned so the recordings are authored against known offers.
IDENTITY_OFFERS = {
    "r1": {IDENTITY_LEDGER[0]: "lexical"},
    "r2": {IDENTITY_SMUGGLING[0]: "structural"},
    "r3": {IDENTITY_DEBTS[0]: "structural"},
}


def _identity_parsed() -> dict:
    """The rows as `absorb.parse_output` hands them on, so the case examines
    exactly the shape the app would."""
    return absorb_store.parse_output(json.dumps(
        {"one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],
         **IDENTITY_ROWS}))


def _identity_examine(ctx: dict) -> identity.Examination:
    cid, sid = ctx["cid"], ctx["sid"]
    return identity.examine(cid, sid, ctx["parsed"], chronicle.scene_facts(cid, sid),
                            embed_deadline=None)


def _offers(exam: identity.Examination) -> dict[str, dict[str, str]]:
    return {e.key: {s.ref.partition(":")[2]: sig["via"] for s, sig in e.candidates}
            for e in exam.rows}


def build_continuity_identity() -> dict:
    """Three open threads seeded in the absorbed scene with Seraphine, Winifred
    and Mara present -- so every thread shares the proposed rows' cast and
    scene, and the structural clause is in play -- and a commitment seeded in
    an earlier scene nobody stood in."""
    wid, wroot, sera = _world_with_sera()
    mara, _ = characters.create_character(wroot, "Mara", "default",
                                          characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    croot = campaigns.campaign_root(cid)
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))

    s0 = scenes.create_scene(cid, "Saltmarch docks")
    sid = scenes.create_scene(cid, "The Pier at Dusk")
    appearances.appear(cid, sid, "characters", sera, "default", "npc")
    appearances.appear(cid, sid, "characters", mara, "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    for tid, title, beat in (IDENTITY_LEDGER, IDENTITY_SMUGGLING, IDENTITY_DEBTS):
        plot.set_movement(cid, tid, title, "open", beat, sid)
    did, title, beat = IDENTITY_DEADLINE
    commitments.set_movement(cid, did, title, "threat", "open", "midnight", beat, s0)

    ctx = {"cid": cid, "sid": sid, "parsed": _identity_parsed()}
    exam = _identity_examine(ctx)
    # Every row is proposed-new (so "not examined" below is not a drop), the
    # three plot rows are examined in order, and each is offered exactly the
    # record its case is about, through the clause the case claims.
    assert exam.proposed == 4, exam.proposed
    assert [(e.section, e.index) for e in exam.rows] == [
        ("plot_movements", 0), ("plot_movements", 1), ("plot_movements", 2)], exam.rows
    assert _offers(exam) == IDENTITY_OFFERS, _offers(exam)
    assert not any(e.section == "commitment_movements" for e in exam.rows)
    return ctx


def _identity_prompt(ctx: dict) -> list[dict]:
    """The resolver prompt, through the production builder, for what `examine`
    finds now -- stored on `ctx` with the offers and verdicts the grader reads."""
    exam = _identity_examine(ctx)
    ctx["exam"] = exam
    ctx["offered"] = {key: set(ids) for key, ids in _offers(exam).items()}
    ctx["kinds"] = {e.key: e.kind for e in exam.rows}
    ctx["expected"] = {e.key: IDENTITY_VERDICTS[e.index] for e in exam.rows
                       if e.section == "plot_movements"}
    return identity.build_prompt(exam.prompt_rows())


def grade_continuity_identity(ctx: dict, output: str) -> list[Check]:
    # The quoted enum words alone also appear in the reply-shape line, so they
    # would survive deleting every rule; each decision instruction gets the
    # unique phrase that states it.
    prompt = graders.grade_prompt(
        ctx["messages"],
        {f"asks_{d}": f'"{d}"' for d in identity.DECISIONS}
        | {"asks_existing_rule": "only when a listed candidate is the same narrative "
                                 "question or obligation",
           "asks_closed_is_not_existing": 'A closed or resolved candidate is never "existing"',
           "asks_continuation_is_new": "a continuation, or a related subplot",
           "asks_uncertain_rule": "when the transcript cannot tell",
           "asks_signals_are_hints": "are hints, not proof"})
    # §28.10 case 4's identity half: a row is only ever compared with its own type, so
    # a commitment is never offered a thread however close the words are. Graded
    # over the examination rather than the prompt text, because it must hold
    # even if a floor change makes the commitment row examined.
    exam = ctx["exam"]
    mixed = sorted(f"{row.key}: {s.ref}" for row in exam.rows
                   for s, _ in row.candidates if s.kind != row.kind)
    same_type = Check("prompt.same_type_only", not mixed,
                      f"rows offered a candidate of another type: {mixed}")
    return [*prompt, same_type,
            *graders.grade_identity(output, ctx["expected"], ctx["offered"],
                                    ctx["kinds"])]


# ----------------------------------------------- case 8: continuity reconcile

# §28.10 cases 2-8, the reconciliation half. Each candidate is specified by
# hand -- its kind, its refs, its signals -- rather than discovered: which
# pairs a sweep finds is pinned by the reconcile tests, and this case asks
# only what the model decides about a candidate once it is sent. Every
# record's title and beats are spelled out here, once, with the scene each
# beat lands in (an index into RECONCILE_SCENES).

#: The scenes, in play order; the last is the one just played.
RECONCILE_SCENES = (("Saltmarch docks", "Seraphine and Mara met on the Saltmarch docks."),
                    ("Realm road", "Mara caught Winifred up on the Realm road."),
                    ("The Pier at Dusk", "Mara followed her map along the pier at dusk."))
#: The campaign date, and the due both resolution candidates passed.
RECONCILE_NOW, RECONCILE_DUE = "2026-05-10", "2026-05-05"

#: ref -> (title, commitment kind or "" for a thread, due, [(scene, beat)]).
RECONCILE_RECORDS: dict[str, tuple[str, str, str, list[tuple[int, str]]]] = {
    # case 2: the same topic, two distinct questions.
    "thread:the-saltmarch-smuggling": (
        "Who runs the Saltmarch smuggling", "", "",
        [(0, "Seraphine would not say who pays for the night cargo.")]),
    "thread:who-bribes-the-saltmarch-harbourmaster": (
        "Who bribes the Saltmarch harbourmaster", "", "",
        [(2, "Mara saw the harbourmaster pocket a purse after the night cargo landed.")]),
    # case 3: a broad thread and the concrete question that grew out of it.
    "thread:seraphines-debts": (
        "Seraphine's debts", "", "",
        [(0, "Seraphine owes money all along the Saltmarch waterfront.")]),
    "thread:what-seraphines-debt-to-mara-costs-her": (
        "What Seraphine's debt to Mara costs her", "", "",
        [(1, "Mara told Seraphine the debt is due at the next tide.")]),
    # case 4: a thread and a commitment about the same business.
    "thread:find-the-ledger": (
        "Find the ledger", "", "",
        [(0, "Winifred learned the harbour ledger exists.")]),
    "commitment:pay-mara-for-finding-the-ledger": (
        "Pay Mara for finding the ledger", "promise", "",
        [(1, "Winifred promised to pay Mara once the ledger turns up.")]),
    # case 5: a thread whose last beats answer it, moved in the last scene.
    "thread:maras-map": (
        "Mara's map", "", "",
        [(0, "Mara's map is torn, and nobody knows where it leads."),
         (2, "Mara matched the torn corner; the map marks a cove past the pier."),
         (2, "Mara walked to the cove the map marks and found it, as drawn.")]),
    # case 6: an old thread nothing has answered.
    "thread:winifreds-chart": (
        "Winifred's chart", "", "",
        [(0, "Winifred's chart shows a reef nobody has sailed past.")]),
    # case 7: a passed due, and a beat showing the promise kept.
    "commitment:maras-oath": (
        "Mara's oath", "promise", RECONCILE_DUE,
        [(0, "Mara swore to return Winifred's ring by the fifth of May."),
         (1, "Mara put the ring back in Winifred's hand on the road, as she swore.")]),
    # case 8: a passed due, and nothing shown about how it went.
    "commitment:seraphines-berth": (
        "Seraphine's berth for Winifred", "promise", RECONCILE_DUE,
        [(0, "Seraphine promised Winifred a berth on a boat out by the fifth of May.")]),
}


def _reconcile_pair(lexical: float) -> dict:
    return {"title_exact": False, "slug_equal": False, "lexical": lexical, "cosine": None,
            "shared_actors": [], "shared_scenes": [], "shared_anchors": [], "via": "lexical"}


_PAST_DUE = {"reason": "overdue", "in_days": -5, "via": "deadline"}

#: The candidates in the order they are sent: (§28.10 case, kind, refs,
#: signals, the vocabulary the case needs, the check that scores it, the
#: words that pass it, and per directed word the ref its ``from`` must be).
#: Pair refs are sorted, as discovery stores them, so a cross pair's A is the
#: commitment. Cases 2 and 3 pass every answer that keeps the pair apart and
#: that the system prompt supports: it calls a narrower question "continuation"
#: or "subthread" with no rule between the two, and the case-2 beats both touch
#: the night cargo, which the prompt's "related" covers. What §28.10 asks of
#: them is that neither pair is merged.
RECONCILE_CASES: tuple[tuple[int, str, tuple[str, ...], dict, str, str,
                             tuple[str, ...], dict[str, str]], ...] = (
    (2, "possible_duplicate",
     ("thread:the-saltmarch-smuggling", "thread:who-bribes-the-saltmarch-harbourmaster"),
     _reconcile_pair(0.42), "same_thread", "distinct", ("distinct", "related"), {}),
    (3, "possible_duplicate",
     ("thread:seraphines-debts", "thread:what-seraphines-debt-to-mara-costs-her"),
     _reconcile_pair(0.38), "same_thread", "continuation", ("continuation", "subthread"),
     {"continuation": "thread:what-seraphines-debt-to-mara-costs-her",
      "subthread": "thread:what-seraphines-debt-to-mara-costs-her"}),
    (4, "possible_relation",
     ("commitment:pay-mara-for-finding-the-ledger", "thread:find-the-ledger"),
     _reconcile_pair(0.35), "cross", "cross_type", ("pays_off", "related"),
     {"pays_off": "thread:find-the-ledger"}),
    (5, "possible_thread_closure", ("thread:maras-map",), {"reason": "touched"},
     "thread", "close", ("close",), {}),
    (6, "possible_thread_closure", ("thread:winifreds-chart",),
     {"reason": "stale", "days_since": 75}, "thread", "keep_open", ("keep_open",), {}),
    (7, "possible_commitment_resolution", ("commitment:maras-oath",), _PAST_DUE,
     "commitment", "fulfilled", ("fulfilled",), {}),
    (8, "possible_commitment_resolution", ("commitment:seraphines-berth",), _PAST_DUE,
     "commitment", "unproven", ("keep_open", "uncertain"), {}),
)


def _seed_reconcile(cid: str, sids: list[str]) -> None:
    for ref, (title, kind, due, beats) in RECONCILE_RECORDS.items():
        rtype, _, rid = ref.partition(":")
        for at, text in beats:
            if rtype == "thread":
                plot.set_movement(cid, rid, title, "open", text, sids[at])
            else:
                commitments.set_movement(cid, rid, title, kind, "open", due, text, sids[at])


def build_continuity_reconcile() -> dict:
    """Three scenes played, the last just now, and every record above seeded
    beat by beat into its scenes; the clock stands five days past both dues.
    The candidates are what `select` would hand `build_payload` -- id, kind,
    refs, signals and the current fingerprint."""
    wid, wroot, _ = _world_with_sera()
    characters.create_character(wroot, "Mara", "default", characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    pcs.create_pc(campaigns.campaign_root(cid), "Winifred", [],
                  persona=pcs.blank_persona("Winifred"))
    clock.advance(cid, to=RECONCILE_NOW)
    sids = [scenes.create_scene(cid, title) for title, _ in RECONCILE_SCENES]
    for sid, (_, line) in zip(sids, RECONCILE_SCENES, strict=True):
        chronicle.absorb(cid, {"id": sid, "one_line": line, "cast": ["characters/mara"]})
    _seed_reconcile(cid, sids)

    current = pending.Current.load(cid)
    picked = []
    for _, kind, refs, signals, *_ in RECONCILE_CASES:
        fp = pending.fingerprint(current, kind, list(refs))
        assert fp is not None, refs
        picked.append({"id": canon.candidate_id(kind, refs), "kind": kind, "refs": list(refs),
                       "signals": dict(signals), "fingerprint": fp})
    return {"cid": cid, "sids": sids, "candidates": picked}


def _reconcile_payload(ctx: dict) -> dict:
    """The sweep's payload, through the production `build_payload`, for the
    candidates `build` specified -- asserted, and stored on `ctx` with the
    vocabularies, known scenes and verdicts the graders read."""
    payload = reconcile.build_payload(ctx["cid"], ctx["candidates"])
    sent = payload["candidates"]
    # Every case is sent, in order, under the vocabulary it needs, with each
    # record found (a record missing from the ledger renders as its bare ref).
    assert [c["id"] for c in sent] == [c["id"] for c in ctx["candidates"]], sent
    assert [c["vocabulary"] for c in sent] == [case[4] for case in RECONCILE_CASES], sent
    assert all(r["line"] != r["ref"] for c in sent for r in c["records"]), sent
    # Every scene is known evidence: the recordings cite them by id.
    assert payload["known_scenes"] == sorted(ctx["sids"]), payload["known_scenes"]
    ctx["payload"] = payload
    ctx["vocab"] = {c["key"]: reconcile.DECISIONS[c["vocabulary"]] for c in sent}
    ctx["known"] = set(payload["known_scenes"])
    ctx["expected"] = {}
    for cand, (_, _, _, _, _, check, words, froms) in zip(sent, RECONCILE_CASES, strict=True):
        letters = {r["ref"]: r["letter"] for r in cand["records"]}
        ctx["expected"][cand["key"]] = {
            "check": check, "decisions": words,
            "from": {word: letters[ref] for word, ref in froms.items()}}
    return payload


def _reconcile_prompt(ctx: dict) -> list[dict]:
    """The sweep's prompt, through the production `build_prompt`, over
    `_reconcile_payload`."""
    return reconcile.build_prompt(_reconcile_payload(ctx))


#: The system prompt's opener and its one phrase per decision rule (Task 5's
#: needles): the quoted words alone also appear in the vocabulary lines, so
#: they would survive deleting every rule.
RECONCILE_RULES = {
    "asks_reconcile": "You are reviewing a campaign's story ledger for records that "
                      "may overlap or be finished",
    "asks_duplicate_rule": '"duplicate" only when both records are the same question '
                           "or obligation",
    "asks_continuation_is_not_duplicate": 'a narrower or later question is "continuation" '
                                          'or "subthread", not "duplicate"',
    "asks_cross_never_duplicate": 'A thread and a commitment are never "duplicate"',
    "asks_age_is_not_evidence": "Age alone is never evidence that a thread is finished",
    "asks_deadline_is_not_evidence": "A passed deadline alone is never evidence that a "
                                     "promise was kept or broken",
    "asks_evidence_scene": "name at least one evidence scene id from the lines shown",
    "asks_no_invented_date": "Do not invent a date",
}


def grade_continuity_reconcile(ctx: dict, output: str) -> list[Check]:
    words = sorted({w for vocab in reconcile.DECISIONS.values() for w in vocab})
    prompt = graders.grade_prompt(
        ctx["messages"], {f"asks_{w}": f'"{w}"' for w in words} | RECONCILE_RULES)
    return [*prompt, *graders.grade_reconcile(output, ctx["expected"], ctx["vocab"],
                                              ctx["known"])]


# ------------------------------------------- case 9: scene-suggestion control

# §28.10 cases 9-10: two focused drivers and a batch time anchor, in a calendar
# no built-in provider knows. The calendar is eval-owned -- the same shape as
# the test suite's wide test provider (evals does not import tests), with a few
# named months so its friendly form and its native form differ visibly.
_SALTMARCH_CALENDAR_SRC = '''
from grimoire.store.calendars.base import CalendarError, CalendarProvider, register

MONTHS = ["Frost", "Thaw", "Bloom", "Highsun", "Harvest", "Ember"]   # 30 days each
DAYS = 30


class _SaltmarchReckoning(CalendarProvider):
    def __init__(self, config):
        self.custom_holidays = []

    def parse(self, native):
        try:
            y, m, d = str(native).split("-")
            day = int(d)
            if not 1 <= day <= DAYS:
                raise ValueError(day)
            return int(y) * DAYS * len(MONTHS) + MONTHS.index(m) * DAYS + day - 1
        except (ValueError, IndexError) as e:
            raise CalendarError(f"bad Saltmarch date: {native!r}") from e

    def _split(self, fixed):
        y, rest = divmod(fixed, DAYS * len(MONTHS))
        m, d = divmod(rest, DAYS)
        return y, m, d + 1

    def format(self, fixed):
        y, m, d = self._split(fixed)
        return f"{y}-{MONTHS[m]}-{d:02d}"

    def describe(self, fixed):
        y, m, d = self._split(fixed)
        return {"year": y, "month": m + 1, "month_name": MONTHS[m], "day": d,
                "weekday_name": "Tideday", "weekday_index": 0,
                "friendly": f"{d} {MONTHS[m]} {y}"}

    def holidays(self, start_fixed, end_fixed):
        return []

    def months(self, year):
        return [{"key": k, "name": k, "days": DAYS} for k in MONTHS]


register("saltmarch-reckoning", _SaltmarchReckoning, "Saltmarch Reckoning")
'''

#: The present, and the coronation ten days after it, in the plugin's notation.
SUGGEST_NOW, SUGGEST_CORONATION = "5-Thaw-07", "5-Thaw-17"
SUGGEST_THREADS = (
    ("maras-map", "Mara's map", "Mara's map is torn, and nobody knows where it leads."),
    ("find-the-ledger", "Find the ledger", "Winifred learned the harbour ledger exists."),
    ("seraphines-debts", "Seraphine's debts",
     "Seraphine owes money all along the Saltmarch waterfront."),
)
SUGGEST_FOCUS = ("thread:maras-map", "thread:find-the-ledger")
SUGGEST_ANCHOR = "event:the-coronation"


def _build_suggestions(**controls) -> dict:
    """Realm with Seraphine and Mara; a campaign on the Saltmarch Reckoning,
    its clock set in that calendar's notation; three open threads and the
    coronation ten days out. `controls` go to the production
    `suggest.resolve_controls`, against the snapshot the prompt renders."""
    from grimoire.store.paths import home

    plugins = home() / "calendars"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "saltmarch_reckoning.py").write_text(_SALTMARCH_CALENDAR_SRC, encoding="utf-8")
    wid, wroot, _ = _world_with_sera()
    characters.create_character(wroot, "Mara", "default", characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    cfg["primary"] = {"provider": "saltmarch-reckoning", "region": "",
                      "custom_holidays": [], "anchor": None}
    calendars.write_calendar(croot, cfg)
    clock.advance(cid, to=SUGGEST_NOW, reason="setup")
    sid = scenes.create_scene(cid, "Saltmarch docks")
    for tid, title, beat in SUGGEST_THREADS:
        plot.set_movement(cid, tid, title, "open", beat, sid)
    eid = events.create(cid, "The coronation", SUGGEST_CORONATION)
    assert f"event:{eid}" == SUGGEST_ANCHOR, eid

    snapshot = suggest.build_snapshot(cid)
    # The fixture this case was written around: every focus ref is a driver,
    # and the coronation is an anchor option in the plugin's own notation.
    index = {d["ref"] for d in snapshot["driver_index"]}
    assert set(SUGGEST_FOCUS) <= index, index
    assert [(a["ref"], a["native"]) for a in snapshot["anchors"]] == [
        (SUGGEST_ANCHOR, SUGGEST_CORONATION)], snapshot["anchors"]
    return {"cid": cid, "snapshot": snapshot,
            "controls": suggest.resolve_controls(cid, snapshot, time_mode="anchor",
                                                 time_anchor_ref=SUGGEST_ANCHOR, **controls),
            "provider": calendars.primary_provider(croot)}


def build_scene_suggestions() -> dict:
    return _build_suggestions(focus_refs=list(SUGGEST_FOCUS), time_anchor_relation="before")


def build_scene_suggestions_on() -> dict:
    """The same campaign anchored `on` the coronation with no focus: §28.10
    case 10's derivation half, an `on` date derived in the plugin's notation
    through the production parser, whatever the recording wrote."""
    return _build_suggestions(time_anchor_relation="on")


def _suggestions_prompt(ctx: dict) -> list[dict]:
    return suggest.build_prompt(ctx["snapshot"], None, controls=ctx["controls"])


def grade_scene_suggestions(ctx: dict, output: str) -> list[Check]:
    # Ids, keys and values only (grade_prompt's contract): the quoted action
    # words, the two keys, the high-pressure states, and the refs the
    # controls name. The sentences around them are covered whole by the two
    # rendered addenda, so a reword moves both sides together.
    controls = ctx["controls"]
    needles = {
        **{f"asks_{a}": f'"{a}"' for a in continuity_drivers.DRIVER_ACTIONS},
        "asks_drivers_key": '"drivers"',
        "asks_time_anchor_key": '"time_anchor"',
        **{f"state_{s}": f'"{s}"' for s in pressure.SORT_ORDER if s in pressure.HIGH_PRESSURE},
        **{f"focus_{ref}": ref for ref in controls.focus},
        # an empty needle is in every prompt, so an unanchored batch asks none
        **({"anchor_ref": controls.anchor} if controls.anchor else {}),
    }
    view = suggest.driver_view(ctx["snapshot"], controls)
    return [
        *graders.grade_prompt(ctx["messages"], needles),
        *graders.grade_prompt_section(ctx["messages"], "drivers_addendum",
                                      "scene_suggestions/instruction/drivers_addendum.j2",
                                      view=view),
        *graders.grade_prompt_section(ctx["messages"], "controls_addendum",
                                      "scene_suggestions/instruction/controls_addendum.j2",
                                      view=view),
        *graders.grade_scene_suggestions(output, ctx["snapshot"], controls, ctx["provider"]),
    ]


# ------------------------------------------- case 10: decide, scene-break

#: The cadence the fixture is scored at: six posts is twice it, so the length
#: signal fires on its own at full weight and nothing else does.
DECIDE_BREAK_EVERY = 3


def build_decide_scene_break() -> dict:
    """A Saltmarch scene whose beat has resolved: Seraphine settles Mara's debt
    and the ledger changes hands. Placed at the pier on a date, with the cast
    seated, so the item's context carries the whole head."""
    wid, wroot, sera = _world_with_sera()
    mara, _ = characters.create_character(wroot, "Mara", "default",
                                          characters.blank_card("Mara"))
    pier = entities.create_entity(wroot, "locations", "Saltmarch Pier",
                                  "Salt-white planks over black water.")
    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    croot = campaigns.campaign_root(cid)
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))

    sid = scenes.create_scene(cid, "The Debt at the Pier")
    appearances.appear(cid, sid, "characters", sera, "default", "npc")
    appearances.appear(cid, sid, "characters", mara, "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    scenes.set_location(cid, sid, pier)
    sid = scenes.set_datetime(cid, sid, "2026-07-05")["id"]

    scenes.append_message(cid, sid, "user", "I set Seraphine's purse on the crate between them.",
                          speaker="Winifred")
    scenes.append_reply(cid, sid, [
        {"speaker": "Seraphine Vale", "content": "Count it, Mara. Every coin you are owed, "
                                                 "and the tide's interest besides."},
    ])
    scenes.append_message(cid, sid, "user", "I watch Mara count.", speaker="Winifred")
    scenes.append_reply(cid, sid, [
        {"speaker": "Mara", "content": "It is all here. We are square, Seraphine."},
        {"speaker": None, "content": "Mara slides the ledger across the crate, and Seraphine "
                                     "tucks it under her coat."},
    ])
    scenes.append_message(cid, sid, "user", "I tell them I am glad that is over.",
                          speaker="Winifred")
    scenes.append_reply(cid, sid, [
        {"speaker": "Seraphine Vale", "content": "So am I. Nobody on this pier owes anybody "
                                                 "anything tonight."},
    ])
    return {"cid": cid, "sid": sid}


def _decide_scene_break_prompt(ctx: dict) -> list[dict]:
    """The structured prompt for the item `build_item` makes from this scene,
    gathered as `routes/scenes._break_ask` gathers it -- the posts through the
    prompt-phase regex view, the facts, the signals the scorer found."""
    cid, sid = ctx["cid"], ctx["sid"]
    scene = scenes.read_scene(cid, sid)
    messages = scene["messages"]
    history = scenes.histories(scene["meta"])
    scored = scene_break.evaluate(messages, history["locations"], history["times"],
                                  {"at": 0, "locs": 0, "times": 0}, DECIDE_BREAK_EVERY)
    assert scored["due"] and len(scored["signals"]) == 1, scored
    shown = regex_view.view(messages, cid=cid, phase="prompt", offset=0, total=len(messages))
    transcript = chronicle.transcript_text(shown, appearances.player_label(cid, sid))
    item = scene_break.build_item(transcript, scored["signals"],
                                  chronicle.scene_facts(cid, sid),
                                  scene["meta"].get("title", ""))
    ctx.update(items=(item,), transcript=transcript, explain=scene_break.explain())
    return inference.structured_messages([item], explain=ctx["explain"])


def _decide_scene_break_schema(ctx: dict) -> dict:
    return decisions.schema(ctx["items"], explain=bool(ctx["explain"]))


def grade_decide_scene_break(ctx: dict, output: str) -> list[Check]:
    messages = ctx["messages"]
    text = graders.prompt_text(messages)
    system = messages[0]["content"]
    schema = _SCHEMA_ENV.from_string("{{ schema | tojson(indent=2) }}").render(
        schema=_decide_scene_break_schema(ctx))
    explain = ctx["explain"]
    return [*graders.grade_prompt_section(messages, "question", "scene_break/question.j2"),
            Check("prompt.context", ctx["transcript"] in text,
                  "the scene's transcript is not in the prompt"),
            Check("prompt.schema", schema in system,
                  "the reply's JSON Schema is not in the system message"),
            Check("prompt.explain", bool(explain) and explain in text,
                  "scene_break/explain.j2 no longer reaches the prompt"),
            *graders.grade_decision(output, ctx["items"], explain=bool(explain),
                                    question=scene_break.QUESTION_ID, expected=True)]


# ------------------------------------------- case 11: decide, voice drift

#: Seraphine's voice anchor: the standard the judge holds her lines to.
DECIDE_DRIFT_ANCHOR = ("Clipped. Never uses contractions. Answers a question with a "
                       "question, and volunteers nothing.")

#: Her outstanding correction from an earlier scene. It narrows the anchor
#: without loosening it, so the lines below break both and the right verdict
#: stays `drift` -- the case proves the correction reaches the judge, not that
#: it changes the answer.
DECIDE_DRIFT_CORRECTION = ("Keep Seraphine's answers to a sentence; last scene she "
                           "explained herself at length.")


def build_decide_voice_drift() -> dict:
    """A night-dock scene where Seraphine, whose anchor is clipped and
    contraction-free, chatters loosely in contractions -- with a standing
    correction in force, fingerprinted to the anchor it was judged against."""
    wid, wroot, sera = _world_with_sera()
    voice_anchors.write(wroot, sera, DECIDE_DRIFT_ANCHOR)
    cid = campaigns.create_campaign("Saltmarch Nights", wid)
    croot = campaigns.campaign_root(cid)
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))
    sid = scenes.create_scene(cid, "The Night Dock")
    appearances.appear(cid, sid, "characters", sera, "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    record = overlay.voice_anchor_record(cid, sera)
    voice_drift.write(appearances.locked_actor_root(cid), sera, DECIDE_DRIFT_CORRECTION,
                      voice_drift.anchor_fingerprint(record["text"], record["id"]))

    scenes.append_message(cid, sid, "user", "I ask Seraphine where she was last night.",
                          speaker="Winifred")
    scenes.append_reply(cid, sid, [
        {"speaker": "Seraphine Vale",
         "content": "Oh, y'know, I couldn't sleep, so I just wandered down to the pier "
                    "for a bit. It's quiet there, isn't it? I didn't want to wake "
                    "anybody, so I figured I'd let you all rest."},
    ])
    scenes.append_message(cid, sid, "user", "I ask whether she met anyone.",
                          speaker="Winifred")
    scenes.append_reply(cid, sid, [
        {"speaker": "Seraphine Vale",
         "content": "Nah, not really. Well, there was this fisherman, and we got to "
                    "talking about the tides, and honestly I'd have stayed all night if "
                    "it hadn't started raining. You'd have liked him."},
    ])
    return {"cid": cid, "sid": sid, "char": sera}


def _decide_voice_drift_prompt(ctx: dict) -> list[dict]:
    """The structured prompt for the item the absorb phase makes for Seraphine,
    gathered through the store helpers `_stage_voice_drift` calls: the
    absorb transcript through the prompt-phase regex view, the locked card's
    name (`locked_name`), and the effective anchor with the correction only
    while it is in force (`judge_item`)."""
    cid, sid, aid = ctx["cid"], ctx["sid"], ctx["char"]
    scene = scenes.read_scene(cid, sid)
    shown = regex_view.view(scene["messages"], cid=cid, phase="prompt")
    transcript = chronicle.transcript_text(shown, appearances.player_label(cid, sid))
    record = overlay.voice_anchor_record(cid, aid)
    flag = voice_drift.read_record(appearances.locked_actor_root(cid), aid)
    name = voice_drift.locked_name(cid, aid)
    assert isinstance(name, str), name
    item = voice_drift.judge_item(name, record, transcript, flag)
    ctx.update(items=(item,), transcript=transcript,
               correction=voice_drift.live_correction(flag, record),
               explain=voice_drift.explain())
    return inference.structured_messages([item], explain=ctx["explain"])


def _decide_voice_drift_schema(ctx: dict) -> dict:
    return decisions.schema(ctx["items"], explain=bool(ctx["explain"]))


def grade_decide_voice_drift(ctx: dict, output: str) -> list[Check]:
    messages = ctx["messages"]
    text = graders.prompt_text(messages)
    system = messages[0]["content"]
    schema = _SCHEMA_ENV.from_string("{{ schema | tojson(indent=2) }}").render(
        schema=_decide_voice_drift_schema(ctx))
    (choice,) = ctx["items"][0].questions
    missing = [opt.id for opt in choice.options
               if f"- {opt.id}: {prompts.render('voice_drift/option.j2', verdict=opt.id)}"
               not in text]
    return [*graders.grade_prompt_section(messages, "question", "voice_drift/question.j2"),
            Check("prompt.options", not missing,
                  f"verdicts missing from the prompt, or not described by option.j2: {missing}"),
            Check("prompt.context", ctx["transcript"] in text
                  and DECIDE_DRIFT_ANCHOR in text,
                  "the scene's transcript or the voice anchor is not in the prompt"),
            Check("prompt.correction", bool(ctx["correction"])
                  and ctx["correction"] in text,
                  "the outstanding correction did not reach the prompt"),
            Check("prompt.schema", schema in system,
                  "the reply's JSON Schema is not in the system message"),
            *graders.grade_decision(output, ctx["items"], explain=bool(ctx["explain"]),
                                    question=voice_drift.QUESTION_ID,
                                    expected=voice_drift.DRIFT,
                                    max_rationale=voice_drift.MAX_NOTE)]


# ------------------------------------------- case 12: decide, speaker

#: The scene's posts, oldest first, as `(role, speaker, content)`: the
#: player's post last, putting a question to Winifred by name. The director
#: note between them is a synthetic line the pick never reads.
DECIDE_SPEAKER_POSTS = [
    ("user", "", ("I set the lantern on the crate between Mara and Winifred and wait "
                  "for the tide bell.")),
    ("assistant", "Mara", "Well? Somebody say something, or I am going home."),
    ("assistant", scenes.DIRECTOR_SPEAKER, "Keep Mara impatient."),
    ("user", "", "Winifred, where were you when the tide turned?"),
]


def build_decide_speaker() -> dict:
    """Two NPCs who could open the round -- Mara cast first, and the last to
    speak -- and a player who has just asked the other one a question
    directly: the pick is Winifred."""
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    npcs = [characters.create_character(wroot, name, "default",
                                        characters.blank_card(name))[0]
            for name in ("Mara", "Winifred")]
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    pid, _ = pcs.create_pc(croot, "Rowan", [], persona=pcs.blank_persona("Rowan"))
    sid = scenes.create_scene(cid, "The Tide Bell")
    for npc in npcs:
        appearances.appear(cid, sid, "characters", npc, "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    for role, speaker, content in DECIDE_SPEAKER_POSTS:
        scenes.append_message(cid, sid, role, content, **({"speaker": speaker} if speaker
                                                          else {}))
    return {"cid": cid, "sid": sid}


def _decide_speaker_prompt(ctx: dict) -> list[dict]:
    """The structured prompt for the item `_select` sends, gathered through the
    store helpers `_selector_item` calls: the present cast but the player
    (`npc_roster`, every NPC eligible, as in a round nobody sits out of) and
    the observable transcript through the prompt-phase regex view
    (`observable_conversation`). No rationale is asked for."""
    cid, sid = ctx["cid"], ctx["sid"]
    roster = response_protocol.npc_roster(appearances.scene_cast(cid, sid))
    messages = scenes.read_scene(cid, sid)["messages"]
    # The twin of `_selector_item`'s lambda (routes/character_turns.py), the
    # one `test_regex_prompt_guard.py` holds to the prompt phase: keep the two
    # identical, since only the route's is guarded.
    conversation = response_protocol.observable_conversation(
        messages, lambda posts, offset, total: regex_view.view(
            posts, cid=cid, phase="prompt", offset=offset, total=total))
    item = response_protocol.selector_item(roster, conversation)
    ctx.update(items=(item,), roster=roster, conversation=conversation)
    return inference.structured_messages([item])


def _decide_speaker_schema(ctx: dict) -> dict:
    return decisions.schema(ctx["items"], explain=False)


def grade_decide_speaker(ctx: dict, output: str) -> list[Check]:
    messages = ctx["messages"]
    text = graders.prompt_text(messages)
    system = messages[0]["content"]
    schema = _SCHEMA_ENV.from_string("{{ schema | tojson(indent=2) }}").render(
        schema=_decide_speaker_schema(ctx))
    grimoire = prompts.render("scene/response_selector_grimoire.j2")
    wanted = [f"- {response_protocol.SELECTOR_QUESTION} (choice, or null for none of these): ",
              *(f"- {entry['ref']}: {entry['name']}" for entry in ctx["roster"]),
              f"- {response_protocol.GRIMOIRE_REF}: {grimoire}"]
    missing = [line for line in wanted if line not in text]
    heard = [turn["content"] for turn in ctx["conversation"] if turn["content"] not in text]
    return [*graders.grade_prompt_section(messages, "question",
                                          "scene/response_selector_question.j2"),
            Check("prompt.options", bool(grimoire.strip()) and not missing,
                  f"the roster, grimoire or null is missing from the choice: {missing}"),
            Check("prompt.context", ctx["items"][0].context in text and not heard,
                  f"the observable transcript is not in the prompt: {heard}"),
            Check("prompt.posts", len(ctx["conversation"]) == 3,
                  f"the pick read {len(ctx['conversation'])} posts, not the scene's three "
                  "story posts (the gathering kept a synthetic line or lost a post)"),
            Check("prompt.synthetic", DECIDE_SPEAKER_POSTS[2][2] not in text,
                  "a director note reached the speaker pick's prompt"),
            Check("prompt.schema", schema in system,
                  "the reply's JSON Schema is not in the system message"),
            *graders.grade_decision(output, ctx["items"], explain=False,
                                    question=response_protocol.SELECTOR_QUESTION,
                                    expected="characters:winifred")]


# ------------------------------------- case 13: decide, continuity identity
#
# The duplicate check as `decide()` will send it after the switch: the same
# fixture and the same examination as case 7 (`build_continuity_identity`),
# one item per examined row through `identity.build_items`. Its recordings
# carry each of case 7's failure modes into the decide shape.

def _decide_identity_prompt(ctx: dict) -> list[dict]:
    """The structured prompt for what `examine` finds now, built as the
    switched call site will build it -- stored on `ctx` with the items, the
    rows, the offers and the verdicts the grader reads, as `_identity_prompt`
    stores them."""
    exam = _identity_examine(ctx)
    rows = exam.prompt_rows()
    items = identity.build_items(rows, exam.live)
    ctx.update(exam=exam, rows=rows, items=items, explain=identity.explain(),
               offered={key: set(ids) for key, ids in _offers(exam).items()},
               kinds={e.key: e.kind for e in exam.rows},
               expected={e.key: IDENTITY_VERDICTS[e.index] for e in exam.rows
                         if e.section == "plot_movements"})
    return inference.structured_messages(items, explain=ctx["explain"])


def _decide_identity_schema(ctx: dict) -> dict:
    return decisions.schema(ctx["items"], explain=True)


def grade_decide_continuity_identity(ctx: dict, output: str) -> list[Check]:
    messages = ctx["messages"]
    text = graders.prompt_text(messages)
    user = messages[1]["content"]
    schema = _SCHEMA_ENV.from_string("{{ schema | tojson(indent=2) }}").render(
        schema=_decide_identity_schema(ctx))
    missing = [row["title"] for row in ctx["rows"] if row["title"] not in user]
    return [*graders.grade_prompt_section(messages, "question",
                                          "continuity_identity/question.j2"),
            Check("prompt.schema", schema in messages[0]["content"],
                  "the reply's JSON Schema is not in the system message"),
            Check("prompt.context", not missing,
                  f"examined rows missing from the user message: {missing}"),
            Check("prompt.explain", f"Rationale, for each item: {ctx['explain']}" in text,
                  "the rationale instruction did not reach the prompt"),
            *graders.grade_identity_decision(output, ctx["items"], ctx["rows"],
                                             ctx["expected"])]



# ------------------------------------ case 14: decide, continuity reconcile
#
# The reconciliation sweep as `decide()` will send it after the switch: the
# same fixture and the same payload as case 8 (`build_continuity_reconcile`),
# one item per candidate through `reconcile.build_items`. Its recordings carry
# each of case 8's failure modes into the decide shape. Every scene is in the
# recent window, so every item shows all three and asks three evidence
# questions.

def _decide_reconcile_prompt(ctx: dict) -> list[dict]:
    """The structured prompt for the case's payload, built as the switched
    call site will build it -- stored on `ctx` with the items and the
    rationale instruction, beside what `_reconcile_payload` stores."""
    payload = _reconcile_payload(ctx)
    items = reconcile.build_items(payload)
    ctx.update(items=items, explain=reconcile.explain())
    return inference.structured_messages(items, explain=ctx["explain"])


def _decide_reconcile_schema(ctx: dict) -> dict:
    return decisions.schema(ctx["items"], explain=True)


def grade_decide_continuity_reconcile(ctx: dict, output: str) -> list[Check]:
    messages = ctx["messages"]
    text = graders.prompt_text(messages)
    user = messages[1]["content"]
    schema = _SCHEMA_ENV.from_string("{{ schema | tojson(indent=2) }}").render(
        schema=_decide_reconcile_schema(ctx))
    vocabularies = sorted({c["vocabulary"] for c in ctx["payload"]["candidates"]})
    missing = [r["ref"] for c in ctx["payload"]["candidates"] for r in c["records"]
               if r["line"] not in user]
    return [*(check for vocab in vocabularies
              for check in graders.grade_prompt_section(
                  messages, f"question.{vocab}", "continuity_reconcile/question.j2",
                  vocabulary=vocab)),
            *graders.grade_prompt_section(messages, "direction",
                                          "continuity_reconcile/direction.j2"),
            *graders.grade_prompt_section(messages, "evidence",
                                          "continuity_reconcile/evidence.j2"),
            Check("prompt.schema", schema in messages[0]["content"],
                  "the reply's JSON Schema is not in the system message"),
            Check("prompt.context", not missing,
                  f"candidate records missing from the user message: {missing}"),
            Check("prompt.explain", f"Rationale, for each item: {ctx['explain']}" in text,
                  "the rationale instruction did not reach the prompt"),
            *graders.grade_reconcile_decision(output, ctx["items"], ctx["payload"],
                                              ctx["expected"])]

# ------------------------------------------------------------------- the suite

def _scene_prompt(ctx: dict) -> list[dict]:
    if ctx.get("actor_ref"):
        return context.compose_turn(ctx["cid"], ctx["sid"], describe=False,
                                    actor_ref=ctx["actor_ref"])[0]
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
         hypothesis="one assigned actor's prose respects its word and paragraph ceilings",
         build=build_scene_length, prompt=_scene_prompt, grade=grade_scene_length,
         recordings=(
             Recording(BASELINE),
             Recording("bloated", ("length.words", "length.paragraphs")),
             Recording("collapsed"),
             Recording("empty", ("length.words",)))),
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
         task="absorb",
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
                    "of the prompt whoever is on stage, a secret one "
                    "arrives under its heading, and lore known_by one actor "
                    "reaches that actor's call and the narrator but not its owner's",
         build=build_owned_lore, prompt=_scene_prompt, grade=grade_owned_lore,
         recordings=(
             Recording(BASELINE),
             Recording("leaked", ("containment.output",)))),
    Case(id="natural-prose",
         hypothesis="a reply contains none of the stock names or literal "
                    "banned phrases the selected legacy prose guide lists, does not "
                    "repeat a single beat word past the cap or use the "
                    "enumerated not-X-but-Y forms, and does not flatten into "
                    "uniform sentence and paragraph length",
         build=build_natural_prose, prompt=_scene_prompt,
         grade=grade_natural_prose,
         recordings=(
             Recording(BASELINE),
             # Literally sloppy, rhythmically fine: so the four literal checks
             # cannot pass unnoticed behind a rhythm hit.
             Recording("slop", ("slop.phrases", "slop.stock_names",
                                "slop.beat_words", "slop.not_x_but_y")),
             # Rhythmically flat, literally clean: the reverse.
             Recording("flat", ("slop.sentence_variance",
                                "slop.paragraph_uniformity",
                                "slop.em_dash_spacing")),
             # A collapsed generation. Proves the vacuous-pass gate gates:
             # without slop.measurable this recording would score all green.
             Recording("terse", ("slop.measurable",)))),
    Case(id="continuity-identity",
         task="continuity-identity",
         hypothesis="the identity resolver maps a reworded duplicate to the existing "
                    "record and keeps a same-topic question and a concrete continuation new",
         build=build_continuity_identity,
         prompt=_identity_prompt,
         grade=grade_continuity_identity,
         recordings=(
             Recording(BASELINE, ext="json"),
             Recording("undecodable", ("identity.json",), "json"),
             # Both ids were offered, so known_ids still passes: what trips is
             # exactly the two rows that should have stayed new.
             Recording("merged", ("identity.distinct", "identity.continuation"), "json"),
             # An id offered nowhere: rejected as unknown, and the wrong
             # verdict for the row it was given on.
             Recording("unknown-id", ("identity.known_ids", "identity.same_obligation"),
                       "json"))),
    Case(id="continuity-reconcile",
         task="continuity-reconcile",
         hypothesis="the reconciliation sweep keeps a same-topic question distinct, "
                    "reads a concrete question as a continuation, never merges a thread "
                    "with a commitment, closes or resolves only on a shown beat, and "
                    "keeps an old or overdue record open when nothing settles it",
         build=build_continuity_reconcile,
         prompt=_reconcile_prompt,
         grade=grade_continuity_reconcile,
         recordings=(
             Recording(BASELINE, ext="json"),
             Recording("undecodable", ("reconcile.json",), "json"),
             # Both pairs merged, each with valid letters, so shape and enum
             # still pass: what trips is exactly the two pair verdicts.
             Recording("merged", ("reconcile.distinct", "reconcile.continuation"), "json"),
             # A closure and a resolution on the two candidates nothing
             # settles, each citing a shown scene, so evidence still passes.
             Recording("eager", ("reconcile.keep_open", "reconcile.unproven"), "json"),
             # The right word on the answered thread, with no scene cited.
             Recording("unfounded", ("reconcile.evidence",), "json"),
             # §28.10 cases 4, 5 and 7 held back: the thread and commitment
             # kept apart as `distinct`, the answered thread and the kept
             # promise each `keep_open` with no scene cited. Every word is in
             # its candidate's vocabulary and none claims an outcome, so enum
             # and evidence still pass: what trips is exactly the three
             # verdicts no other counterexample reaches.
             Recording("timid", ("reconcile.cross_type", "reconcile.close",
                                 "reconcile.fulfilled"), "json"))),
    Case(id="decide-continuity-reconcile",
         task="continuity-reconcile",
         hypothesis="asked through decide() about each candidate the sweep sends, the "
                    "reply is the schema's object, keeps a same-topic question distinct, "
                    "reads a concrete question as a continuation, never merges a thread "
                    "with a commitment, closes or resolves only on a shown scene, and "
                    "keeps an old or overdue record open when nothing settles it",
         build=build_continuity_reconcile,
         prompt=_decide_reconcile_prompt,
         grade=grade_decide_continuity_reconcile,
         schema=_decide_reconcile_schema,
         recordings=(
             Recording(BASELINE, ext="json"),
             # Cut off mid-reply: nothing decodes, so no other output check
             # is reported rather than failed.
             Recording("undecodable", ("reconcile.json",), "json"),
             # Both pairs merged, each with valid letters: what trips is
             # exactly the two pair verdicts.
             Recording("merged", ("reconcile.distinct", "reconcile.continuation"), "json"),
             # A closure and a resolution on the two candidates nothing
             # settles, each citing a shown scene, so evidence still passes.
             Recording("eager", ("reconcile.keep_open", "reconcile.unproven"), "json"),
             # The right word on the answered thread, with every evidence
             # slot null.
             Recording("unfounded", ("reconcile.evidence",), "json"),
             # §28.10 cases 4, 5 and 7 held back, as case 8's `timid`.
             Recording("timid", ("reconcile.cross_type", "reconcile.close",
                                 "reconcile.fulfilled"), "json"))),
    Case(id="scene-suggestions",
         task="suggestions",
         hypothesis="with two focused drivers and a batch anchor in a custom calendar, "
                    "the suggestions spread focus coverage instead of cloning one premise, "
                    "cite only known drivers, and carry dates the anchor rule accepts",
         build=build_scene_suggestions, prompt=_suggestions_prompt,
         grade=grade_scene_suggestions,
         recordings=(
             Recording(BASELINE, ext="json"),
             Recording("undecodable", ("suggest.json",), "json"),
             # Three takes on Mara's map: the ledger is never served, and no
             # two suggestions differ in what they claim.
             Recording("cloned", ("suggest.focus_coverage", "suggest.distinct"), "json"),
             # One premise under three titles, the first card claiming both
             # focus drivers and the other two nothing: every focus ref is
             # covered and the titles and claim sets all differ, so what trips
             # is exactly the premise half of `distinct` and the spread.
             Recording("one-premise", ("suggest.distinct", "suggest.focus_spread"), "json"),
             # The compliant claims, every date after the coronation.
             Recording("bad-date", ("suggest.date_consistent",), "json"),
             # The compliant reply plus one driver the index never listed:
             # `claim` drops it, so only the raw reply shows it.
             Recording("unknown-ref", ("suggest.known_refs",), "json"))),
    Case(id="scene-suggestions-anchor-on",
         task="suggestions",
         hypothesis="with a batch anchor 'on' an event in a custom calendar, every parsed "
                    "date is the anchor's own date in the calendar's notation, whatever the "
                    "model wrote",
         build=build_scene_suggestions_on, prompt=_suggestions_prompt,
         grade=grade_scene_suggestions,
         recordings=(
             Recording(BASELINE, ext="json"),
             Recording("undecodable", ("suggest.json",), "json"))),
    Case(id="decide-scene-break",
         task="scene-break",
         hypothesis="asked through decide() whether a scene whose beat has resolved is "
                    "over, the reply is the schema's object, answers yes, and says why",
         build=build_decide_scene_break,
         prompt=_decide_scene_break_prompt,
         grade=grade_decide_scene_break,
         schema=_decide_scene_break_schema,
         recordings=(
             Recording(BASELINE, ext="json"),
             # Cut off mid-rationale: nothing decodes, so the answer and
             # rationale checks are not reported rather than failed.
             Recording("undecodable", ("decide.json",), "json"),
             # Well formed, with a reason, and the wrong answer.
             Recording("wrong", ("decide.answer",), "json"),
             # The right answer and nothing said about why.
             Recording("no-reason", ("decide.rationale",), "json"))),
    Case(id="decide-voice-drift",
         task="voice-drift",
         hypothesis="asked through decide() whether a character with a clipped, "
                    "contraction-free anchor and a standing correction drifted when she "
                    "chattered in contractions, the reply is the schema's object, answers "
                    "drift, and gives a corrective short enough to store",
         build=build_decide_voice_drift,
         prompt=_decide_voice_drift_prompt,
         grade=grade_decide_voice_drift,
         schema=_decide_voice_drift_schema,
         recordings=(
             Recording(BASELINE, ext="json"),
             # Cut off mid-corrective: nothing decodes, so the answer and
             # rationale checks are not reported rather than failed.
             Recording("undecodable", ("decide.json",), "json"),
             # Well formed, and judged in voice: the clear a garbled judge
             # must never reach, reached by a readable wrong answer.
             Recording("wrong", ("decide.answer",), "json"),
             # Drift with no corrective: the route reports it as a failed
             # check, so the case fails it too.
             Recording("no-note", ("decide.rationale",), "json"),
             # Drift with a corrective over MAX_NOTE, which the route refuses
             # to put in front of every following turn.
             Recording("long-note", ("decide.rationale",), "json"))),
    Case(id="decide-continuity-identity",
         task="continuity-identity",
         hypothesis="asked through decide() about each examined row, the reply is the "
                    "schema's object, maps a reworded duplicate to the existing record by "
                    "an offered id, and keeps a same-topic question and a concrete "
                    "continuation new",
         build=build_continuity_identity,
         prompt=_decide_identity_prompt,
         grade=grade_decide_continuity_identity,
         schema=_decide_identity_schema,
         recordings=(
             Recording(BASELINE, ext="json"),
             # Cut off mid-rationale: nothing decodes, so no other output
             # check is reported rather than failed.
             Recording("undecodable", ("identity.json",), "json"),
             # Both ids were offered, so known_ids still passes: what trips is
             # exactly the two rows that should have stayed new.
             Recording("merged", ("identity.distinct", "identity.continuation"), "json"),
             # An id offered nowhere: no option, so `existing` names nothing,
             # and the row it was given on gets the wrong verdict.
             Recording("unknown-id", ("identity.known_ids", "identity.same_obligation"),
                       "json"))),
    Case(id="decide-speaker",
         task="response-selector",
         hypothesis="asked through decide() who opens a round in which the player has "
                    "just put a question to one of two NPCs by name, the reply is the "
                    "schema's object and picks that NPC",
         build=build_decide_speaker,
         prompt=_decide_speaker_prompt,
         grade=grade_decide_speaker,
         schema=_decide_speaker_schema,
         recordings=(
             Recording(BASELINE, ext="json"),
             # Cut off mid-reference: nothing decodes, so the answer check is
             # not reported rather than failed.
             Recording("undecodable", ("decide.json",), "json"),
             # Well formed, naming an NPC the round does not offer: today's
             # "ineligible or repeated speaker".
             Recording("off-roster", ("decide.answer",), "json"),
             # Well formed, and a null: control handed back to the player
             # when the player had asked someone a question.
             Recording("abstained", ("decide.answer",), "json"))),
)

BY_ID = {c.id: c for c in CASES}
