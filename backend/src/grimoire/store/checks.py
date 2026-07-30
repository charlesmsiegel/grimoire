"""Check resolution (#162, mechanics Phase 4): pure — RNG draw only, no writes.

`resolve_check` reads a campaign's sheet + bound module pack, substitutes the
sheet's numeric/derived values into the check's roll formula, draws the dice,
and grades the result against an outcome ladder. Nothing here appends to
rolls.py or proposals.py -- callers own the write path so a resolution can be
previewed, retried, or discarded without side effects.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase4-play-integration-design.md.
"""

from __future__ import annotations

import re

from . import (characters, dice, entities, expressions,
               locks, modules, overlay, pcs)
from .appearances import cast as appearances_cast, paths as appearances_paths, versions as appearances_versions
from .sheets import paths as sheets_paths, reader as sheets_reader, schema as sheets_schema


class CheckError(Exception):
    """A check can't be resolved (unknown check, missing/invalid sheet,
    ungated sheet type, bad formula, or bad dice notation)."""


_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def roll_scope(result: dict) -> dict:
    """Expression scope for outcome `when` clauses. Absent values are
    omitted (not None) so a `when` that references them is treated as
    "doesn't apply to this roll shape" rather than evaluated against a
    sentinel."""
    scope: dict = {}
    dice_list = result.get("dice") if isinstance(result.get("dice"), list) else []
    scope["dice"] = len(dice_list)
    raw = [r for d in dice_list if isinstance(d, dict)
           for r in (d.get("rolls") or []) if isinstance(r, int)]
    scope["ones"] = raw.count(1)
    if dice_list and isinstance(dice_list[0], dict):
        first = dice_list[0].get("rolls") or []
        if first and isinstance(first[0], int):
            scope["natural"] = first[0]
    if isinstance(result.get("total"), int):
        scope["total"] = result["total"]
        if isinstance(result.get("vs"), int):
            scope["margin"] = result["total"] - result["vs"]
    if isinstance(result.get("successes"), int):
        scope["successes"] = result["successes"]
    return scope


def evaluate_tier(check_def: dict, defaults: dict, scope: dict) -> tuple[str | None, list[str]]:
    """First matching outcome label, or (None, warnings).

    Fallback semantics: a check-level `outcomes` ladder, if present, is used
    exclusively -- the `_defaults` ladder is only consulted when the check
    defines none of its own. A tier whose `when` references a roll-scope name
    that simply doesn't apply to this roll (e.g. `natural` on a pool roll) is
    silently skipped -- module validation already guarantees every `when`
    only references ``modules.ROLL_SCOPE_NAMES``, so that's an expected,
    per-roll absence, not a bug. A name outside that vocabulary is a genuine
    error and produces a warning instead.
    """
    warnings: list[str] = []
    for source in (check_def.get("outcomes"), (defaults or {}).get("outcomes")):
        if not isinstance(source, list):
            continue
        for tier in source:
            if not isinstance(tier, dict):
                continue
            label, when = tier.get("label"), tier.get("when", "")
            try:
                needed = expressions.names(when)
            except expressions.ExpressionError as e:
                warnings.append(f"{label}: {e}")
                continue
            if not needed <= scope.keys():
                if needed - set(modules.ROLL_SCOPE_NAMES):
                    warnings.append(f"{label}: unknown name(s) in {when!r}")
                continue  # a known roll-scope name absent this roll: skip quietly
            try:
                if expressions.evaluate(when, scope):
                    return label, warnings
            except expressions.ExpressionError as e:
                warnings.append(f"{label}: {e}")
        return None, warnings          # a present ladder that matched nothing
    return None, warnings


def _actor_label(cid: str, kind: str, eid: str) -> str:
    """Display name for an actor/entity reference; falls back to the id."""
    if kind in appearances_paths.ACTOR_KINDS:
        vid = appearances_versions.locked_version(cid, kind, eid)
        if vid is not None:
            name = appearances_cast._actor_name(appearances_paths.locked_actor_root(cid), kind, eid, vid)
            return name or eid
        # Never appeared: no locked version (and possibly no campaign copy).
        # A None vid must not reach read_card/read_persona (TypeError); the
        # container meta carries the name without needing any version.
        try:
            root = overlay.actor_root(cid, kind, eid)
            if kind == "pcs":
                return pcs.read_pc(root, eid)["meta"].get("name") or eid
            return characters.read_character(root, eid)["meta"].get("name") or eid
        except (pcs.PCNotFound, pcs.PCVersionNotFound,
                characters.CharacterNotFound, characters.VersionNotFound):
            return eid
    try:
        return overlay.read_entity(cid, kind, eid)["meta"].get("name") or eid
    except entities.EntityNotFound:
        return eid


