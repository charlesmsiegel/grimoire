"""A proposition's thread as the table may read it (081): only what the player's own characters know.

Spoiler exposure is controlled by building the table's thread from 012's ``player_knowledge`` alone, never by
hiding parts of the director's: the reports the player's characters received (as each holder has them: who they
take the source to be, never who really sent it), the reports they knowingly sent, their beliefs, and what they
observed that those beliefs cite (their own percept), each graded ``proposal`` or ``belief``. An NPC-era belief
the present player did not author is kept and marked ``inherited`` (012), never shown as theirs. There is no outcome: canon's standing, rulings, events, other characters' evidence
and every contradiction with canon are the director's. A proposition none of the player's characters heard of or
holds a belief about has no table thread at all (``unknown_proposition``), so even its existence stays the
director's.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.knowledge import Knowledge, KnowledgeError
from grimoire.core.perspective import ActorKnowledge, KnownBelief, PlayerKnowledge

from .model import Entry, Summary, Thread
from .thread import EMPTY, order, ranked, scenes_of


def _beliefs(known: ActorKnowledge, chat_of) -> list[tuple[KnownBelief, Entry]]:  # type: ignore[no-untyped-def]
    groups: list[tuple[Iterable[KnownBelief], bool, bool]] = [
        (known.current, True, False), (known.history, False, False),
        (known.inherited or (), True, True), (known.inherited_history or (), False, True)]
    return [(item, Entry("belief", f"belief:{item.belief_id}", "belief", item.moment, item.text, (known.actor,),
                         chat_of("belief", item.belief_id), holder=known.actor, stance=item.stance, current=current,
                         inherited=inherited, supersedes=item.supersedes))
            for items, current, inherited in groups for item in items]


def _entries(player: PlayerKnowledge, origins: Mapping[str, ChatRef]) -> dict[str, tuple[str, list[Entry]]]:
    """Every proposition the player's characters know of: its text as they have it, and their entries."""
    def chat_of(family: str, record_id: str) -> ChatRef | None:
        return origins.get(f"{family}:{record_id}")

    found: dict[str, tuple[str, list[Entry]]] = {}

    def add(proposition_id: str, text: str, entry: Entry) -> None:
        found.setdefault(proposition_id, (text, []))[1].append(entry)

    for known in player.actors:
        for item in known.evidence:
            if item.kind != "report" or item.proposition_id is None:
                continue
            people = (known.actor,) if item.attributed_to is None else (known.actor, item.attributed_to)
            add(item.proposition_id, item.content,
                Entry("report", f"evidence:{item.evidence_id}", "proposal", item.moment, item.content, people,
                      chat_of("evidence", item.evidence_id), holder=known.actor, attributed_to=item.attributed_to,
                      confirmed=True))
        for sent in known.sent:
            # the sender is the table's own character: who passed it on, to whom (as a director ``sent`` entry)
            add(sent.proposition_id, sent.text,
                Entry("sent", f"evidence:{sent.evidence_id}", "proposal", sent.sent_at, sent.text,
                      (known.actor, sent.recipient), chat_of("evidence", sent.evidence_id), holder=sent.recipient,
                      sender=known.actor))
        seen = {item.evidence_id: item for item in known.evidence if item.kind == "perception"}
        for item, entry in _beliefs(known, chat_of):
            add(item.proposition_id, item.text, entry)
            # what this character saw that their belief cites: their own percept, never the event as canon has it
            for cited in item.evidence_ids:
                percept = seen.get(cited)
                if percept is not None:
                    add(item.proposition_id, item.text,
                        Entry("observation", f"evidence:{cited}", "proposal", percept.moment, percept.content,
                              (known.actor,), chat_of("evidence", cited), holder=known.actor, confirmed=True))
    return found


def _thread(knowledge: Knowledge, player: PlayerKnowledge, proposition_id: str, text: str,
            entries: list[Entry]) -> Thread:
    # one entry per record and kind: a report one of the player's characters sent another is both a send and a
    # receipt, and the table holds both
    entries = sorted({(entry.kind, entry.record): entry for entry in entries}.values(), key=order)
    # the proposition record carries its text and id to the view; its moment, provenance and ``about`` are the
    # director's and are never shown in a table thread
    return Thread(knowledge.proposition(proposition_id), player.as_of, tuple(entries), None, (), scenes_of(entries))


def table_thread(knowledge: Knowledge, player: PlayerKnowledge, proposition_id: str, *,
                 origins: Mapping[str, ChatRef] = EMPTY) -> Thread:
    """The table's thread of one proposition: see the module docstring. ``player`` is 012's ``player_knowledge``
    over ``knowledge``; ``origins`` 020's provenance map (a chat is shown only beside an entry the table holds)."""
    found = _entries(player, origins).get(proposition_id)
    if found is None:
        raise KnowledgeError("unknown_proposition")
    return _thread(knowledge, player, proposition_id, *found)


def table_search(knowledge: Knowledge, player: PlayerKnowledge, *, query: object = None,
                 actor: InstanceRef | None = None, origins: Mapping[str, ChatRef] = EMPTY,
                 limit: int = 50) -> tuple[tuple[Summary, ...], int]:
    """Every table thread that matches ``query`` (in the text as the table has it) and involves ``actor``."""
    found = _entries(player, origins)
    return ranked((_thread(knowledge, player, key, *value) for key, value in sorted(found.items())), query, actor,
                  limit)


__all__ = ["table_search", "table_thread"]
