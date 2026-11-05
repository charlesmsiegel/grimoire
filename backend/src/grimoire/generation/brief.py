"""Sealed per-call briefs: one NPC's own perspective, or the scene's authored material.

An ``NpcBrief`` is built only from one actor's ``ActorView`` (its own ``perspective`` projection,
its composed card) and its resolved voice. It never takes director knowledge, another actor's
records or a world truth, so the prompt it renders cannot hold them. A ``DirectorBrief`` takes
authored scenario text and table-visible lines only: there is no knowledge parameter at all.
Pure and deterministic; the runtime turns a brief into provider messages.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import InitVar, dataclass

from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import InstanceRef
from grimoire.core.model import ActorView, ControlAssignment
from grimoire.core.perspective import ActorKnowledge
from grimoire.evals.text import contains_marker, has_words
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
    hidden: tuple[SecretFact, ...]  # every secret: the director brief has no knowledge to learn one from
    exposed: tuple[str, ...]  # authored material and table-visible lines
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
              context_chars: int = CONTEXT_CHARS, voice_chars: int = DEFAULT_MAX_CHARS) -> NpcBrief:
    """Build one NPC's sealed brief from its own view; ``secrets`` has no default on purpose.

    ``view`` must carry the actor's own ``ActorKnowledge`` (``no_knowledge``/``foreign_knowledge``
    otherwise). ``sections`` is the actor's composed card (``snapshot.effective(...).sections``), for
    context. ``heard`` is what the actor heard aloud before this turn's calls: its text counts as
    learned, as a line spoken earlier this turn does, so the caller must pass only lines this actor
    heard. A speaker label is not learned: one that states a hidden secret is shown as "Someone".
    ``names`` labels other actors in knowledge and in heard lines that carry an ``actor``.
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
    sections = tuple(entry for entry in _sections(sections) if entry[0] != VOICE_SECTION)
    names = dict(names or {})
    if not all(isinstance(key, InstanceRef) and isinstance(value, str) for key, value in names.items()):
        raise GenerationError("bad_names", "names must map InstanceRef values to display names")
    knowledge_chars = _budget(knowledge_chars, "knowledge_chars")
    context_chars = _budget(context_chars, "context_chars")

    known = known_ids(knowledge)
    hidden = tuple(secret for secret in secrets if secret.fact_id not in known)
    context = _context(view.description, sections, context_chars)
    # What the actor holds, in full: what its projection and its own card say, whatever the budget
    # showed. Names come after, screened against this, so a label never exposes a secret.
    own = _contents(knowledge) + ((view.description,) if has_words(view.description) else ()) + tuple(
        text for _name, text in sections)
    rendered = [text for _key, text in _knowledge_items(knowledge, names, hidden, own)]
    try:
        fragment = speaker_fragment(voice, secrets=secrets, known=known, max_chars=voice_chars)
    except ValueError as error:
        raise GenerationError(getattr(error, "reason", "bad_voice"), f"{actor} voice: {error}") from None
    name = _one_line(voice.name)
    if len(name) > MAX_NAME_CHARS:
        raise GenerationError("bad_voice", f"{actor} has a name longer than {MAX_NAME_CHARS} characters")
    shown = _newest_within(rendered, knowledge_chars)
    parts = [
        f"You are {name}, a character in a shared story. Write only {name}'s next contribution: what {name} "
        f"says and does, in {name}'s own voice. Never write for any other character, and never describe what "
        "anyone else thinks, feels or decides.",
        fragment.text,
    ]
    if context:
        parts.append("Who you are:\n" + "\n".join(context))
    if shown:
        parts.append("What you know, oldest first:\n" + "\n".join(f"- {text}" for text in shown))
    elif not rendered:
        parts.append("You know nothing yet beyond what you hear now.")
    names[actor] = name
    return NpcBrief(actor, name, voice, fragment, "\n\n".join(parts), heard, hidden,
                    own + (name,) + tuple(line.text for line in heard), tuple(names.items()), _SEAL)


def director_brief(scenario: str, *, campaign_id: str, present: Iterable[str],
                   controls: Iterable[ControlAssignment], names: Mapping[str, str], heard: Iterable[HeardLine] = (),
                   secrets: Iterable[SecretFact], scenario_chars: int = SCENARIO_CHARS) -> DirectorBrief:
    """The scene-level reply's sealed brief: authored scenario text and table-visible lines only.

    The player characters are derived here from ``controls``, never passed in: every ``present``
    actor whose controller is a human. A present actor with no controller is ``unassigned_actor``.
    ``names`` must name each of them (``bad_players``), and the
    brief forbids writing their words, thoughts, feelings or decisions. ``run_turn`` checks the
    same set against the plan and controls before any call. The reply is an unreviewed proposal:
    035's ``review_narration`` reviews it before it is kept.
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
    secrets = _secrets(secrets)
    heard = heard_lines(heard)
    parts = ["You are the narrator of a shared story. Describe the scene and what happens next, and voice only "
             "minor unnamed background figures. Write one short narration."]
    if players:
        listed = ", ".join(_one_line(name) for name in labels)  # type: ignore[arg-type]
        parts.append(f"The player characters are: {listed}. Never write their words, thoughts, feelings or "
                     "decisions; their players do that.")
    parts.append("Scenario:\n" + scenario.strip())
    # The player characters' names are table-visible: naming them in narration is not a leak.
    return DirectorBrief(campaign_id, frozenset(players), "\n\n".join(parts), heard, secrets,
                         (scenario, *labels) + tuple(line.text for line in heard), _SEAL)


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
