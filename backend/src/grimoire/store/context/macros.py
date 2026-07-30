"""Macro expansion: the choke point every piece of prompt text passes through.

`expand_macros` resolves {{user}}/{{char}}, the scene's {{date}}/{{weekday}}/
{{time}}, {{random:...}} and {{roll:...}}, then drops whatever is left. The
substitutions come from the scene's cast (the appearance record) and the
campaign's primary calendar, which is why this file reads the store at all.
"""

from __future__ import annotations

import random
import re

from .. import calendars, characters, dice, pcs
from ..appearances import (cast as appearances_cast, paths as appearances_paths,
                           versions as appearances_versions)
from ..campaigns import paths as campaigns_paths
from ..scenes import read as scenes_read
# Aliased to match `assemble.py` and `world_state.py`, where the plain name
# `cast` is taken by a local holding the scene's cast list.
from . import cast as cast_data


def _substitute(text: str, subs: dict[str, str]) -> str:
    for token, value in subs.items():
        if value:  # only replace when non-empty, so player-less scenes keep {{user}} literal
            # function replacement => `value` is inserted literally (no backslash/group expansion)
            text = re.sub(re.escape(token), lambda _m, v=value: v, text, flags=re.IGNORECASE)
    return text


def scene_substitutions(cid: str, sid: str) -> dict[str, str]:
    """Token map for a scene's current cast: {{user}} -> player names. {{char}}
    is NOT resolved here (#137): with more than one NPC present, "the whole
    present cast" is ambiguous (which one?), so {{char}} is baked to a card's/
    greeting's own name at creation time (cards.bake_char_name,
    greetings.create_greeting) instead -- any {{char}} still unresolved at
    prompt time stays literal, same as an unresolved {{user}}."""
    aroot = appearances_paths.locked_actor_root(cid)
    player_names: list[str] = []
    for a in appearances_cast.scene_cast(cid, sid):
        vid = appearances_versions.locked_version(cid, a["kind"], a["id"])
        try:
            if a["role"] == "npc":
                continue
            elif a["kind"] == "pcs":
                player_names.append(pcs.read_persona(aroot, a["id"], vid).get("name", a["id"]))
            else:
                player_names.append(characters.read_card(aroot, a["id"], vid)["data"].get("name", a["id"]))
        except (characters.CharacterNotFound, characters.VersionNotFound,
                pcs.PCNotFound, pcs.PCVersionNotFound):
            continue
    if not any(player_names) and scenes_read.is_pcless(cid, sid):
        player_names = cast_data._campaign_player_refs(cid, aroot)[1]
    return {"{{user}}": ", ".join(n for n in player_names if n)}


_LITERAL_MACROS = {"{{user}}", "{{char}}"}  # kept raw when unresolved (e.g. no NPCs/players yet)
_RANDOM_MACRO = re.compile(r"\{\{random:([^{}]*)\}\}", re.IGNORECASE)
_ROLL_MACRO = re.compile(r"\{\{roll:([^{}]*)\}\}", re.IGNORECASE)
_MACRO_TOKEN = re.compile(r"\{\{[^{}]*\}\}")


def _datetime_subs(cid: str, sid: str) -> dict[str, str]:
    """{{date}}/{{weekday}}/{{time}} from the scene's current native datetime, via
    the campaign's primary calendar. {} when the scene has no time yet or the
    stored datetime no longer parses -- _substitute then leaves the tokens
    literal for _strip_unknown_macros to drop."""
    history = scenes_read.get_time_history(cid, sid)
    if not history:
        return {}
    native = history[-1]
    try:
        provider = calendars.get_provider(calendars.read_calendar(campaigns_paths.campaign_root(cid))["primary"])
        desc = provider.describe(calendars.fixed_of(provider, native))
    except calendars.CalendarError:
        return {}
    _, time_str = calendars.split_native(native)
    return {"{{date}}": desc["friendly"], "{{weekday}}": desc["weekday_name"],
            "{{time}}": time_str or ""}


def _expand_random(text: str) -> str:
    def repl(m: re.Match) -> str:
        options = [o.strip() for o in m.group(1).split(",") if o.strip()]
        return random.choice(options) if options else m.group(0)
    return _RANDOM_MACRO.sub(repl, text)


def _expand_rolls(text: str) -> str:
    def repl(m: re.Match) -> str:
        try:
            result = dice.roll(m.group(1).strip())
        except dice.DiceError:
            return m.group(0)  # malformed notation -> left for _strip_unknown_macros
        return str(result["total"] if result["total"] is not None else result["successes"])
    return _ROLL_MACRO.sub(repl, text)


def _strip_unknown_macros(text: str) -> str:
    def repl(m: re.Match) -> str:
        # {{user}}/{{char}} stay literal (existing _substitute contract); any
        # other macro this scene can't resolve is dropped rather than leaked
        # raw into the model.
        return m.group(0) if m.group(0).lower() in _LITERAL_MACROS else ""
    return _MACRO_TOKEN.sub(repl, text)


def expand_macros(text: str, subs: dict[str, str], cid: str, sid: str) -> str:
    """The single choke point all prompt text flows through: `subs` ({{user}}/
    {{char}}, caller-supplied) plus {{date}}/{{time}}/{{weekday}} (scene calendar)
    substitute literally; {{random:a,b,...}} and {{roll:<dice.py notation>}} expand
    per-occurrence (dice.py's full grammar -- NdM, keep/drop, exploding, pools,
    vs-target); anything left over is an unresolved macro and gets dropped so raw
    tokens never reach the model -- except {{user}}/{{char}}, which stay literal
    per _substitute's existing contract."""
    text = _substitute(text, {**subs, **_datetime_subs(cid, sid)})
    text = _expand_random(text)
    text = _expand_rolls(text)
    return _strip_unknown_macros(text)
