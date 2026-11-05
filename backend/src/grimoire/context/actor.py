"""Candidates for one actor's context, chosen by audience rules, then packed (031).

Who may see what is decided here, before any ranking or budget, and only from inputs the actor is
entitled to:

1. **Only active bindings.** Every source comes from the snapshot's selections, which for a chat are
   its active bindings (022, 107). A world is provenance only: nothing in its catalog is read unless
   it is bound. Detached and unbound sources never contribute.
2. **A card is its own actor's.** A character or persona source's composed name, description and
   sections (022's ``effective``, so a cleared section never appears) reach only the actor bound to
   it. Another actor's card, an absent antagonist's secret included, never reaches this pack, and a
   character source is never read for lore: lore embedded in a card waits until 037/027 give it an
   audience.
3. **Memory is the actor's own projection.** Items come from 012's ``actor_knowledge`` as of the
   moment: confirmed perceptions and held propositions with the current stance. Never events,
   rulings, standing, true senders, unconfirmed evidence or other actors' records. 012's
   ``knowledge_about`` and 017's ``claims_about`` for the actor and the present cast only **rank and
   annotate** items the actor already holds. The director's graph is never consulted, so it cannot
   choose, rank or trigger anything here.
4. **Lore is ``common`` entries of ``lorebook`` and ``scenario`` bindings only**, read from the
   selected revision (never an ancestor) by a reader for its schema, and only those that are
   ``constant`` or whose key occurs in what the actor perceived: the caller's ``scene_text`` plus the
   texts of the actor's own held memory.

Rendered lines carry no record ids (012: ids are not secrecy-safe text) and no other actor's name;
provenance sits beside each line as structured data. What the rules withheld is not reported, since
that is the director's.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from grimoire.core.entity import EntityError
from grimoire.core.identity import InstanceRef
from grimoire.core.index import MissingDefinition
from grimoire.core.knowledge import Knowledge, Subject
from grimoire.core.perspective import ActorKnowledge, actor_knowledge
from grimoire.core.portrayal import PortrayalError
from grimoire.core.snapshot import EffectiveSnapshot, Resolution
from grimoire.graph import PREDICATES, ClaimSet

from .basis import knowledge_digest, selection_basis
from .lore import LORE_ROLES, Unpack, check_readers, lore, lore_problems
from .model import (
    MAX_PRESENT, MAX_SCENE_TEXT, Budget, ClaimReading, ContextError, ContextPack, EntryReader, Origin,
    SourceProblem, TokenCounter, estimate_tokens,
)
from .pack import Candidate, pack
from .trigger import SceneWords

#: A common entry is what anyone in the setting may know: the only lore an actor's pack holds.
ACTOR_AUDIENCES = frozenset({"common"})
MEASUREMENTS = frozenset({"estimated", "counted"})
#: 014/027 own voice anchors and their screening; a card's voice section is theirs, not context.
DEFAULT_EXCLUDED_SECTIONS = frozenset({"voice-evidence"})
_STANCE_LEAD = {"believed": "You believe", "suspected": "You suspect", "rejected": "You do not believe"}


def _fail(message: str, reason: str, detail: str | None = None) -> ContextError:
    return ContextError(message, reason, detail)


def _check(knowledge: object, actor: object, as_of: object, budget: object, present: object,
           scene_text: object, readers: object, measurement: object, claims: object,
           excluded: object) -> tuple[tuple[InstanceRef, ...], frozenset[str]]:
    if not isinstance(knowledge, Knowledge):
        raise TypeError(f"expected a Knowledge ledger, not {type(knowledge).__name__}")
    if not isinstance(actor, InstanceRef):
        raise _fail("the audience must be an InstanceRef", "invalid_audience")
    if actor.campaign_id != knowledge.campaign_id:
        raise _fail("the audience belongs to another campaign than the ledger", "campaign_mismatch")
    if type(as_of) is not int or as_of < 0:
        raise _fail("as_of must be a non-negative int", "invalid_as_of")
    if not isinstance(budget, Budget):
        raise _fail("budget must be a Budget", "invalid_budget")
    if isinstance(present, (str, bytes)) or not isinstance(present, Iterable):
        raise _fail("present must be a collection of InstanceRefs", "invalid_present")
    cast = tuple(present)
    if len(cast) > MAX_PRESENT or not all(isinstance(a, InstanceRef) for a in cast) or len(set(cast)) != len(cast):
        raise _fail(f"present must be at most {MAX_PRESENT} distinct InstanceRefs", "invalid_present")
    if any(a.campaign_id != knowledge.campaign_id for a in cast):
        raise _fail("a present actor belongs to another campaign", "campaign_mismatch")
    if type(scene_text) is not str or len(scene_text) > MAX_SCENE_TEXT:
        raise _fail(f"scene_text must be text of at most {MAX_SCENE_TEXT} characters", "invalid_scene_text")
    check_readers(readers)
    if type(measurement) is not str or measurement not in MEASUREMENTS:
        raise _fail(f"measurement must be one of {sorted(MEASUREMENTS)}", "invalid_measurement")
    if claims is not None:
        if type(claims) is not ClaimSet:
            raise _fail("claims must be the ledger's 017 ClaimSet", "invalid_claims")
        if claims.campaign_id != knowledge.campaign_id:
            raise _fail("the claim shapes belong to another campaign", "campaign_mismatch")
    if isinstance(excluded, (str, bytes)) or not isinstance(excluded, Iterable):
        raise _fail("exclude_sections must be a collection of section names", "invalid_sections")
    names = tuple(excluded)
    if not all(type(name) is str for name in names):
        raise _fail("exclude_sections must be a collection of section names", "invalid_sections")
    return cast, frozenset(names)


# -- the actor's own portrayal ------------------------------------------------------------------------

def _owner(snapshot: EffectiveSnapshot, actor: InstanceRef) -> Resolution | None:
    return next((r for r in snapshot.sources() if r.source.actor == actor), None)


def _portrayal(snapshot: EffectiveSnapshot, actor: InstanceRef, excluded: frozenset[str]) -> list[Candidate]:
    try:
        entity = snapshot.entity(actor)  # the instance composed over its binding, overrides included
        owner = _owner(snapshot, actor)
        effective = (snapshot.effective(owner.resolved, owner.source.role)
                     if owner is not None and owner.resolved is not None else None)
    except (PortrayalError, MissingDefinition, EntityError) as error:
        reason = getattr(error, "reason", type(error).__name__)
        raise _fail(f"the audience {actor} does not resolve in this snapshot ({reason})", "unresolved_audience",
                    reason) from None
    authored = {} if effective is None else {entry.field: entry.source.ref for entry in effective.provenance}
    # The name alone is the one line a pack must hold; a long card description is sheddable.
    found = [Candidate("portrayal", (0, 0), f"You are {entity.name}.",
                       Origin("identity", entity.resolved_source, actor))]
    description = entity.description.strip()
    if description:
        source = authored.get("description", entity.resolved_source)
        found.append(Candidate("portrayal", (0, 1), description, Origin("description", source, actor)))
    if effective is not None:
        for position, (name, text) in enumerate(effective.sections):
            if name in excluded:
                continue
            origin = Origin("section", authored.get(f"section:{name}", effective.ref), actor, name)
            found.append(Candidate("portrayal", (1, position), f"{name}: {text}", origin))
    return found


# -- what the actor holds ------------------------------------------------------------------------------

def _held_propositions(held: ActorKnowledge) -> list[str]:
    found = [item.proposition_id for item in held.evidence if item.kind == "report" and item.proposition_id]
    found += [sent.proposition_id for sent in held.sent] + [belief.proposition_id for belief in held.current]
    return list(dict.fromkeys(found))


def _focus(knowledge: Knowledge, held: ActorKnowledge, claims: ClaimSet | None,
           nodes: tuple[InstanceRef, ...]) -> tuple[set[str], dict[str, list[ClaimReading]]]:
    """The held propositions about the actor or a present actor, and 017's typed readings of them.

    The same answer as 012's ``knowledge_about`` and 017's ``claims_about`` asked once per node (a
    held proposition is about a node when its ``about`` overtly names it; a claim reading is a held
    proposition's shape naming the node), computed in one pass over what the actor holds.
    """
    subjects = {Subject("instance", str(node)) for node in nodes}
    keys: set[str] = set()
    readings: dict[str, list[ClaimReading]] = {}
    for proposition_id in _held_propositions(held):
        if subjects.intersection(knowledge.proposition(proposition_id).about):
            keys.add(f"p:{proposition_id}")
        shape = claims.shapes.get(proposition_id) if claims is not None else None
        if shape is None:
            continue
        for node in nodes:
            if shape.names(node):
                position = ("either" if PREDICATES[shape.predicate].symmetric
                            else "subject" if shape.subject == node else "object")
                keys.add(f"p:{proposition_id}")
                readings.setdefault(proposition_id, []).append(ClaimReading(shape.predicate, position, node))
    return keys, readings


def _memory(held: ActorKnowledge, focus: set[str], readings: dict[str, list[ClaimReading]]) -> list[Candidate]:
    found: list[Candidate] = []
    for item in held.evidence:
        if item.kind == "perception":  # a perception is of an event, never about an instance node
            found.append(Candidate("memory", (True, -item.moment.sequence, 0, item.evidence_id),
                                   f"You perceived ({item.channel}): {item.content}",
                                   Origin("perception", record=item.evidence_id), sequence=item.moment.sequence))
    texts: dict[str, str] = {}
    sequences: dict[str, int] = {}
    evidence: dict[str, set[str]] = {}
    heard: dict[str, str] = {}  # the channel of the latest report of each proposition

    def note(proposition_id: str, text: str, sequence: int, evidence_id: str | None) -> None:
        texts.setdefault(proposition_id, text)
        sequences[proposition_id] = max(sequences.get(proposition_id, sequence), sequence)
        if evidence_id is not None:
            evidence.setdefault(proposition_id, set()).add(evidence_id)

    for item in held.evidence:
        if item.kind == "report" and item.proposition_id is not None:
            note(item.proposition_id, item.content, item.moment.sequence, item.evidence_id)
            heard[item.proposition_id] = item.channel
    for sent in held.sent:
        note(sent.proposition_id, sent.text, sent.sent_at.sequence, sent.evidence_id)
    beliefs = {}
    for belief in held.current:  # one current belief per proposition (012's chain rule)
        note(belief.proposition_id, belief.text, belief.moment.sequence, None)
        evidence.setdefault(belief.proposition_id, set()).update(belief.evidence_ids)
        beliefs[belief.proposition_id] = belief
    for proposition_id, text in texts.items():
        belief = beliefs.get(proposition_id)
        if belief is not None:
            lead = _STANCE_LEAD[belief.stance]
        else:
            channel = heard.get(proposition_id)
            lead = ("You told someone" if channel is None else "You were told" if channel == "told"
                    else f"You heard ({channel})")
        sequence = sequences[proposition_id]
        origin = Origin("proposition", record=proposition_id, evidence=tuple(sorted(evidence.get(proposition_id, ()))),
                        belief=None if belief is None else belief.belief_id)
        typed = tuple(sorted(readings.get(proposition_id, ()), key=lambda r: (str(r.node), r.predicate, r.position)))
        found.append(Candidate("memory", (f"p:{proposition_id}" not in focus, -sequence, 1, proposition_id),
                               f"{lead}: {text}", origin, None if belief is None else belief.stance, sequence,
                               typed))
    return found


def _perceived(held: ActorKnowledge) -> list[str]:
    """The texts of everything the actor holds: what it perceived, heard, said and concluded."""
    return ([item.content for item in held.evidence] + [sent.text for sent in held.sent]
            + [belief.text for belief in held.current])


# -- problems with the sources read ------------------------------------------------------------------

def _sources(snapshot: EffectiveSnapshot, actor: InstanceRef) -> list[SourceProblem]:
    """Broken bindings and drift notices, for the sources this pack read: the actor's own and lore.

    Another actor's binding is not reported here, since its state is not this actor's to know; a
    chat's own problems come from 022's ``chat_snapshot``.
    """
    found: list[SourceProblem] = []
    for resolution in snapshot.sources():
        if resolution.source.actor != actor or resolution.source.role in LORE_ROLES:
            continue  # lore bindings are reported once, by ``lore_problems``
        if resolution.resolved is None:
            reason = resolution.broken.reason if resolution.broken is not None else None
            found.append(SourceProblem("missing_source", resolution.source.ref, reason))
        found += [SourceProblem(notice.code, resolution.source.ref, notice.detail or None)
                  for notice in resolution.notices]
    return found + lore_problems(snapshot)


def pack_actor_context(snapshot: EffectiveSnapshot, knowledge: Knowledge, actor: InstanceRef, *, as_of: int,
                       budget: Budget, present: Iterable[InstanceRef] = (), scene_text: str = "",
                       claims: ClaimSet | None = None, readers: Mapping[str, EntryReader] | None = None,
                       unpack: Unpack | None = None, count: TokenCounter = estimate_tokens,
                       measurement: str = "estimated",
                       exclude_sections: Iterable[str] = DEFAULT_EXCLUDED_SECTIONS) -> ContextPack:
    """The audience-safe, budgeted context for ``actor`` at ``as_of``.

    **Trusted inputs; only the result goes toward the actor.** ``snapshot`` is one frozen view (022:
    for a chat, ``chat_snapshot(...).snapshot``); ``knowledge`` and ``claims`` are the campaign's 012
    ledger and 017 claim shapes (020: a director ``read``'s ``knowledge`` and ``claims``).

    **Caller obligations,** which this function cannot check:
    - ``present`` is the cast this actor perceives in the scene. It only ranks memory, but a hidden
      actor listed here would lift memories about them above others.
    - ``scene_text`` is text this actor perceived: its own window of the visible transcript, never a
      whisper it did not hear, another player's interiority, director narration or table talk. It
      triggers lore, as do the texts of the actor's own held memory.

    ``readers`` maps a stored record schema to the reader of that format's lore entries, and ``unpack``
    gives a raw definition's ``(schema, payload)``; ``grimoire.contextstore`` supplies the store's.
    ``count`` is the route's token counter, ``measurement`` says whether it is ``estimated`` or
    ``counted``. ``exclude_sections`` names composed card sections left to another owner (by
    default ``voice-evidence``, which 014/027 own). Raises ``ContextError("budget_too_small")`` when
    even the actor's name does not fit: a pack never leaves the actor nameless.

    **An NPC's pack.** Memory is 012's ``actor_knowledge``, so every current belief reads as the
    actor's own. A player-controlled actor's NPC-era beliefs are ``inherited`` under
    ``player_knowledge``; packing for a PC's controller is not this slice's (027/035).
    """
    if not isinstance(snapshot, EffectiveSnapshot):
        raise TypeError(f"expected an EffectiveSnapshot, not {type(snapshot).__name__}")
    readers = {} if readers is None else readers
    if not callable(count):
        raise _fail("count must be a token counter", "invalid_count")
    if unpack is not None and not callable(unpack):
        raise _fail("unpack must be callable", "invalid_unpack")
    cast, excluded = _check(knowledge, actor, as_of, budget, present, scene_text, readers, measurement, claims,
                            exclude_sections)
    candidates = _portrayal(snapshot, actor, excluded)
    held = actor_knowledge(knowledge, actor, as_of=as_of)
    nodes = tuple(dict.fromkeys((actor, *sorted(cast, key=str))))
    focus, readings = _focus(knowledge, held, claims, nodes)
    candidates += _memory(held, focus, readings)
    found, untriggered, problems = lore(snapshot, SceneWords([scene_text, *_perceived(held)]), readers, unpack,
                                        ACTOR_AUDIENCES)
    candidates += found
    items, omitted = pack(candidates, budget, count)
    if not items or items[0].origin.kind != "identity":
        raise _fail("the budget cannot hold even the actor's name", "budget_too_small")
    problems += _sources(snapshot, actor)
    return ContextPack(actor, as_of, budget, measurement, items, omitted,
                       tuple(sorted(untriggered, key=lambda pair: str(pair[0]))),
                       tuple(sorted(set(problems), key=lambda p: (str(p.source), p.code, p.detail or ""))),
                       selection_basis(snapshot, actor.campaign_id, actor), knowledge_digest(held))


__all__ = ["DEFAULT_EXCLUDED_SECTIONS", "MEASUREMENTS", "pack_actor_context"]
