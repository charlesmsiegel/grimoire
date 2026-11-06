"""Sealed per-call briefs: one NPC's own perspective, or the scene's authored material.

An ``NpcBrief`` is built only from one actor's ``ActorView`` (its own ``perspective`` projection,
its composed card) and its resolved voice, or from 031's ``ContextPack`` built for that same actor
over the same snapshot.
It never takes director knowledge, another actor's records or a world truth, so the prompt it
renders cannot hold them. A ``DirectorBrief`` takes authored scenario text, 031's director lore and
table-visible lines only: there is no knowledge parameter at all. Pure and deterministic; the
runtime turns a brief into provider messages.

031's packs are the extension seam 027's own minimal rendering was built to leave open: this module
imports their value types and ``selection_basis``, never the packer, so 031 depends on nothing here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import InitVar, dataclass

from grimoire.context import ContextError, ContextPack, LorePack, knowledge_digest, selection_basis
from grimoire.core.authority import DIRECTOR
from grimoire.core.entity import EntityError
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.core.model import ActorView, ControlAssignment
from grimoire.core.perspective import ActorKnowledge
from grimoire.core.portrayal import PortrayalError
from grimoire.core.resolve import MissingDefinition, snapshot_actor_view
from grimoire.core.snapshot import EffectiveSnapshot
from grimoire.evals.text import contains_marker, count_words, has_words
from grimoire.goals import Choice
from grimoire.voice import DEFAULT_MAX_CHARS, ResolvedVoice, SecretFact, SpeakerFragment, speaker_fragment

from .errors import GenerationError
from .plan import checked_controls, controller
from .voice import VOICE_SECTION

#: Budgets, in characters, for the parts of a brief that grow with the campaign.
KNOWLEDGE_CHARS = 2400
CONTEXT_CHARS = 1600
SCENARIO_CHARS = 4000
MAX_HEARD = 40
MAX_LINE_CHARS = 2000
MAX_NAME_CHARS = 200

_SPACE = re.compile(r"\s+")
#: Closing quotes and brackets that may follow a stop: ``"He left." "She stayed."`` is two sentences.
_CLOSERS = "\"'\u2019\u201d\u00bb\u203a\u300d\u300f\uff09)\\]"
#: Where one sentence of a director lore entry ends: a stop (an ellipsis is one), any closing quotes,
#: then a space; or a full-width stop, which scripts written without spaces need no space after.
_SENTENCE_END = re.compile(rf"(?<=[.!?;\u2026])[{_CLOSERS}]*\s+|(?<=[\u3002\uff01\uff1f])[{_CLOSERS}]*\s*")
_EDGE = re.compile(r"^\W+|\W+$")
#: A director lore sentence shorter than this ("It is late.") is ordinary prose, so screening it would
#: reject innocent narration and halt the turn; it is left unscreened.
MIN_LORE_MARKER_WORDS = 5
#: A fragment shorter than this before a stop is an abbreviation ("Mr.", "Dr."), not a sentence.
_MIN_SENTENCE_WORDS = 2
_SEAL = object()
_STANCE = {"believed": "You believe", "suspected": "You suspect", "rejected": "You doubt"}
_EARLIER = {"believed": "Earlier, you believed", "suspected": "Earlier, you suspected",
            "rejected": "Earlier, you doubted"}


def _one_line(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


@dataclass(frozen=True, slots=True)
class HeardLine:
    """One line spoken aloud at the table, as every present actor heard it.

    ``speaker`` is the caller's label for who spoke. ``actor``, when given, lets each listener's
    brief label the line by its own ``names`` instead. A label stating a secret the listener has not
    learned is shown as "Someone": hearing a voice does not teach a name.
    """

    speaker: str
    text: str
    actor: InstanceRef | None = None

    def __post_init__(self) -> None:
        if self.actor is not None and not isinstance(self.actor, InstanceRef):
            raise GenerationError("bad_heard", "a heard line's actor must be an InstanceRef")
        for value, label in ((self.speaker, "speaker"), (self.text, "text")):
            if not isinstance(value, str) or not has_words(value):
                raise GenerationError("bad_heard", f"a heard line needs a nonblank {label}")
        if len(self.text) > MAX_LINE_CHARS or len(self.speaker) > MAX_LINE_CHARS:
            raise GenerationError("bad_heard", f"a heard line is longer than {MAX_LINE_CHARS} characters")


def heard_lines(heard: object) -> tuple[HeardLine, ...]:
    if isinstance(heard, (str, bytes, Mapping)):
        raise GenerationError("bad_heard", "heard must be a sequence of HeardLine values")
    try:
        heard = tuple(heard)  # type: ignore[arg-type]
    except TypeError:
        raise GenerationError("bad_heard", "heard must be a sequence of HeardLine values") from None
    if not all(isinstance(line, HeardLine) for line in heard):
        raise GenerationError("bad_heard", "heard must be HeardLine values")
    if len(heard) > MAX_HEARD:
        raise GenerationError("bad_heard", f"more than {MAX_HEARD} heard lines")
    return heard


def _spoken(spoken: object) -> tuple[HeardLine, ...]:
    """This turn's lines: typed, but not capped (the plan bounds them)."""
    if isinstance(spoken, (str, bytes, Mapping)):
        raise GenerationError("bad_heard", "spoken must be a sequence of HeardLine values")
    spoken = tuple(spoken)  # type: ignore[arg-type]
    if not all(isinstance(line, HeardLine) for line in spoken):
        raise GenerationError("bad_heard", "spoken must be HeardLine values")
    return spoken


