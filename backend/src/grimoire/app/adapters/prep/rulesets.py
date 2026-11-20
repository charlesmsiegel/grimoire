"""062's ruleset action contracts on the Prep page: ``prep.rulesets`` and ``prep.ruleset_check``.

062 (``grimoire.rulesets``) checks a supplied action and a recorded result against one of four reference profiles
and answers an unaccepted ``ResolutionProposal``: it rolls nothing, stores nothing and applies nothing. No campaign or
chat records which ruleset module it plays under (062 has no store, and 075's recorded rolls take a module per
request), so there is no *pinned* campaign ruleset to show: the page says so, and offers the four reference profiles
as 062 defines them. A check is made against the profile's reference module, named ``reference:062/ruleset/<family>@
<edition>``: an identity of this page's own, never a library record.

The shapes are public (they are 062's contract, not campaign data), so both views read them. Who an action is for
is the one thing a view limits: the table proposes only for a character it controls now, the director for anyone in
the chat's cast. Nothing here is a roll, a resolution, an effect or a record: the answer says so, every time.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.core import DIRECTOR
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.rulesets import ActionRequest, ModuleBinding, RulesetModule, propose
from grimoire.rulesets.families import ACTION_TYPES, RESULT_TYPES
from grimoire.rulesets.model import PROFILES
from grimoire.textapi import ApiRefused

from ..campaign import hub_of
from .common import audience_of, chat_of, segment

#: The source this page names 062's reference modules in: not a library source, and never written.
REFERENCE_SOURCE = "reference:062"

ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
ARTS = ("creo", "intellego", "muto", "perdo", "rego", "animal", "aquam", "auram", "corpus", "herbam", "ignem",
        "imaginem", "mentem", "terram", "vim")
SEASONS = ("spring", "summer", "autumn", "winter")


def _choice(name: str, label: str, options: tuple[str, ...]) -> dict:
    return {"name": name, "label": label, "type": "choice", "options": list(options)}


def _whole(name: str, label: str, low: int, high: int, note: str | None = None) -> dict:
    return {"name": name, "label": label, "type": "whole", "min": low, "max": high, "note": note}


def _text(name: str, label: str, *, optional: bool = False, note: str | None = None) -> dict:
    return {"name": name, "label": label, "type": "text", "max": 2000, "optional": optional, "note": note}


def _dice(name: str, label: str, sides: int, most: int, *, exactly: int | None = None) -> dict:
    return {"name": name, "label": label, "type": "dice", "sides": sides, "max": most, "exactly": exactly}


#: 062's four reference profiles as forms: each field's shape is a hint for the form only; the check is 062's own
#: constructors, which refuse whatever does not fit. The examples are 062's documented ones (docs/RULESETS.md).
CONTRACTS = {
    "d20": {
        "title": "D20 ability check", "edition": PROFILES["d20"],
        "action": [_choice("ability", "Ability", ABILITIES), _text("skill", "Skill"),
                   _whole("dc", "DC", 1, 100), _whole("modifier", "Modifier", -100, 100)],
        "result": [_whole("die", "The d20 as rolled", 1, 20), _whole("total", "Total", -99, 120),
                   {"name": "success", "label": "Success", "type": "yesno"}],
        "example": {"action": {"ability": "dexterity", "skill": "stealth", "dc": 15, "modifier": 3},
                    "result": {"die": 12, "total": 15, "success": True}},
        "notes": ["One single-die ability check: attacks, saves and advantage need an edition hook of their own.",
                  "A natural 20 is not an automatic success on an ability check."],
    },
    "wod": {
        "title": "V5 fixed-difficulty test", "edition": PROFILES["wod"],
        "action": [_text("attribute", "Attribute"), _text("skill", "Skill"), _whole("pool", "Dice pool", 1, 30),
                   _whole("hunger", "Hunger dice", 0, 5, "at most the pool"),
                   _whole("difficulty", "Difficulty", 1, 60)],
        "result": [_dice("normal", "Normal dice as rolled", 10, 30), _dice("hunger", "Hunger dice as rolled", 10, 5),
                   _whole("successes", "Successes", 0, 60),
                   _choice("outcome", "Outcome",
                           ("success", "critical", "messy-critical", "failure", "bestial-failure"))],
        "example": {"action": {"attribute": "wits", "skill": "awareness", "pool": 3, "hunger": 1, "difficulty": 3},
                    "result": {"normal": [10, 6], "hunger": [10], "successes": 5, "outcome": "messy-critical"}},
        "notes": ["Opposed tests need an opponent and both results: not this profile."],
    },
    "pbta": {
        "title": "Dungeon World: Defy Danger", "edition": PROFILES["pbta"],
        "action": [_text("trigger", "The fiction that would trigger the move"), _choice("stat", "Stat", ABILITIES),
                   _whole("modifier", "Modifier", -100, 100)],
        "result": [_dice("dice", "The two d6 as rolled", 6, 2, exactly=2), _whole("total", "Total", -98, 112),
                   _choice("outcome", "Outcome", ("miss", "partial", "strong")),
                   _text("choice", "The cost or choice left open", optional=True,
                         note="required on a partial result, and only then")],
        "example": {"action": {"trigger": "Mara crosses a collapsing bridge", "stat": "dexterity", "modifier": 1},
                    "result": {"dice": [3, 4], "total": 8, "outcome": "partial",
                               "choice": "Leave the pack or lose time taking a safer path"}},
        "notes": ["Whether the fiction triggers the move is the adjudicator's to say: the trigger is only proposed.",
                  "A partial result keeps its cost or choice open for the player; nothing picks it for them."],
    },
    "ars": {
        "title": "Ars Magica: a season of summa study", "edition": PROFILES["ars"],
        "action": [_choice("art", "Art", ARTS), _whole("starting_xp", "Experience in the Art already", 0, 1_000_000),
                   _whole("level", "Summa level", 1, 100), _whole("quality", "Summa quality", 1, 100),
                   _whole("year", "Year", 1, 9999), _choice("season", "Season", SEASONS)],
        "result": [_whole("xp", "Experience gained", 1, 100), _whole("year", "Year", 1, 9999),
                   _choice("season", "Season", SEASONS)],
        "example": {"action": {"art": "ignem", "starting_xp": 13, "level": 5, "quality": 8, "year": 1220,
                               "season": "spring"},
                    "result": {"xp": 2, "year": 1220, "season": "spring"}},
        "notes": ["A season is three months: proposing one passes no time and reserves no season.",
                  "No virtues, flaws or interruptions; a summa cannot take an Art past its level."],
    },
}

#: Said with every profile and every check: what this is not.
NONCANONICAL = ("A checked action is a proposal only: no dice are rolled, nothing is resolved or recorded, no "
                "effect is applied and no time passes. Recorded rolls and resolution are later work (075, 090).")
NOT_PINNED = ("No campaign or chat selects a ruleset module: 062 keeps no module record and nothing pins one, so "
              "the checks below use 062's reference profiles.")


def reference_module(family: str) -> RulesetModule:
    edition = PROFILES[family]
    return RulesetModule(SourceRef(REFERENCE_SOURCE, "ruleset", family, edition), (ModuleBinding(family, edition),))


def _who(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    """The chat, the view and the cast members this view may propose an action for, with their names."""
    chat = chat_of(args)
    audience = audience_of(args)
    scene = hub_of(ctx).runner.open(chat)
    actors = []
    for actor in scene.cast:
        controller = scene.controller(actor)
        if controller is None:
            continue
        mine = controller == ctx.owner
        if audience == "director" or mine:
            actors.append({"actor": str(actor), "name": scene.names.get(actor),
                           "role": "npc" if controller == DIRECTOR else "player", "yours": mine})
    return chat, audience, actors


def _rulesets(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    chat, audience, actors = _who(ctx, args)
    profiles = [{"family": family, "edition": item["edition"], "title": item["title"],
                 "module": str(reference_module(family).ref), "action": item["action"], "result": item["result"],
                 "example": item["example"], "notes": item["notes"]} for family, item in CONTRACTS.items()]
    return {"chat": str(chat), "audience": audience, "pinned": None, "not_pinned": NOT_PINNED,
            "noncanonical": NONCANONICAL, "profiles": profiles, "actors": actors}


def _values(kind: type, raw: object, label: str) -> dict:
    """``raw`` as keyword arguments of ``kind``: exactly its fields; a list of dice becomes the tuple 062 takes."""
    if not isinstance(raw, Mapping):
        raise ApiRefused("invalid_field", f"{label} must be an object")
    names = [item.name for item in dataclasses.fields(kind)]
    required = [item.name for item in dataclasses.fields(kind)
                if item.default is dataclasses.MISSING and item.default_factory is dataclasses.MISSING]
    unknown = sorted(set(raw) - set(names))
    missing = [name for name in required if name not in raw]
    if unknown or missing:
        raise ApiRefused("invalid_field", f"{label} takes exactly {', '.join(names)}")
    return {name: tuple(value) if isinstance(value, list) else value for name, value in raw.items()}


def _shown(value: object) -> dict:
    return {item.name: list(getattr(value, item.name)) if isinstance(getattr(value, item.name), tuple)
            else getattr(value, item.name) for item in dataclasses.fields(value)}  # type: ignore[arg-type]


def _ruleset_check(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Check one proposed action and its recorded result against a reference profile: 062's answer, or its refusal
    (``invalid_proposal``, with 062's own words). Nothing is rolled, kept or applied."""
    chat, audience, actors = _who(ctx, args)
    family = args.get("family")
    if family not in CONTRACTS:
        raise ApiRefused("invalid_field", "family must be d20, wod, pbta or ars")
    value = args.get("actor")
    if type(value) is not str or value not in {item["actor"] for item in actors}:
        raise ApiRefused("not_your_character" if audience == "table" else "no_actor",
                         "propose only for a character of this chat's cast "
                         + ("you control" if audience == "table" else "that has a controller"))
    request_id = segment(args, "request_id") if args.get("request_id") is not None else "check"
    action_kind = ACTION_TYPES[family]
    module = reference_module(family)
    try:
        action = action_kind(**_values(action_kind, args.get("action"), "action"))
        result_kind = RESULT_TYPES[action_kind]
        result = result_kind(**_values(result_kind, args.get("result"), "result"))
        request = ActionRequest(request_id, InstanceRef.parse(value), module, module.bindings[0], action)
        proposal = propose(module, request, result)
    except ApiRefused:  # a request not shaped like the contract: refused by field, before 062 sees it
        raise
    except (ValueError, TypeError) as error:
        raise ApiRefused("invalid_proposal", str(error) or type(error).__name__) from None
    assert proposal is not None
    return {"chat": str(chat), "audience": audience, "valid": True, "canonical": False, "family": family,
            "edition": module.bindings[0].edition, "module": str(module.ref), "request": request_id,
            "actor": value, "action": _shown(proposal.request.action), "result": _shown(proposal.result),
            "noncanonical": NONCANONICAL}


__all__ = ["CONTRACTS", "NONCANONICAL", "REFERENCE_SOURCE", "reference_module"]
