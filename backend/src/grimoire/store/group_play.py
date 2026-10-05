"""Group play: who speaks, decided without touching the store.

A scene's group-play settings (`parse`/`validate`/`dump`) and the planner that
turns them, the present non-player cast and a round's trigger text into the
speakers for that round (`plan_post`, `plan_continue`, `next_planned`).

PURE: nothing here reads or writes the store, the clock or the filesystem, and
all randomness arrives through an injected `random.Random`. That is why this
module has no entry in `store/locks.py` -- it mutates no campaign state. The
caller (the character-turn engine) stores the result on the round record so a
retry, a recovery or a roll resume continues the same plan and never re-rolls.

A roster entry is `{"ref": "kind:id", "name": str}`, the shape
`routes.character_turns.roster` returns. `"grimoire"` is never in the roster
and is always available as a speaker: it is the narrator.

Names are mapped to refs through each entry's `name`. Two entries sharing a
name make that name ambiguous, which `speaker.mentioned` already reads as
"names nobody", so a duplicate can never be summoned by a mention -- it is
still planned, by roll or by silence, like anyone else.
"""

from __future__ import annotations

import json
import random

from .context import speaker

ORDERS = ("directed", "manual", "list", "natural")
DEFAULT_ORDER = "directed"
DEFAULT_TALKATIVENESS = 50
MAX_AUTO_ROUNDS = 5
GRIMOIRE = "grimoire"

_KEYS = ("order", "order_list", "sitting_out", "auto_rounds", "talkativeness")


def _in_range(v: object, hi: int) -> bool:
    # bool is an int subclass; `true` is not a talkativeness.
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= hi


def _defaults() -> dict:
    return {"order": DEFAULT_ORDER, "order_list": [], "sitting_out": [],
            "auto_rounds": 0, "talkativeness": {}}


def _refs(v: object) -> list[str]:
    """Non-empty string refs of a list, each once, first occurrence kept."""
    if not isinstance(v, list):
        return []
    return list(dict.fromkeys(r for r in v if isinstance(r, str) and r))


def parse(raw: str) -> dict:
    """The settings a stored string holds. Lenient: always all five keys, and
    each bad key falls back to its own default rather than failing the rest."""
    out = _defaults()
    try:
        data = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict):
        return out
    if data.get("order") in ORDERS:
        out["order"] = data["order"]
    out["order_list"] = _refs(data.get("order_list"))
    out["sitting_out"] = [r for r in _refs(data.get("sitting_out")) if r != GRIMOIRE]
    rounds = data.get("auto_rounds")
    if _in_range(rounds, MAX_AUTO_ROUNDS):
        out["auto_rounds"] = rounds
    talk = data.get("talkativeness")
    if isinstance(talk, dict):
        out["talkativeness"] = {r: v for r, v in talk.items()
                                if isinstance(r, str) and r and _in_range(v, 100)}
    return out


def settings_of(meta: dict) -> dict:
    return parse(meta.get("group_play", ""))


def validate(body: dict) -> dict:
    """The settings a request body asks for. Strict: `ValueError` names the
    first thing wrong. A missing key takes its default; list refs are deduped,
    keeping the first occurrence."""
    if not isinstance(body, dict):
        raise ValueError("group play settings must be an object")
    out = _defaults()
    order = body.get("order", DEFAULT_ORDER)
    if order not in ORDERS:
        raise ValueError(f"order must be one of {', '.join(ORDERS)}")
    out["order"] = order
    for key in ("order_list", "sitting_out"):
        v = body.get(key, [])
        if not isinstance(v, list) or any(not isinstance(r, str) or not r for r in v):
            raise ValueError(f"{key} must be a list of non-empty refs")
        out[key] = list(dict.fromkeys(v))
    if GRIMOIRE in out["sitting_out"]:
        raise ValueError("grimoire cannot sit out")
    rounds = body.get("auto_rounds", 0)
    if not _in_range(rounds, MAX_AUTO_ROUNDS):
        raise ValueError(f"auto_rounds must be an integer 0..{MAX_AUTO_ROUNDS}")
    out["auto_rounds"] = rounds
    out["talkativeness"] = _valid_talkativeness(body.get("talkativeness", {}))
    return out