def resolve_check(cid: str, check_id: str, actor_ref: str, difficulty: int | None = None,
                  modifier: int = 0, seed: int | None = None) -> dict:
    with locks.campaign_lock(cid):
        mid = modules.resolve(cid)
        if mid is None:
            raise CheckError("no mechanics module is bound to this campaign")
        pack = modules.load_pack(mid)
        checks_def = pack["checks"] if isinstance(pack["checks"], dict) else {}
        check = checks_def.get(check_id)
        if check_id == "_defaults" or not isinstance(check, dict):
            raise CheckError(f"unknown check {check_id!r}")
        defaults = checks_def.get("_defaults")
        defaults = defaults if isinstance(defaults, dict) else {}

        kind, sep, eid = (actor_ref or "").partition(":")
        if not sep or kind not in sheets_paths.FILE_KINDS:
            raise CheckError(f"bad actor reference {actor_ref!r}")
        sheet = sheets_reader.read(cid, kind, eid)
        if sheet is None:
            raise CheckError(f"{actor_ref} has no sheet")
        if sheet["errors"]:
            raise CheckError(f"{actor_ref}'s sheet is invalid: {sheet['errors'][0]}")

        sheets_def = pack["sheets"] if isinstance(pack["sheets"], dict) else {}
        st = sheets_def.get("sheet_types", {}).get(sheet["sheet_type"])
        st = st if isinstance(st, dict) else {}
        st_groups = set(st.get("groups", []) if isinstance(st.get("groups"), list) else [])
        required = check.get("requires", []) if isinstance(check.get("requires"), list) else []
        missing = [g for g in required if g not in st_groups]
        if missing:
            raise CheckError(f"{actor_ref}'s sheet type lacks required groups: {missing}")

        if difficulty is None:
            difficulty = check.get("difficulty", defaults.get("difficulty"))

        scope = dict(sheets_schema.expression_scope(sheet, sheets_def))
        scope["modifier"] = modifier if isinstance(modifier, int) and not isinstance(modifier, bool) else 0
        if isinstance(difficulty, int) and not isinstance(difficulty, bool):
            scope["difficulty"] = difficulty

        def sub(m):
            try:
                return str(int(expressions.evaluate(m.group(1), scope)))
            except expressions.ExpressionError as e:
                raise CheckError(f"check formula: {e}")

        roll_template = check.get("roll", "")
        roll_template = roll_template if isinstance(roll_template, str) else ""
        notation = _PLACEHOLDER.sub(sub, roll_template)
        try:
            result = dice.roll(notation, seed)
        except dice.DiceError as e:
            raise CheckError(f"bad roll notation {notation!r}: {e}")

        tier, tier_warnings = evaluate_tier(check, defaults, roll_scope(result))
        tier = tier or result.get("outcome")

        return {"check": check_id, "check_label": check.get("label", check_id),
                "actor": actor_ref, "actor_label": _actor_label(cid, kind, eid),
                "notation": notation, "result": result, "tier": tier,
                "difficulty": difficulty, "modifier": scope["modifier"],
                "tier_warnings": tier_warnings}


def available_checks(cid: str, sid: str) -> list[dict]:
    """Sheeted scene cast + sheeted current location, each with the check ids
    their sheet type is gated for. Uses the same current-location source as
    context._assemble (scenes.get_location_history's last entry)."""
    mid = modules.resolve(cid)
    if mid is None:
        return []
    pack = modules.load_pack(mid)
    checks_def = pack["checks"] if isinstance(pack["checks"], dict) else {}
    sheets_def = pack["sheets"] if isinstance(pack["sheets"], dict) else {}
    sheet_types = sheets_def.get("sheet_types", {})

    def entry(kind: str, eid: str, label: str) -> dict | None:
        sheet = sheets_reader.read(cid, kind, eid)
        if sheet is None or sheet["errors"]:
            return None
        st = sheet_types.get(sheet["sheet_type"])
        st = st if isinstance(st, dict) else {}
        st_groups = set(st.get("groups", []) if isinstance(st.get("groups"), list) else [])
        options = [[check_id, check.get("label", check_id)]
                   for check_id, check in checks_def.items()
                   if check_id != "_defaults" and isinstance(check, dict)
                   and set(check.get("requires", []) if isinstance(check.get("requires"), list) else [])
                   <= st_groups]
        return {"ref": f"{kind}:{eid}", "label": label,
                "sheet_type": sheet["sheet_type"], "checks": options}

    out: list[dict] = []
    for actor in appearances_cast.scene_cast(cid, sid):
        e = entry(actor["kind"], actor["id"], actor["name"])
        if e is not None:
            out.append(e)

    from . import scenes  # function-level: avoid import-order surprises
    history_ids = scenes.get_location_history(cid, sid)
    current_loc = history_ids[-1] if history_ids else None
    if current_loc:
        try:
            loc = overlay.read_entity(cid, "locations", current_loc)
        except entities.EntityNotFound:
            loc = None
        if loc is not None:
            e = entry("locations", current_loc, loc["meta"].get("name", current_loc))
            if e is not None:
                out.append(e)
    return out


_DICE_HEAD_RE = re.compile(r"^\U0001F3B2 `.*?` → ")


def roll_label(resolution: dict) -> str:
    """The roll log's label for a resolved check — the same "actor — check"
    head the 🎲 transcript line uses, shared so the two can never drift."""
    return f"{resolution['actor_label']} — {resolution['check_label']}"


def format_check_roll(resolution: dict) -> str:
    """The 🎲 transcript line for a resolved check; delegates the dice
    segment to `dice.format_roll` rather than reimplementing it."""
    segment = _DICE_HEAD_RE.sub("", dice.format_roll(resolution["result"]), count=1)
    head = f"\U0001F3B2 **{roll_label(resolution)}"
    if resolution.get("difficulty") is not None:
        head += f" (diff {resolution['difficulty']})"
    head += f":** {segment}"
    if resolution.get("tier"):
        head += f" · *{resolution['tier']}*"
    return head
