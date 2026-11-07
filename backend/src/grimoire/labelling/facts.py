"""The facts a labeller may cite, and the prompt that offers them (035 milestone 2).

A labeller frames a player character's action for 029: which established facts favour, hinder or prevent
it, and what is at stake. Narrator fiat is a difficulty invented in the same breath as the ruling, so the
model is never asked to state a fact. It is offered a numbered list of facts that are already part of the
fiction, taken from the acting character's own 031 context pack, and may only cite them by number. A
factor's text, sequence and source come from that list, never from the reply.

The pack is the acting character's, so every fact offered is one their player may see: framing never
shows a player a secret. A hindrance the character does not know of is therefore never labelled (a
documented limit, docs/SCENES.md). Of the character's memories only perceptions and believed propositions
are offered: a suspicion or a rejected claim is not something the fiction has established for them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from grimoire.adjudication import MAX_FACTORS, MAX_STAKES, PROSE_MAX
from grimoire.context import PackedItem

#: Pack items that state a fact of the fiction. The identity line only names the actor.
_FACT_KINDS = ("description", "section", "perception", "proposition", "lore")
#: The most facts one prompt offers: a pack is already budgeted, and 029 records at most 32 factors.
MAX_FACTS = 64
#: How long a stake may be: one consequence, in a sentence.
MAX_STAKE_CHARS = 300


@dataclass(frozen=True, slots=True)
class Fact:
    """One fact a labeller may cite: ``fact_id`` is how it is cited (``f1``, ``f2``...).

    ``established`` is the campaign sequence the fact entered the fiction at (0 for authored setting and
    descriptions), and ``source`` names where it came from, as 029 records it: attribution, never authority.
    """

    fact_id: str
    text: str
    established: int
    source: str


def _source(item: PackedItem) -> str | None:
    origin = item.origin
    if origin.kind in ("description", "section"):
        return f"{origin.kind}:{origin.source}" if origin.source is not None else f"{origin.kind}:{origin.actor}"
    if origin.kind in ("perception", "proposition"):
        return f"{origin.kind}:{origin.record}" if origin.record else None
    if origin.kind == "lore":
        return f"lore:{origin.source}#{origin.name}" if origin.source is not None and origin.name else None
    return None


def facts(items: Iterable[PackedItem]) -> tuple[Fact, ...]:
    """The citable facts of a 031 pack, in pack order, numbered from 1 (at most ``MAX_FACTS``)."""
    found: list[Fact] = []
    for item in items:
        if len(found) >= MAX_FACTS:
            break
        if item.origin.kind not in _FACT_KINDS:
            continue
        if item.origin.kind == "proposition" and item.stance != "believed":
            continue  # a suspicion or a rejected claim is not part of the fiction for this actor
        if item.origin.kind == "lore" and item.audience not in (None, "common"):
            continue  # an actor's pack holds no director lore; never offer any
        source = _source(item)
        text = item.line.strip()
        if source is None or not text or len(text) > PROSE_MAX:
            continue
        sequence = item.sequence if type(item.sequence) is int and item.sequence > 0 else 0
        found.append(Fact(f"f{len(found) + 1}", text, sequence, source))
    return tuple(found)


_SYSTEM = (
    "You frame one action in a roleplay for a referee. You never decide whether it succeeds.\n"
    "Read the action and the numbered facts. Cite only facts from the list, by number, and say for each "
    "one whether it favours the action, hinders it, or prevents it outright. Leave out facts that do not "
    "bear on it. Then list what is at stake: the consequences a failure, or a success at a cost, could "
    "bring, one short sentence each. List nothing at stake if a failure would cost nothing.\n"
    f"Answer with JSON only, no other text: {{\"factors\": [{{\"fact\": \"f1\", \"bearing\": \"hinders\"}}], "
    f"\"stakes\": [\"...\"]}}. At most {MAX_FACTORS} factors and {MAX_STAKES} stakes, each stake at most "
    f"{MAX_STAKE_CHARS} characters. The bearing is \"favours\", \"hinders\" or \"prevents\"."
)


def label_messages(actor: str, action: str, offered: Sequence[Fact], scene: str = "") -> tuple[tuple[str, str], ...]:
    """The labeller's chat messages: ``(role, content)`` pairs. ``scene`` is what the actor heard lately:
    context for the model, never citable."""
    listed = "\n".join(f"{fact.fact_id}: {fact.text}" for fact in offered) or "(no facts)"
    parts = [f"Acting character: {actor}", f"Action: {action}"]
    if scene.strip():
        parts.append(f"Recent scene (context only, not citable):\n{scene}")
    parts.append(f"Facts:\n{listed}")
    return (("system", _SYSTEM), ("user", "\n\n".join(parts)))


__all__ = ["Fact", "MAX_FACTS", "MAX_STAKE_CHARS", "facts", "label_messages"]