def _valid_talkativeness(talk: object) -> dict:
    if not isinstance(talk, dict):
        raise ValueError("talkativeness must be an object")
    for ref, v in talk.items():
        if not isinstance(ref, str) or not ref:
            raise ValueError("talkativeness keys must be non-empty refs")
        if not _in_range(v, 100):
            raise ValueError("talkativeness must be an integer 0..100")
    return dict(talk)


def dump(settings: dict) -> str:
    return json.dumps(settings, sort_keys=True, separators=(",", ":"))


def available(settings: dict, roster: list[dict]) -> list[dict]:
    """The roster minus whoever is sitting out, in roster order."""
    out = set(settings["sitting_out"])
    return [e for e in roster if e["ref"] not in out]


def _name(entry: dict) -> str:
    n = entry.get("name")
    return n.strip() if isinstance(n, str) else ""


def _named(text: str, entries: list[dict]) -> list[str]:
    """Refs of the entries `text` names, in order of first mention."""
    by_name: dict[str, str] = {}
    for e in entries:
        by_name.setdefault(_name(e), e["ref"])
    # Duplicates stay in the list on purpose: that is what makes their name
    # ambiguous, and an ambiguous name is one `mentioned` never returns.
    names = speaker.mentioned(text, [_name(e) for e in entries])
    return [by_name[n] for n in names if n in by_name]


def _by_silence(entries: list[dict], history: list[dict]) -> list[str]:
    """Every entry's ref, the longest-silent first (roster order breaks ties,
    and stands in for an entry whose name is blank)."""
    refs_of: dict[str, list[str]] = {}
    for e in entries:
        refs_of.setdefault(_name(e), []).append(e["ref"])
    ranked = speaker.quietest([n for n in refs_of if n], history)
    out = [r for n in ranked for r in refs_of[n]]
    return out + refs_of.get("", [])


def _talk(settings: dict, ref: str) -> int:
    return settings["talkativeness"].get(ref, DEFAULT_TALKATIVENESS)


def _directed_talk(settings: dict, ref: str) -> int:
    """Directed's talkativeness: an UNSET ref is 100, always kept. Only a ref
    the player gave a value filters, so saving any other setting (one sit-out
    toggle) never starts dropping every unnamed character from the selector.
    Natural keeps the shared default (`_talk`)."""
    return settings["talkativeness"].get(ref, 100)


def _list_sequence(settings: dict, avail: list[dict]) -> list[str]:
    here = {e["ref"] for e in avail}
    head = [r for r in settings["order_list"] if r in here or r == GRIMOIRE]
    rest = [e["ref"] for e in avail if e["ref"] not in head]
    return (head + rest) or [GRIMOIRE]


def _natural_sequence(settings: dict, avail: list[dict], text: str, history: list[dict],
                      rng: random.Random, author: str | None = None) -> list[str]:
    if not avail:
        return [GRIMOIRE]
    # A character naming themselves does not summon themselves.
    named = [r for r in _named(text, avail) if r != author]
    rest = [e["ref"] for e in avail if e["ref"] not in named]
    rng.shuffle(rest)
    order = named + [r for r in rest if rng.random() * 100 < _talk(settings, r)]
    if order:
        return order
    others = [e for e in avail if e["ref"] != author] or avail
    return [_by_silence(others, history)[0]]


