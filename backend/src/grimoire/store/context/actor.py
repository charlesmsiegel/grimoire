"""Evidence boundaries for an assigned writer, before template rendering.

Presence is a half-open transcript interval. Unknown historical presence is
not proof of observation; legacy records receive only the latest player input.
Other cast cards and symmetric GM notes never constitute public knowledge.
"""
from __future__ import annotations

import re

from ... import prompts
from .. import entities, relationships
from ..appearances import paths
from . import pack


def ref(actor: dict) -> str:
    return f"{actor['kind']}:{actor['id']}"


def public_roster(cast: list[dict]) -> list[dict]:
    return [{"ref": ref(a), "name": a.get("name") or a["id"]} for a in cast]


def observed_history(cid: str, sid: str, actor_ref: str, history: list[dict]) -> list[dict]:
    record = paths.record(cid).get(actor_ref.replace(":", "/", 1), {})
    intervals = record.get("presence", {}).get(sid)
    if not isinstance(intervals, list):
        start = next((i for i in range(len(history) - 1, -1, -1)
                      if history[i].get("role") == "user"), len(history))
        return history[start:]
    valid = [r for r in intervals if isinstance(r, dict)
             and type(r.get("start")) is int and r["start"] >= 0
             and (r.get("end") is None or type(r.get("end")) is int)]
    return [m for i, m in enumerate(history) if any(
        r["start"] <= i and (r.get("end") is None or i < r["end"]) for r in valid)]


def known_entries(entries: list[dict], actor_ref: str | None) -> list[dict]:
    """Ownership attributes private lore; unowned public lore is shared."""
    return [e for e in entries
            if entities.normalize_secrecy(e.get("secrecy")) != entities.GM_ONLY
            and (actor_ref in (e.get("owners") or [])
                 or (not e.get("owners") and
                     entities.normalize_secrecy(e.get("secrecy")) == entities.PUBLIC))]


def own_relationships(cid: str, actor_ref: str | None, roster: list[dict]) -> list[str]:
    names = {a["ref"]: a["name"] for a in roster}
    data = relationships.read(cid)
    lines = []
    for key, feeling in data.get("feelings", {}).items():
        source, _, target = key.partition("->")
        if source == actor_ref and target in names:
            lines.append(prompts.render("snippets/feeling_line.j2",
                a=names[source], b=names[target], f=feeling))
    # Bonds are symmetric established facts, unlike another actor's feelings.
    for key, bond in data.get("bonds", {}).items():
        pair = key.split("|")
        if len(pair) == 2 and actor_ref in pair and all(r in names for r in pair):
            lines.append(prompts.render("snippets/bond_line.j2",
                a=names[pair[0]], b=names[pair[1]], bond=bond))
    return lines


def select_examples(text: str, cap: int, recent: str = "") -> str:
    """Keep whole authored exchanges, never a severed sentence or reply.

    The card format uses <START> to delimit exchanges. Undelimited text is
    one exchange. Large exchanges are skipped rather than sliced; smaller,
    relevant exchanges can still fit. Ties retain authored order.
    """
    text = text.strip()
    if not text:
        return ""
    marked = "<START>" in text
    units = [s.strip() for s in text.split("<START>") if s.strip()]
    words = set(re.findall(r"\w{4,}", recent.casefold()))
    ranked = sorted(enumerate(units), key=lambda pair: (
        -len(words & set(re.findall(r"\w{4,}", pair[1].casefold()))), pair[0]))
    selected: list[tuple[int, str]] = []
    used = 0
    for index, unit in ranked:
        block = ("<START>\n" if marked else "") + unit
        cost = len(block) + (2 if selected else 0)
        if used + cost <= cap:
            selected.append((index, block))
            used += cost
        if len(selected) == 3:
            break
    return "\n\n".join(block for _, block in sorted(selected))


def add_contract(sections: list[dict], data: dict) -> None:
    """Assigned response rules are mandatory regardless of the user's layout."""
    if not data.get("response_actor"):
        return
    text = prompts.render("scene/response_actor.j2", **data).strip()
    sections.append({"id": "response_actor", "label": "Assigned speaker", "text": text,
                     "tier": pack.LOCK_IN, "pinned": False, "heading": "", "heading_text": ""})
    # The NPC evidence is capped before rendering; protecting a narrator's
    # entire ensemble instead would grow an unbounded mandatory voice budget.
    if data["response_actor"]["ref"] != "grimoire":
        for section in sections:
            if section["id"] == "voice_examples":
                section["tier"] = pack.LOCK_IN