def _secrets(secrets: object) -> tuple[SecretFact, ...]:
    if isinstance(secrets, (str, bytes, Mapping)):
        raise GenerationError("bad_secrets", "secrets must be an ordered sequence of SecretFact values")
    try:
        secrets = tuple(secrets)  # type: ignore[arg-type]
    except TypeError:
        raise GenerationError("bad_secrets", "secrets must be an ordered sequence of SecretFact values") from None
    if not all(isinstance(secret, SecretFact) for secret in secrets):
        raise GenerationError("bad_secrets", "secrets must be SecretFact values")
    return secrets


def _budget(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GenerationError("bad_budget", f"{label} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class NpcBrief:
    """Everything one NPC's call may see. Only ``npc_brief`` builds one."""

    actor: InstanceRef
    name: str
    voice: ResolvedVoice
    fragment: SpeakerFragment
    system: str
    heard: tuple[HeardLine, ...]
    hidden: tuple[SecretFact, ...]  # the secrets this actor has not learned, by id
    exposed: tuple[str, ...]  # what the actor holds or heard: a marker in here is not a leak
    names: tuple[tuple[InstanceRef, str], ...]  # how this actor labels others
    _seal: InitVar[object] = None

    __hash__ = None  # type: ignore[assignment]  # holds a ResolvedVoice

    def __post_init__(self, _seal: object) -> None:
        if _seal is not _SEAL:
            raise GenerationError("unsealed_brief", "build NPC briefs with npc_brief()")


@dataclass(frozen=True, slots=True)
class DirectorBrief:
    """Everything the scene-level reply may see. Only ``director_brief`` builds one."""

    campaign_id: str
    players: frozenset[str]  # the present player characters' actor ids, derived from control
    system: str
    heard: tuple[HeardLine, ...]
    # every secret (the director brief has no knowledge to learn one from) and its director lore's markers
    hidden: tuple[SecretFact, ...]
    exposed: tuple[str, ...]  # authored material and table-visible lines
    #: director lore entries shown with no sentence long enough to screen, as (source, entry id): the
    #: trusted caller's to review, since narration stating one is not caught
    unscreened: tuple[tuple[SourceRef, str], ...] = ()
    _seal: InitVar[object] = None

    def __post_init__(self, _seal: object) -> None:
        if _seal is not _SEAL:
            raise GenerationError("unsealed_brief", "build director briefs with director_brief()")


def _check_view(view: object) -> tuple[InstanceRef, ActorKnowledge]:
    if not isinstance(view, ActorView):
        raise GenerationError("bad_view", "npc_brief needs an ActorView")
    who = f"{view.campaign_id}/{view.actor_id}"
    knowledge = view.knowledge
    if knowledge is None:
        # None means no projection was supplied, never "knows everything".
        raise GenerationError("no_knowledge", f"{who} has no perspective projection")
    if type(knowledge) is not ActorKnowledge:
        raise GenerationError("foreign_knowledge", f"{who} knowledge must be its own ActorKnowledge")
    # The view names the actor by campaign and id only; its typed ref (a character, or a speaking
    # item such as a sentient sword) comes from its own projection.
    actor = knowledge.actor
    if (actor.campaign_id, actor.instance_id) != (view.campaign_id, view.actor_id):
        raise GenerationError("foreign_knowledge", f"{who} was given another actor's knowledge")
    return actor, knowledge


def known_ids(knowledge: ActorKnowledge) -> frozenset[str]:
    """Proposition ids the actor holds: reports it received or sent, and its own beliefs."""
    beliefs = (*knowledge.current, *knowledge.history, *(knowledge.inherited or ()),
               *(knowledge.inherited_history or ()))
    return frozenset({item.proposition_id for item in knowledge.evidence if item.proposition_id is not None}
                     | {report.proposition_id for report in knowledge.sent}
                     | {belief.proposition_id for belief in beliefs})


def _who(ref: InstanceRef | None, names: Mapping[InstanceRef, str], hidden: tuple[SecretFact, ...],
         exposed: Sequence[str]) -> str:
    """How the actor names ``ref``: "Someone" when unnamed, or when the name states a hidden secret."""
    name = names.get(ref) if ref is not None else None
    if not (isinstance(name, str) and has_words(name)):
        return "Someone"
    name = _one_line(name)
    return "Someone" if _leaks(name, hidden, exposed) is not None else name


def _contents(knowledge: ActorKnowledge) -> tuple[str, ...]:
    """What the actor's knowledge says, without who said it: a label is never learned."""
    beliefs = (*knowledge.current, *knowledge.history)
    return (tuple(item.content for item in knowledge.evidence) + tuple(report.text for report in knowledge.sent)
            + tuple(belief.text for belief in beliefs))


def _knowledge_items(knowledge: ActorKnowledge, names: Mapping[InstanceRef, str], hidden: tuple[SecretFact, ...],
                     exposed: Sequence[str]) -> list[tuple[tuple, str]]:
    """Each item the actor knows, rendered without ids, with a stable campaign-order key."""
    items: list[tuple[tuple, str]] = []
    labels: dict[InstanceRef | None, str] = {}

    def who(ref: InstanceRef | None) -> str:  # one screen per actor named, not per item
        if ref not in labels:
            labels[ref] = _who(ref, names, hidden, exposed)
        return labels[ref]

    for item in knowledge.evidence:
        if item.kind == "report":
            text = f"{who(item.attributed_to)} told you: {_one_line(item.content)}"
        else:
            text = f"You noticed ({_one_line(item.channel)}): {_one_line(item.content)}"
        items.append(((item.moment.sequence, 0, item.evidence_id), text))
    for report in knowledge.sent:
        items.append(((report.sent_at.sequence, 1, report.evidence_id),
                      f"You told {who(report.recipient)}: {_one_line(report.text)}"))
    for belief in knowledge.current:
        items.append(((belief.moment.sequence, 2, belief.belief_id),
                      f"{_STANCE.get(belief.stance, 'You believe')}: {_one_line(belief.text)}"))
    for belief in knowledge.history:
        items.append(((belief.moment.sequence, 2, belief.belief_id),
                      f"{_EARLIER.get(belief.stance, 'Earlier, you thought')}: {_one_line(belief.text)}"))
    return sorted(items)


def _newest_within(texts: list[str], budget: int) -> list[str]:
    """The newest whole items that fit ``budget``, shown oldest first; an item too big alone is skipped."""
    kept: list[str] = []
    used = 0
    for text in reversed(texts):
        cost = len(text) + 3  # "- " and a newline
        if cost > budget:
            continue
        if used + cost > budget:
            break
        kept.append(text)
        used += cost
    return kept[::-1]


def _context(description: str, sections: tuple[tuple[str, str], ...], budget: int) -> list[str]:
    parts = []
    used = 0
    for text in ([_one_line(description)] if has_words(description) else []) + [
            f"{name}: {_one_line(text)}" for name, text in sections]:
        if used + len(text) + 1 > budget:
            continue  # whole parts only; a later, shorter section may still fit
        parts.append(text)
        used += len(text) + 1
    return parts


def _sections(sections: object) -> tuple[tuple[str, str], ...]:
    if isinstance(sections, (str, bytes, Mapping)):
        raise GenerationError("bad_sections", "sections must be (name, text) pairs")
    try:
        sections = tuple(sections)  # type: ignore[arg-type]
    except TypeError:
        raise GenerationError("bad_sections", "sections must be (name, text) pairs") from None
    for entry in sections:
        if not (isinstance(entry, tuple) and len(entry) == 2 and all(isinstance(v, str) for v in entry)):
            raise GenerationError("bad_sections", "sections must be (name, text) pairs")
    return sections


def _check_context(context: object, actor: InstanceRef, knowledge: ActorKnowledge, view: ActorView,
                   snapshot: object) -> ContextPack:
    """``context`` if it is a pack built for this actor, at this moment, over ``snapshot``'s selection,
    and ``view`` is that snapshot's view of the actor."""
    if isinstance(context, LorePack):
        # The director's lore may hold director-only entries: it is never an actor's.
        raise GenerationError("foreign_context", f"{actor} was given the director's lore pack")
    if not isinstance(context, ContextPack):
        raise GenerationError("bad_context", "context must be a ContextPack from pack_actor_context")
    if not isinstance(snapshot, EffectiveSnapshot):
        # Without it nothing says which selection the brief is for, so any chat's pack would pass.
        raise GenerationError("bad_context", "a context pack needs the EffectiveSnapshot the view came from")
    if context.actor != actor:
        raise GenerationError("foreign_context", f"the context pack was built for {context.actor}, not {actor}")
    identity = context.items[0].origin if context.items else None
    if identity is None or identity.kind != "identity" or identity.actor != actor:
        raise GenerationError("bad_context", f"the context pack for {actor} does not open with its name")
    if any(item.lane == "lore" and item.audience != "common" for item in context.items):
        raise GenerationError("foreign_context", f"the context pack for {actor} holds lore no actor may read")
    if context.as_of != knowledge.as_of:
        # Its memory would be another moment's than the projection that decides what is learned.
        raise GenerationError("context_mismatch", f"the context pack is as of {context.as_of}, "
                              f"the actor's knowledge as of {knowledge.as_of}")
    if context.knowledge != knowledge_digest(knowledge):
        # Same moment, other records: a ledger reload quarantined (or restored) one. A stale pack would
        # show a memory the actor no longer holds, and count it as exposed.
        raise GenerationError("context_mismatch", f"the context pack for {actor} was built from another "
                              "projection of its knowledge")
    try:
        # The view must be this snapshot's, so the snapshot names the brief's own selection.
        own = snapshot_actor_view(snapshot.instance(actor), snapshot, knowledge)
        basis = selection_basis(snapshot, actor.campaign_id, actor)
    except (PortrayalError, MissingDefinition, EntityError, ContextError) as error:
        raise GenerationError("context_mismatch", f"{actor} does not resolve in the snapshot given "
                              f"({getattr(error, 'reason', type(error).__name__)})") from None
    if own != view:
        raise GenerationError("context_mismatch", f"the view of {actor} is not the snapshot's")
    if context.basis != basis:
        # Another chat's, or this chat's before an attach, detach or re-pin: its lore and card are
        # not what this brief's selection binds, and what it shows would count as exposed.
        raise GenerationError("context_mismatch", f"the context pack for {actor} was built over another "
                              "selection than the snapshot's")
    return context


def _check_intention(intention: object, actor: InstanceRef, knowledge: ActorKnowledge) -> None:
    """``intention`` if it is this actor's choice, made from this very projection of its knowledge."""
    if not isinstance(intention, Choice):
        raise GenerationError("bad_intention", "intention must be a Choice from grimoire.goals.choose")
    if intention.actor != actor:
        raise GenerationError("foreign_intention", f"the intention was chosen for {intention.actor}, not {actor}")
    if intention.knowledge != knowledge:
        # Another moment's, or a projection a reload has since changed: it may rest on a belief the actor
        # no longer holds, or miss one that would have stopped it.
        raise GenerationError("intention_mismatch", f"the intention for {actor} was chosen from another "
                              "projection of its knowledge")


def _intention(intention: Choice, actor: InstanceRef, hidden: tuple[SecretFact, ...],
               exposed: Sequence[str]) -> list[tuple[str, list[str]]]:
    """The intention's parts as (heading, lines); refused if any states a secret the actor has not learned.

    The agenda is the director's, so its text may name what this NPC has not learned yet: an approach
    gated on holding the proposition is safe (the actor then holds it), any other is an authoring
    error, refused rather than shown.
    """
    parts: list[tuple[str, list[str]]] = []
    goal, approach = intention.goal, intention.approach
    if goal is not None and approach is not None:
        parts.append(("What you are after right now:", [_one_line(goal.aim)]))
        parts.append(("What you mean to do about it:", [_one_line(approach.action)]))
    rules = [_one_line(limit.rule) for limit in intention.refusals]
    if rules:
        parts.append(("What you will not do here, whatever it would gain you:", rules))
    for _heading, texts in parts:
        for text in texts:
            leak = _leaks(text, hidden, exposed)
            if leak is not None:
                raise GenerationError("intention_leak", f"the intention for {actor} states secret {leak}, "
                                      "which it has not learned")
    return parts


def _packed(context: ContextPack, lane: str) -> list[str]:
    """A lane's lines as the brief shows them: never the name line, never the card's voice section."""
    return [_one_line(item.line) for item in context.lane(lane) if item.origin.kind != "identity"
            and not (item.origin.kind == "section" and item.origin.name == VOICE_SECTION)]


def _listed(heading: str, lines: Sequence[str]) -> str:
    return heading + "\n" + "\n".join(f"- {line}" for line in lines)


def _leaks(text: str, hidden: tuple[SecretFact, ...], exposed: Sequence[str]) -> str | None:
    """The id of a hidden secret whose marker ``text`` states and nothing in ``exposed`` does."""
    for secret in hidden:
        for marker in secret.markers:
            if contains_marker(text, marker) and not any(contains_marker(seen, marker) for seen in exposed):
                return secret.fact_id
    return None


def _label(line: HeardLine, names: Mapping[InstanceRef, str], hidden: tuple[SecretFact, ...],
           exposed: Sequence[str]) -> str:
    """Who spoke, as this listener labels them; "Someone" when the label would state a hidden secret."""
    label = names.get(line.actor) if line.actor is not None else None
    label = _one_line(label) if isinstance(label, str) and has_words(label) else _one_line(line.speaker)
    return "Someone" if _leaks(label, hidden, exposed) is not None else label


def npc_brief(view: ActorView, voice: ResolvedVoice, *, heard: Iterable[HeardLine] = (),
              secrets: Iterable[SecretFact], sections: Iterable[tuple[str, str]] = (),
              names: Mapping[InstanceRef, str] | None = None, knowledge_chars: int = KNOWLEDGE_CHARS,
              context_chars: int = CONTEXT_CHARS, voice_chars: int = DEFAULT_MAX_CHARS,
              context: ContextPack | None = None, snapshot: EffectiveSnapshot | None = None,
              intention: Choice | None = None) -> NpcBrief:
    """Build one NPC's sealed brief from its own view; ``secrets`` has no default on purpose.

    ``view`` must carry the actor's own ``ActorKnowledge`` (``no_knowledge``/``foreign_knowledge``
    otherwise). ``sections`` is the actor's composed card (``snapshot.effective(...).sections``), for
    context. ``heard`` is what the actor heard aloud before this turn's calls: its text counts as
    learned, as a line spoken earlier this turn does, so the caller must pass only lines this actor
    heard. A speaker label is not learned: one that states a hidden secret is shown as "Someone".
    ``names`` labels other actors in knowledge and in heard lines that carry an ``actor``.

    ``context`` is 031's pack for this same actor (``pack_actor_context``): its portrayal, memory
    and lore lanes replace the card context and knowledge this function renders itself, already
    within the pack's token budget, so ``sections``, ``context_chars`` and ``knowledge_chars`` do not
    apply. It needs ``snapshot``, the frozen view ``view`` and ``voice`` were resolved from
    (``bad_context`` without one). A pack for another actor, the director's lore, or one with
    director-only lore is ``foreign_context``. ``context_mismatch`` is a pack built as of another
    moment than the view's knowledge, a ``view`` that is not ``snapshot``'s view of the actor, or a
    pack whose ``basis`` is not ``snapshot``'s: built in another chat (one that reuses the actor
    included), or before a lore or card binding was attached, detached, re-pinned or moved, or before
    the actor's instance changed. Everything it shows counts as exposed to this actor when screening.

    ``intention`` is 039's ``Choice`` for this actor (``choose``): what it is after, what it means to
    do about it now and what it will not do here are added to the brief, so the NPC pursues its goal
    in this same call and no other is made. It must be this actor's (``foreign_intention``), made from
    this very knowledge projection (``intention_mismatch``), and none of its shown text may state a
    secret hidden from the actor (``intention_leak``). Its shown text then counts as exposed.
    """
    actor, knowledge = _check_view(view)
    # The voice reaches the prompt only through the screened fragment: the card's raw voice-evidence
    # text would bypass suppression and secret screening, so it is neither shown nor counted as held.
    if not isinstance(voice, ResolvedVoice):
        raise GenerationError("bad_voice", "npc_brief needs a ResolvedVoice")
    if voice.actor != actor:
        raise GenerationError("voice_mismatch", f"the voice belongs to {voice.actor}, not {actor}")
    if view.resolved_source is not None and voice.actor_revision != view.resolved_source:
        # A voice resolved for another selected revision would mix that portrayal into this card.
        raise GenerationError("voice_mismatch", f"the voice was resolved for {voice.actor_revision}, "
                              f"not {view.resolved_source}")
    secrets = _secrets(secrets)
    heard = heard_lines(heard)
    sections = _sections(sections)
    if intention is not None:
        _check_intention(intention, actor, knowledge)
    if context is not None:
        context = _check_context(context, actor, knowledge, view, snapshot)
        if sections:
            raise GenerationError("bad_sections", "a context pack already carries the card's sections")
    sections = tuple(entry for entry in sections if entry[0] != VOICE_SECTION)
    names = dict(names or {})
    if not all(isinstance(key, InstanceRef) and isinstance(value, str) for key, value in names.items()):
        raise GenerationError("bad_names", "names must map InstanceRef values to display names")
    knowledge_chars = _budget(knowledge_chars, "knowledge_chars")
    context_chars = _budget(context_chars, "context_chars")

    known = known_ids(knowledge)
    hidden = tuple(secret for secret in secrets if secret.fact_id not in known)
    if context is None:
        card = _context(view.description, sections, context_chars)
        setting: list[str] = []
        memory: list[str] = []
    else:
        card, setting, memory = (_packed(context, lane) for lane in ("portrayal", "lore", "memory"))
    # What the actor holds, in full: what its projection and its own card say, whatever the budget
    # showed, and what its pack gave it. The pack was checked above to be built over this brief's own
    # snapshot and to hold common lore only, so quoting it back is no leak and no director-only or
    # other-selection text becomes exposed. Names come after, screened against this, so
    # a label never exposes a secret.
    own = _contents(knowledge) + ((view.description,) if has_words(view.description) else ()) + tuple(
        text for _name, text in sections)
    if context is not None:
        own += (*card, *setting, *memory)
    aims = _intention(intention, actor, hidden, own) if intention is not None else []
    own += tuple(text for _heading, texts in aims for text in texts)
    rendered = [text for _key, text in _knowledge_items(knowledge, names, hidden, own)]
    try:
        fragment = speaker_fragment(voice, secrets=secrets, known=known, max_chars=voice_chars)
    except ValueError as error:
        raise GenerationError(getattr(error, "reason", "bad_voice"), f"{actor} voice: {error}") from None
    name = _one_line(voice.name)
    if len(name) > MAX_NAME_CHARS:
        raise GenerationError("bad_voice", f"{actor} has a name longer than {MAX_NAME_CHARS} characters")
    shown = _newest_within(rendered, knowledge_chars) if context is None else memory
    parts = [
        f"You are {name}, a character in a shared story. Write only {name}'s next contribution: what {name} "
        f"says and does, in {name}'s own voice. Never write for any other character, and never describe what "
        "anyone else thinks, feels or decides.",
        fragment.text,
    ]
    if card:
        parts.append("Who you are:\n" + "\n".join(card))
    if setting:
        parts.append(_listed("What anyone here may know:", setting))
    if shown:
        # 027's own rendering is in time order; a pack's memory is in 031's rank order.
        parts.append(_listed("What you know, oldest first:" if context is None else "What you know:", shown))
    elif not rendered:
        parts.append("You know nothing yet beyond what you hear now.")
    parts.extend(_listed(heading, texts) for heading, texts in aims)
    names[actor] = name
    return NpcBrief(actor, name, voice, fragment, "\n\n".join(parts), heard, hidden,
                    own + (name,) + tuple(line.text for line in heard), tuple(names.items()), _SEAL)


def _check_lore(lore: object, campaign_id: str, snapshot: object) -> None:
    if not isinstance(lore, LorePack):
        raise GenerationError("bad_lore", "lore must be a LorePack from director_lore")
    if not isinstance(snapshot, EffectiveSnapshot):
        # Without it nothing says which selection the brief is for, so any chat's lore would pass.
        raise GenerationError("bad_lore", "director lore needs the EffectiveSnapshot it is rendered for")
    if lore.basis.campaign_id != campaign_id:
        raise GenerationError("foreign_context", f"the director lore was built for campaign "
                              f"{lore.basis.campaign_id}, not {campaign_id}")
    if lore.basis != selection_basis(snapshot, campaign_id):  # the campaign id is the lore's own, so valid
        # A stale pack could show an entry the current selection made director-only as common.
        raise GenerationError("context_mismatch", "the director lore was built over another selection than "
                              "the snapshot's")


def _sentences(line: str) -> list[str]:
    """``line``'s sentences, edge punctuation trimmed. A fragment of under ``_MIN_SENTENCE_WORDS`` words
    before a stop ("Mr.") stays with what follows it; what follows is also given alone, so a one-word
    sentence ("Rain.") never hides the sentence after it."""
    found: list[str] = []
    start, rest = 0, None
    for stop in (*_SENTENCE_END.finditer(line), None):
        end, after = (stop.start(), stop.end()) if stop is not None else (len(line), len(line))
        if stop is not None and count_words(line[rest if rest is not None else start:end]) < _MIN_SENTENCE_WORDS:
            rest = after  # "Mr." ends no sentence: keep reading from the same start
            continue
        found.append(_EDGE.sub("", line[start:end]))
        if rest is not None:
            found.append(_EDGE.sub("", line[rest:end]))
        start, rest = after, None
    return [sentence for sentence in found if sentence]


def _lore_markers(line: str) -> tuple[str, ...]:
    """A director lore line's markers: each of its sentences of at least ``MIN_LORE_MARKER_WORDS`` words.

    Screening matches whole phrases word for word, so a sentence is the unit: narration that repeats
    one states it. A shorter sentence would also match ordinary prose, so it is not a marker.
    """
    return tuple(dict.fromkeys(part for part in _sentences(line) if count_words(part) >= MIN_LORE_MARKER_WORDS))


def director_brief(scenario: str, *, campaign_id: str, present: Iterable[str],
                   controls: Iterable[ControlAssignment], names: Mapping[str, str], heard: Iterable[HeardLine] = (),
                   secrets: Iterable[SecretFact], scenario_chars: int = SCENARIO_CHARS,
                   lore: LorePack | None = None, snapshot: EffectiveSnapshot | None = None) -> DirectorBrief:
    """The scene-level reply's sealed brief: authored scenario text, lore and table-visible lines only.

    The player characters are derived here from ``controls``, never passed in: every ``present``
    actor whose controller is a human. A present actor with no controller is ``unassigned_actor``.
    ``names`` must name each of them (``bad_players``), and the
    brief forbids writing their words, thoughts, feelings or decisions. ``run_turn`` checks the
    same set against the plan and controls before any call. The reply is an unreviewed proposal:
    035's ``review_narration`` reviews it before it is kept.

    ``lore`` is 031's ``director_lore`` for this scene (an actor's ``ContextPack`` is ``bad_lore``: its
    memory is one actor's own). It needs ``snapshot``, the chat's frozen view this brief is for
    (``bad_lore`` without one). Lore built for another campaign is ``foreign_context``; lore whose
    ``basis`` is not ``snapshot``'s (another chat's, or this chat's before a lore binding was attached,
    detached, re-pinned or moved) is ``context_mismatch``. Its ``common`` entries are setting the
    table may hear, so narration quoting one is no leak. Its ``director`` entries guide the narrator
    and are hidden like secrets: narration that repeats, word for word, a whole sentence of one of at
    least ``MIN_LORE_MARKER_WORDS`` words is ``unlearned_secret``, unless the scenario, common lore, the
    table text the lore was triggered on or a heard line says it too. Part of a sentence, a paraphrase
    or a shorter sentence is not caught; an entry with no sentence that long is listed in
    ``unscreened`` for the trusted caller.
    """
    if not isinstance(scenario, str) or not has_words(scenario):
        raise GenerationError("bad_scenario", "a director reply needs nonblank authored scenario text")
    scenario_chars = _budget(scenario_chars, "scenario_chars")
    if len(scenario) > scenario_chars:
        raise GenerationError("scenario_too_long", f"scenario text is longer than {scenario_chars} characters")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise GenerationError("bad_input", "campaign_id must be nonblank text")
    if isinstance(present, (str, bytes)) or not isinstance(names, Mapping):
        raise GenerationError("bad_players", "present must be a collection of ids and names a mapping")
    present = tuple(present)
    controls = checked_controls(controls)
    if not all(isinstance(actor_id, str) and actor_id for actor_id in present):
        raise GenerationError("bad_players", "present must hold nonblank actor ids")
    owners = {actor_id: controller(campaign_id, actor_id, controls) for actor_id in present}
    unassigned = sorted(actor_id for actor_id, owner in owners.items() if owner is None)
    if unassigned:
        # Fail closed: an actor nobody controls could be a player character the brief fails to name.
        raise GenerationError("unassigned_actor", f"no controller for present actors {unassigned}")
    players = sorted(actor_id for actor_id, owner in owners.items() if owner != DIRECTOR)
    labels = [names.get(actor_id) for actor_id in players]
    if not all(isinstance(name, str) and has_words(name) and len(name) <= MAX_NAME_CHARS for name in labels):
        raise GenerationError("bad_players", "every present player character needs a nonblank display name")
    if lore is not None:
        _check_lore(lore, campaign_id, snapshot)
    secrets = _secrets(secrets)
    heard = heard_lines(heard)
    parts = ["You are the narrator of a shared story. Describe the scene and what happens next, and voice only "
             "minor unnamed background figures. Write one short narration."]
    if players:
        listed = ", ".join(_one_line(name) for name in labels)  # type: ignore[arg-type]
        parts.append(f"The player characters are: {listed}. Never write their words, thoughts, feelings or "
                     "decisions; their players do that.")
    parts.append("Scenario:\n" + scenario.strip())
    common = [_one_line(item.line) for item in lore.audience("common")] if lore is not None else []
    directors = lore.audience("director") if lore is not None else ()
    private = [_one_line(item.line) for item in directors]
    if common:
        parts.append(_listed("What anyone here may know:", common))
    if private:
        parts.append(_listed("Known only to you, the narrator. Let it shape what happens, but never state it "
                             "outright:", private))
    hidden = list(secrets)
    unscreened = []
    for index, (item, line) in enumerate(zip(directors, private)):
        markers = _lore_markers(line)
        if markers:
            hidden.append(SecretFact(f"director-lore-{index}", markers))
        else:
            unscreened.append((item.origin.source, item.origin.name))
    # The player characters' names and what the table saw are table-visible: stating them is no leak.
    table = (lore.table_text,) if lore is not None and has_words(lore.table_text) else ()
    return DirectorBrief(campaign_id, frozenset(players), "\n\n".join(parts), heard, tuple(hidden),
                         (scenario, *labels, *common, *table) + tuple(line.text for line in heard),
                         tuple(unscreened), _SEAL)


def _transcript(lines: Sequence[HeardLine], names: Mapping[InstanceRef, str], hidden: tuple[SecretFact, ...],
                exposed: Sequence[str]) -> str:
    seen = tuple(exposed) + tuple(line.text for line in lines)
    return "\n".join(f"{_label(line, names, hidden, seen)}: {_one_line(line.text)}" for line in lines)


def npc_messages(brief: NpcBrief, spoken: Sequence[HeardLine] = ()) -> tuple[tuple[str, str], ...]:
    """The (role, content) messages of one NPC call: its brief, then everything it heard so far."""
    if not isinstance(brief, NpcBrief):
        raise GenerationError("bad_brief", "npc_messages needs an NpcBrief")
    lines = brief.heard + _spoken(spoken)
    said = (f"What was just said:\n{_transcript(lines, dict(brief.names), brief.hidden, brief.exposed)}\n\n"
            if lines else "Nothing has been said yet.\n\n")
    return (("system", brief.system), ("user", f"{said}Give {brief.name}'s reply only."))


def director_messages(brief: DirectorBrief, spoken: Sequence[HeardLine] = ()) -> tuple[tuple[str, str], ...]:
    """The (role, content) messages of the scene-level call."""
    if not isinstance(brief, DirectorBrief):
        raise GenerationError("bad_brief", "director_messages needs a DirectorBrief")
    lines = brief.heard + _spoken(spoken)
    said = f"What was just said:\n{_transcript(lines, {}, brief.hidden, brief.exposed)}\n\n" if lines else ""
    return (("system", brief.system), ("user", f"{said}Narrate what happens next."))