def _directed(settings: dict, roster: list[dict], avail: list[dict], trigger: str,
              history: list[dict], rng: random.Random, force: tuple[str, ...],
              author: str | None) -> list[dict]:
    named = set(_named(trigger, avail)) - {author}
    # Every available entry rolls, named or not and set or not (an unset 100
    # always passes: `random()` is below 1), so the draw count depends on the
    # cast alone and a seeded rng reads the same however the post words it or
    # whichever values the player has set.
    rolls = {e["ref"]: rng.random() * 100 < _directed_talk(settings, e["ref"]) for e in avail}
    # An explicit pick wins over sitting out: a forced ref stays eligible.
    eligible = [e for e in roster
                if e["ref"] in force
                or (e["ref"] in rolls and (e["ref"] in named or rolls[e["ref"]]))]
    if eligible or not avail:
        return eligible
    silence = _by_silence(avail, history)
    keep = max(avail, key=lambda e: (_directed_talk(settings, e["ref"]),
                                     -silence.index(e["ref"])))
    return [keep]


def plan_post(settings: dict, roster: list[dict], *, trigger: str, history: list[dict],
              rng: random.Random, force: tuple[str, ...] = (),
              author: str | None = None) -> dict:
    """The speakers for an automatic round answering a player post.

    `{"mode", "eligible", "actor_ref", "plan"}`. Directed and Manual name no
    actor: Directed hands the selector a filtered `eligible`, Manual generates
    nothing. List and Natural name the lead (`actor_ref`) and the refs after it
    (`plan`), with `force` refs removed because the caller supplies that lead.
    `author` is whoever wrote the trigger text (a character, in an auto round);
    their own name in it never counts as naming them. A `force` ref stays
    eligible under Directed even while sitting out.
    """
    mode = settings["order"]
    avail = available(settings, roster)
    if mode == "manual":
        return {"mode": mode, "eligible": roster, "actor_ref": None, "plan": []}
    if mode == "directed":
        return {"mode": mode, "actor_ref": None, "plan": [],
                "eligible": _directed(settings, roster, avail, trigger, history, rng,
                                      force, author)}
    if mode == "list":
        order = _list_sequence(settings, avail)
    else:
        order = _natural_sequence(settings, avail, trigger, history, rng, author)
    order = [r for r in order if r not in force]
    return {"mode": mode, "eligible": roster,
            "actor_ref": order[0] if order else None, "plan": order[1:]}


def plan_continue(settings: dict, roster: list[dict], *, last: dict | None,
                  history: list[dict], rng: random.Random) -> str | None:
    """The one speaker for an empty send, or None to leave it to the selector
    (Directed and Manual, and Natural when nobody but `last` is available).

    `last` is `{"ref": str | None, "text": str}`, the newest non-synthetic
    contribution.
    """
    mode = settings["order"]
    avail = available(settings, roster)
    if mode == "list":
        seq = _list_sequence(settings, avail)
        ref = last.get("ref") if last else None
        # Anchor in the full configured order, so a last speaker who has since
        # sat out still has a place to continue from.
        full = list(dict.fromkeys(settings["order_list"] + [e["ref"] for e in roster]))
        if ref in full:
            ok = set(seq)
            for i in range(1, len(full) + 1):
                cand = full[(full.index(ref) + i) % len(full)]
                if cand in ok:
                    return cand
        return seq[0]
    if mode == "natural":
        # Never the speaker of that contribution, and never counted as named
        # by their own words: leave them out of the pool entirely.
        ref = last.get("ref") if last else None
        others = [e for e in avail if e["ref"] != ref]
        if not others:
            return None
        text = (last or {}).get("text") or ""
        return _natural_sequence(settings, others, text, history, rng)[0]
    return None


def next_planned(settings: dict, roster: list[dict], plan: list[str]
                 ) -> tuple[str | None, list[str]]:
    """The first planned ref still able to speak, and what follows it. A ref
    that has left or started sitting out since the plan was made is skipped."""
    here = {e["ref"] for e in available(settings, roster)}
    for i, ref in enumerate(plan):
        if ref == GRIMOIRE or ref in here:
            return ref, plan[i + 1:]
    return None, []
